from __future__ import annotations

from dataclasses import dataclass, replace
from random import uniform
from typing import Callable
import threading
import time

from core.config import AppSettings
from core.paths import app_path
from core.template_matcher import TemplateMatcher
from integrations.golike_api import (
    GoLikeAuthenticationError,
    JobAction,
    REACTION_ACTIONS,
)
from integrations.golike_service import GoLikeJobService, sanitize_message

from automation.device_session import DeviceBootstrapService

from .config import JobAutomationConfig
from .deeplink import (
    FacebookJobRouter,
    FacebookJobTarget,
    FacebookJobTargetResolver,
    FacebookRouteOpenResult,
    InvalidFacebookRedirect,
    InvalidFacebookUrl,
    normalize_facebook_url,
)
from .detectors import FeedLikeLocator, ReactionPanelLocator, ReelLikeLocator, ScreenClassifier
from .events import JobAutomationEvent
from .evidence import FailureEvidenceStore
from .executors import (
    ExecutorRegistry,
    FollowExecutor,
    LikePageExecutor,
    ReactionExecutor,
)
from .failure_codes import FailureCode, should_report_failure
from .models import (
    ContentKind,
    DeviceAccountBinding,
    JobExecutionContext,
    JobExecutionResult,
    JobState,
    JobWorkerStats,
    TerminalApiAction,
)
from .navigation_verifier import (
    NavigationBaseline,
    NavigationProbe,
    NavigationProbeState,
    NavigationVerifier,
)
from .state_machine import JobStateMachine

JobEventCallback = Callable[[JobAutomationEvent], None]
ServiceFactory = Callable[[str], GoLikeJobService]


@dataclass(slots=True)
class NavigationAttemptResult:
    ready: bool
    stopped: bool
    state: NavigationProbeState
    probe: NavigationProbe | None
    elapsed_sec: float
    timed_out: bool = False
    system_failure: bool = False
    route_transition_seen: bool = False


class JobRunner:
    def __init__(
        self,
        *,
        binding: DeviceAccountBinding,
        settings: AppSettings,
        config: JobAutomationConfig,
        auth_token: str,
        stop_event: threading.Event,
        pause_event: threading.Event,
        emit: JobEventCallback | None = None,
        service_factory: ServiceFactory | None = None,
        bootstrap_factory=None,
        router: FacebookJobRouter | None = None,
        navigation_verifier: NavigationVerifier | None = None,
        executor_registry: ExecutorRegistry | None = None,
        monotonic_fn=None,
    ) -> None:
        self.binding = binding
        self.settings = settings
        self.config = config
        self._auth_token = str(auth_token or "")
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.emit_cb = emit or (lambda _event: None)
        self.service_factory = service_factory or (
            lambda token: GoLikeJobService(token, timeout_seconds=15.0, max_retries=2)
        )
        self.bootstrap_factory = bootstrap_factory or DeviceBootstrapService
        self.router = router or FacebookJobRouter(
            settings.package_name,
            target_resolver=FacebookJobTargetResolver(
                short_link_resolve_timeout_sec=(
                    config.short_link_resolve_timeout_sec
                )
            ),
        )
        self.classifier = ScreenClassifier()
        self.navigation_verifier = navigation_verifier or NavigationVerifier(
            self.classifier,
            settings.package_name,
        )
        self.executor_registry = executor_registry
        self.state_machine = JobStateMachine()
        self.state = JobState.IDLE
        self.context: JobExecutionContext | None = None
        self.stats = JobWorkerStats()
        self.processed_keys: set[tuple[str, str]] = set()
        self.service: GoLikeJobService | None = None
        self.server = ""
        self.device = None
        self.bootstrap = None
        self.evidence_store = FailureEvidenceStore()
        self._monotonic = monotonic_fn or time.monotonic

    def _emit(
        self,
        message: str,
        *,
        level: str = "INFO",
        failure_code: FailureCode | None = None,
        evidence_path: str = "",
    ) -> None:
        context = self.context
        job = context.job if context else None
        self.emit_cb(
            JobAutomationEvent(
                ld_index=self.binding.ld_index,
                facebook_uid=self.binding.facebook_uid,
                state=self.state,
                message=sanitize_message(message),
                level=level,
                job_id=job.id if job else "",
                job_type=job.provider_type if job else "",
                action=job.action.value if job else self.stats.current_action,
                failure_code=failure_code,
                evidence_path=evidence_path,
                stats=replace(self.stats),
            )
        )

    def _transition(self, next_state: JobState, message: str = "") -> None:
        self.state = self.state_machine.transition_state(self.state, next_state)
        if self.context:
            self.context.state = self.state
        self._emit(message or next_state.value)

    def _sleep_interruptible(self, seconds: float) -> bool:
        remaining = max(0.0, float(seconds))
        while remaining > 0:
            if self.stop_event.is_set():
                return False
            while self.pause_event.is_set() and not self.stop_event.is_set():
                self.stop_event.wait(0.25)
            slice_sec = min(0.25, remaining)
            if self.stop_event.wait(slice_sec):
                return False
            remaining -= slice_sec
        return not self.stop_event.is_set()

    def _build_executor_registry(self) -> ExecutorRegistry:
        template_dir = app_path(self.settings.runtime_config.template_dir)
        matcher = TemplateMatcher(
            template_dir,
            self.settings.runtime_config.confidence_like,
            self.settings.runtime_config.confidence_reaction,
            screen_width_px=self.config.screen_width_px,
            screen_height_px=self.config.screen_height_px,
            screen_dpi=self.config.screen_dpi,
        )
        feed = FeedLikeLocator(
            matcher,
            minimum_confidence=self.settings.runtime_config.confidence_like,
        )
        reel = ReelLikeLocator(
            matcher,
            self.settings.runtime_config.reel_like_template,
            self.settings.runtime_config.reel_like_confidence,
        )
        panel = ReactionPanelLocator(matcher)
        reaction = ReactionExecutor(
            config=self.config,
            feed_locator=feed,
            reel_locator=reel,
            panel_locator=panel,
            sleep_interruptible=self._sleep_interruptible,
        )
        registry = ExecutorRegistry()
        registry.register(set(REACTION_ACTIONS), reaction)
        registry.register({JobAction.FOLLOW}, FollowExecutor())
        registry.register({JobAction.LIKE_PAGE}, LikePageExecutor())
        return registry

    def _save_failure_evidence(
        self,
        image,
        code: FailureCode,
        details=None,
        initial_image=None,
        reaction_debug=None,
    ) -> str:
        if not self.config.save_failure_evidence or self.context is None:
            return ""
        image_path = self.evidence_store.save(
            image,
            ld_index=self.binding.ld_index,
            job_id=self.context.job.id,
            label=code.value,
            retention=self.config.failure_evidence_retention,
        )
        self.evidence_store.save(
            initial_image,
            ld_index=self.binding.ld_index,
            job_id=self.context.job.id,
            label=f"{code.value}_attempt_start",
            retention=self.config.failure_evidence_retention,
        )
        timeline_path = self.evidence_store.save_timeline(
            details,
            ld_index=self.binding.ld_index,
            job_id=self.context.job.id,
            label=f"{code.value}_timeline",
            retention=self.config.failure_evidence_retention,
        )
        bundle_path = self.evidence_store.save_reaction_bundle(
            reaction_debug,
            ld_index=self.binding.ld_index,
            job_id=self.context.job.id,
            retention=self.config.failure_evidence_retention,
        )
        return bundle_path or image_path or timeline_path

    def _abort_context_without_api(self) -> None:
        if (
            self.context
            and self.context.terminal_api_action is TerminalApiAction.NONE
        ):
            self.context.set_terminal_api_action(
                TerminalApiAction.ABORTED_WITHOUT_API_ACTION
            )

    def _cooldown(self, *, error: bool = False) -> bool:
        self._transition(JobState.COOLDOWN, "Cooldown")
        delay = (
            self.config.error_cooldown_sec
            if error
            else uniform(
                self.config.between_jobs_min_sec,
                self.config.between_jobs_max_sec,
            )
        )
        return self._sleep_interruptible(delay)

    def _report_failure(
        self,
        result: JobExecutionResult,
    ) -> bool:
        assert self.context is not None
        code = result.failure_code or FailureCode.JOB_INVALID
        image = result.evidence.get("image") if result.evidence else None
        details = result.evidence.get("details") if result.evidence else None
        initial_image = (
            result.evidence.get("initial_image") if result.evidence else None
        )
        reaction_debug = (
            result.evidence.get("reaction_debug") if result.evidence else None
        )
        evidence_path = self._save_failure_evidence(
            image,
            code,
            details,
            initial_image,
            reaction_debug,
        )
        self.stats.jobs_failed += 1
        if not should_report_failure(
            code,
            report_unsupported_actions=self.config.report_unsupported_actions,
        ):
            self._abort_context_without_api()
            self._emit(
                result.message,
                level="WARN",
                failure_code=code,
                evidence_path=evidence_path,
            )
            return self._cooldown(error=True)
        self._transition(JobState.REPORTING, f"Reporting {code.value}")
        description = sanitize_message(f"{code.value}: {result.message}")[:180]
        try:
            assert self.service is not None
            report = self.service.report(
                job_id=self.context.job.id,
                facebook_uid=self.binding.facebook_uid,
                description=description,
            )
            self.context.set_terminal_api_action(TerminalApiAction.REPORTED)
            self.stats.jobs_reported += 1
            if report.server:
                self.server = report.server
            self._emit(
                f"Job reported once: {code.value}",
                level="WARN",
                failure_code=code,
                evidence_path=evidence_path,
            )
            return self._cooldown(error=True)
        except Exception as exc:
            self._abort_context_without_api()
            self._emit(
                f"Report Job failed: {sanitize_message(exc)}",
                level="ERROR",
                failure_code=FailureCode.API_REPORT_FAILED,
                evidence_path=evidence_path,
            )
            self._transition(JobState.ERROR, "Worker stopped after Report failure")
            return False

    def _validate_job(self) -> JobExecutionResult | None:
        assert self.context is not None
        job = self.context.job
        if job.action not in self.config.enabled_actions:
            return JobExecutionResult(
                False,
                FailureCode.UNSUPPORTED_JOB_ACTION,
                f"Action {job.action.value} is disabled or unsupported",
            )
        if not job.link:
            return JobExecutionResult(
                False,
                FailureCode.JOB_LINK_MISSING,
                "Job does not contain a link",
            )
        try:
            normalize_facebook_url(job.link)
        except InvalidFacebookUrl as exc:
            return JobExecutionResult(
                False,
                FailureCode.JOB_TARGET_INVALID,
                sanitize_message(exc),
            )
        return None

    def _wait_for_navigation_target(
        self,
        *,
        opened: FacebookRouteOpenResult,
        baseline: NavigationBaseline,
        target: FacebookJobTarget,
    ) -> NavigationAttemptResult:
        assert self.context is not None
        assert self.device is not None
        job = self.context.job
        stable_ready = 0
        terminal_state = NavigationProbeState.UNKNOWN
        terminal_samples = 0
        last_probe: NavigationProbe | None = None
        last_logged_state: NavigationProbeState | None = None
        last_log_at = self._monotonic()
        probe_count = 0
        probe_error_count = 0
        saw_route_transition = False
        attempt_started = self.context.attempt_started_at
        self._transition(JobState.WAITING_TARGET, "Waiting for Facebook route target")
        while self._monotonic() < self.context.attempt_deadline:
            if self.stop_event.is_set():
                return NavigationAttemptResult(
                    False,
                    True,
                    last_probe.state if last_probe else NavigationProbeState.UNKNOWN,
                    last_probe,
                    max(0.0, self._monotonic() - attempt_started),
                    route_transition_seen=saw_route_transition,
                )
            pause_started = self._monotonic()
            while self.pause_event.is_set() and not self.stop_event.is_set():
                if self.stop_event.wait(0.25):
                    break
            paused_for = self._monotonic() - pause_started
            if paused_for > 0:
                self.context.attempt_deadline += paused_for
            if self.stop_event.is_set():
                continue

            now = self._monotonic()
            elapsed = max(0.0, now - attempt_started)
            try:
                probe = self.navigation_verifier.probe(
                    device=self.device,
                    open_result=opened,
                    expected_url=opened.candidate_url,
                    object_id=target.expected_object_id,
                    baseline=baseline,
                    raw_job=job.raw,
                    elapsed_sec=elapsed,
                    initial_grace_sec=self.config.job_route_initial_grace_sec,
                )
            except Exception as exc:
                probe_error_count += 1
                probe = NavigationProbe(
                    state=NavigationProbeState.UNKNOWN,
                    confidence=0.0,
                    evidence=[f"probe_error={sanitize_message(exc)}"],
                )
            last_probe = probe
            probe_count += 1
            saw_route_transition = bool(
                saw_route_transition
                or probe.route_transition_observed
                or probe.state is NavigationProbeState.ROUTER_ACTIVITY
            )
            self.context.last_probe_state = probe.state.value
            summary = (
                f"{elapsed:.1f}s state={probe.state.value}; "
                f"foreground={probe.foreground}; layout={probe.layout_match}; "
                f"identity={probe.intent_match}; home={probe.home_detected}; "
                f"activity={sanitize_message(probe.activity)[:120] or '-'}"
            )
            if probe.state is not last_logged_state:
                self.context.navigation_evidence.append(summary)

            if probe.state is NavigationProbeState.TARGET_READY:
                stable_ready += 1
                terminal_samples = 0
                if (
                    last_logged_state is not NavigationProbeState.TARGET_READY
                    or stable_ready <= self.config.job_route_ready_stable_samples
                ):
                    self._emit(
                        "Target candidate ready "
                        f"{stable_ready}/{self.config.job_route_ready_stable_samples}"
                    )
                if stable_ready >= self.config.job_route_ready_stable_samples:
                    self.context.content_kind = probe.observed_content_kind
                    self.context.navigation_evidence.extend(probe.evidence)
                    self._transition(
                        JobState.VERIFYING_TARGET,
                        "Facebook target verified and stable",
                    )
                    return NavigationAttemptResult(
                        True,
                        False,
                        probe.state,
                        probe,
                        max(0.0, self._monotonic() - attempt_started),
                        route_transition_seen=saw_route_transition,
                    )
            else:
                stable_ready = 0
                if probe.state is terminal_state:
                    terminal_samples += 1
                else:
                    terminal_state = probe.state
                    terminal_samples = 1

            should_log = (
                probe.state is not last_logged_state or now - last_log_at >= 5.0
            )
            if should_log and probe.state is not NavigationProbeState.TARGET_READY:
                detail = {
                    NavigationProbeState.ROUTER_ACTIVITY: "Facebook router activity",
                    NavigationProbeState.LOADING: "route loading or layout pending",
                    NavigationProbeState.RETURNED_HOME: "route returned to Facebook Home",
                    NavigationProbeState.WRONG_APP: "Facebook is not foreground",
                    NavigationProbeState.BLOCKED: "blocked/error page",
                    NavigationProbeState.TERMINAL_ERROR: "route command rejected",
                    NavigationProbeState.UNKNOWN: "target identity is not clear",
                }[probe.state]
                self._emit(f"Waiting for target: {detail}")
                last_log_at = now
            last_logged_state = probe.state

            if probe.state in {
                NavigationProbeState.BLOCKED,
                NavigationProbeState.TERMINAL_ERROR,
                NavigationProbeState.WRONG_APP,
            }:
                return NavigationAttemptResult(
                    False,
                    False,
                    probe.state,
                    probe,
                    max(0.0, self._monotonic() - attempt_started),
                    system_failure=probe.state is NavigationProbeState.TERMINAL_ERROR,
                    route_transition_seen=saw_route_transition,
                )
            if (
                probe.state is NavigationProbeState.RETURNED_HOME
                and saw_route_transition
                and terminal_samples >= 2
            ):
                return NavigationAttemptResult(
                    False,
                    False,
                    probe.state,
                    probe,
                    max(0.0, self._monotonic() - attempt_started),
                    route_transition_seen=True,
                )

            remaining = self.context.attempt_deadline - self._monotonic()
            if remaining <= 0:
                break
            if not self._sleep_interruptible(
                min(self.config.job_route_poll_interval_sec, remaining)
            ):
                continue

        last_state = (
            last_probe.state if last_probe else NavigationProbeState.UNKNOWN
        )
        return NavigationAttemptResult(
            False,
            False,
            last_state,
            last_probe,
            max(0.0, self._monotonic() - attempt_started),
            timed_out=True,
            system_failure=bool(
                probe_count > 0 and probe_error_count == probe_count
            ),
            route_transition_seen=saw_route_transition,
        )

    def _navigation_failure_result(
        self,
        *,
        open_results: list[FacebookRouteOpenResult],
        last_result: NavigationAttemptResult | None,
        baseline: NavigationBaseline,
    ) -> JobExecutionResult:
        state = (
            last_result.state.value
            if last_result is not None
            else NavigationProbeState.UNKNOWN.value
        )
        probe = last_result.probe if last_result is not None else None
        details = list(self.context.navigation_evidence) if self.context else []
        if probe is not None:
            details.extend(probe.evidence)
        final_details = ""
        if probe is not None:
            activity = sanitize_message(probe.activity)[:120] or "-"
            final_details = (
                f"; activity={activity}; intent_match={probe.intent_match}; "
                f"layout={probe.observed_content_kind.value}/{probe.layout_match}; "
                f"home={probe.home_detected}; blank={not probe.screen_not_blank}"
            )
        last_open = open_results[-1] if open_results else None
        security_rejected = bool(last_open and last_open.security_rejected)
        opened_any = any(result.command_accepted for result in open_results)
        if security_rejected:
            code = FailureCode.FACEBOOK_ROUTE_COMPONENT_REJECTED
            message = "Facebook rejected the dynamically resolved VIEW component"
        elif last_result is not None and last_result.system_failure:
            code = FailureCode.ADB_OFFLINE
            message = "Navigation probe failed repeatedly"
        elif opened_any and last_result is not None:
            if last_result.state is NavigationProbeState.RETURNED_HOME:
                code = FailureCode.FACEBOOK_ROUTE_RETURNED_HOME
                message = "Facebook router returned to Home before target verification"
            elif last_result.timed_out:
                code = FailureCode.FACEBOOK_ROUTE_TIMEOUT
                message = "Facebook route did not reach a verified target before timeout"
            elif last_result.state is NavigationProbeState.WRONG_APP:
                code = FailureCode.FACEBOOK_NOT_FOREGROUND
                message = "Facebook lost foreground while routing the job"
            else:
                code = FailureCode.TARGET_NOT_VERIFIED
                message = f"Facebook target was not verified; last_state={state}"
        elif last_open and last_open.status == "FACEBOOK_VIEW_HANDLER_NOT_FOUND":
            code = FailureCode.FACEBOOK_VIEW_HANDLER_NOT_FOUND
            message = "Package Manager found no exported Facebook VIEW handler"
        elif last_open and last_open.status == "FACEBOOK_ROUTE_COMPONENT_REJECTED":
            code = FailureCode.FACEBOOK_ROUTE_COMPONENT_REJECTED
            message = "Facebook rejected the dynamically resolved VIEW component"
        else:
            code = FailureCode.FACEBOOK_ROUTE_COMMAND_FAILED
            message = "Android rejected the explicit Facebook route command"
        return JobExecutionResult(
            False,
            code,
            f"{message}{final_details}",
            evidence={
                "image": probe.image if probe is not None else None,
                "initial_image": baseline.image,
                "details": details,
            },
        )

    def _route_and_verify_job(self) -> JobExecutionResult | None:
        assert self.context is not None
        assert self.device is not None
        job = self.context.job
        try:
            target = self.router.prepare_target(job)
        except InvalidFacebookRedirect as exc:
            return JobExecutionResult(
                False,
                FailureCode.JOB_LINK_REDIRECT_INVALID,
                sanitize_message(exc),
            )
        except InvalidFacebookUrl as exc:
            return JobExecutionResult(
                False,
                FailureCode.JOB_TARGET_INVALID,
                sanitize_message(exc),
            )
        self.context.target_source = target.source
        open_results: list[FacebookRouteOpenResult] = []
        last_result: NavigationAttemptResult | None = None
        baseline = self.navigation_verifier.capture_baseline(self.device)
        for candidate_index, candidate in enumerate(target.candidate_urls):
            if self.stop_event.is_set():
                return JobExecutionResult(
                    False,
                    FailureCode.STOP_REQUESTED,
                    "Stop requested",
                )
            if candidate_index:
                baseline = self.navigation_verifier.capture_baseline(self.device)
            self.context.route_open_count += 1
            self.context.attempts["route_candidate"] = candidate_index + 1
            self.context.attempt_started_at = self._monotonic()
            self.context.attempt_deadline = (
                self.context.attempt_started_at + self.config.job_route_timeout_sec
            )
            self._transition(
                JobState.NAVIGATING,
                f"Routing candidate {candidate_index + 1}/{len(target.candidate_urls)}",
            )
            opened = self.router.open_candidate(self.device, target, candidate)
            open_results.append(opened)
            if not opened.command_accepted:
                if opened.status == "FACEBOOK_VIEW_HANDLER_NOT_FOUND":
                    sdk_fn = getattr(self.device, "android_sdk_version", None)
                    sdk = sdk_fn() if callable(sdk_fn) else "unknown"
                    self._emit(
                        "Facebook VIEW handler not found; "
                        f"sdk={sdk}; versionCode={opened.facebook_version_code}; "
                        f"family={opened.url_family}; queried={opened.queried_activity_count}",
                        level="ERROR",
                    )
                if opened.security_rejected:
                    break
                if candidate_index + 1 < len(target.candidate_urls):
                    continue
                break
            timing = ", ".join(
                part
                for part in (
                    f"ThisTime={opened.this_time_ms}ms" if opened.this_time_ms is not None else "",
                    f"TotalTime={opened.total_time_ms}ms" if opened.total_time_ms is not None else "",
                    f"WaitTime={opened.wait_time_ms}ms" if opened.wait_time_ms is not None else "",
                )
                if part
            )
            self._emit(
                f"Explicit Facebook route accepted; component={opened.resolved_component}"
                + (f"; {timing}" if timing else "")
            )
            last_result = self._wait_for_navigation_target(
                opened=opened,
                baseline=baseline,
                target=target,
            )
            if last_result.ready:
                self.context.routed_url = candidate
                return None
            if last_result.stopped:
                return JobExecutionResult(
                    False,
                    FailureCode.STOP_REQUESTED,
                    "Stop requested while waiting for Facebook target",
                )
            can_try_next = bool(
                candidate_index + 1 < len(target.candidate_urls)
                and last_result.state
                not in {NavigationProbeState.BLOCKED, NavigationProbeState.WRONG_APP}
            )
            if not can_try_next:
                break
        return self._navigation_failure_result(
            open_results=open_results,
            last_result=last_result,
            baseline=baseline,
        )

    def _classify_current_screen(self) -> JobExecutionResult | None:
        assert self.context is not None
        assert self.device is not None
        self._transition(JobState.CLASSIFYING_SCREEN, "Classifying current screen")
        image = self.device.capture_screen_image()
        ui_xml = self.device.dump_ui_xml_text()
        routed_url = self.context.routed_url or self.context.job.link or ""
        result = self.classifier.classify(
            url=routed_url,
            raw_job=self.context.job.raw,
            ui_xml=ui_xml,
            image=image,
        )
        expected = self.classifier.classify_url(routed_url)
        if (
            result.kind is ContentKind.UNKNOWN
            or expected is ContentKind.UNKNOWN
            or result.kind is not expected
            or not result.has_layout_signal
        ):
            return JobExecutionResult(
                False,
                FailureCode.TARGET_LAYOUT_MISMATCH,
                "URL and observed Facebook layout do not agree",
                evidence={"image": image, "details": result.evidence},
            )
        self.context.content_kind = result.kind
        return None

    def _execute_job(self) -> JobExecutionResult:
        assert self.context is not None
        assert self.device is not None
        if self.executor_registry is None:
            self.executor_registry = self._build_executor_registry()
        registry = self.executor_registry
        executor = registry.get(self.context.job.action)
        if executor is None:
            return JobExecutionResult(
                False,
                FailureCode.UNSUPPORTED_JOB_ACTION,
                f"No executor registered for {self.context.job.action.value}",
            )
        self._transition(JobState.LOCATING_ACTION, "Locating deterministic action")
        self._transition(JobState.EXECUTING_ACTION, "Executing deterministic action")
        result = executor.execute(self.context, self.device, self.stop_event)
        self._transition(JobState.VERIFYING_ACTION, "Checking action result")
        return JobExecutionResult(
            result.success,
            result.failure_code,
            result.message,
            evidence=result.evidence,
        )

    def _submit(self) -> bool:
        assert self.context is not None
        if not self.context.action_verified:
            return self._report_failure(
                JobExecutionResult(
                    False,
                    FailureCode.ACTION_NOT_CONFIRMED,
                    "Submit blocked because action_verified is false",
                )
            )
        self._transition(JobState.SUBMITTING, "Submitting verified job")
        try:
            assert self.service is not None
            submit = self.service.submit(
                job=self.context.job,
                facebook_uid=self.binding.facebook_uid,
                provider_account_id=self.binding.provider_account_id,
            )
            self.context.set_terminal_api_action(TerminalApiAction.SUBMITTED)
            self.stats.jobs_completed += 1
            self._emit(f"Job submitted successfully; reward={submit.reward}")
            return self._cooldown(error=False)
        except Exception as exc:
            self.context.set_terminal_api_action(TerminalApiAction.SUBMIT_UNKNOWN)
            self.stats.jobs_failed += 1
            self._emit(
                f"Submit result is unknown: {sanitize_message(exc)}",
                level="ERROR",
                failure_code=FailureCode.API_SUBMIT_FAILED,
            )
            return self._cooldown(error=True)

    def run(self) -> JobWorkerStats:
        self.config.validate()
        system_errors = 0
        try:
            self._transition(JobState.AUTHENTICATING, "Authenticating worker session")
            self.service = self.service_factory(self._auth_token)
            self.service.verify_auth()
            self.server = self.service.get_publisher_server()
            self._auth_token = ""
            self._transition(JobState.AUTHENTICATED, "Worker authenticated")
            self._transition(JobState.DEVICE_BOOTSTRAP, "Bootstrapping device")
            bootstrap_config = replace(self.settings.runtime_config)
            bootstrap_config.launch_app = False
            self.bootstrap = self.bootstrap_factory(
                self.binding.ld_index,
                self.settings,
                bootstrap_config,
                self.stop_event,
                self.pause_event,
                emit=lambda type_, message, level, status: self._emit(
                    message,
                    level=level,
                ),
            )
            session = self.bootstrap.bootstrap()
            if session is None:
                if self.stop_event.is_set():
                    self._transition(JobState.STOPPING, "Stop requested during bootstrap")
                else:
                    self._transition(JobState.ERROR, "Device bootstrap failed")
                return self.stats
            self.device = session.device
            reset = self.device.force_stop(self.settings.package_name)
            if not getattr(reset, "ok", False):
                self._emit(
                    "Could not clear the old Facebook task before Auto Job",
                    level="ERROR",
                    failure_code=FailureCode.ADB_OFFLINE,
                )
                self._transition(JobState.ERROR, "Facebook startup reset failed")
                return self.stats
            self._emit("Facebook task cleared once; app remains stopped until first job")
            self._transition(JobState.GETTING_JOB, "Ready to get jobs")
            empty_high_job_pulls = 0
            while not self.stop_event.is_set():
                if self.config.max_jobs and self.stats.jobs_received >= self.config.max_jobs:
                    break
                while self.pause_event.is_set() and not self.stop_event.is_set():
                    self.stop_event.wait(0.25)
                fallback_enabled = self.config.low_job_fallback_enabled
                fallback_after = self.config.low_job_fallback_after_empty
                if not fallback_enabled:
                    empty_high_job_pulls = 0
                low_job = int(
                    fallback_enabled
                    and empty_high_job_pulls >= fallback_after
                )
                if low_job:
                    self._emit(
                        f"No high-score job after {empty_high_job_pulls} pulls; "
                        "trying low_job=1 once"
                    )
                    # Low-job mode is one-shot even if this request fails.
                    empty_high_job_pulls = 0
                try:
                    assert self.service is not None
                    jobs = self.service.get_jobs(
                        facebook_uid=self.binding.facebook_uid,
                        server=self.server,
                        low_job=low_job,
                    )
                    system_errors = 0
                except Exception as exc:
                    system_errors += 1
                    self._emit(
                        f"Get Job failed: {sanitize_message(exc)}",
                        level="ERROR",
                        failure_code=FailureCode.API_GET_FAILED,
                    )
                    if system_errors >= self.config.max_system_errors:
                        self._transition(JobState.ERROR, "Too many API Get failures")
                        break
                    self._transition(JobState.RECOVERING, "Recovering after API failure")
                    if not self._sleep_interruptible(self.config.error_cooldown_sec):
                        break
                    self._transition(JobState.GETTING_JOB, "Retrying Get Job")
                    continue
                eligible_jobs = [
                    job
                    for job in jobs
                    if (
                        job.action in self.config.enabled_actions
                        and (job.id, self.binding.facebook_uid) not in self.processed_keys
                    )
                ]
                if eligible_jobs:
                    empty_high_job_pulls = 0
                elif low_job == 0:
                    empty_high_job_pulls += 1
                if not eligible_jobs:
                    if not self._cooldown(error=False):
                        break
                    self._transition(JobState.GETTING_JOB, "Getting next job")
                    continue
                job = eligible_jobs[0]
                self.context = JobExecutionContext(
                    job=job,
                    binding=self.binding,
                    state=self.state,
                )
                self.stats.jobs_received += 1
                self.stats.current_job_id = job.id
                self.stats.current_action = job.action.value
                self._transition(JobState.VALIDATING_JOB, "Validating job")
                failure = self._validate_job()
                if failure is None:
                    failure = self._route_and_verify_job()
                if failure is None:
                    failure = self._classify_current_screen()
                if failure is None:
                    failure = self._execute_job()
                self.processed_keys.add(self.context.dedupe_key)
                if failure.success:
                    if not self._submit():
                        break
                else:
                    if failure.failure_code is FailureCode.STOP_REQUESTED:
                        self._abort_context_without_api()
                        break
                    if not self._report_failure(failure):
                        break
                if self.stop_event.is_set():
                    break
                self._transition(JobState.GETTING_JOB, "Getting next job")
        except GoLikeAuthenticationError as exc:
            self._emit(
                f"Authentication failed: {sanitize_message(exc)}",
                level="ERROR",
                failure_code=FailureCode.AUTH_INVALID,
            )
            if self.state is not JobState.ERROR:
                self._transition(JobState.ERROR, "Authentication failed")
        except Exception as exc:
            self._emit(
                f"Worker error: {sanitize_message(exc)}",
                level="ERROR",
            )
            if self.state not in {JobState.ERROR, JobState.STOPPING, JobState.STOPPED}:
                try:
                    self._transition(JobState.ERROR, "Unhandled worker error")
                except RuntimeError:
                    self.state = JobState.ERROR
        finally:
            self._abort_context_without_api()
            if self.service is not None:
                self.service.close()
            self._auth_token = ""
            if self.state is not JobState.STOPPED:
                if self.state is not JobState.STOPPING:
                    try:
                        self._transition(JobState.STOPPING, "Stopping worker")
                    except RuntimeError:
                        self.state = JobState.STOPPING
                        self._emit("Stopping worker")
                self._transition(JobState.STOPPED, "Worker stopped")
        return self.stats
