from __future__ import annotations

import argparse
from pathlib import Path
import shlex
import sys
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_ROOT = PROJECT_ROOT.parent
for path in (PROJECT_ROOT, REPOSITORY_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from automation.jobs.deeplink import (  # noqa: E402
    FacebookJobRouter,
    FacebookJobTargetResolver,
    InvalidFacebookUrl,
)
from automation.jobs.navigation_verifier import (  # noqa: E402
    NavigationProbeState,
    NavigationVerifier,
)
from core.adb_device import ADBDevice  # noqa: E402
from core.config import FACEBOOK_PACKAGE_NAME  # noqa: E402
from integrations.golike_api import JobAction, NormalizedJob  # noqa: E402

_DISPLAY_IDENTITY_KEYS = {"story_fbid", "id", "fbid", "v"}


def display_url(url: str) -> str:
    parsed = urlsplit(url)
    query = urlencode(
        [
            (key, value if key.casefold() in _DISPLAY_IDENTITY_KEYS else "[redacted]")
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
    )
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve and observe one explicit Facebook VIEW route."
    )
    parser.add_argument("--serial", required=True, help="ADB serial, e.g. localhost:5557")
    parser.add_argument("--url", required=True, help="Facebook http/https URL")
    parser.add_argument("--adb", default="adb", help="Path to adb.exe")
    parser.add_argument("--object-id", default=None)
    parser.add_argument("--seconds", type=float, default=12.0)
    parser.add_argument("--poll", type=float, default=1.0)
    parser.add_argument(
        "--screenshot",
        default="",
        help="Final screenshot path; defaults under screenshots/.",
    )
    return parser.parse_args()


def make_job(url: str, object_id: str | None) -> NormalizedJob:
    return NormalizedJob(
        id="diagnostic-only",
        object_id=object_id,
        link=url,
        provider_type="like",
        action=JobAction.LIKE,
        reaction="like",
        reward=None,
        raw={},
    )


def main() -> int:
    args = parse_args()
    if args.seconds <= 0 or args.poll <= 0:
        raise SystemExit("--seconds and --poll must be positive")

    device = ADBDevice(Path(args.adb), args.serial)
    if device.get_state() != "device":
        device.connect()
    if device.get_state() != "device":
        print(f"ADB_OFFLINE serial={args.serial}")
        return 2
    if not device.is_package_installed(FACEBOOK_PACKAGE_NAME):
        print(f"FACEBOOK_NOT_INSTALLED package={FACEBOOK_PACKAGE_NAME}")
        return 3

    resolver = FacebookJobTargetResolver(short_link_resolve_timeout_sec=8.0)
    router = FacebookJobRouter(
        FACEBOOK_PACKAGE_NAME,
        target_resolver=resolver,
    )
    try:
        target = router.prepare_target(make_job(args.url, args.object_id))
    except InvalidFacebookUrl as exc:
        print(f"JOB_TARGET_INVALID {exc}")
        return 4

    print(f"SDK={device.android_sdk_version() or 'unknown'}")
    print(
        "Facebook versionName="
        f"{device.package_version_name(FACEBOOK_PACKAGE_NAME) or 'unknown'} "
        "versionCode="
        f"{device.package_version_code(FACEBOOK_PACKAGE_NAME) or 'unknown'}"
    )
    print(f"source={target.source}")
    print(f"normalized_original_url={display_url(target.candidate_urls[-1])}")
    print(f"canonical_url={display_url(target.canonical_url)}")
    print(f"candidate_count={len(target.candidate_urls)}")
    for index, value in enumerate(target.candidate_urls, start=1):
        print(f"candidate_{index}={display_url(value)}")

    candidate = target.candidate_urls[0]
    components = device.query_view_activities(
        candidate,
        FACEBOOK_PACKAGE_NAME,
        user_id=0,
    )
    display_command = [
        display_url(value) if value == candidate else value
        for value in device.last_view_activity_query_args
    ]
    print("query_activity_command=" + shlex.join(display_command))
    print("facebook_components=" + (", ".join(components) if components else "<none>"))

    device.force_stop(FACEBOOK_PACKAGE_NAME)
    verifier = NavigationVerifier(package_name=FACEBOOK_PACKAGE_NAME)
    baseline = verifier.capture_baseline(device)
    opened = router.open_candidate(device, target, candidate)
    print(f"selected_component={opened.resolved_component or '<none>'}")
    print(
        "launch_result="
        f"accepted={opened.command_accepted} status={opened.status} "
        f"activity={opened.launched_activity or '-'} "
        f"ThisTime={opened.this_time_ms} TotalTime={opened.total_time_ms} "
        f"WaitTime={opened.wait_time_ms} security={opened.security_rejected}"
    )

    conclusion = opened.status
    if opened.command_accepted:
        start = time.monotonic()
        ready_samples = 0
        home_samples = 0
        saw_transition = False
        conclusion = "FACEBOOK_ROUTE_TIMEOUT"
        while time.monotonic() - start < args.seconds:
            elapsed = time.monotonic() - start
            probe = verifier.probe(
                device=device,
                open_result=opened,
                expected_url=candidate,
                object_id=target.expected_object_id,
                baseline=baseline,
                raw_job={},
                elapsed_sec=elapsed,
                initial_grace_sec=3.0,
            )
            saw_transition = bool(
                saw_transition
                or probe.route_transition_observed
                or probe.state is NavigationProbeState.ROUTER_ACTIVITY
            )
            activity = probe.activity.replace("\r", " ").replace("\n", " ")[:140]
            print(
                f"{elapsed:04.1f}s state={probe.state.value} "
                f"foreground={probe.foreground} layout={probe.layout_match} "
                f"identity={probe.intent_match} activity={activity or '-'}"
            )
            ready_samples = (
                ready_samples + 1
                if probe.state is NavigationProbeState.TARGET_READY
                else 0
            )
            home_samples = (
                home_samples + 1
                if probe.state is NavigationProbeState.RETURNED_HOME
                else 0
            )
            if ready_samples >= 2:
                conclusion = "TARGET_READY"
                break
            if saw_transition and home_samples >= 2:
                conclusion = "FACEBOOK_ROUTE_RETURNED_HOME"
                break
            if probe.state in {
                NavigationProbeState.BLOCKED,
                NavigationProbeState.TERMINAL_ERROR,
                NavigationProbeState.WRONG_APP,
            }:
                conclusion = probe.state.value.upper()
                break
            time.sleep(args.poll)

    screenshot = (
        Path(args.screenshot)
        if args.screenshot
        else PROJECT_ROOT
        / "screenshots"
        / f"facebook_route_probe_{int(time.time())}.png"
    )
    if not screenshot.is_absolute():
        screenshot = PROJECT_ROOT / screenshot
    saved = device.screenshot(screenshot)
    print(f"final_screenshot={screenshot if saved else '<capture-failed>'}")
    print(f"CONCLUSION={conclusion}")
    return 0 if conclusion == "TARGET_READY" else 5


if __name__ == "__main__":
    raise SystemExit(main())
