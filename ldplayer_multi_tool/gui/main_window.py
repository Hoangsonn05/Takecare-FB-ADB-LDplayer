from __future__ import annotations

import copy
from pathlib import Path
from queue import Empty, Queue
import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSpinBox,
    QTabWidget,
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
    COL_REACTIONS = 7
    COL_SCREENSHOTS = 8
    COL_ERRORS = 9
    COL_LAST = 10
    COL_ACTION = 11

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
        self.view_delay_min_spin = QDoubleSpinBox()
        self.view_delay_min_spin.setRange(0.0, 300.0)
        self.view_delay_min_spin.setSingleStep(0.5)
        self.view_delay_min_spin.setDecimals(1)
        self.view_delay_max_spin = QDoubleSpinBox()
        self.view_delay_max_spin.setRange(0.0, 300.0)
        self.view_delay_max_spin.setSingleStep(0.5)
        self.view_delay_max_spin.setDecimals(1)
        self.reel_delay_min_spin = QDoubleSpinBox()
        self.reel_delay_min_spin.setRange(0.0, 300.0)
        self.reel_delay_min_spin.setSingleStep(0.5)
        self.reel_delay_min_spin.setDecimals(1)
        self.reel_delay_max_spin = QDoubleSpinBox()
        self.reel_delay_max_spin.setRange(0.0, 300.0)
        self.reel_delay_max_spin.setSingleStep(0.5)
        self.reel_delay_max_spin.setDecimals(1)
        self.reverse_scroll_percent_spin = QSpinBox()
        self.reverse_scroll_percent_spin.setRange(0, 100)
        self.run_newfeed_check = QCheckBox("Luot Newfeed")
        self.run_newfeed_check.setChecked(True)
        self.run_reel_check = QCheckBox("Luot Reel")
        self.run_reel_check.setChecked(False)
        self.run_notifications_check = QCheckBox("Doc thong bao")
        self.run_notifications_check.setChecked(False)
        self.script_order_combo = QComboBox()
        self.script_order_combo.addItem("Newfeed -> Reel", "newfeed_then_reel")
        self.script_order_combo.addItem("Reel -> Newfeed", "reel_then_newfeed")
        self.script_order_combo.addItem("Newfeed -> Thong bao", "newfeed_then_notifications")
        self.script_order_combo.addItem("Thong bao -> Newfeed", "notifications_then_newfeed")
        self.script_order_combo.addItem("Reel -> Thong bao", "reel_then_notifications")
        self.script_order_combo.addItem("Thong bao -> Reel", "notifications_then_reel")
        self.script_order_combo.addItem("Newfeed -> Reel -> Thong bao", "newfeed_then_reel_then_notifications")
        self.script_order_combo.addItem("Newfeed -> Thong bao -> Reel", "newfeed_then_notifications_then_reel")
        self.script_order_combo.addItem("Reel -> Newfeed -> Thong bao", "reel_then_newfeed_then_notifications")
        self.script_order_combo.addItem("Reel -> Thong bao -> Newfeed", "reel_then_notifications_then_newfeed")
        self.script_order_combo.addItem("Thong bao -> Newfeed -> Reel", "notifications_then_newfeed_then_reel")
        self.script_order_combo.addItem("Thong bao -> Reel -> Newfeed", "notifications_then_reel_then_newfeed")
        self.script_order_combo.addItem("Random", "random")
        self.reel_minutes_spin = QSpinBox()
        self.reel_minutes_spin.setRange(0, 1440)
        self.reel_minutes_spin.setSpecialValueText("Dung chung")
        self.notification_count_spin = QSpinBox()
        self.notification_count_spin.setRange(0, 10000)
        self.notification_read_min_spin = QDoubleSpinBox()
        self.notification_read_min_spin.setRange(0.0, 300.0)
        self.notification_read_min_spin.setSingleStep(0.5)
        self.notification_read_min_spin.setDecimals(1)
        self.notification_read_max_spin = QDoubleSpinBox()
        self.notification_read_max_spin.setRange(0.0, 300.0)
        self.notification_read_max_spin.setSingleStep(0.5)
        self.notification_read_max_spin.setDecimals(1)
        self.reel_reaction_enabled_check = QCheckBox("Bat cam xuc Reel")
        self.reel_reaction_enabled_check.setChecked(False)
        self.reel_reaction_probability_spin = QSpinBox()
        self.reel_reaction_probability_spin.setRange(0, 100)

        # ── Tab: Kich ban ──
        script_tab = QWidget()
        script_form = QFormLayout(script_tab)
        script_checks = QHBoxLayout()
        script_checks.addWidget(self.run_newfeed_check)
        script_checks.addWidget(self.run_reel_check)
        script_checks.addWidget(self.run_notifications_check)
        script_form.addRow("Hanh dong", script_checks)
        script_form.addRow("Thu tu kich ban", self.script_order_combo)
        script_form.addRow("So phut Newfeed", self.run_minutes_spin)
        script_form.addRow("", self.save_config_btn)

        # ── Tab: Newfeed ──
        newfeed_tab = QWidget()
        newfeed_form = QFormLayout(newfeed_tab)
        newfeed_form.addRow("So post", self.max_posts_spin)
        newfeed_form.addRow("Xem bai min (s)", self.view_delay_min_spin)
        newfeed_form.addRow("Xem bai max (s)", self.view_delay_max_spin)
        newfeed_form.addRow("Ti le cuon nguoc (%)", self.reverse_scroll_percent_spin)

        # ── Tab: Cam xuc ──
        reaction_tab = QWidget()
        reaction_form = QFormLayout(reaction_tab)
        reaction_form.addRow("", self.reaction_enabled_check)
        reaction_form.addRow("So cam xuc toi da", self.max_reactions_spin)
        reaction_form.addRow("Ti le tha cam xuc (%)", self.reaction_probability_spin)
        reaction_row = QHBoxLayout()
        for reaction in ALLOWED_REACTIONS:
            reaction_row.addWidget(self.reaction_checks[reaction])
        reaction_form.addRow("Loai cam xuc", reaction_row)

        # ── Tab: Reel ──
        reel_tab = QWidget()
        reel_form = QFormLayout(reel_tab)
        reel_form.addRow("Phut Reel (0=chung)", self.reel_minutes_spin)
        reel_form.addRow("Xem Reel min (s)", self.reel_delay_min_spin)
        reel_form.addRow("Xem Reel max (s)", self.reel_delay_max_spin)
        reel_form.addRow("", self.reel_reaction_enabled_check)
        reel_form.addRow("Ti le cam xuc Reel (%)", self.reel_reaction_probability_spin)

        # ── Tab: Thong bao ──
        notification_tab = QWidget()
        notification_form = QFormLayout(notification_tab)
        notification_form.addRow("So thong bao doc", self.notification_count_spin)
        notification_form.addRow("Doc TB min (s)", self.notification_read_min_spin)
        notification_form.addRow("Doc TB max (s)", self.notification_read_max_spin)

        # ── Tab: Nang cao (trang) ──
        advanced_tab = QWidget()

        run_tabs = QTabWidget()
        run_tabs.addTab(script_tab, "Kich ban")
        run_tabs.addTab(newfeed_tab, "Newfeed")
        run_tabs.addTab(reaction_tab, "Cam xuc")
        run_tabs.addTab(reel_tab, "Reel")
        run_tabs.addTab(notification_tab, "Thong bao")
        run_tabs.addTab(advanced_tab, "Nang cao")

        run_box_layout = QVBoxLayout(run_box)
        run_box_layout.addWidget(run_tabs)
        layout.addWidget(run_box)

        button_row = QHBoxLayout()
        self.scan_btn = QPushButton("Scan")
        self.clear_btn = QPushButton("Xóa tất cả")
        self.connect_btn = QPushButton("Check ADB")
        self.start_btn = QPushButton("Start Selected")
        self.pause_btn = QPushButton("Pause")
        self.resume_btn = QPushButton("Resume")
        self.stop_btn = QPushButton("Stop Selected")
        for btn in [
            self.scan_btn,
            self.clear_btn,
            self.connect_btn,
            self.start_btn,
            self.stop_btn,
            self.pause_btn,
            self.resume_btn,
        ]:
            button_row.addWidget(btn)
        layout.addLayout(button_row)

        self.scan_btn.clicked.connect(self.scan_devices)
        self.clear_btn.clicked.connect(self.clear_all_devices)
        self.connect_btn.clicked.connect(self.check_selected_adb)
        self.start_btn.clicked.connect(self.start_selected)
        self.pause_btn.clicked.connect(lambda: self.scheduler.pause(self.selected_indices()))
        self.resume_btn.clicked.connect(lambda: self.scheduler.resume(self.selected_indices()))
        self.stop_btn.clicked.connect(self.stop_selected)

        self.table = QTableWidget(0, 12)
        self.table.setHorizontalHeaderLabels([
            "Use",
            "Index",
            "Name",
            "ADB Serial",
            "ADB State",
            "Status",
            "Runtime",
            "Reactions",
            "Screenshots",
            "Errors",
            "Last message",
            "Action",
        ])
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
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
        self.view_delay_min_spin.setValue(cfg.delay_min_sec)
        self.view_delay_max_spin.setValue(cfg.delay_max_sec)
        self.reel_delay_min_spin.setValue(cfg.reel_delay_min_sec)
        self.reel_delay_max_spin.setValue(cfg.reel_delay_max_sec)
        self.reverse_scroll_percent_spin.setValue(cfg.reverse_scroll_percent)
        self.run_newfeed_check.setChecked(cfg.run_newfeed)
        self.run_reel_check.setChecked(cfg.run_reel)
        self.run_notifications_check.setChecked(cfg.run_notifications)
        idx = self.script_order_combo.findData(cfg.script_order)
        if idx >= 0:
            self.script_order_combo.setCurrentIndex(idx)
        self.reel_minutes_spin.setValue(cfg.reel_run_minutes)
        self.notification_count_spin.setValue(cfg.notification_count)
        self.notification_read_min_spin.setValue(cfg.notification_read_min_sec)
        self.notification_read_max_spin.setValue(cfg.notification_read_max_sec)
        self.reel_reaction_enabled_check.setChecked(cfg.reel_reaction_enabled)
        self.reel_reaction_probability_spin.setValue(cfg.reel_reaction_probability_percent)
        selected = set(cfg.selected_reactions)
        for reaction, checkbox in self.reaction_checks.items():
            checkbox.setChecked(reaction in selected)
        self._load_recent_ld_ids_to_table()

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
        self.scheduler.set_max_workers(self.settings.max_concurrent_devices)
        if not manager.tools_exist():
            self._append_log(manager.path_status_message())

    def _runtime_config_from_ui(self) -> RuntimeConfig:
        base = self.settings.runtime_config
        selected = [name for name, checkbox in self.reaction_checks.items() if checkbox.isChecked()]
        if self.reaction_enabled_check.isChecked() and not selected:
            raise ValueError("Select at least one reaction")
        if self.view_delay_min_spin.value() > self.view_delay_max_spin.value():
            raise ValueError("Thoi gian xem bai min phai <= max")
        if self.reel_delay_min_spin.value() > self.reel_delay_max_spin.value():
            raise ValueError("Xem Reel min phai <= max")
        if self.notification_read_min_spin.value() > self.notification_read_max_spin.value():
            raise ValueError("Doc TB min phai <= max")
        run_newfeed = self.run_newfeed_check.isChecked()
        run_reel = self.run_reel_check.isChecked()
        run_notifications = self.run_notifications_check.isChecked()
        if not run_newfeed and not run_reel and not run_notifications:
            raise ValueError("Chon it nhat mot hanh dong")
        script_order = self.script_order_combo.currentData() or "newfeed_then_reel"
        cfg = RuntimeConfig.from_dict({
            **base.__dict__,
            "max_posts": self.max_posts_spin.value(),
            "run_minutes": self.run_minutes_spin.value(),
            "max_reactions": self.max_reactions_spin.value(),
            "reaction_probability_percent": self.reaction_probability_spin.value(),
            "reaction_enabled": self.reaction_enabled_check.isChecked(),
            "selected_reactions": selected,
            "delay_min_sec": self.view_delay_min_spin.value(),
            "delay_max_sec": self.view_delay_max_spin.value(),
            "reel_delay_min_sec": self.reel_delay_min_spin.value(),
            "reel_delay_max_sec": self.reel_delay_max_spin.value(),
            "reverse_scroll_percent": self.reverse_scroll_percent_spin.value(),
            "run_newfeed": run_newfeed,
            "run_reel": run_reel,
            "run_notifications": run_notifications,
            "script_order": script_order,
            "reel_run_minutes": self.reel_minutes_spin.value(),
            "notification_count": self.notification_count_spin.value(),
            "notification_read_min_sec": self.notification_read_min_spin.value(),
            "notification_read_max_sec": self.notification_read_max_spin.value(),
            "reel_reaction_enabled": self.reel_reaction_enabled_check.isChecked(),
            "reel_reaction_probability_percent": self.reel_reaction_probability_spin.value(),
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

    def _make_action_widget(self, index: int) -> QWidget:
        """Tao widget chua nut Start/Stop cho 1 dong LD."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(3)
        start_btn = QPushButton("Start")
        stop_btn = QPushButton("Stop")
        start_btn.setFixedWidth(48)
        stop_btn.setFixedWidth(48)
        start_btn.clicked.connect(lambda checked=False, i=index: self._row_start(i))
        stop_btn.clicked.connect(lambda checked=False, i=index: self._row_stop(i))
        layout.addWidget(start_btn)
        layout.addWidget(stop_btn)
        return container

    def _row_start(self, index: int) -> None:
        """Start rieng 1 LD tu nut tren dong."""
        try:
            self._apply_ui_to_settings()
            cfg = self._runtime_config_from_ui()
            self.scheduler.start_one(index, cfg)
        except Exception as exc:
            self._append_log(f"[LD-{index}] Start error: {exc}")

    def _row_stop(self, index: int) -> None:
        """Stop rieng 1 LD tu nut tren dong."""
        self.scheduler.stop_one(index)

    def _add_or_update_device(self, index: int, name: str, serial: str, adb_state: str = "unknown", initial_status: str = "IDLE", last: str = "", checked: bool = True) -> None:
        if index in self.row_by_index:
            row = self.row_by_index[index]
            cb = self.table.cellWidget(row, self.COL_SELECT)
            if isinstance(cb, QCheckBox):
                cb.setChecked(checked)
            # Cap nhat ten neu moi hon fallback
            cur_name_item = self.table.item(row, self.COL_NAME)
            cur_name = cur_name_item.text() if cur_name_item else ""
            if name and name != f"LD-{index}" and (not cur_name or cur_name == f"LD-{index}"):
                self._set_cell(row, self.COL_NAME, name)
        else:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.row_by_index[index] = row
            cb = QCheckBox()
            cb.setChecked(checked)
            cb.setStyleSheet("margin-left: 12px;")
            self.table.setCellWidget(row, self.COL_SELECT, cb)
            self.table.setCellWidget(row, self.COL_ACTION, self._make_action_widget(index))
            self._set_cell(row, self.COL_INDEX, str(index))
            self._set_cell(row, self.COL_NAME, name)
            self._set_cell(row, self.COL_SERIAL, serial)
            self._set_cell(row, self.COL_ADB, adb_state)
            self._set_cell(row, self.COL_STATUS, initial_status)
            self._set_cell(row, self.COL_RUNTIME, "0s")
            self._set_cell(row, self.COL_REACTIONS, "0")
            self._set_cell(row, self.COL_SCREENSHOTS, "0")
            self._set_cell(row, self.COL_ERRORS, "0")
            self._set_cell(row, self.COL_LAST, last)
            return
        # Update cac cell khac cho row da ton tai (tru ten da xu ly o tren)
        self._set_cell(row, self.COL_INDEX, str(index))
        self._set_cell(row, self.COL_SERIAL, serial)
        self._set_cell(row, self.COL_ADB, adb_state)
        self._set_cell(row, self.COL_STATUS, initial_status)
        self._set_cell(row, self.COL_RUNTIME, "0s")
        self._set_cell(row, self.COL_REACTIONS, "0")
        self._set_cell(row, self.COL_SCREENSHOTS, "0")
        self._set_cell(row, self.COL_ERRORS, "0")
        self._set_cell(row, self.COL_LAST, last)

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
            self._set_recent_ld_ids(indices, save=True)
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
            self._set_recent_ld_ids(indices, save=True)
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

    def show_context_menu(self, pos) -> None:
        menu = QMenu(self)
        delete_selected_action = menu.addAction("Xóa các dòng đã chọn")
        delete_all_action = menu.addAction("Xóa tất cả")
        
        action = menu.exec(self.table.mapToGlobal(pos))
        if action == delete_selected_action:
            self.delete_selected_devices()
        elif action == delete_all_action:
            self.clear_all_devices()

    def delete_selected_devices(self) -> None:
        selected_ranges = self.table.selectedRanges()
        rows_to_delete = set()
        for r in selected_ranges:
            for row in range(r.topRow(), r.bottomRow() + 1):
                rows_to_delete.add(row)
        
        if not rows_to_delete:
            return
            
        for row in sorted(rows_to_delete, reverse=True):
            self.table.removeRow(row)
            
        self._rebuild_row_by_index()
        remaining = []
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_INDEX)
            if item:
                remaining.append(int(item.text()))
        self._set_recent_ld_ids(remaining, save=True)
        self._append_log(f"Đã xóa {len(rows_to_delete)} dòng được chọn khỏi danh sách")

    def clear_all_devices(self) -> None:
        self.table.setRowCount(0)
        self.row_by_index.clear()
        self._set_recent_ld_ids([], save=True)
        self._append_log("Đã xóa toàn bộ danh sách thiết bị")

    def _rebuild_row_by_index(self) -> None:
        self.row_by_index.clear()
        for row in range(self.table.rowCount()):
            item = self.table.item(row, self.COL_INDEX)
            if item:
                try:
                    idx = int(item.text())
                    self.row_by_index[idx] = row
                except ValueError:
                    pass

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
            if state == "offline":
                self._append_log(
                    f"[LD-{index}] ADB offline: instance is reachable but ADB daemon is not ready. "
                    "Try restarting this LD profile or use Start to trigger recovery."
                )

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
            cfg = copy.deepcopy(self.settings.runtime_config)
            cfg.auto_quit_ld_after_run = True
            self._set_recent_ld_ids(indices, save=True)
            self.scheduler.start(indices, cfg)
            self._append_log("Start requested")
        except Exception as exc:
            self._append_log(f"Start error: {exc}")
            QMessageBox.critical(self, "Start error", str(exc))

    def stop_selected(self) -> None:
        indices = self.selected_indices()
        if not indices:
            QMessageBox.information(self, "Stop", "No LD selected")
            return
        self.scheduler.stop_selected(indices)
        self._append_log(f"Stop requested for LD ID(s): {', '.join(map(str, indices))}")

    def _drain_events(self) -> None:
        try:
            while True:
                event: AutomationEvent = self.event_queue.get_nowait()
                self._render_event(event)
        except Empty:
            pass
        self.scheduler.finalize_finished_run_if_any()

    # Cac event type/status khong can log vao panel
    _SILENT_TYPES = {"counter"}
    # Cac status quan trong can cap nhat Last message
    _IMPORTANT_STATUSES = {
        "STARTING", "CHECKING_PATHS", "CONNECTING_ADB", "LAUNCHING",
        "WAITING_BOOT", "WAITING_FACEBOOK", "OPENING_APP", "RUNNING",
        "PAUSED", "STOPPING", "STOPPED", "DONE", "ERROR",
    }

    def _render_event(self, event: AutomationEvent) -> None:
        if event.index < 0:
            # Scheduler message
            if event.message:
                self._append_log(event.message)
            return
        row = self.row_by_index.get(event.index)
        if row is None:
            serial = LDConsoleManager(self.settings.ld_base, self.settings.adb_port_base).serial_for_index(event.index)
            self._add_or_update_device(event.index, f"LD-{event.index}", serial)
            row = self.row_by_index[event.index]
        # Luon cap nhat counters (Posts/Swipes khong hien thi tren bang nhung van dem noi bo)
        if event.status:
            self._set_cell(row, self.COL_STATUS, event.status)
        self._set_cell(row, self.COL_RUNTIME, f"{int(event.runtime_sec)}s")
        self._set_cell(row, self.COL_REACTIONS, str(event.reaction_count))
        self._set_cell(row, self.COL_SCREENSHOTS, str(event.screenshot_count))
        self._set_cell(row, self.COL_ERRORS, str(event.error_count))
        # Khong spam log voi event counter/silent
        is_silent = event.type in self._SILENT_TYPES or not event.message
        if is_silent:
            return
        # Cap nhat Last message chi cho cac moc quan trong
        is_important = (event.status in self._IMPORTANT_STATUSES
                        or event.type in {"error", "reaction", "screenshot", "status"}
                        or event.level in {"ERROR", "WARN"})
        if is_important:
            self._set_cell(row, self.COL_LAST, event.message)
        # Ghi log panel: loc bot log swipe/action neu khong phai verbose
        verbose = getattr(self.settings.runtime_config, "verbose_action_log", False)
        if event.type == "action" and not verbose:
            return
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
        try:
            indices = []
            for row in range(self.table.rowCount()):
                item = self.table.item(row, self.COL_INDEX)
                if item:
                    indices.append(int(item.text()))
            self.settings.recent_ld_ids = list(dict.fromkeys(indices))
            self._apply_ui_to_settings()
            save_settings(self.settings, self.config_path)
        except Exception as exc:
            self._append_log(f"Close save config error: {exc}")
        event.accept()

    # ──────────────────────────────────────────────
    # Recent LD IDs helpers
    # ──────────────────────────────────────────────

    def _set_recent_ld_ids(self, indices: list, save: bool = True) -> None:
        """Luu danh sach LD IDs gan nhat vao settings va ld_ids_edit."""
        clean = list(dict.fromkeys(int(i) for i in indices if int(i) >= 0))
        self.settings.recent_ld_ids = clean
        self.ld_ids_edit.setText(",".join(map(str, clean)))
        if save:
            try:
                save_settings(self.settings, self.config_path)
            except Exception as exc:
                self._append_log(f"Save recent LD IDs failed: {exc}")

    def _load_recent_ld_ids_to_table(self) -> None:
        """Khoi phuc danh sach LD IDs tu settings vao bang khi mo tool."""
        ids = list(dict.fromkeys(
            int(x) for x in getattr(self.settings, "recent_ld_ids", [])
            if str(x).lstrip("-").isdigit() and int(x) >= 0
        ))
        if not ids:
            return
        self.ld_ids_edit.setText(",".join(map(str, ids)))
        manager = self._make_manager()
        try:
            known = {inst.index: inst for inst in manager.list_instances()}
        except Exception:
            known = {}
        for index in ids:
            inst = known.get(index)
            name = inst.name if inst else f"LD-{index}"
            if inst and inst.is_running is True:
                status = "READY"
                last = "Restored from config; LDPlayer running"
            elif inst and inst.is_running is False:
                status = "STOPPED"
                last = "Restored from config; LDPlayer not running"
            else:
                status = "IDLE"
                last = "Restored from config; not confirmed"
            self._add_or_update_device(
                index=index,
                name=name,
                serial=manager.serial_for_index(index),
                adb_state="unknown",
                initial_status=status,
                last=last,
                checked=True,
            )
        self._append_log(f"Restored LD ID(s): {', '.join(map(str, ids))}")
