from __future__ import annotations

from dataclasses import dataclass, field

from integrations.golike_api import JobAction, REACTION_ACTIONS


@dataclass(slots=True)
class JobAutomationConfig:
    max_jobs: int = 10
    between_jobs_min_sec: float = 8.0
    between_jobs_max_sec: float = 15.0
    error_cooldown_sec: float = 20.0
    job_route_timeout_sec: float = 30.0
    job_route_poll_interval_sec: float = 1.0
    job_route_ready_stable_samples: int = 2
    job_route_initial_grace_sec: float = 3.0
    short_link_resolve_timeout_sec: float = 8.0
    feed_max_search_swipes: int = 3
    feed_search_swipe_duration_ms: int = 450
    screen_width_px: int = 540
    screen_height_px: int = 960
    screen_dpi: int = 150
    low_job_fallback_enabled: bool = True
    low_job_fallback_after_empty: int = 3
    action_settle_sec: float = 1.5
    panel_open_timeout_sec: float = 3.0
    verify_action_timeout_sec: float = 5.0
    save_failure_evidence: bool = True
    failure_evidence_retention: int = 30
    enabled_actions: set[JobAction] = field(
        default_factory=lambda: set(REACTION_ACTIONS)
    )
    report_unsupported_actions: bool = False
    stale_coordinate_tolerance_px: int = 28
    long_press_duration_ms: int = 1250
    max_system_errors: int = 3

    def validate(self) -> None:
        if self.max_jobs < 0:
            raise ValueError("max_jobs must be >= 0")
        if self.between_jobs_min_sec < 0 or self.between_jobs_max_sec < 0:
            raise ValueError("between job delays must be >= 0")
        if self.between_jobs_min_sec > self.between_jobs_max_sec:
            raise ValueError("between_jobs_min_sec must be <= between_jobs_max_sec")
        if self.error_cooldown_sec < 0:
            raise ValueError("error_cooldown_sec must be >= 0")
        if self.job_route_timeout_sec <= 0:
            raise ValueError("job_route_timeout_sec must be > 0")
        if self.job_route_poll_interval_sec <= 0:
            raise ValueError("job_route_poll_interval_sec must be > 0")
        if self.job_route_poll_interval_sec >= self.job_route_timeout_sec:
            raise ValueError(
                "job_route_poll_interval_sec must be < job_route_timeout_sec"
            )
        if self.job_route_ready_stable_samples < 1:
            raise ValueError("job_route_ready_stable_samples must be >= 1")
        if self.job_route_initial_grace_sec < 0:
            raise ValueError("job_route_initial_grace_sec must be >= 0")
        if self.short_link_resolve_timeout_sec <= 0:
            raise ValueError("short_link_resolve_timeout_sec must be > 0")
        if self.feed_max_search_swipes < 0:
            raise ValueError("feed_max_search_swipes must be >= 0")
        if self.low_job_fallback_after_empty < 1:
            raise ValueError("low_job_fallback_after_empty must be >= 1")
        if self.feed_search_swipe_duration_ms < 100:
            raise ValueError("feed_search_swipe_duration_ms must be >= 100")
        if self.screen_width_px < 240:
            raise ValueError("screen_width_px must be >= 240")
        if self.screen_height_px < 320:
            raise ValueError("screen_height_px must be >= 320")
        if not 72 <= self.screen_dpi <= 640:
            raise ValueError("screen_dpi must be 72..640")
        if self.action_settle_sec < 0:
            raise ValueError("action_settle_sec must be >= 0")
        if self.panel_open_timeout_sec <= 0 or self.verify_action_timeout_sec <= 0:
            raise ValueError("verification timeouts must be > 0")
        if self.failure_evidence_retention < 0:
            raise ValueError("failure_evidence_retention must be >= 0")
        if not self.enabled_actions:
            raise ValueError("enabled_actions must not be empty")
        if self.stale_coordinate_tolerance_px < 0:
            raise ValueError("stale_coordinate_tolerance_px must be >= 0")
        if not 1100 <= self.long_press_duration_ms <= 1500:
            raise ValueError("long_press_duration_ms must be 1100..1500")
        if self.max_system_errors < 1:
            raise ValueError("max_system_errors must be >= 1")
