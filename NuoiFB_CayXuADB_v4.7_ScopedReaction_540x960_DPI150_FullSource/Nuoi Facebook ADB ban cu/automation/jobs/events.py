from __future__ import annotations

from dataclasses import dataclass, field
import time

from .failure_codes import FailureCode
from .models import JobState, JobWorkerStats


def mask_uid(uid: str) -> str:
    text = str(uid or "")
    if len(text) <= 6:
        return "***"
    return f"{text[:3]}***{text[-3:]}"


@dataclass(slots=True)
class JobAutomationEvent:
    ld_index: int
    facebook_uid: str
    state: JobState
    message: str
    level: str = "INFO"
    job_id: str = ""
    job_type: str = ""
    action: str = ""
    failure_code: FailureCode | None = None
    evidence_path: str = ""
    stats: JobWorkerStats = field(default_factory=JobWorkerStats)
    ts: float = field(default_factory=time.time)

    @property
    def masked_facebook_uid(self) -> str:
        return mask_uid(self.facebook_uid)
