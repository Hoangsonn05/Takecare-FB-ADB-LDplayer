from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any

from integrations.golike_api import NormalizedJob

from .failure_codes import FailureCode


class ContentKind(str, Enum):
    FEED = "feed"
    REEL = "reel"
    UNKNOWN = "unknown"


class JobState(str, Enum):
    IDLE = "IDLE"
    AUTHENTICATING = "AUTHENTICATING"
    AUTHENTICATED = "AUTHENTICATED"
    DEVICE_BOOTSTRAP = "DEVICE_BOOTSTRAP"
    GETTING_JOB = "GETTING_JOB"
    VALIDATING_JOB = "VALIDATING_JOB"
    NAVIGATING = "NAVIGATING"
    WAITING_TARGET = "WAITING_TARGET"
    VERIFYING_TARGET = "VERIFYING_TARGET"
    CLASSIFYING_SCREEN = "CLASSIFYING_SCREEN"
    LOCATING_ACTION = "LOCATING_ACTION"
    EXECUTING_ACTION = "EXECUTING_ACTION"
    VERIFYING_ACTION = "VERIFYING_ACTION"
    SUBMITTING = "SUBMITTING"
    REPORTING = "REPORTING"
    COOLDOWN = "COOLDOWN"
    RECOVERING = "RECOVERING"
    ERROR = "ERROR"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"


class TerminalApiAction(str, Enum):
    NONE = "NONE"
    SUBMITTED = "SUBMITTED"
    REPORTED = "REPORTED"
    SUBMIT_UNKNOWN = "SUBMIT_UNKNOWN"
    ABORTED_WITHOUT_API_ACTION = "ABORTED_WITHOUT_API_ACTION"


@dataclass(slots=True, frozen=True)
class DeviceAccountBinding:
    ld_index: int
    adb_serial: str
    facebook_uid: str
    provider_account_id: str
    facebook_name: str


@dataclass(slots=True)
class JobExecutionContext:
    job: NormalizedJob
    binding: DeviceAccountBinding
    content_kind: ContentKind = ContentKind.UNKNOWN
    state: JobState = JobState.IDLE
    attempts: dict[str, int] = field(default_factory=dict)
    navigation_evidence: list[str] = field(default_factory=list)
    route_open_count: int = 0
    attempt_started_at: float = 0.0
    attempt_deadline: float = 0.0
    last_probe_state: str = ""
    routed_url: str = ""
    target_source: str = ""
    detected_coordinate: tuple[int, int] | None = None
    action_verified: bool = False
    terminal_api_action: TerminalApiAction = TerminalApiAction.NONE

    @property
    def dedupe_key(self) -> tuple[str, str]:
        return self.job.id, self.binding.facebook_uid

    def set_terminal_api_action(self, action: TerminalApiAction) -> None:
        if action is TerminalApiAction.NONE:
            raise ValueError("terminal API action cannot be NONE")
        if self.terminal_api_action is not TerminalApiAction.NONE:
            raise RuntimeError(
                f"terminal API action already set: {self.terminal_api_action.value}"
            )
        self.terminal_api_action = action


@dataclass(slots=True)
class JobExecutionResult:
    success: bool
    failure_code: FailureCode | None = None
    message: str = ""
    submit_result: Any = None
    report_result: Any = None
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class JobWorkerStats:
    jobs_received: int = 0
    jobs_completed: int = 0
    jobs_reported: int = 0
    jobs_failed: int = 0
    current_job_id: str = ""
    current_action: str = ""
    started_at: float = field(default_factory=time.time)

    @property
    def runtime_sec(self) -> float:
        return max(0.0, time.time() - self.started_at)
