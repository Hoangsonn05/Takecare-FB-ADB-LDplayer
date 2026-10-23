from __future__ import annotations

from queue import Empty, Queue

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Qt, Signal, Slot
from PySide6.QtWidgets import (
    QMessageBox,
    QSplitter,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from automation.jobs.config import JobAutomationConfig
from automation.jobs.controller import JobAutomationController
from automation.jobs.events import JobAutomationEvent
from automation.jobs.models import DeviceAccountBinding
from core.config import AppSettings
from core.ldconsole import LDConsoleManager
from gui.widgets import (
    AccountBindingPanel,
    AuthPanel,
    JobConfigPanel,
    WorkerMonitorPanel,
)
from integrations.golike_api import JobAction
from integrations.golike_service import GoLikeAuthSnapshot, GoLikeJobService, sanitize_message


class _TaskSignals(QObject):
    finished = Signal(int, object, object)


class _FunctionTask(QRunnable):
    def __init__(self, generation: int, function) -> None:
        super().__init__()
        self.generation = generation
        self.function = function
        self.signals = _TaskSignals()

    @Slot()
    def run(self) -> None:
        try:
            result = self.function()
            self.signals.finished.emit(self.generation, result, None)
        except Exception as exc:
            self.signals.finished.emit(self.generation, None, exc)


class AutoJobTab(QWidget):
    def __init__(self, settings: AppSettings, parent=None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.event_queue: Queue = Queue()
        self.controller = JobAutomationController(settings, self.event_queue)
        self.task_pool = QThreadPool(self)
        self.task_pool.setMaxThreadCount(2)
        self.auth_generation = 0
        self.auth_in_progress = False
        self.scan_generation = 0
        self.auth_snapshot: GoLikeAuthSnapshot | None = None
        self.accounts = []
        self.devices = []
        self.pending_clear_bindings = False
        self.worker_row_by_ld: dict[int, int] = {}

        self._build_ui()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_events)
        self.timer.start(250)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        self.auth_panel = AuthPanel()
        self.token_edit = self.auth_panel.token_edit
        self.auth_btn = self.auth_panel.auth_btn
        self.disconnect_btn = self.auth_panel.disconnect_btn
        self.auth_status = self.auth_panel.auth_status
        self.username_label = self.auth_panel.username_label
        self.coin_label = self.auth_panel.coin_label
        self.auth_btn.clicked.connect(self.authenticate)
        self.disconnect_btn.clicked.connect(self.disconnect_session)
        self.token_edit.textChanged.connect(self._token_changed)
        root.addWidget(self.auth_panel)

        self.main_splitter = QSplitter(Qt.Vertical)
        self.main_splitter.setChildrenCollapsible(False)
        self.top_splitter = QSplitter(Qt.Horizontal)
        self.top_splitter.setChildrenCollapsible(False)

        self.account_binding_panel = AccountBindingPanel()
        self.account_table = self.account_binding_panel.account_table
        self.scan_ld_btn = self.account_binding_panel.scan_ld_btn
        self.ld_combo = self.account_binding_panel.ld_combo
        self.account_combo = self.account_binding_panel.account_combo
        self.binding_confirm = self.account_binding_panel.binding_confirm
        self.binding_warning = self.account_binding_panel.binding_warning
        self.bind_btn = self.account_binding_panel.bind_btn
        self.remove_binding_btn = self.account_binding_panel.remove_binding_btn
        self.binding_table = self.account_binding_panel.binding_table
        self.scan_ld_btn.clicked.connect(self.scan_ldplayers)
        self.bind_btn.clicked.connect(self.bind_account)
        self.remove_binding_btn.clicked.connect(self.remove_selected_binding)
        self.top_splitter.addWidget(self.account_binding_panel)

        self.config_panel = JobConfigPanel()
        self.max_jobs_spin = self.config_panel.max_jobs_spin
        self.low_job_fallback_check = self.config_panel.low_job_fallback_check
        self.low_job_fallback_spin = self.config_panel.low_job_fallback_spin
        self.delay_min_spin = self.config_panel.delay_min_spin
        self.delay_max_spin = self.config_panel.delay_max_spin
        self.error_cooldown_spin = self.config_panel.error_cooldown_spin
        self.job_route_timeout_spin = self.config_panel.job_route_timeout_spin
        self.feed_swipes_spin = self.config_panel.feed_swipes_spin
        self.screen_width_spin = self.config_panel.screen_width_spin
        self.screen_height_spin = self.config_panel.screen_height_spin
        self.screen_dpi_spin = self.config_panel.screen_dpi_spin
        self.save_evidence_check = self.config_panel.save_evidence_check
        self.action_checks = self.config_panel.action_checks
        self.start_btn = self.config_panel.start_btn
        self.stop_btn = self.config_panel.stop_btn
        self.pause_btn = self.config_panel.pause_btn
        self.resume_btn = self.config_panel.resume_btn
        self.start_btn.clicked.connect(self.start_jobs)
        self.stop_btn.clicked.connect(self._stop_jobs)
        self.pause_btn.clicked.connect(self._pause_jobs)
        self.resume_btn.clicked.connect(self._resume_jobs)

        self.top_splitter.addWidget(self.config_panel)
        self.top_splitter.setStretchFactor(0, 1)
        self.top_splitter.setStretchFactor(1, 1)
        self.top_splitter.setSizes([640, 620])

        self.monitor_tabs = WorkerMonitorPanel()
        self.worker_table = self.monitor_tabs.worker_table
        self.log = self.monitor_tabs.log

        self.main_splitter.addWidget(self.top_splitter)
        self.main_splitter.addWidget(self.monitor_tabs)
        self.main_splitter.setStretchFactor(0, 2)
        self.main_splitter.setStretchFactor(1, 1)
        self.main_splitter.setSizes([430, 210])
        root.addWidget(self.main_splitter, 1)

        self._workers_paused = False
        self._reset_account_combo()
        self._reset_ld_combo()
        self._update_controls()

    def _append_log(self, message: str) -> None:
        self.log.appendPlainText(sanitize_message(message))

    def _update_controls(self) -> None:
        running = self.controller.scheduler.is_running()
        has_bindings = bool(self.controller.bindings.bindings())
        authenticated = self.auth_snapshot is not None
        self.start_btn.setEnabled(
            authenticated
            and has_bindings
            and not running
            and not self.auth_in_progress
        )
        self.stop_btn.setEnabled(running)
        self.pause_btn.setEnabled(running and not self._workers_paused)
        self.resume_btn.setEnabled(running and self._workers_paused)
        self.auth_btn.setEnabled(not running and not self.auth_in_progress)
        self.bind_btn.setEnabled(authenticated and not running)
        self.remove_binding_btn.setEnabled(not running)
        self.low_job_fallback_check.setEnabled(not running)
        self.low_job_fallback_spin.setEnabled(
            not running and self.low_job_fallback_check.isChecked()
        )

    def _stop_jobs(self) -> None:
        self.controller.stop()
        self._workers_paused = False
        self._append_log("Đã gửi yêu cầu dừng Auto Job.")
        self._update_controls()

    def _pause_jobs(self) -> None:
        self.controller.scheduler.pause_all()
        self._workers_paused = True
        self._append_log("Đã tạm dừng các worker Auto Job.")
        self._update_controls()

    def _resume_jobs(self) -> None:
        self.controller.scheduler.resume_all()
        self._workers_paused = False
        self._append_log("Đã tiếp tục các worker Auto Job.")
        self._update_controls()

    def _reset_account_combo(self) -> None:
        self.account_combo.clear()
        self.account_combo.addItem("— Chọn tài khoản —", None)

    def _reset_ld_combo(self) -> None:
        self.ld_combo.clear()
        self.ld_combo.addItem("— Chọn LDPlayer —", None)

    def _token_changed(self) -> None:
        if (
            self.auth_snapshot is None
            and not self.auth_in_progress
            and not self.controller.scheduler.is_running()
        ):
            return
        self.auth_generation += 1
        self.auth_in_progress = False
        self.controller.stop()
        self.pending_clear_bindings = True
        self.auth_snapshot = None
        self.accounts = []
        self.account_table.setRowCount(0)
        self.binding_table.setRowCount(0)
        self._reset_account_combo()
        self.username_label.setText("-")
        self.coin_label.setText("-")
        self.auth_status.setText("Token đã đổi; đang dừng phiên cũ")
        self._update_controls()

    def authenticate(self) -> None:
        if self.controller.scheduler.is_running():
            QMessageBox.warning(self, "Auto Job", "Đang dừng worker cũ; vui lòng chờ.")
            return
        token = self.token_edit.text().strip()
        if not token:
            QMessageBox.information(self, "AuthToken", "Nhập AuthToken GoLike.")
            return
        self.auth_generation += 1
        generation = self.auth_generation
        self.auth_in_progress = True
        self.auth_status.setText("Đang xác thực...")
        self._update_controls()

        def work():
            with GoLikeJobService(token, timeout_seconds=15.0, max_retries=2) as service:
                return service.authenticate()

        task = _FunctionTask(generation, work)
        task.signals.finished.connect(self._auth_finished)
        self.task_pool.start(task)

    @Slot(int, object, object)
    def _auth_finished(self, generation: int, result, error) -> None:
        if generation != self.auth_generation:
            return
        self.auth_in_progress = False
        if error is not None:
            self.auth_status.setText("Xác thực thất bại")
            self._append_log(f"Xác thực thất bại: {sanitize_message(error)}")
            self._update_controls()
            return
        self.auth_snapshot = result
        self.accounts = list(result.inventory.accounts)
        self.auth_status.setText("Đã xác thực")
        self.username_label.setText(result.identity.username)
        coin = result.identity.coin
        self.coin_label.setText("-" if coin is None else f"{coin:g}")
        self._render_accounts()
        self._append_log(
            f"Đã xác thực {result.identity.username}; "
            f"account active={result.inventory.active}, banned={result.inventory.banned}"
        )
        self._update_controls()

    def _render_accounts(self) -> None:
        self.account_table.setRowCount(0)
        self._reset_account_combo()
        for account in self.accounts:
            row = self.account_table.rowCount()
            self.account_table.insertRow(row)
            jobs_text = ", ".join(sorted(str(key) for key in account.jobs_can_work)) or "-"
            values = [
                account.facebook_name,
                account.facebook_uid,
                account.provider_account_id,
                "Active",
                jobs_text,
            ]
            for col, value in enumerate(values):
                self.account_table.setItem(row, col, QTableWidgetItem(str(value)))
            self.account_combo.addItem(
                f"{account.facebook_name} | {account.facebook_uid}",
                account,
            )
        self.account_combo.setCurrentIndex(0)

    def disconnect_session(self) -> None:
        self.controller.stop()
        self.pending_clear_bindings = True
        self.auth_generation += 1
        self.auth_in_progress = False
        self.token_edit.blockSignals(True)
        self.token_edit.clear()
        self.token_edit.blockSignals(False)
        self.auth_snapshot = None
        self.accounts = []
        self.account_table.setRowCount(0)
        self.binding_table.setRowCount(0)
        self._reset_account_combo()
        self.username_label.setText("-")
        self.coin_label.setText("-")
        self.auth_status.setText("Đã ngắt phiên")
        self._append_log("Đã xóa token khỏi bộ nhớ giao diện và yêu cầu dừng worker.")
        self._update_controls()

    def scan_ldplayers(self) -> None:
        self.scan_generation += 1
        generation = self.scan_generation
        self.scan_ld_btn.setEnabled(False)
        self._append_log("Đang quét LDPlayer...")

        def work():
            manager = LDConsoleManager(
                self.settings.ld_base,
                self.settings.adb_port_base,
            )
            return manager, manager.list_instances()

        task = _FunctionTask(generation, work)
        task.signals.finished.connect(self._scan_finished)
        self.task_pool.start(task)

    @Slot(int, object, object)
    def _scan_finished(self, generation: int, result, error) -> None:
        if generation != self.scan_generation:
            return
        self.scan_ld_btn.setEnabled(True)
        if error is not None:
            self._append_log(f"Quét LDPlayer lỗi: {sanitize_message(error)}")
            return
        manager, instances = result
        self.devices = list(instances)
        self._reset_ld_combo()
        for instance in self.devices:
            state = "running" if instance.running is True else "stopped/unknown"
            self.ld_combo.addItem(
                f"LD-{instance.index} | {instance.name} | {state}",
                instance,
            )
        self.ld_combo.setCurrentIndex(0)
        self._append_log(f"Đã quét {len(self.devices)} LDPlayer.")

    def bind_account(self) -> None:
        instance = self.ld_combo.currentData()
        account = self.account_combo.currentData()
        if instance is None:
            QMessageBox.information(self, "Binding", "Chọn chính xác một LDPlayer.")
            return
        if account is None:
            QMessageBox.information(self, "Binding", "Chọn chính xác một tài khoản Facebook.")
            return
        if not self.binding_confirm.isChecked():
            QMessageBox.warning(
                self,
                "Xác nhận mapping",
                "Hãy xác nhận tài khoản đã đăng nhập đúng trên LD đã chọn.",
            )
            return
        manager = LDConsoleManager(self.settings.ld_base, self.settings.adb_port_base)
        binding = DeviceAccountBinding(
            ld_index=instance.index,
            adb_serial=manager.serial_for_index(instance.index),
            facebook_uid=account.facebook_uid,
            provider_account_id=account.provider_account_id,
            facebook_name=account.facebook_name,
        )
        try:
            self.controller.bind(binding)
        except Exception as exc:
            QMessageBox.warning(self, "Binding", sanitize_message(exc))
            return
        self._render_bindings()
        self.binding_confirm.setChecked(False)
        self.ld_combo.setCurrentIndex(0)
        self.account_combo.setCurrentIndex(0)
        self._append_log(
            f"Đã gán LD-{binding.ld_index} với UID "
            f"{binding.facebook_uid[:3]}***{binding.facebook_uid[-3:]}"
        )

    def _render_bindings(self) -> None:
        bindings = self.controller.bindings.bindings()
        self.binding_table.setRowCount(0)
        for binding in bindings:
            row = self.binding_table.rowCount()
            self.binding_table.insertRow(row)
            values = [
                binding.ld_index,
                binding.adb_serial,
                binding.facebook_name,
                binding.facebook_uid,
                binding.provider_account_id,
            ]
            for col, value in enumerate(values):
                self.binding_table.setItem(row, col, QTableWidgetItem(str(value)))
        self._update_controls()

    def remove_selected_binding(self) -> None:
        rows = sorted({index.row() for index in self.binding_table.selectedIndexes()}, reverse=True)
        for row in rows:
            item = self.binding_table.item(row, 0)
            if item:
                try:
                    self.controller.bindings.remove(int(item.text()))
                except Exception as exc:
                    self._append_log(f"Không thể xóa binding: {sanitize_message(exc)}")
        self._render_bindings()

    def _config_from_ui(self) -> JobAutomationConfig:
        enabled = {
            action
            for action, checkbox in self.action_checks.items()
            if checkbox.isChecked()
        }
        config = JobAutomationConfig(
            max_jobs=self.max_jobs_spin.value(),
            low_job_fallback_enabled=self.low_job_fallback_check.isChecked(),
            low_job_fallback_after_empty=self.low_job_fallback_spin.value(),
            between_jobs_min_sec=self.delay_min_spin.value(),
            between_jobs_max_sec=self.delay_max_spin.value(),
            error_cooldown_sec=self.error_cooldown_spin.value(),
            job_route_timeout_sec=self.job_route_timeout_spin.value(),
            feed_max_search_swipes=self.feed_swipes_spin.value(),
            screen_width_px=self.screen_width_spin.value(),
            screen_height_px=self.screen_height_spin.value(),
            screen_dpi=self.screen_dpi_spin.value(),
            save_failure_evidence=self.save_evidence_check.isChecked(),
            enabled_actions=enabled,
        )
        config.validate()
        return config

    def start_jobs(self) -> None:
        if self.auth_snapshot is None:
            QMessageBox.information(self, "Auto Job", "Xác thực AuthToken trước khi Start.")
            return
        try:
            config = self._config_from_ui()
            self.controller.start(config, self.token_edit.text())
            self._workers_paused = False
            self._append_log("Đã gửi yêu cầu Start Auto Job.")
            self._update_controls()
        except Exception as exc:
            QMessageBox.critical(self, "Start Auto Job", sanitize_message(exc))

    def _set_worker_cell(self, row: int, col: int, value: object) -> None:
        item = self.worker_table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            self.worker_table.setItem(row, col, item)
        item.setText(str(value))

    def _render_event(self, event: JobAutomationEvent) -> None:
        row = self.worker_row_by_ld.get(event.ld_index)
        if row is None:
            row = self.worker_table.rowCount()
            self.worker_table.insertRow(row)
            self.worker_row_by_ld[event.ld_index] = row
        errors = event.stats.jobs_failed
        values = [
            event.ld_index,
            event.masked_facebook_uid,
            event.state.value,
            event.job_id,
            event.action,
            event.stats.jobs_completed,
            event.stats.jobs_reported,
            errors,
            event.message,
        ]
        for col, value in enumerate(values):
            self._set_worker_cell(row, col, value)
        prefix = (
            f"[LD-{event.ld_index}] [{event.masked_facebook_uid}] "
            f"[{event.level}] [{event.state.value}]"
        )
        suffix = f" ({event.failure_code.value})" if event.failure_code else ""
        evidence = f" | evidence={event.evidence_path}" if event.evidence_path else ""
        self._append_log(f"{prefix} {event.message}{suffix}{evidence}")

    def _drain_events(self) -> None:
        try:
            while True:
                self._render_event(self.event_queue.get_nowait())
        except Empty:
            pass
        if self.pending_clear_bindings and not self.controller.scheduler.is_running():
            try:
                self.controller.clear_bindings()
            except Exception:
                return
            self.pending_clear_bindings = False
        if not self.controller.scheduler.is_running():
            self._workers_paused = False
        self._update_controls()

    def is_running(self) -> bool:
        return self.controller.scheduler.is_running()

    def shutdown(self) -> bool:
        self.timer.stop()
        stopped = self.controller.scheduler.shutdown(timeout_sec=25.0)
        self.auth_generation += 1
        self.scan_generation += 1
        self.task_pool.waitForDone(20000)
        self.token_edit.clear()
        return stopped
