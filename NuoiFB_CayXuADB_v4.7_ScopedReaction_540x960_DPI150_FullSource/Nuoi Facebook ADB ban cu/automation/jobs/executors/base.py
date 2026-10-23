from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from integrations.golike_api import JobAction

from ..failure_codes import FailureCode
from ..models import JobExecutionContext


@dataclass(slots=True)
class ExecutorResult:
    success: bool
    action_verified: bool = False
    failure_code: FailureCode | None = None
    message: str = ""
    evidence: dict = field(default_factory=dict)


class JobActionExecutor(Protocol):
    def execute(self, context: JobExecutionContext, device, stop_event) -> ExecutorResult:
        ...


class ExecutorRegistry:
    def __init__(self) -> None:
        self._executors: dict[JobAction, JobActionExecutor] = {}

    def register(self, actions: set[JobAction], executor: JobActionExecutor) -> None:
        for action in actions:
            self._executors[action] = executor

    def get(self, action: JobAction) -> JobActionExecutor | None:
        return self._executors.get(action)
