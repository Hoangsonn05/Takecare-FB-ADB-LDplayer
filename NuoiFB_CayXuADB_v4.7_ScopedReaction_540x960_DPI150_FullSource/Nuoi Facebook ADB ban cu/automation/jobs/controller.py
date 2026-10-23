from __future__ import annotations

from queue import Queue

from core.config import AppSettings

from .account_binding import AccountBindingRegistry
from .config import JobAutomationConfig
from .models import DeviceAccountBinding
from .scheduler import JobAutomationScheduler


class JobAutomationController:
    """GUI-facing coordinator; it contains no Qt widgets or API contracts."""

    def __init__(self, settings: AppSettings, event_queue: Queue | None = None) -> None:
        self.bindings = AccountBindingRegistry()
        self.scheduler = JobAutomationScheduler(
            settings,
            event_queue=event_queue,
            binding_registry=self.bindings,
        )

    def bind(self, binding: DeviceAccountBinding) -> None:
        self.bindings.bind(binding)

    def clear_bindings(self) -> None:
        self.bindings.clear()

    def start(self, config: JobAutomationConfig, auth_token: str) -> None:
        bindings = self.bindings.bindings()
        if not bindings:
            raise ValueError("At least one LD/account binding is required")
        self.scheduler.start(bindings, config, auth_token)

    def stop(self) -> None:
        self.scheduler.stop_all()
