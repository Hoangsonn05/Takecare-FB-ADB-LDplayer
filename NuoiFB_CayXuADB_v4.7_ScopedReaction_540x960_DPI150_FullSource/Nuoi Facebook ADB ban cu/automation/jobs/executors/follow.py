from __future__ import annotations

from ..failure_codes import FailureCode
from .base import ExecutorResult


class FollowExecutor:
    def execute(self, context, device, stop_event) -> ExecutorResult:
        return ExecutorResult(
            False,
            False,
            FailureCode.UNSUPPORTED_JOB_ACTION,
            "Follow UI flow is not verified; no coordinate was tapped",
        )
