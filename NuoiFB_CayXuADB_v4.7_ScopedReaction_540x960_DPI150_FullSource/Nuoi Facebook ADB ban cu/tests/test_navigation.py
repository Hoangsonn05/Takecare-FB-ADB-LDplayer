from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest

import numpy as np

from automation.jobs.deeplink import (
    FacebookJobRouter,
    FacebookJobTargetResolver,
    InvalidFacebookRedirect,
    InvalidFacebookUrl,
)
from automation.jobs.models import ContentKind
from automation.jobs.navigation_verifier import (
    NavigationBaseline,
    NavigationProbeState,
    NavigationVerifier,
)
from core.adb_device import ADBDevice, ActivitySnapshot
from core.command_runner import CommandResult
from integrations.golike_api import JobAction, NormalizedJob
from tests.test_detectors import feed_ui_xml


def make_job(link: str, object_id: str | None = "456") -> NormalizedJob:
    return NormalizedJob(
        id="123",
        object_id=object_id,
        link=link,
        provider_type="like",
        action=JobAction.LIKE,
        reaction="like",
        reward=5,
        raw={},
    )


class FakeResponse:
    def __init__(self, status: int, url: str, location: str = "") -> None:
        self.status_code = status
        self.url = url
        self.headers = {"Location": location} if location else {}

    def close(self) -> None:
        pass


class ProbeDevice:
    def __init__(self, image, ui_xml: str, snapshot: ActivitySnapshot) -> None:
        self.image = image
        self.ui_xml = ui_xml
        self.snapshot = snapshot

    def capture_screen_image(self):
        return self.image.copy()

    def dump_ui_xml_text(self):
        return self.ui_xml

    def activity_snapshot(self):
        return self.snapshot

    def is_package_foreground(self, package_name: str):
        return self.snapshot.package_name == package_name


class RecordingRunner:
    def __init__(self, outputs=None) -> None:
        self.outputs = list(outputs or [])
        self.calls: list[list[str]] = []

    def run(self, args, **_kwargs):
        normalized = [str(arg) for arg in args]
        self.calls.append(normalized)
        stdout, stderr, returncode = (
            self.outputs.pop(0) if self.outputs else ("", "", 0)
        )
        return CommandResult(normalized, returncode, stdout, stderr)


class FakeRouteDevice:
    def __init__(self) -> None:
        self.serial = "localhost:5557"
        self.version = "100"
        self.query_calls = 0
        self.launch_calls: list[tuple[str, str, int]] = []

    def package_version_code(self, _package: str) -> str:
        return self.version

    def query_view_activities(self, _url: str, _package: str, user_id: int = 0):
        self.query_calls += 1
        return [
            "com.android.chrome/.Main",
            "com.facebook.katana/.MainActivity",
            "com.facebook.katana/.ExportedViewHandler",
        ]

    def query_launcher_activities(self, _package: str, user_id: int = 0):
        return ["com.facebook.katana/.MainActivity"]

    def launch_explicit_view_activity(self, *, component: str, url: str, user_id: int = 0):
        self.launch_calls.append((component, url, user_id))
        return CommandResult(
            ["adb"],
            0,
            "Status: ok\nActivity: com.facebook.katana/.ExportedViewHandler\n"
            "ThisTime: 10\nTotalTime: 20\nWaitTime: 20\n",
            "",
        )


class NavigationTests(unittest.TestCase):
    def test_legacy_auto_job_transport_symbols_are_absent(self) -> None:
        project_root = Path(__file__).resolve().parents[1]
        source = "\n".join(
            path.read_text(encoding="utf-8")
            for folder in ("automation", "core", "gui")
            for path in (project_root / folder).rglob("*.py")
        )
        legacy_symbols = (
            "open_url_" + "in_package",
            "hard_" + "recover",
            "deeplink_max_" + "attempts",
            "deeplink_retry_" + "delay_sec",
            "_prepare_navigation_" + "retry",
        )
        for symbol in legacy_symbols:
            self.assertNotIn(symbol, source)
        runner_source = (
            project_root / "automation" / "jobs" / "runner.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("open_app_with_" + "monkey", runner_source)
        self.assertNotIn(".back" + "(", runner_source)

    def test_direct_feed_url_keeps_identity_and_single_candidate(self) -> None:
        target = FacebookJobTargetResolver().resolve(
            make_job(
                " https://m.facebook.com/user/posts/456?fbclid=tracking&amp;ref=x#frag "
            )
        )
        self.assertEqual(
            target.canonical_url,
            "https://www.facebook.com/user/posts/456",
        )
        self.assertEqual(target.candidate_urls, (target.canonical_url,))
        self.assertEqual(target.expected_content_kind, ContentKind.FEED)

    def test_reel_url_stays_reel(self) -> None:
        target = FacebookJobTargetResolver().resolve(
            make_job("https://facebook.com/reel/456")
        )
        self.assertEqual(target.expected_content_kind, ContentKind.REEL)
        self.assertIn("/reel/456", target.canonical_url)

    def test_share_redirect_uses_canonical_then_original_once(self) -> None:
        responses = [
            FakeResponse(
                302,
                "https://www.facebook.com/share/p/token/",
                "https://www.facebook.com/user/posts/456?fbclid=drop",
            ),
            FakeResponse(200, "https://www.facebook.com/user/posts/456"),
        ]

        def request(*_args, **_kwargs):
            return responses.pop(0)

        target = FacebookJobTargetResolver(request_fn=request).resolve(
            make_job("https://www.facebook.com/share/p/token/?mibextid=drop")
        )
        self.assertEqual(
            target.candidate_urls,
            (
                "https://www.facebook.com/user/posts/456",
                "https://www.facebook.com/share/p/token/",
            ),
        )

    def test_short_redirect_outside_facebook_is_rejected(self) -> None:
        def request(*_args, **_kwargs):
            return FakeResponse(302, "https://fb.me/abc", "https://example.com/x")

        with self.assertRaises(InvalidFacebookRedirect):
            FacebookJobTargetResolver(request_fn=request).resolve(
                make_job("https://fb.me/abc", None)
            )

    def test_redirect_to_home_is_rejected(self) -> None:
        responses = [
            FakeResponse(302, "https://fb.me/abc", "https://facebook.com/"),
            FakeResponse(200, "https://www.facebook.com/"),
        ]
        with self.assertRaises(InvalidFacebookRedirect):
            FacebookJobTargetResolver(
                request_fn=lambda *_args, **_kwargs: responses.pop(0)
            ).resolve(make_job("https://fb.me/abc", None))

    def test_object_id_conflict_is_rejected(self) -> None:
        with self.assertRaises(InvalidFacebookUrl):
            FacebookJobTargetResolver().resolve(
                make_job("https://www.facebook.com/user/posts/999", "456")
            )

    def test_package_component_parser_handles_old_and_new_output(self) -> None:
        old = "com.facebook.katana/.IntentUriHandler\ncom.android.chrome/.Main"
        new = (
            "activityInfo=ActivityInfo{a com.facebook.katana/"
            "com.facebook.katana.ViewHandler}\n"
            "com.facebook.orca/.Other"
        )
        self.assertEqual(
            ADBDevice._parse_package_components(old, "com.facebook.katana"),
            ["com.facebook.katana/.IntentUriHandler"],
        )
        self.assertEqual(
            ADBDevice._parse_package_components(new, "com.facebook.katana"),
            ["com.facebook.katana/com.facebook.katana.ViewHandler"],
        )

    def test_router_excludes_launcher_and_invalidates_cache_on_version_change(self) -> None:
        device = FakeRouteDevice()
        router = FacebookJobRouter()
        target = router.prepare_target(
            make_job("https://www.facebook.com/user/posts/456")
        )
        first = router.open_candidate(device, target, target.canonical_url)
        second = router.open_candidate(device, target, target.canonical_url)
        self.assertEqual(first.resolved_component, "com.facebook.katana/.ExportedViewHandler")
        self.assertEqual(second.resolved_component, first.resolved_component)
        self.assertEqual(device.query_calls, 1)
        device.version = "101"
        router.open_candidate(device, target, target.canonical_url)
        self.assertEqual(device.query_calls, 2)

    def test_explicit_command_has_component_user_flags_and_no_package_launch(self) -> None:
        runner = RecordingRunner(
            [
                ("Activity manager commands: -f", "", 0),
                ("Status: ok", "", 0),
            ]
        )
        device = ADBDevice(Path("adb"), "localhost:5557", runner=runner)
        url = "https://www.facebook.com/user/posts/456"
        component = "com.facebook.katana/.ExportedViewHandler"
        device.launch_explicit_view_activity(
            component=component,
            url=url,
            user_id=0,
        )
        command = runner.calls[-1]
        self.assertIn("-n", command)
        self.assertIn(component, command)
        self.assertIn("android.intent.action.VIEW", command)
        self.assertIn("android.intent.category.BROWSABLE", command)
        self.assertIn(url, command)
        self.assertIn("--user", command)
        self.assertIn("0x34000000", command)
        self.assertNotIn("-p", command)
        self.assertNotIn("monkey", command)

    def test_stale_intent_from_old_task_is_not_returned(self) -> None:
        output = """
* Task{old #11}
  * Hist #0: ActivityRecord{aaa u0 com.facebook.katana/.Old t11}
    Intent { act=android.intent.action.VIEW dat=https://www.facebook.com/posts/456 }
mResumedActivity: ActivityRecord{bbb u0 com.facebook.katana/.Main t22}
* Task{new #22}
  * Hist #0: ActivityRecord{bbb u0 com.facebook.katana/.Main t22}
    Intent { act=android.intent.action.MAIN cmp=com.facebook.katana/.Main }
"""
        snapshot = ADBDevice._parse_resumed_activity_output(output)
        self.assertEqual(snapshot.task_id, "22")
        self.assertEqual(snapshot.intent_data, "")

    def test_gray_router_screen_is_router_activity(self) -> None:
        verifier = NavigationVerifier()
        baseline = NavigationBaseline(
            np.full((700, 400, 3), 180, dtype=np.uint8),
            "",
            ActivitySnapshot(),
        )
        component = "com.facebook.katana/.ExportedViewHandler"
        device = ProbeDevice(
            np.full((700, 400, 3), 160, dtype=np.uint8),
            "",
            ActivitySnapshot(
                package_name="com.facebook.katana",
                activity_line=component,
                resumed=True,
            ),
        )
        probe = verifier.probe(
            device=device,
            open_result=SimpleNamespace(
                command_accepted=True,
                status_ok=True,
                resolved_component=component,
                launched_activity=component,
            ),
            expected_url="https://www.facebook.com/user/posts/456",
            object_id="456",
            baseline=baseline,
            raw_job={},
            elapsed_sec=1,
            initial_grace_sec=3,
        )
        self.assertEqual(probe.state, NavigationProbeState.ROUTER_ACTIVITY)
        self.assertTrue(probe.route_transition_observed)

    def test_unchanged_home_feed_is_returned_home(self) -> None:
        verifier = NavigationVerifier()
        image = np.zeros((700, 400, 3), dtype=np.uint8)
        image[:, :200] = 70
        image[:, 200:] = 190
        home_xml = feed_ui_xml(width=400, y=580).replace(
            "<hierarchy>",
            '<hierarchy><node text="" content-desc="Home" resource-id="" '
            'bounds="[0,650][80,700]" clickable="true" selected="true" '
            'checked="false" />',
        )
        snapshot = ActivitySnapshot(
            package_name="com.facebook.katana",
            activity_line="com.facebook.katana/.Main",
            resumed=True,
        )
        baseline = NavigationBaseline(image.copy(), home_xml, snapshot)
        probe = verifier.probe(
            device=ProbeDevice(image, home_xml, snapshot),
            open_result=SimpleNamespace(command_accepted=True, status_ok=True),
            expected_url="https://www.facebook.com/user/posts/456",
            object_id="456",
            baseline=baseline,
            raw_job={},
            elapsed_sec=5,
            initial_grace_sec=3,
        )
        self.assertEqual(probe.state, NavigationProbeState.RETURNED_HOME)

    def test_matching_intent_and_feed_layout_is_target_ready(self) -> None:
        verifier = NavigationVerifier()
        baseline_image = np.zeros((700, 400, 3), dtype=np.uint8)
        target_image = np.zeros((700, 400, 3), dtype=np.uint8)
        target_image[:, :200] = 80
        target_image[:, 200:] = 210
        baseline = NavigationBaseline(baseline_image, "", ActivitySnapshot())
        device = ProbeDevice(
            target_image,
            feed_ui_xml(width=400, y=580),
            ActivitySnapshot(
                package_name="com.facebook.katana",
                activity_line="com.facebook.katana/.Content",
                intent_data="https://www.facebook.com/user/posts/456",
                resumed=True,
            ),
        )
        probe = verifier.probe(
            device=device,
            open_result=SimpleNamespace(command_accepted=True, status_ok=True),
            expected_url="https://www.facebook.com/user/posts/456",
            object_id="456",
            baseline=baseline,
            raw_job={},
            elapsed_sec=4,
            initial_grace_sec=3,
        )
        self.assertEqual(probe.state, NavigationProbeState.TARGET_READY)


if __name__ == "__main__":
    unittest.main()
