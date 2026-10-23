from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
import threading
import time

from core.adb_device import ADBDevice, ScreenSize
from core.config import AppSettings, RuntimeConfig
from core.ldconsole import LDConsoleManager

BootstrapEmitter = Callable[[str, str, str, str | None], None]


@dataclass(slots=True)
class DeviceSession:
    index: int
    ld: LDConsoleManager
    device: ADBDevice
    screen_size: ScreenSize | None


class DeviceBootstrapService:
    """Shared LDPlayer/ADB/Facebook bootstrap used by both automation modes."""

    def __init__(
        self,
        index: int,
        settings: AppSettings,
        config: RuntimeConfig,
        stop_event: threading.Event,
        pause_event: threading.Event,
        emit: BootstrapEmitter | None = None,
        require_existing: bool = False,
    ) -> None:
        self.index = int(index)
        self.settings = settings
        self.config = config
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.emit_cb = emit or (lambda *_: None)
        self.require_existing = require_existing
        self.ld = LDConsoleManager(settings.ld_base, settings.adb_port_base)
        self.device = ADBDevice(
            self.ld.adb_path,
            self.ld.serial_for_index(self.index),
            index=self.index,
        )

    def emit(
        self,
        type_: str,
        message: str,
        level: str = "INFO",
        status: str | None = None,
    ) -> None:
        self.emit_cb(type_, message, level, status)

    def sleep_interruptible(self, seconds: float) -> bool:
        deadline = time.monotonic() + max(0.0, float(seconds))
        while time.monotonic() < deadline:
            if self.stop_event.is_set():
                return False
            while self.pause_event.is_set() and not self.stop_event.is_set():
                self.stop_event.wait(0.25)
            remaining = max(0.0, deadline - time.monotonic())
            if self.stop_event.wait(min(0.25, remaining)):
                return False
        return not self.stop_event.is_set()

    def ensure_ldplayer_started(self) -> bool:
        self.emit("status", f"Checking LDPlayer instance {self.index}", status="LAUNCHING")
        running = self.ld.is_instance_running_by_ldconsole(self.index)
        if running is True:
            self.emit("status", "LDPlayer already running by ldconsole", status="LAUNCHING")
            return True
        if self.require_existing:
            self.emit("error", f"LD-{self.index} is not confirmed running", level="ERROR", status="ERROR")
            return False
        if running is False:
            self.emit(
                "status",
                f"LDPlayer index {self.index} is not running; launching",
                status="LAUNCHING",
            )
        else:
            self.emit(
                "status",
                f"LDPlayer running state unknown for index {self.index}; launching anyway",
                level="WARN",
                status="LAUNCHING",
            )
        result = self.ld.launch(self.index)
        if result.ok:
            self.emit("status", "LDPlayer launch command sent", status="LAUNCHING")
            return self.sleep_interruptible(4.0)
        if self.ld.looks_like_usage_output(result):
            self.emit(
                "error",
                "LDPlayer launch returned Usage/help output; "
                f"args={result.args}; output={result.brief()}",
                level="ERROR",
                status="ERROR",
            )
            return False
        if self.ld.is_instance_running_by_ldconsole(self.index) is True:
            self.emit(
                "status",
                "LDPlayer launch returned non-zero but the instance is running",
                level="WARN",
                status="LAUNCHING",
            )
            return self.sleep_interruptible(3.0)
        self.emit(
            "error",
            f"LDPlayer launch failed: args={result.args}; output={result.brief()}",
            level="ERROR",
            status="ERROR",
        )
        return False

    def _adb_not_ready_details(self) -> str:
        adb_list = self.device.devices_list().strip()[:500]
        running_state = self.ld.is_instance_running_by_ldconsole(self.index)
        return (
            f"ADB device not ready after LD launch: {self.device.serial}; "
            f"ldconsole_running={running_state}; adb_devices={adb_list}"
        )

    def restart_after_adb_not_ready(self) -> bool | None:
        if self.require_existing:
            return None
        restarts = max(0, int(getattr(self.config, "adb_recovery_restarts", 0)))
        if not getattr(self.config, "restart_ld_on_adb_offline", True) or restarts <= 0:
            return None
        self.emit(
            "status",
            f"ADB not ready for {self.device.serial}; restarting LDPlayer index {self.index}",
            level="WARN",
            status="CONNECTING_ADB",
        )
        self.ld.quit(self.index)
        if not self.sleep_interruptible(5.0):
            return False
        launch = self.ld.launch(self.index)
        if not launch.ok:
            self.emit(
                "error",
                f"LDPlayer relaunch failed after ADB offline: {launch.brief()}",
                level="ERROR",
                status="ERROR",
            )
            return False
        if not self.sleep_interruptible(8.0):
            return False
        if self.device.wait_for_device(
            timeout_sec=self.config.ld_boot_timeout_sec,
            poll_sec=2,
            stop_event=self.stop_event,
        ):
            self.emit("status", "ADB connected after LDPlayer restart", status="CONNECTING_ADB")
            return True
        self.emit(
            "error",
            self._adb_not_ready_details(),
            level="ERROR",
            status="ERROR",
        )
        return False

    def open_facebook(self) -> bool:
        package = self.settings.package_name
        if self.require_existing:
            foreground = bool(package and self.device.is_package_foreground(package))
            if not foreground:
                self.emit("error", "Facebook is not foreground", level="ERROR", status="ERROR")
            return foreground
        if not self.device.is_package_installed(package):
            self.emit(
                "error",
                f"Facebook package not installed: {package}",
                level="ERROR",
                status="ERROR",
            )
            return False
        self.emit("status", f"Opening Facebook: {package}", status="OPENING_APP")
        for attempt in range(1, 4):
            app = self.ld.run_app(self.index, package)
            if not app.ok:
                self.emit(
                    "status",
                    f"ldconsole runapp failed: {app.brief()}",
                    level="WARN",
                    status="OPENING_APP",
                )
            if not self.sleep_interruptible(2.5):
                return False
            self.emit("status", "Checking Facebook foreground", status="WAITING_FACEBOOK")
            if self.device.is_package_foreground(package):
                self.emit("status", "Facebook foreground confirmed", status="WAITING_FACEBOOK")
                if not self.sleep_interruptible(self.config.facebook_settle_sec):
                    return False
                self.emit("status", "Facebook ready", status="WAITING_FACEBOOK")
                return True
            self.emit(
                "status",
                f"Facebook not foreground, retry {attempt}/3",
                level="WARN",
                status="WAITING_FACEBOOK",
            )
            if not self.device.open_app_with_monkey(package):
                self.emit(
                    "status",
                    "Facebook monkey fallback failed",
                    level="WARN",
                    status="OPENING_APP",
                )
            if not self.sleep_interruptible(2.5):
                return False
            if self.device.is_package_foreground(package):
                if not self.sleep_interruptible(self.config.facebook_settle_sec):
                    return False
                self.emit("status", "Facebook ready", status="WAITING_FACEBOOK")
                return True
        focused = self.device.foreground_activity_line(package)
        suffix = f"; last focused line: {focused}" if focused else ""
        self.emit(
            "error",
            f"Facebook did not reach foreground{suffix}",
            level="ERROR",
            status="ERROR",
        )
        return False

    def bootstrap(self) -> DeviceSession | None:
        self.emit("status", "Checking LDPlayer executable paths", status="CHECKING_PATHS")
        if self.ld.ld_base != self.ld.raw_ld_base:
            self.emit(
                "status",
                f"Resolved LDPlayer base: {self.ld.ld_base}",
                status="CHECKING_PATHS",
            )
        paths_ok, missing = self.ld.validate_paths()
        if not paths_ok:
            self.emit(
                "error",
                "Missing executable(s): " + "; ".join(missing),
                level="ERROR",
                status="ERROR",
            )
            return None
        if not self.ensure_ldplayer_started():
            return None
        self.emit("status", f"ADB serial: {self.device.serial}", status="CONNECTING_ADB")
        if self.require_existing:
            adb_ready = self.device.get_state() == "device"
        else:
            adb_ready = self.device.wait_for_device(
                timeout_sec=self.config.ld_boot_timeout_sec,
                poll_sec=2,
                stop_event=self.stop_event,
            )
        if not adb_ready:
            recovered = self.restart_after_adb_not_ready()
            if recovered is None:
                self.emit(
                    "error",
                    self._adb_not_ready_details(),
                    level="ERROR",
                    status="ERROR",
                )
                return None
            if recovered is False:
                return None
        else:
            self.emit("status", "ADB connected", status="CONNECTING_ADB")
        self.emit("status", "Waiting for Android boot completed", status="WAITING_BOOT")
        if self.require_existing:
            boot = self.device.shell("getprop", "sys.boot_completed", timeout=5)
            boot_ready = boot.ok and boot.stdout.strip() == "1"
        else:
            boot_ready = self.device.wait_for_boot_completed(
                timeout_sec=self.config.ld_boot_timeout_sec,
                poll_sec=2,
                stop_event=self.stop_event,
            )
        if not boot_ready:
            self.emit(
                "error",
                f"Android boot did not complete within {self.config.ld_boot_timeout_sec}s",
                level="ERROR",
                status="ERROR",
            )
            return None
        if not self.sleep_interruptible(self.config.ld_settle_sec):
            return None
        if self.device.get_state() != "device":
            self.emit(
                "error",
                f"ADB became unavailable after boot: {self.device.serial}",
                level="ERROR",
                status="ERROR",
            )
            return None
        package = self.settings.package_name
        if package and not self.device.is_package_installed(package):
            self.emit(
                "error",
                f"Facebook package not installed: {package}",
                level="ERROR",
                status="ERROR",
            )
            return None
        size = self.device.screen_size()
        if size:
            self.emit("status", f"Screen size: {size.width}x{size.height}")
        if (self.require_existing or self.config.launch_app) and package:
            if not self.open_facebook():
                return None
        return DeviceSession(self.index, self.ld, self.device, size)
