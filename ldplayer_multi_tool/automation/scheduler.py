from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from queue import Queue
from typing import Callable, Dict, Iterable, Optional
import copy
import random
import threading
import time

from core.config import AppSettings, RuntimeConfig
from core.events import AutomationEvent
from core.ldconsole import LDConsoleManager
from storage.db import RunDatabase
from .feed_runner import FeedRunner


class DeviceJob:
    def __init__(self, index: int, future: Future, stop_event: threading.Event, pause_event: threading.Event):
        self.index = index
        self.future = future
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.is_paused = False
        self.ld_quit_requested = False


class AutomationScheduler:
    def __init__(self, settings: AppSettings, event_queue: Optional[Queue] = None, db: Optional[RunDatabase] = None):
        self.settings = settings
        self.event_queue = event_queue or Queue()
        self.db = db or RunDatabase(settings.database_path)
        self.executor = ThreadPoolExecutor(max_workers=settings.max_concurrent_devices)
        self.jobs: Dict[int, DeviceJob] = {}
        self.run_id: Optional[int] = None
        self.lock = threading.Lock()
        self.pending_starts = 0
        self.pending_indices: set[int] = set()

    def emit(self, event: AutomationEvent) -> None:
        self.event_queue.put(event)
        if self.run_id is not None:
            try:
                self.db.add_event(self.run_id, event)
                serial = LDConsoleManager(self.settings.ld_base, self.settings.adb_port_base).serial_for_index(event.index)
                self.db.upsert_device(self.run_id, event.index, serial, event)
            except Exception:
                # Storage must not kill workers.
                pass

    def scan_instances(self):
        return LDConsoleManager(self.settings.ld_base, self.settings.adb_port_base).list_instances()

    def start(self, indices: Iterable[int], runtime_config: RuntimeConfig) -> None:
        unique = [int(i) for i in dict.fromkeys(indices)]
        if not unique:
            return
        config_snapshot = copy.deepcopy(runtime_config)
        with self.lock:
            if self.run_id is None:
                self.run_id = self.db.begin_run(config_snapshot.name, len(unique))
            to_start: list[int] = []
            for index in unique:
                existing = self.jobs.get(index)
                if existing and not existing.future.done():
                    continue
                if index in self.pending_indices:
                    continue
                to_start.append(index)
            self.pending_starts += len(to_start)
            self.pending_indices.update(to_start)
        if not to_start:
            return
        self._emit_scheduler(
            f"[Scheduler] Requested {len(to_start)} worker(s), max concurrent={self.settings.max_concurrent_devices}"
        )
        thread = threading.Thread(target=self._start_staggered, args=(to_start, config_snapshot), daemon=True)
        thread.start()

    def _emit_scheduler(self, message: str) -> None:
        self.event_queue.put(AutomationEvent(index=-1, type="scheduler", status="SCHEDULER", message=message))

    def _start_staggered(self, indices: list[int], runtime_config: RuntimeConfig) -> None:
        for offset, index in enumerate(indices):
            with self.lock:
                if index not in self.pending_indices:
                    continue
            self._emit_scheduler(f"[Scheduler] Submitting LD-{index}")
            self._submit_worker(index, runtime_config)
            if offset < len(indices) - 1:
                stagger = random.uniform(5.0, 7.0)
                self._emit_scheduler(f"[Scheduler] Waiting {stagger:.1f}s before next worker")
                time.sleep(stagger)

    def _submit_worker(self, index: int, runtime_config: RuntimeConfig) -> None:
        consumed_pending = False
        try:
            with self.lock:
                if index not in self.pending_indices:
                    return
                self.pending_indices.discard(index)
                consumed_pending = True
                existing = self.jobs.get(index)
                if existing and not existing.future.done():
                    return
                stop_event = threading.Event()
                pause_event = threading.Event()
                runner_config = copy.deepcopy(runtime_config)
                runner = FeedRunner(index, self.settings, runner_config, stop_event, pause_event, self.emit)
                future = self.executor.submit(runner.run)
                self.jobs[index] = DeviceJob(index, future, stop_event, pause_event)
        finally:
            if consumed_pending:
                with self.lock:
                    self.pending_starts = max(0, self.pending_starts - 1)

    def pause(self, indices: Iterable[int]) -> None:
        for index in indices:
            job = self.jobs.get(int(index))
            if job and not job.future.done() and not job.is_paused:
                job.is_paused = True
                job.pause_event.set()
                self.emit(AutomationEvent(index=int(index), type="status", status="PAUSED", message="Paused"))

    def resume(self, indices: Iterable[int]) -> None:
        for index in indices:
            job = self.jobs.get(int(index))
            if job and not job.future.done() and job.is_paused:
                job.is_paused = False
                job.pause_event.clear()
                self.emit(AutomationEvent(index=int(index), type="status", status="RUNNING", message="Resumed"))

    def _cancel_pending_locked(self, index: int) -> bool:
        if index not in self.pending_indices:
            return False
        self.pending_indices.discard(index)
        self.pending_starts = max(0, self.pending_starts - 1)
        return True

    def stop(self, indices: Iterable[int]) -> None:
        for index in indices:
            index = int(index)
            job = self.jobs.get(index)
            if job and not job.future.done():
                job.stop_event.set()
                self.emit(AutomationEvent(index=index, type="status", status="STOPPING", message="Stop requested"))
            with self.lock:
                canceled = self._cancel_pending_locked(index)
            if canceled:
                self._emit_scheduler(f"[Scheduler] Canceled pending LD-{index}")

    def stop_all(self) -> None:
        with self.lock:
            indices = sorted(set(self.jobs.keys()) | set(self.pending_indices))
        self.stop(indices)

    def stop_selected(self, indices: Iterable[int]) -> None:
        self.stop(indices)

    def _cleanup_finished_jobs_locked(self) -> None:
        """Xoa job da xong khoi self.jobs. Goi ben trong lock."""
        finished = [idx for idx, job in self.jobs.items() if job.future.done()]
        for idx in finished:
            self.jobs.pop(idx, None)

    def set_max_workers(self, max_workers: int) -> None:
        """Doi so luong worker toi da. An toan khi co worker dang chay."""
        max_workers = max(1, int(max_workers))
        self.settings.max_concurrent_devices = max_workers

        with self.lock:
            self._cleanup_finished_jobs_locked()
            has_active_jobs = any(not job.future.done() for job in self.jobs.values())
            has_pending = bool(self.pending_indices) or self.pending_starts > 0

        if has_active_jobs or has_pending:
            self._emit_scheduler(
                f"[Scheduler] Max concurrent will apply next idle session: {max_workers}"
            )
            return

        old = self.executor
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            old.shutdown(wait=False, cancel_futures=False)
        except TypeError:
            old.shutdown(wait=False)
        self._emit_scheduler(f"[Scheduler] Max concurrent set to {max_workers}")

    def start_indices(self, indices: list[int], config: RuntimeConfig) -> None:
        """Alias ro rang cho start(). Dung boi GUI Start Selected."""
        self.start(indices, config)

    def start_one(self, index: int, config: RuntimeConfig) -> None:
        """Start duy nhat 1 worker cho index. Khong tao trung neu dang chay."""
        index = int(index)
        with self.lock:
            existing = self.jobs.get(index)
            if existing and not existing.future.done():
                self.event_queue.put(AutomationEvent(
                    index=index, type="scheduler", status="SCHEDULER",
                    message=f"LD-{index} already running"
                ))
                return
        self.start([index], config)

    def _quit_ldplayer_async(self, index: int) -> None:
        def worker() -> None:
            manager = LDConsoleManager(self.settings.ld_base, self.settings.adb_port_base)
            result = manager.quit(index)
            if result.ok:
                self.emit(AutomationEvent(
                    index=index,
                    type="status",
                    status="STOPPED",
                    message="LDPlayer quit command sent",
                ))
                return
            self.emit(AutomationEvent(
                index=index,
                type="status",
                level="ERROR",
                status="STOPPING",
                message=f"LDPlayer quit failed: args={result.args}; output={result.brief()}",
            ))

        threading.Thread(target=worker, daemon=True).start()

    def _request_ldplayer_quit(self, index: int, job: DeviceJob) -> None:
        if job.ld_quit_requested:
            self.emit(AutomationEvent(
                index=index,
                type="status",
                status="STOPPING",
                message="LDPlayer quit already requested",
            ))
            return
        job.ld_quit_requested = True
        self.emit(AutomationEvent(
            index=index,
            type="status",
            status="STOPPING",
            message="Second stop requested; closing LDPlayer",
        ))
        self._quit_ldplayer_async(index)

    def stop_one(self, index: int) -> None:
        """Stop rieng 1 worker. Lan 2 sau stop se tat LDPlayer cua index do."""
        index = int(index)
        job = self.jobs.get(index)
        if job and not job.stop_event.is_set() and not job.future.done():
            job.stop_event.set()
            self.emit(AutomationEvent(index=index, type="status", status="STOPPING", message="Stop requested"))
        elif job and job.stop_event.is_set():
            self._request_ldplayer_quit(index, job)
        else:
            with self.lock:
                canceled = self._cancel_pending_locked(index)
                if not canceled:
                    self.event_queue.put(AutomationEvent(
                        index=index, type="scheduler", status="SCHEDULER",
                        message=f"LD-{index} is not running"
                    ))
            if canceled:
                self._emit_scheduler(f"[Scheduler] Canceled pending LD-{index}")

    def is_running(self, index: int) -> bool:
        """Kiem tra worker cua index co dang chay hay khong."""
        index = int(index)
        job = self.jobs.get(index)
        if job and not job.future.done():
            return True
        with self.lock:
            return index in self.pending_indices

    def active_indices(self) -> list[int]:
        with self.lock:
            active = [i for i, j in self.jobs.items() if not j.future.done()]
            return sorted(set(active) | set(self.pending_indices))

    def finalize_finished_run_if_any(self) -> None:
        if self.run_id is None:
            return
        if self.pending_starts > 0:
            return
        if self.jobs and all(job.future.done() for job in self.jobs.values()):
            status = "DONE"
            for job in self.jobs.values():
                if job.future.cancelled():
                    status = "STOPPED"
                elif job.future.exception() is not None:
                    status = "ERROR"
            self.db.end_run(self.run_id, status=status)
            self.run_id = None
