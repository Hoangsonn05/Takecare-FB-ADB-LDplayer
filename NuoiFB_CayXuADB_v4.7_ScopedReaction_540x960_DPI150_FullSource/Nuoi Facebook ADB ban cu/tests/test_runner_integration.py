from __future__ import annotations

from types import SimpleNamespace
import threading
import time
import unittest

import numpy as np

from automation.jobs.config import JobAutomationConfig
from automation.jobs.deeplink import FacebookJobTarget, FacebookRouteOpenResult
from automation.jobs.executors.base import ExecutorRegistry, ExecutorResult
from automation.jobs.failure_codes import FailureCode
from automation.jobs.models import ContentKind, DeviceAccountBinding
from automation.jobs.navigation_verifier import (
    NavigationBaseline,
    NavigationProbe,
    NavigationProbeState,
)
from automation.jobs.runner import JobRunner
from core.adb_device import ActivitySnapshot
from core.config import AppSettings, RuntimeConfig
from integrations.golike_api import GoLikeApiError, JobAction, NormalizedJob
from tests.test_detectors import feed_ui_xml


class FakeClock:
    def __init__(self, stop_event: threading.Event) -> None:
        self.now = 0.0
        self.stop_event = stop_event

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> bool:
        if self.stop_event.is_set():
            return False
        self.now += max(0.0, float(seconds))
        return not self.stop_event.is_set()


class FakeDevice:
    def __init__(self) -> None:
        self.serial = "localhost:5557"
        self.image = np.full((700, 400, 3), 230, dtype=np.uint8)
        self.force_stop_calls = 0
        self.back_calls = 0
        self.monkey_calls = 0

    def capture_screen_image(self):
        return self.image.copy()

    def dump_ui_xml_text(self) -> str:
        return feed_ui_xml(width=400, y=580)

    def force_stop(self, _package_name: str):
        self.force_stop_calls += 1
        return SimpleNamespace(ok=True)

    def android_sdk_version(self) -> str:
        return "32"


class FakeBootstrap:
    last_device: FakeDevice | None = None
    launch_app_values: list[bool] = []

    def __init__(self, _index, _settings, config, *_args, **_kwargs) -> None:
        self.device = FakeDevice()
        FakeBootstrap.last_device = self.device
        FakeBootstrap.launch_app_values.append(config.launch_app)

    def bootstrap(self):
        return SimpleNamespace(device=self.device, ld=None, screen_size=None)


class FakeService:
    def __init__(
        self,
        job: NormalizedJob,
        responses: list[object] | None = None,
    ) -> None:
        self.job = job
        self.responses = list(responses or [])
        self.submit_calls = 0
        self.report_calls = 0
        self.get_calls = 0
        self.low_job_values: list[int] = []

    def verify_auth(self):
        return SimpleNamespace(username="tester")

    def get_publisher_server(self) -> str:
        return "sv-test"

    def get_jobs(self, *, low_job: int = 0, **_kwargs):
        self.get_calls += 1
        self.low_job_values.append(low_job)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, Exception):
                raise response
            return response
        return [self.job]

    def submit(self, **_kwargs):
        self.submit_calls += 1
        return SimpleNamespace(reward=5)

    def report(self, **_kwargs):
        self.report_calls += 1
        return SimpleNamespace(server=None)

    def close(self) -> None:
        pass


class FakeRouter:
    def __init__(
        self,
        clock: FakeClock,
        *,
        candidates: tuple[str, ...],
        launch_statuses: tuple[str, ...] = (),
    ) -> None:
        self.clock = clock
        self.candidates = candidates
        self.launch_statuses = list(launch_statuses)
        self.open_urls: list[str] = []
        self.open_times: list[float] = []

    def prepare_target(self, job: NormalizedJob) -> FacebookJobTarget:
        return FacebookJobTarget(
            original_url=job.link or "",
            canonical_url=self.candidates[0],
            candidate_urls=self.candidates,
            expected_object_id=job.object_id,
            expected_content_kind=ContentKind.FEED,
            source="fake",
        )

    def open_candidate(self, _device, _target, candidate_url: str):
        self.open_urls.append(candidate_url)
        self.open_times.append(self.clock.monotonic())
        status = self.launch_statuses.pop(0) if self.launch_statuses else "ok"
        security = status == "security"
        accepted = status == "ok"
        status_text = {
            "ok": "ok",
            "security": "FACEBOOK_ROUTE_COMPONENT_REJECTED",
            "handler": "FACEBOOK_VIEW_HANDLER_NOT_FOUND",
            "command": "FACEBOOK_ROUTE_COMMAND_FAILED",
        }[status]
        return FacebookRouteOpenResult(
            command_accepted=accepted,
            candidate_url=candidate_url,
            resolved_component=(
                "com.facebook.katana/.ExportedViewHandler"
                if status != "handler"
                else ""
            ),
            status=status_text,
            launched_activity="com.facebook.katana/.ExportedViewHandler",
            this_time_ms=10 if accepted else None,
            total_time_ms=20 if accepted else None,
            wait_time_ms=20 if accepted else None,
            stdout="Status: ok" if accepted else "",
            stderr="SecurityException" if security else "",
            security_rejected=security,
            url_family="feed",
            facebook_version_code="100",
            queried_activity_count=1 if status != "handler" else 0,
            returncode=0 if accepted else 1,
        )


class FakeProbeVerifier:
    def __init__(
        self,
        states,
        *,
        stop_event: threading.Event | None = None,
        stop_on_probe: int | None = None,
    ) -> None:
        self.states = list(states)
        self.probe_calls = 0
        self.stop_event = stop_event
        self.stop_on_probe = stop_on_probe

    def capture_baseline(self, device) -> NavigationBaseline:
        return NavigationBaseline(
            device.capture_screen_image(),
            device.dump_ui_xml_text(),
            ActivitySnapshot(),
        )

    def probe(self, **_kwargs) -> NavigationProbe:
        self.probe_calls += 1
        if self.stop_event is not None and self.probe_calls == self.stop_on_probe:
            self.stop_event.set()
        item = self.states.pop(0) if self.states else NavigationProbeState.LOADING
        if isinstance(item, Exception):
            raise item
        state = item
        ready = state is NavigationProbeState.TARGET_READY
        transition = state in {
            NavigationProbeState.ROUTER_ACTIVITY,
            NavigationProbeState.LOADING,
        }
        return NavigationProbe(
            state=state,
            confidence=0.9 if ready else 0.3,
            evidence=[f"fake:{state.value}"],
            activity="com.facebook.katana/.Content",
            observed_content_kind=ContentKind.FEED,
            image=np.full((700, 400, 3), 220, dtype=np.uint8),
            ui_xml=feed_ui_xml(width=400, y=580),
            foreground=state is not NavigationProbeState.WRONG_APP,
            screen_not_blank=state is not NavigationProbeState.LOADING,
            layout_match=ready,
            intent_match=ready,
            home_detected=state is NavigationProbeState.RETURNED_HOME,
            router_activity=state is NavigationProbeState.ROUTER_ACTIVITY,
            route_transition_observed=transition,
        )


class FakeExecutor:
    def __init__(self, result: ExecutorResult) -> None:
        self.result = result
        self.calls = 0

    def execute(self, context, _device, _stop_event):
        self.calls += 1
        if self.result.action_verified:
            context.action_verified = True
        return self.result


def make_job(link: str = "https://www.facebook.com/user/posts/456") -> NormalizedJob:
    return NormalizedJob(
        id="123",
        object_id="456",
        link=link,
        provider_type="like",
        action=JobAction.LIKE,
        reaction="like",
        reward=5,
        raw={},
    )


def make_runner(
    *,
    service: FakeService,
    verifier: FakeProbeVerifier,
    executor: FakeExecutor,
    candidates: tuple[str, ...] | None = None,
    launch_statuses: tuple[str, ...] = (),
    stop_event: threading.Event | None = None,
    timeout: float = 6.0,
) -> tuple[JobRunner, FakeRouter, FakeClock]:
    stop = stop_event or threading.Event()
    clock = FakeClock(stop)
    settings = AppSettings(
        ld_base=".",
        runtime_config=RuntimeConfig(
            run_minutes=1,
            run_newfeed=True,
            run_reel=False,
            run_notifications=False,
        ),
    )
    config = JobAutomationConfig(
        max_jobs=1,
        between_jobs_min_sec=0,
        between_jobs_max_sec=0,
        error_cooldown_sec=0,
        job_route_timeout_sec=timeout,
        job_route_poll_interval_sec=1,
        job_route_ready_stable_samples=2,
        job_route_initial_grace_sec=0,
        enabled_actions={JobAction.LIKE},
        save_failure_evidence=False,
    )
    binding = DeviceAccountBinding(
        ld_index=1,
        adb_serial="localhost:5557",
        facebook_uid="123456789",
        provider_account_id="99",
        facebook_name="Tester",
    )
    registry = ExecutorRegistry()
    registry.register({JobAction.LIKE}, executor)
    route_candidates = candidates or (service.job.link or "",)
    router = FakeRouter(
        clock,
        candidates=route_candidates,
        launch_statuses=launch_statuses,
    )
    runner = JobRunner(
        binding=binding,
        settings=settings,
        config=config,
        auth_token="test-token",
        stop_event=stop,
        pause_event=threading.Event(),
        service_factory=lambda _token: service,
        bootstrap_factory=FakeBootstrap,
        router=router,
        navigation_verifier=verifier,
        executor_registry=registry,
        monotonic_fn=clock.monotonic,
    )
    runner._sleep_interruptible = clock.sleep
    return runner, router, clock


class RunnerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeBootstrap.launch_app_values.clear()

    def test_route_success_opens_executes_and_submits_once(self) -> None:
        service = FakeService(make_job())
        executor = FakeExecutor(ExecutorResult(True, True, message="verified"))
        verifier = FakeProbeVerifier(
            [
                NavigationProbeState.ROUTER_ACTIVITY,
                NavigationProbeState.LOADING,
                NavigationProbeState.TARGET_READY,
                NavigationProbeState.TARGET_READY,
            ]
        )
        runner, router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
        )
        stats = runner.run()
        self.assertEqual(stats.jobs_completed, 1)
        self.assertEqual(router.open_urls, [make_job().link])
        self.assertEqual(executor.calls, 1)
        self.assertEqual(service.submit_calls, 1)
        self.assertEqual(service.report_calls, 0)
        self.assertEqual(FakeBootstrap.launch_app_values, [False])
        self.assertEqual(FakeBootstrap.last_device.force_stop_calls, 1)

    def test_disabled_jobs_are_skipped_before_an_enabled_reaction(self) -> None:
        reaction = make_job()
        follow = NormalizedJob(
            id="124",
            object_id="follow-target",
            link="https://www.facebook.com/follow-target",
            provider_type="follow",
            action=JobAction.FOLLOW,
            reaction=None,
            reward=1,
            raw={},
        )
        like_page = NormalizedJob(
            id="125",
            object_id="page-target",
            link="https://www.facebook.com/page-target",
            provider_type="like_page",
            action=JobAction.LIKE_PAGE,
            reaction=None,
            reward=1,
            raw={},
        )
        service = FakeService(
            reaction,
            responses=[[follow, like_page, reaction]],
        )
        executor = FakeExecutor(ExecutorResult(True, True, message="verified"))
        verifier = FakeProbeVerifier(
            [
                NavigationProbeState.TARGET_READY,
                NavigationProbeState.TARGET_READY,
            ]
        )
        runner, router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
        )

        stats = runner.run()

        self.assertEqual(service.get_calls, 1)
        self.assertEqual(router.open_urls, [reaction.link])
        self.assertEqual(executor.calls, 1)
        self.assertEqual(service.submit_calls, 1)
        self.assertEqual(service.report_calls, 0)
        self.assertEqual(stats.jobs_received, 1)
        self.assertEqual(stats.jobs_completed, 1)
        self.assertEqual(stats.jobs_failed, 0)
        self.assertEqual(stats.jobs_reported, 0)

    def test_disabled_only_responses_advance_low_job_fallback(self) -> None:
        reaction = make_job()
        follow = NormalizedJob(
            id="124",
            object_id="follow-target",
            link="https://www.facebook.com/follow-target",
            provider_type="follow",
            action=JobAction.FOLLOW,
            reaction=None,
            reward=1,
            raw={},
        )
        like_page = NormalizedJob(
            id="125",
            object_id="page-target",
            link="https://www.facebook.com/page-target",
            provider_type="like_page",
            action=JobAction.LIKE_PAGE,
            reaction=None,
            reward=1,
            raw={},
        )
        service = FakeService(
            reaction,
            responses=[[follow], [like_page], [follow], [], [reaction]],
        )
        executor = FakeExecutor(ExecutorResult(True, True, message="verified"))
        verifier = FakeProbeVerifier(
            [
                NavigationProbeState.TARGET_READY,
                NavigationProbeState.TARGET_READY,
            ]
        )
        runner, router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
        )

        stats = runner.run()

        self.assertEqual(service.low_job_values, [0, 0, 0, 1, 0])
        self.assertEqual(router.open_urls, [reaction.link])
        self.assertEqual(service.report_calls, 0)
        self.assertEqual(stats.jobs_received, 1)
        self.assertEqual(stats.jobs_completed, 1)
        self.assertEqual(stats.jobs_failed, 0)

    def test_low_job_mode_is_one_shot_after_three_empty_high_pulls(self) -> None:
        job = make_job()
        service = FakeService(
            job,
            responses=[[], [], [], [], [], [], [], [], [job]],
        )
        executor = FakeExecutor(ExecutorResult(True, True, message="verified"))
        verifier = FakeProbeVerifier(
            [
                NavigationProbeState.TARGET_READY,
                NavigationProbeState.TARGET_READY,
            ]
        )
        runner, _router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
        )
        runner.run()
        self.assertEqual(
            service.low_job_values,
            [0, 0, 0, 1, 0, 0, 0, 1, 0],
        )

    def test_low_job_fallback_can_be_disabled(self) -> None:
        job = make_job()
        service = FakeService(job, responses=[[], [], [job]])
        executor = FakeExecutor(ExecutorResult(True, True, message="verified"))
        verifier = FakeProbeVerifier(
            [
                NavigationProbeState.TARGET_READY,
                NavigationProbeState.TARGET_READY,
            ]
        )
        runner, _router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
        )
        runner.config.low_job_fallback_enabled = False
        runner.run()
        self.assertEqual(service.low_job_values, [0, 0, 0])

    def test_get_job_error_does_not_count_as_empty_high_pull(self) -> None:
        job = make_job()
        service = FakeService(
            job,
            responses=[GoLikeApiError("offline"), [], [], [], [], [job]],
        )
        executor = FakeExecutor(ExecutorResult(True, True, message="verified"))
        verifier = FakeProbeVerifier(
            [
                NavigationProbeState.TARGET_READY,
                NavigationProbeState.TARGET_READY,
            ]
        )
        runner, _router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
        )
        runner.run()
        self.assertEqual(service.low_job_values, [0, 0, 0, 0, 1, 0])

    def test_unexpected_get_job_error_recovers_without_exiting_worker(self) -> None:
        job = make_job()
        service = FakeService(job, responses=[RuntimeError("bad contract"), [job]])
        executor = FakeExecutor(ExecutorResult(True, True, message="verified"))
        verifier = FakeProbeVerifier(
            [
                NavigationProbeState.TARGET_READY,
                NavigationProbeState.TARGET_READY,
            ]
        )
        runner, _router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
        )
        stats = runner.run()
        self.assertEqual(stats.jobs_completed, 1)
        self.assertEqual(service.low_job_values, [0, 0])

    def test_returned_home_never_executes_and_reports_once(self) -> None:
        service = FakeService(make_job())
        executor = FakeExecutor(ExecutorResult(True, True))
        verifier = FakeProbeVerifier(
            [
                NavigationProbeState.ROUTER_ACTIVITY,
                NavigationProbeState.LOADING,
                NavigationProbeState.RETURNED_HOME,
                NavigationProbeState.RETURNED_HOME,
            ]
        )
        runner, router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
        )
        stats = runner.run()
        self.assertEqual(len(router.open_urls), 1)
        self.assertEqual(executor.calls, 0)
        self.assertEqual(service.submit_calls, 0)
        self.assertEqual(service.report_calls, 1)
        self.assertEqual(stats.jobs_reported, 1)

    def test_two_distinct_candidates_are_each_launched_once(self) -> None:
        canonical = "https://www.facebook.com/user/posts/456"
        original = "https://www.facebook.com/share/p/token/"
        service = FakeService(make_job(original))
        executor = FakeExecutor(ExecutorResult(True, True))
        verifier = FakeProbeVerifier(
            [
                NavigationProbeState.ROUTER_ACTIVITY,
                NavigationProbeState.RETURNED_HOME,
                NavigationProbeState.RETURNED_HOME,
                NavigationProbeState.ROUTER_ACTIVITY,
                NavigationProbeState.RETURNED_HOME,
                NavigationProbeState.RETURNED_HOME,
            ]
        )
        runner, router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
            candidates=(canonical, original),
        )
        runner.run()
        self.assertEqual(router.open_urls, [canonical, original])
        self.assertEqual(len(set(router.open_urls)), 2)
        self.assertEqual(executor.calls, 0)
        self.assertEqual(service.submit_calls, 0)
        self.assertEqual(service.report_calls, 1)

    def test_security_rejection_is_system_failure_and_not_reported(self) -> None:
        service = FakeService(make_job())
        executor = FakeExecutor(ExecutorResult(True, True))
        verifier = FakeProbeVerifier([])
        runner, router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
            launch_statuses=("security",),
        )
        runner.run()
        self.assertEqual(len(router.open_urls), 1)
        self.assertEqual(verifier.probe_calls, 0)
        self.assertEqual(executor.calls, 0)
        self.assertEqual(service.submit_calls, 0)
        self.assertEqual(service.report_calls, 0)

    def test_handler_not_found_does_not_open_home_or_report_job(self) -> None:
        service = FakeService(make_job())
        executor = FakeExecutor(ExecutorResult(True, True))
        verifier = FakeProbeVerifier([])
        runner, router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
            launch_statuses=("handler",),
        )
        runner.run()
        device = FakeBootstrap.last_device
        self.assertEqual(len(router.open_urls), 1)
        self.assertEqual(device.force_stop_calls, 1)
        self.assertEqual(device.back_calls, 0)
        self.assertEqual(device.monkey_calls, 0)
        self.assertEqual(executor.calls, 0)
        self.assertEqual(service.report_calls, 0)

    def test_probe_system_failure_is_not_reported(self) -> None:
        service = FakeService(make_job())
        executor = FakeExecutor(ExecutorResult(True, True))
        verifier = FakeProbeVerifier([RuntimeError("ADB unavailable")] * 3)
        runner, _router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
            timeout=3,
        )
        runner.run()
        self.assertEqual(executor.calls, 0)
        self.assertEqual(service.submit_calls, 0)
        self.assertEqual(service.report_calls, 0)

    def test_route_timeout_reports_once_without_relaunch(self) -> None:
        service = FakeService(make_job())
        executor = FakeExecutor(ExecutorResult(True, True))
        verifier = FakeProbeVerifier([NavigationProbeState.LOADING] * 4)
        runner, router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
            timeout=3,
        )
        runner.run()
        self.assertEqual(len(router.open_urls), 1)
        self.assertEqual(executor.calls, 0)
        self.assertEqual(service.submit_calls, 0)
        self.assertEqual(service.report_calls, 1)

    def test_unknown_action_result_is_not_repeated_or_submitted(self) -> None:
        service = FakeService(make_job())
        executor = FakeExecutor(
            ExecutorResult(
                False,
                False,
                FailureCode.ACTION_RESULT_UNKNOWN,
                "uncertain",
            )
        )
        verifier = FakeProbeVerifier(
            [
                NavigationProbeState.TARGET_READY,
                NavigationProbeState.TARGET_READY,
            ]
        )
        runner, _router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
        )
        runner.run()
        self.assertEqual(executor.calls, 1)
        self.assertEqual(service.submit_calls, 0)
        self.assertEqual(service.report_calls, 1)

    def test_stop_during_route_does_not_open_next_candidate_or_call_api(self) -> None:
        service = FakeService(make_job())
        executor = FakeExecutor(ExecutorResult(True, True))
        stop_event = threading.Event()
        verifier = FakeProbeVerifier(
            [NavigationProbeState.ROUTER_ACTIVITY],
            stop_event=stop_event,
            stop_on_probe=1,
        )
        runner, router, _clock = make_runner(
            service=service,
            verifier=verifier,
            executor=executor,
            candidates=(make_job().link, "https://www.facebook.com/story.php?story_fbid=456&id=1"),
            stop_event=stop_event,
            timeout=20,
        )
        started = time.monotonic()
        runner.run()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(len(router.open_urls), 1)
        self.assertEqual(executor.calls, 0)
        self.assertEqual(service.submit_calls, 0)
        self.assertEqual(service.report_calls, 0)


if __name__ == "__main__":
    unittest.main()
