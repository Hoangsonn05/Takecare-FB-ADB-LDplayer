from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import time

from .command_runner import CommandRunner, CommandResult


@dataclass
class ScreenSize:
    width: int
    height: int


class ADBDevice:
    def __init__(self, adb_path: str | Path, serial: str, runner: Optional[CommandRunner] = None, index: Optional[int] = None):
        self.adb_path = Path(adb_path)
        self.serial = serial
        self.runner = runner or CommandRunner()
        self.index = index

    def _adb(self, *args: str | int | Path) -> list[str]:
        return [str(self.adb_path), "-s", self.serial, *[str(a) for a in args]]

    def connect(self) -> bool:
        # adb connect does not use -s.
        result = self.runner.run([self.adb_path, "connect", self.serial], timeout=10)
        text = f"{result.stdout}\n{result.stderr}".lower()
        return result.ok and ("connected" in text or "already connected" in text or not text.strip())

    def disconnect(self) -> bool:
        result = self.runner.run([self.adb_path, "disconnect", self.serial], timeout=10)
        return result.ok

    def devices_list(self) -> str:
        result = self.runner.run([self.adb_path, "devices", "-l"], timeout=10)
        return result.stdout if result.ok else result.stderr

    def get_state(self) -> str:
        result = self.runner.run(self._adb("get-state"), timeout=5)
        return result.stdout.strip() if result.ok else "offline"

    def wait_for_device(self, timeout_sec: float = 90.0, poll_sec: float = 2.0) -> bool:
        end = time.time() + timeout_sec
        offline_hits = 0

        self.connect()
        while time.time() < end:
            state = self.get_state()
            if state == "device":
                return True

            if state == "offline":
                offline_hits += 1
                if offline_hits in {2, 4, 6}:
                    self.disconnect()
                    time.sleep(1.0)
                    self.connect()
                else:
                    self.connect()
            else:
                self.connect()

            time.sleep(poll_sec)
        return False

    def wait_for_boot_completed(self, timeout_sec: int = 90, poll_sec: int = 2) -> bool:
        end = time.time() + timeout_sec
        while time.time() < end:
            if self.get_state() == "device":
                result = self.shell("getprop", "sys.boot_completed", timeout=5)
                if result.ok and result.stdout.strip() == "1":
                    return True
            self.connect()
            time.sleep(poll_sec)
        return False

    def shell(self, *args: str | int | Path, timeout: float = 10.0) -> CommandResult:
        return self.runner.run(self._adb("shell", *args), timeout=timeout)

    def tap(self, x: int, y: int) -> CommandResult:
        return self.shell("input", "tap", int(x), int(y), timeout=8)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> CommandResult:
        return self.shell("input", "swipe", int(x1), int(y1), int(x2), int(y2), int(duration_ms), timeout=10)

    def long_press(self, x: int, y: int, duration_ms: int = 900) -> CommandResult:
        return self.swipe(x, y, x, y, duration_ms)

    def back(self) -> CommandResult:
        return self.shell("input", "keyevent", "4", timeout=8)

    def open_app_with_monkey(self, package_name: str) -> bool:
        result = self.shell("monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1", timeout=15)
        return result.ok

    def is_package_installed(self, package_name: str) -> bool:
        result = self.shell("pm", "list", "packages", package_name, timeout=10)
        if not result.ok:
            return False
        return f"package:{package_name}" in {line.strip() for line in result.stdout.splitlines()}

    @staticmethod
    def _line_has_foreground_package(line: str, package_name: str) -> bool:
        return f"{package_name}/" in line

    def is_package_foreground(self, package_name: str) -> bool:
        window = self.shell("dumpsys", "window", timeout=8)
        if window.ok:
            for line in window.stdout.splitlines():
                if ("mCurrentFocus" in line or "mFocusedApp" in line) and self._line_has_foreground_package(line, package_name):
                    return True

        activity = self.shell("dumpsys", "activity", "activities", timeout=8)
        if activity.ok:
            for line in activity.stdout.splitlines():
                if (
                    "mResumedActivity" in line
                    or "topResumedActivity" in line
                    or "ResumedActivity" in line
                ) and self._line_has_foreground_package(line, package_name):
                    return True
        return False

    def foreground_activity_line(self, package_name: str) -> str:
        window = self.shell("dumpsys", "window", timeout=8)
        if window.ok:
            for line in window.stdout.splitlines():
                if ("mCurrentFocus" in line or "mFocusedApp" in line) and package_name in line:
                    return line.strip()
        activity = self.shell("dumpsys", "activity", "activities", timeout=8)
        if activity.ok:
            for line in activity.stdout.splitlines():
                if (
                    "mResumedActivity" in line
                    or "topResumedActivity" in line
                    or "ResumedActivity" in line
                ) and package_name in line:
                    return line.strip()
        return ""

    def wait_for_package_foreground(self, package_name: str, timeout_sec: int = 45, poll_sec: int = 2) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if self.is_package_foreground(package_name):
                return True
            time.sleep(poll_sec)
        return False

    def screenshot(self, local_path: str | Path) -> bool:
        from core.image_io import image_is_readable

        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)

        if local.exists():
            try:
                local.unlink()
            except OSError:
                pass

        remote = f"/sdcard/ld_tool_screen_{self.serial.replace(':', '_')}.png"
        cap = self.shell("screencap", "-p", remote, timeout=15)
        if not cap.ok:
            return False

        pulled = self.runner.run([self.adb_path, "-s", self.serial, "pull", remote, local], timeout=20)
        if not pulled.ok:
            return False

        if not local.exists() or local.stat().st_size <= 0:
            return False

        if not image_is_readable(local):
            return False

        return True

    def screen_size(self) -> Optional[ScreenSize]:
        result = self.shell("wm", "size", timeout=8)
        if not result.ok:
            return None
        text = result.stdout.strip()
        # Example: Physical size: 720x1280
        for token in text.replace("\r", " ").replace("\n", " ").split():
            if "x" in token:
                left, _, right = token.partition("x")
                try:
                    return ScreenSize(int(left), int(right))
                except ValueError:
                    continue
        return None
