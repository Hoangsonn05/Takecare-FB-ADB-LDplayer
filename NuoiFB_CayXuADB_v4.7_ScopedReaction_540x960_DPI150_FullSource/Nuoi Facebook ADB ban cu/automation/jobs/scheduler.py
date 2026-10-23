from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from queue import Queue
import threading

from core.config import AppSettings
from core.resource_locks import GLOBAL_RESOURCE_LOCKS

from .account_binding import AccountBindingRegistry
from .config import JobAutomationConfig
from .events import JobAutomationEvent
from .models import DeviceAccountBinding, JobState
from .runner import JobRunner


@dataclass(slots=True)
class JobWorkerHandle:
    binding: DeviceAccountBinding
    future: Future
    stop_event: threading.Event
    pause_event: threading.Event
    resource_owner: str


class JobAutomationScheduler:
    def __init__(
        self,
        settings: AppSettings,
        *,
        event_queue: Queue | None = None,
        max_workers: int | None = None,
        binding_registry: AccountBindingRegistry | None = None,
    ) -> None:
        self.settings = settings
        self.event_queue = event_queue or Queue()
        self.binding_registry = binding_registry or AccountBindingRegistry()
        self.executor = ThreadPoolExecutor(
            max_workers=max_workers or settings.max_concurrent_devices,
            thread_name_prefix="autojob",
        )
        self._lock = threading.RLock()
        self._handles: dict[int, JobWorkerHandle] = {}

    def emit(self, event: JobAutomationEvent) -> None:
        self.event_queue.put(event)

    def start(
        self,
        bindings: list[DeviceAccountBinding],
        config: JobAutomationConfig,
        auth_token: str,
    ) -> None:
        config.validate()
        if not str(auth_token or "").strip():
            raise ValueError("AuthToken is required")
        seen_uids: set[str] = set()
        for binding in bindings:
            if binding.facebook_uid in seen_uids:
                raise ValueError("One Facebook UID cannot be used by two workers")
            seen_uids.add(binding.facebook_uid)
        for binding in bindings:
            with self._lock:
                existing = self._handles.get(binding.ld_index)
                if existing and not existing.future.done():
                    continue
                owner = f"autojob:{id(self)}:{binding.ld_index}"
                if not GLOBAL_RESOURCE_LOCKS.claim_ld(binding.ld_index, owner):
                    busy = GLOBAL_RESOURCE_LOCKS.owner_for_ld(binding.ld_index) or "unknown"
                    self.emit(
                        JobAutomationEvent(
                            ld_index=binding.ld_index,
                            facebook_uid=binding.facebook_uid,
                            state=JobState.ERROR,
                            level="WARN",
                            message=f"LD-{binding.ld_index} is busy in another mode ({busy})",
                        )
                    )
                    continue
                self.binding_registry.mark_active(binding)
                stop_event = threading.Event()
                pause_event = threading.Event()
                runner = JobRunner(
                    binding=binding,
                    settings=self.settings,
                    config=config,
                    auth_token=auth_token,
                    stop_event=stop_event,
                    pause_event=pause_event,
                    emit=self.emit,
                )
                try:
                    future = self.executor.submit(runner.run)
                except Exception:
                    self.binding_registry.mark_inactive(binding)
                    GLOBAL_RESOURCE_LOCKS.release_ld(binding.ld_index, owner)
                    raise
                handle = JobWorkerHandle(
                    binding,
                    future,
                    stop_event,
                    pause_event,
                    owner,
                )
                self._handles[binding.ld_index] = handle
                future.add_done_callback(
                    lambda _future, b=binding, o=owner: self._release(b, o)
                )

    def _release(self, binding: DeviceAccountBinding, owner: str) -> None:
        self.binding_registry.mark_inactive(binding)
        GLOBAL_RESOURCE_LOCKS.release_ld(binding.ld_index, owner)

    def pause_all(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                if not handle.future.done():
                    handle.pause_event.set()

    def resume_all(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                if not handle.future.done():
                    handle.pause_event.clear()

    def stop_all(self) -> None:
        with self._lock:
            for handle in self._handles.values():
                if not handle.future.done():
                    handle.stop_event.set()
                    handle.pause_event.clear()

    def active_bindings(self) -> list[DeviceAccountBinding]:
        with self._lock:
            return [
                handle.binding
                for handle in self._handles.values()
                if not handle.future.done()
            ]

    def is_running(self) -> bool:
        return bool(self.active_bindings())

    def shutdown(self, timeout_sec: float = 25.0) -> bool:
        self.stop_all()
        with self._lock:
            futures = [
                handle.future
                for handle in self._handles.values()
                if not handle.future.done()
            ]
        if futures:
            _done, pending = wait(futures, timeout=max(0.0, timeout_sec))
        else:
            pending = set()
        self.executor.shutdown(wait=not pending, cancel_futures=True)
        return not pending
