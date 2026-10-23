from __future__ import annotations

from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from integrations.golike_api import JobAction


class AuthPanel(QGroupBox):
    def __init__(self, parent=None) -> None:
        super().__init__("Xác thực GoLike", parent)
        layout = QGridLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(4)

        self.token_edit = QLineEdit()
        self.token_edit.setEchoMode(QLineEdit.Password)
        self.token_edit.setPlaceholderText(
            "Nhập AuthToken — chỉ giữ trong bộ nhớ, không lưu vào cấu hình"
        )
        self.auth_btn = QPushButton("Xác thực")
        self.disconnect_btn = QPushButton("Ngắt / Xóa token")
        self.auth_btn.setFixedWidth(90)
        self.disconnect_btn.setFixedWidth(125)
        self.auth_status = QLabel("Chưa xác thực")
        self.username_label = QLabel("-")
        self.coin_label = QLabel("-")

        layout.addWidget(QLabel("Token"), 0, 0)
        layout.addWidget(self.token_edit, 0, 1, 1, 5)
        layout.addWidget(self.auth_btn, 0, 6)
        layout.addWidget(self.disconnect_btn, 0, 7)
        layout.addWidget(QLabel("Trạng thái:"), 1, 0)
        layout.addWidget(self.auth_status, 1, 1)
        layout.addWidget(QLabel("User:"), 1, 2)
        layout.addWidget(self.username_label, 1, 3)
        layout.addWidget(QLabel("Coin:"), 1, 4)
        layout.addWidget(self.coin_label, 1, 5)
        layout.setColumnStretch(1, 2)
        layout.setColumnStretch(3, 1)


class AccountBindingPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        account_box = QGroupBox("Tài khoản Facebook")
        account_layout = QVBoxLayout(account_box)
        account_layout.setContentsMargins(6, 6, 6, 6)
        self.account_table = QTableWidget(0, 5)
        self.account_table.setHorizontalHeaderLabels(
            ["Tên Facebook", "UID", "Provider ID", "Trạng thái", "Loại job"]
        )
        self.account_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.account_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.account_table.setAlternatingRowColors(True)
        self.account_table.verticalHeader().setVisible(False)
        self.account_table.verticalHeader().setDefaultSectionSize(24)
        account_header = self.account_table.horizontalHeader()
        account_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        account_header.setSectionResizeMode(0, QHeaderView.Stretch)
        self.account_table.setMinimumHeight(105)
        account_layout.addWidget(self.account_table)
        root.addWidget(account_box, 1)

        binding_box = QGroupBox("Binding LDPlayer ↔ Facebook")
        binding_grid = QGridLayout(binding_box)
        binding_grid.setContentsMargins(6, 6, 6, 6)
        binding_grid.setHorizontalSpacing(5)
        binding_grid.setVerticalSpacing(4)
        self.scan_ld_btn = QPushButton("Quét LD")
        self.ld_combo = QComboBox()
        self.account_combo = QComboBox()
        self.binding_confirm = QCheckBox(
            "Xác nhận account đang đăng nhập đúng trên LD"
        )
        self.binding_warning = QLabel(
            "Facebook không cung cấp UID đăng nhập ổn định qua ADB; "
            "cần xác nhận mapping thủ công."
        )
        self.binding_warning.setWordWrap(True)
        self.binding_warning.setStyleSheet("color: #8a5a00;")
        self.bind_btn = QPushButton("Gán")
        self.remove_binding_btn = QPushButton("Xóa binding")

        binding_grid.addWidget(self.scan_ld_btn, 0, 0)
        binding_grid.addWidget(QLabel("LD"), 0, 1)
        binding_grid.addWidget(self.ld_combo, 0, 2)
        binding_grid.addWidget(QLabel("Account"), 0, 3)
        binding_grid.addWidget(self.account_combo, 0, 4)
        binding_grid.addWidget(self.bind_btn, 0, 5)
        binding_grid.addWidget(self.binding_warning, 1, 0, 1, 3)
        binding_grid.addWidget(self.binding_confirm, 1, 3, 1, 2)
        binding_grid.addWidget(self.remove_binding_btn, 1, 5)
        binding_grid.setColumnStretch(2, 1)
        binding_grid.setColumnStretch(4, 1)

        self.binding_table = QTableWidget(0, 5)
        self.binding_table.setHorizontalHeaderLabels(
            ["LD", "ADB serial", "Tên Facebook", "UID", "Provider ID"]
        )
        self.binding_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.binding_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.binding_table.setAlternatingRowColors(True)
        self.binding_table.verticalHeader().setVisible(False)
        self.binding_table.verticalHeader().setDefaultSectionSize(24)
        binding_header = self.binding_table.horizontalHeader()
        binding_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        binding_header.setSectionResizeMode(2, QHeaderView.Stretch)
        self.binding_table.setMinimumHeight(90)
        binding_grid.addWidget(self.binding_table, 2, 0, 1, 6)
        root.addWidget(binding_box, 1)


class JobConfigPanel(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(6)

        job_box = QGroupBox("Công việc & Delay")
        job_form = QFormLayout(job_box)
        job_form.setContentsMargins(8, 6, 8, 6)
        job_form.setSpacing(5)
        self.max_jobs_spin = QSpinBox()
        self.max_jobs_spin.setRange(0, 100000)
        self.max_jobs_spin.setValue(10)
        self.max_jobs_spin.setSpecialValueText("Không giới hạn")
        self.low_job_fallback_check = QCheckBox("Cho phép thử job điểm thấp")
        self.low_job_fallback_check.setChecked(True)
        self.low_job_fallback_check.setToolTip(
            "Tắt: luôn gửi low_job=0. Bật: sau N lần không có job điểm cao, "
            "gửi low_job=1 đúng một lần rồi quay lại 0."
        )
        self.low_job_fallback_spin = QSpinBox()
        self.low_job_fallback_spin.setRange(1, 100)
        self.low_job_fallback_spin.setValue(3)
        self.low_job_fallback_spin.setToolTip(
            "Số lần liên tiếp low_job=0 không có job trước khi thử low_job=1."
        )
        self.low_job_fallback_check.toggled.connect(
            self.low_job_fallback_spin.setEnabled
        )
        self.delay_min_spin = QDoubleSpinBox()
        self.delay_min_spin.setRange(0, 3600)
        self.delay_min_spin.setValue(8)
        self.delay_max_spin = QDoubleSpinBox()
        self.delay_max_spin.setRange(0, 3600)
        self.delay_max_spin.setValue(15)
        self.error_cooldown_spin = QDoubleSpinBox()
        self.error_cooldown_spin.setRange(0, 3600)
        self.error_cooldown_spin.setValue(20)
        for spin in (
            self.delay_min_spin,
            self.delay_max_spin,
            self.error_cooldown_spin,
        ):
            spin.setSuffix(" s")
            spin.setDecimals(1)
        job_form.addRow("Max job", self.max_jobs_spin)
        job_form.addRow("Low job", self.low_job_fallback_check)
        job_form.addRow("Thử sau số lần rỗng", self.low_job_fallback_spin)
        job_form.addRow("Delay tối thiểu", self.delay_min_spin)
        job_form.addRow("Delay tối đa", self.delay_max_spin)
        job_form.addRow("Cooldown lỗi", self.error_cooldown_spin)

        navigation_box = QGroupBox("Điều hướng target")
        navigation_form = QFormLayout(navigation_box)
        navigation_form.setContentsMargins(8, 6, 8, 6)
        navigation_form.setSpacing(5)
        self.job_route_timeout_spin = QDoubleSpinBox()
        self.job_route_timeout_spin.setRange(2.0, 180.0)
        self.job_route_timeout_spin.setDecimals(1)
        self.job_route_timeout_spin.setValue(30.0)
        self.job_route_timeout_spin.setSuffix(" s")
        self.job_route_timeout_spin.setToolTip(
            "Thời gian chờ explicit Facebook route tải và xác minh target."
        )
        self.feed_swipes_spin = QSpinBox()
        self.feed_swipes_spin.setRange(0, 20)
        self.feed_swipes_spin.setValue(3)

        self.screen_width_spin = QSpinBox()
        self.screen_width_spin.setRange(240, 4320)
        self.screen_width_spin.setValue(540)
        self.screen_width_spin.setSuffix(" px")
        self.screen_width_spin.setToolTip("Chiều ngang LDPlayer dùng để chuẩn hóa vùng nhận dạng.")
        self.screen_height_spin = QSpinBox()
        self.screen_height_spin.setRange(320, 7680)
        self.screen_height_spin.setValue(960)
        self.screen_height_spin.setSuffix(" px")
        self.screen_height_spin.setToolTip("Chiều dọc LDPlayer dùng để chuẩn hóa vùng nhận dạng.")
        self.screen_dpi_spin = QSpinBox()
        self.screen_dpi_spin.setRange(72, 640)
        self.screen_dpi_spin.setValue(150)
        self.screen_dpi_spin.setSuffix(" DPI")
        self.screen_dpi_spin.setToolTip("DPI điều khiển kích thước icon kỳ vọng của OpenCV.")
        for spin in (
            self.max_jobs_spin,
            self.low_job_fallback_spin,
            self.delay_min_spin,
            self.delay_max_spin,
            self.error_cooldown_spin,
            self.job_route_timeout_spin,
            self.feed_swipes_spin,
            self.screen_width_spin,
            self.screen_height_spin,
            self.screen_dpi_spin,
        ):
            spin.setFixedWidth(105)
        navigation_form.addRow("Chờ mở job", self.job_route_timeout_spin)
        navigation_form.addRow("Kéo tìm Like", self.feed_swipes_spin)
        navigation_form.addRow("Ngang màn hình", self.screen_width_spin)
        navigation_form.addRow("Dọc màn hình", self.screen_height_spin)
        navigation_form.addRow("Mật độ màn hình", self.screen_dpi_spin)
        grid.addWidget(job_box, 0, 0)
        grid.addWidget(navigation_box, 0, 1)

        actions_box = QGroupBox("Loại job")
        actions_grid = QGridLayout(actions_box)
        actions_grid.setContentsMargins(8, 6, 8, 6)
        actions_grid.setHorizontalSpacing(10)
        actions_grid.setVerticalSpacing(4)
        self.action_checks = {
            JobAction.LIKE: QCheckBox("Like"),
            JobAction.LOVE: QCheckBox("Love"),
            JobAction.CARE: QCheckBox("Care"),
            JobAction.HAHA: QCheckBox("Haha"),
            JobAction.WOW: QCheckBox("Wow"),
            JobAction.SAD: QCheckBox("Sad"),
            JobAction.ANGRY: QCheckBox("Angry"),
            JobAction.FOLLOW: QCheckBox("Follow"),
            JobAction.LIKE_PAGE: QCheckBox("Like Page"),
        }
        for index, (action, checkbox) in enumerate(self.action_checks.items()):
            supported = action not in {JobAction.FOLLOW, JobAction.LIKE_PAGE}
            checkbox.setChecked(supported)
            checkbox.setEnabled(supported)
            if not supported:
                checkbox.setToolTip("Chưa có executor được xác minh")
            actions_grid.addWidget(checkbox, index // 4, index % 4)
        grid.addWidget(actions_box, 1, 0, 1, 2)

        diagnostics_box = QGroupBox("Chẩn đoán")
        diagnostics_layout = QHBoxLayout(diagnostics_box)
        diagnostics_layout.setContentsMargins(8, 5, 8, 5)
        self.save_evidence_check = QCheckBox("Lưu ảnh và timeline khi lỗi")
        self.save_evidence_check.setChecked(True)
        diagnostics_layout.addWidget(self.save_evidence_check)
        diagnostics_layout.addStretch(1)
        grid.addWidget(diagnostics_box, 2, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        root.addLayout(grid)

        controls = QHBoxLayout()
        controls.setSpacing(6)
        self.start_btn = QPushButton("Start Auto Job")
        self.stop_btn = QPushButton("Stop")
        self.pause_btn = QPushButton("Pause")
        self.resume_btn = QPushButton("Resume")
        self.start_btn.setStyleSheet("font-weight: 600; padding: 5px 14px;")
        self.stop_btn.setStyleSheet("padding: 5px 14px;")
        controls.addWidget(self.start_btn)
        controls.addWidget(self.pause_btn)
        controls.addWidget(self.resume_btn)
        controls.addWidget(self.stop_btn)
        controls.addStretch(1)
        root.addLayout(controls)
        root.addStretch(1)


class WorkerMonitorPanel(QTabWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.worker_table = QTableWidget(0, 9)
        self.worker_table.setHorizontalHeaderLabels(
            [
                "LD",
                "UID",
                "State",
                "Job ID",
                "Action",
                "Completed",
                "Reported",
                "Errors",
                "Last message",
            ]
        )
        self.worker_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.worker_table.setAlternatingRowColors(True)
        self.worker_table.verticalHeader().setVisible(False)
        self.worker_table.verticalHeader().setDefaultSectionSize(24)
        worker_header = self.worker_table.horizontalHeader()
        worker_header.setSectionResizeMode(QHeaderView.ResizeToContents)
        worker_header.setSectionResizeMode(8, QHeaderView.Stretch)
        self.addTab(self.worker_table, "Trạng thái worker")

        log_page = QWidget()
        log_layout = QVBoxLayout(log_page)
        log_layout.setContentsMargins(4, 4, 4, 4)
        log_layout.setSpacing(4)
        toolbar = QHBoxLayout()
        clear_log_btn = QPushButton("Xóa log")
        clear_log_btn.setFixedWidth(75)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        clear_log_btn.clicked.connect(self.log.clear)
        toolbar.addStretch(1)
        toolbar.addWidget(clear_log_btn)
        log_layout.addLayout(toolbar)
        log_layout.addWidget(self.log)
        self.addTab(log_page, "Nhật ký")
