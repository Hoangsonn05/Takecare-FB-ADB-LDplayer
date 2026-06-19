from __future__ import annotations

from pathlib import Path
from queue import Empty, Queue
import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from automation.scheduler import AutomationScheduler
from core.adb_device import ADBDevice
from core.config import ALLOWED_REACTIONS, AppSettings, FACEBOOK_PACKAGE_NAME, RuntimeConfig, ensure_default_config, load_settings, save_settings
from core.events import AutomationEvent
from core.ldconsole import LDConsoleManager
from storage.db import RunDatabase
from core.paths import ensure_runtime_dirs


class MainWindow(QMainWindow):
    COL_SELECT = 0
    COL_INDEX = 1
    COL_NAME = 2
    COL_SERIAL = 3
    COL_ADB = 4
    COL_STATUS = 5
    COL_RUNTIME = 6
    COL_POSTS = 7
    COL_SWIPES = 8
    COL_REACTIONS = 9
    COL_SCREENSHOTS = 10
    COL_ERRORS = 11
    COL_ACTION = 12
    COL_LAST = 13

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.setWindowTitle("LDPlayer Facebook Feed Tool")
        self.config_path = config_path
        ensure_runtime_dirs()
        ensure_default_config(config_path)
        self.settings: AppSettings = load_settings(config_path)
        self.event_queue: Queue = Queue()
        self.scheduler = AutomationScheduler(self.settings, self.event_queue, RunDatabase(self.settings.database_path))
        self.row_by_index: dict[int, int] = {}

        self._build_ui()
        self._load_settings_to_ui()

        self.timer = QTimer(self)
        self.timer.timeout.connect(self._drain_events)
        self.timer.start(300)

    def _build_ui(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)

        settings_box = QGroupBox("System")
        form = QGridLayout(settings_box)
        self.ld_base_edit = QLineEdit()
        self.browse_btn = QPushButton("Browse")
        self.browse_btn.clicked.connect(self._browse_ld_base)

        self.concurrent_spin = QSpinBox()
        self.concurrent_spin.setRange(1, 64)
        self.ld_ids_edit = QLineEdit()
        self.ld_ids_edit.setPlaceholderText("Nhap index LD, vi du: 0,1,4")
        self.add_ids_btn = QPushButton("Add IDs")
        self.add_ids_btn.clicked.connect(self.add_ids_from_text)
        self.start_ids_btn = QPushButton("Start IDs")
        self.start_ids_btn.clicked.connect(self.start_ids_from_text)
        self.save_config_btn = QPushButton("Save Config")
        self.save_config_btn.clicked.connect(self._save_config_from_ui)

        form.addWidget(QLabel("LDPlayer base"), 0, 0)
        form.addWidget(self.ld_base_edit, 0, 1, 1, 4)
        form.addWidget(self.browse_btn, 0, 5)
        form.addWidget(QLabel("LD IDs"), 1, 0)
        form.addWidget(self.ld_ids_edit, 1, 1)
        form.addWidget(QLabel("Max concurrent"), 1, 2)
        form.addWidget(self.concurrent_spin, 1, 3)
        form.addWidget(self.add_ids_btn, 1, 4)
        form.addWidget(self.start_ids_btn, 1, 5)
        layout.addWidget(settings_box)

        run_box = QGroupBox("Run Config")
        run_form = QGridLayout(run_box)
        self.max_posts_spin = QSpinBox()
        self.max_posts_spin.setRange(0, 10000)
        self.run_minutes_spin = QSpinBox()
        self.run_minutes_spin.setRange(1, 1440)
        self.max_reactions_spin = QSpinBox()
        self.max_reactions_spin.setRange(0, 10000)
        self.reaction_probability_spin = QSpinBox()
        self.reaction_probability_spin.setRange(0, 100)
        self.reaction_enabled_check = QCheckBox("Bat tha cam xuc")
        self.reaction_checks: dict[str, QCheckBox] = {
            "like": QCheckBox("Like"),
            "love": QCheckBox("Love"),
            "thuongthuong": QCheckBox("Thuong thuong"),
            "haha": QCheckBox("Ha ha"),
            "wow": QCheckBox("Wow"),
            "sad": QCheckBox("Sad"),
            "angry": QCheckBox("Angry"),
        }

        run_form.addWidget(QLabel("So post"), 0, 0)
        run_form.addWidget(self.max_posts_spin, 0, 1)
        run_form.addWidget(QLabel("So phut chay"), 0, 2)
        run_form.addWidget(self.run_minutes_spin, 0, 3)
        run_form.addWidget(QLabel("So cam xuc toi da"), 1, 0)
        run_form.addWidget(self.max_reactions_spin, 1, 1)
        run_form.addWidget(QLabel("Ti le tha cam xuc (%)"), 1, 2)
        run_form.addWidget(self.reaction_probability_spin, 1, 3)
        run_form.addWidget(self.reaction_enabled_check, 2, 0)
        for col, reaction in enumerate(ALLOWED_REACTIONS, start=1):
            run_form.addWidget(self.reaction_checks[reaction], 2, col)
        run_form.addWidget(self.save_config_btn, 3, 5)
        layout.addWidget(run_box)

        button_row = QHBoxLayout()
        self.scan_btn = QPushButton("Scan")
        self.connect_btn = QPushButton("Check ADB")
        self.launch_btn = QPushButton("Launch")
        self.open_app_btn = QPushButton("Open Facebook")
        self.start_btn = QPushButton("Start Selected")
        self.pause_btn = QPushButton("Pause")
        self.resume_btn = QPushButton("Resume")
        self.stop_btn = QPushButton("Stop")
        for btn in [
            self.scan_btn,
            self.connect_btn,
            self.launch_btn,
            self.open_app_btn,
            self.start_btn,
            self.pause_btn,
            self.resume_btn,
            self.stop_btn,
        ]:
            button_row.addWidget(btn)
        layout.addLayout(button_row)

        self.scan_btn.clicked.connect(self.scan_devices)
        self.connect_btn.clicked.connect(self.check_selected_adb)
        self.launch_btn.clicked.connect(self.launch_selected)
        self.open_app_btn.clicked.connect(self.open_app_selected)
        self.start_btn.clicked.connect(self.start_selected)
        self.pause_btn.clicked.connect(lambda: self.scheduler.pause(self.selected_indices()))
        self.resume_btn.clicked.connect(lambda: self.scheduler.resume(self.selected_indices()))
        self.stop_btn.clicked.connect(self.scheduler.stop_all)

        self.table = QTableWidget(0, 14)
        self.table.setHorizontalHeaderLabels([
            "Use",
            "Index",
            "Name",
            "ADB Serial",
            "ADB State",
            "Status",
            "Runtime",
            "Posts",
            "Swipes",
            "Reactions",
            "Screenshots",
            "Errors",
            "Action",
            "Last message",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(self.COL_LAST, QHeaderView.Stretch)
        layout.addWidget(self.table, stretch=3)

        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        layout.addWidget(self.log, stretch=2)
        self.setCentralWidget(root)

    def _load_settings_to_ui(self) -> None:
        self.ld_base_edit.setText(self.settings.ld_base)
        self.concurrent_spin.setValue(self.settings.max_concurrent_devices)
        cfg = self.settings.runtime_config
        self.max_posts_spin.setValue(cfg.max_posts)
        self.run_minutes_spin.setValue(cfg.run_minutes)
        self.max_reactions_spin.setValue(cfg.max_reactions)
        self.reaction_probability_spin.setValue(cfg.reaction_probability_percent)
        self.reaction_enabled_check.setChecked(cfg.reaction_enabled)
        selected = set(cfg.selected_reactions)
        for reaction, checkbox in self.reaction_checks.items():
            checkbox.setChecked(reaction in selected)

    def _browse_ld_base(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select LDPlayer base folder", self.ld_base_edit.text())
        if chosen:
            self.ld_base_edit.setText(chosen)

    def _make_manager(self) -> LDConsoleManager:
        manager = LDConsoleManager(self.settings.ld_base, self.settings.adb_port_base)
        if manager.tools_exist() and str(manager.ld_base) != self.settings.ld_base:
            self.settings.ld_base = str(manager.ld_base)
            self.ld_base_edit.setText(self.settings.ld_base)
            self._append_log(f"Auto-resolved LDPlayer base: {self.settings.ld_base}")
        return manager

    def _apply_ui_to_settings(self) -> None:
        self.settings.ld_base = self.ld_base_edit.text().strip()
        self.settings.package_name = FACEBOOK_PACKAGE_NAME
        self.settings.max_concurrent_devices = self.concurrent_spin.value()
        self.settings.runtime_config = self._runtime_config_from_ui()
        self.settings.validate()
        manager = self._make_manager()
        self.scheduler.settings = self.settings
        self.scheduler.executor._max_workers = self.settings.max_concurrent_devices
        if not manager.tools_exist():
            self._append_log(manager.path_status_message())

    def _runtime_config_from_ui(self) -> RuntimeConfig:
        base = self.settings.runtime_config
        selected = [name for name, checkbox in self.reaction_checks.items() if checkbox.isChecked()]
        if self.reaction_enabled_check.isChecked() and not selected:
            raise ValueError("Select at least one reaction")
        cfg = RuntimeConfig.from_dict({
            **base.__dict__,
            "max_posts": self.max_posts_spin.value(),
            "run_minutes": self.run_minutes_spin.value(),
            "max_reactions": self.max_reactions_spin.value(),
            "reaction_probability_percent": self.reaction_probability_spin.value(),
            "reaction_enabled": self.reaction_enabled_check.isChecked(),
            "selected_reactions": selected,
        })
        cfg.validate()
        return cfg

    def _save_config_from_ui(self) -> None:
        try:
            self._apply_ui_to_settings()
            save_settings(self.settings, self.config_path)
            self._append_log("Config saved")
        except Exception as exc:
            self._append_log(f"Config error: {exc}")
            QMessageBox.critical(self, "Config error", str(exc))

    def _append_log(self, text: str) -> None:
        self.log.appendPlainText(text)

    def _set_cell(self, row: int, col: int, text: str) -> None:
        item = self.table.item(row, col)
        if item is None:
            item = QTableWidgetItem()
            self.table.setItem(row, col, item)
        item.setText(text)

    def _add_or_update_device(self, index: int, name: str, serial: str, adb_state: str = "unknown", initial_status: str = "IDLE", last: str = "", checked: bool = True) -> None:
        if index in self.row_by_index:
            row = self.row_by_index[index]
            cb = self.table.cellWidget(row, self.COL_SELECT)
            if isinstance(cb, QCheckBox):
                cb.setChecked(checked)
        else:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.row_by_index[index] = row
            cb = QCheckBox()
            cb.setChecked(checked)
            cb.setStyleSheet("margin-left: 12px;")
            self.table.setCellWidget(row, self.COL_SELECT, cb)
            self._set_action_cell(row, index)
        self._set_cell(row, self.COL_INDEX, str(index))
        self._set_cell(row, self.COL_NAME, name)
        self._set_cell(row, self.COL_SERIAL, serial)
        self._set_cell(row, self.COL_ADB, adb_state)
        self._set_cell(row, self.COL_STATUS, initial_status)
        self._set_cell(row, self.COL_RUNTIME, "0s")
        self._set_cell(row, self.COL_POSTS, "0")
        self._set_cell(row, self.COL_SWIPES, "0")
        self._set_cell(row, self.COL_REACTIONS, "0")
        self._set_cell(row, self.COL_SCREENSHOTS, "0")
        self._set_cell(row, self.COL_ERRORS, "0")
        self._set_cell(row, self.COL_LAST, last)

    def _set_action_cell(self, row: int, index: int) -> None:
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        start_btn = QPushButton("Start")
        stop_btn = QPushButton("Stop")
        start_btn.clicked.connect(lambda _checked=False, idx=index: self.start_one_index(idx))
        stop_btn.clicked.connect(lambda _checked=False, idx=index: self.stop_one_index(idx))
        layout.addWidget(start_btn)
        layout.addWidget(stop_btn)
        self.table.setCellWidget(row, self.COL_ACTION, widget)

    @staticmethod
    def _parse_indices(text: str) -> list[int]:
        indices: list[int] = []
        for part in text.replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            indices.append(int(part))
        return list(dict.fromkeys(indices))

    def add_ids_from_text(self) -> None:
        try:
            self._apply_ui_to_settings()
            indices = self._parse_indices(self.ld_ids_edit.text())
            if not indices:
                QMessageBox.information(self, "LD IDs", "Nhap it nhat mot index, vi du: 0,1,4")
                return
            manager = self._make_manager()
            known = {inst.index: inst for inst in manager.list_instances()}
            if not known:
                self._append_log("ldconsole list2 returned no instances; manual IDs will still be used")
            for index in indices:
                inst = known.get(index)
                running_text = "running" if inst and inst.running is True else "not confirmed"
                name = inst.name if inst else f"LD-{index}"
                self._add_or_update_device(
                    index,
                    name,
                    manager.serial_for_index(index),
                    adb_state="unknown",
                    initial_status="IDLE",
                    last=f"Added by ID; {running_text}",
                    checked=True,
                )
            self._append_log(f"Added LD ID(s): {', '.join(map(str, indices))}")
        except Exception as exc:
            self._append_log(f"LD ID error: {exc}")
            QMessageBox.critical(self, "LD ID error", str(exc))

    def start_ids_from_text(self) -> None:
        try:
            self._apply_ui_to_settings()
            indices = self._parse_indices(self.ld_ids_edit.text())
            if not indices:
                QMessageBox.information(self, "LD IDs", "Nhap it nhat mot index, vi du: 0,1,4")
                return
            self.add_ids_from_text()
            self.scheduler.start(indices, self.settings.runtime_config)
            self._append_log(f"Start requested for LD ID(s): {', '.join(map(str, indices))}")
        except Exception as exc:
            self._append_log(f"Start IDs error: {exc}")
            QMessageBox.critical(self, "Start IDs error", str(exc))

    def scan_devices(self) -> None:
        try:
            self._apply_ui_to_settings()
            self.table.setRowCount(0)
            self.row_by_index.clear()
            manager = self._make_manager()
            instances = manager.list_instances()
            if not instances:
                self._append_log("No LDPlayer instances returned by ldconsole list2")
                return
            for inst in instances:
                if inst.is_running is True:
                    status = "READY"
                    last = "LDPlayer running"
                elif inst.is_running is False:
                    status = "STOPPED"
                    last = "LDPlayer not running"
                else:
                    status = "IDLE"
                    last = "LDPlayer running state unknown"
                self._add_or_update_device(inst.index, inst.name or f"LD-{inst.index}", inst.adb_serial, adb_state="unknown", initial_status=status, last=last)
            self._append_log(f"Scanned {len(instances)} instance(s)")
        except Exception as exc:
            self._append_log(f"Scan error: {exc}")
            QMessageBox.critical(self, "Scan error", str(exc))

    def selected_indices(self) -> list[int]:
        indices: list[int] = []
        for row in range(self.table.rowCount()):
            cb = self.table.cellWidget(row, self.COL_SELECT)
            if isinstance(cb, QCheckBox) and cb.isChecked():
                item = self.table.item(row, self.COL_INDEX)
                if item:
                    indices.append(int(item.text()))
        return indices

    def check_selected_adb(self) -> None:
        self._apply_ui_to_settings()
        manager = self._make_manager()
        for index in self.selected_indices():
            serial = manager.serial_for_index(index)
            device = ADBDevice(manager.adb_path, serial, index=index)
            connected = device.connect()
            state = device.get_state()
            row = self.row_by_index.get(index)
            if row is not None:
                self._set_cell(row, self.COL_ADB, state)
            self._append_log(f"[LD-{index}] ADB {serial}: {'connected' if connected else 'connect failed'}; state={state}")

    def launch_selected(self) -> None:
        self._apply_ui_to_settings()
        manager = self._make_manager()
        if not manager.tools_exist():
            QMessageBox.warning(self, "LDPlayer path", manager.path_status_message())
            return
        for index in self.selected_indices():
            _launched, result, message = manager.launch_if_needed(index)
            suffix = ""
            if result is not None and not result.ok:
                suffix = f": {result.brief()}"
            self._append_log(f"[LD-{index}] {message}{suffix}")

    def open_app_selected(self) -> None:
        self._apply_ui_to_settings()
        manager = self._make_manager()
        for index in self.selected_indices():
            result = manager.run_app(index, FACEBOOK_PACKAGE_NAME)
            if result.ok:
                self._append_log(f"[LD-{index}] open Facebook: ok")
            else:
                serial = manager.serial_for_index(index)
                device = ADBDevice(manager.adb_path, serial, index=index)
                fallback = device.open_app_with_monkey(FACEBOOK_PACKAGE_NAME)
                self._append_log(f"[LD-{index}] open Facebook: {'ok via ADB' if fallback else result.brief()}")

    def start_selected(self) -> None:
        try:
            self._apply_ui_to_settings()
            indices = self.selected_indices()
            if not indices:
                QMessageBox.information(self, "Start", "No LD selected")
                return
            self.scheduler.start(indices, self.settings.runtime_config)
            self._append_log("Start requested")
        except Exception as exc:
            self._append_log(f"Start error: {exc}")
            QMessageBox.critical(self, "Start error", str(exc))

    def start_one_index(self, index: int) -> None:
        try:
            self._apply_ui_to_settings()
            self.scheduler.start_one(index, self.settings.runtime_config)
        except Exception as exc:
            self._append_log(f"Start LD-{index} error: {exc}")
            QMessageBox.critical(self, "Start error", str(exc))

    def stop_one_index(self, index: int) -> None:
        self.scheduler.stop_one(index)

    def _drain_events(self) -> None:
        try:
            while True:
                event: AutomationEvent = self.event_queue.get_nowait()
                self._render_event(event)
        except Empty:
            pass
        self.scheduler.finalize_finished_run_if_any()

    def _render_event(self, event: AutomationEvent) -> None:
        if event.index < 0:
            self._append_log(event.message)
            return
        row = self.row_by_index.get(event.index)
        if row is None:
            serial = LDConsoleManager(self.settings.ld_base, self.settings.adb_port_base).serial_for_index(event.index)
            self._add_or_update_device(event.index, f"LD-{event.index}", serial)
            row = self.row_by_index[event.index]
        if event.status:
            self._set_cell(row, self.COL_STATUS, event.status)
        self._set_cell(row, self.COL_RUNTIME, f"{int(event.runtime_sec)}s")
        self._set_cell(row, self.COL_POSTS, str(event.posts_processed))
        self._set_cell(row, self.COL_SWIPES, str(event.swipe_count))
        self._set_cell(row, self.COL_REACTIONS, str(event.reaction_count))
        self._set_cell(row, self.COL_SCREENSHOTS, str(event.screenshot_count))
        self._set_cell(row, self.COL_ERRORS, str(event.error_count))
        if event.type == "stats":
            return
        self._set_cell(row, self.COL_LAST, event.message)
        self._append_log(f"[{time.strftime('%H:%M:%S')}] [LD-{event.index}] [{event.level}] [{event.status}] {event.message}")

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        if self.scheduler.active_indices():
            answer = QMessageBox.question(
                self,
                "Stop running workers?",
                "Workers are still running. Stop them and close?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if answer != QMessageBox.Yes:
                event.ignore()
                return
            self.scheduler.stop_all()
        event.accept()
