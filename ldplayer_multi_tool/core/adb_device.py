from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple
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

    def get_state(self) -> str:
        result = self.runner.run(self._adb("get-state"), timeout=5)
        return result.stdout.strip() if result.ok else "offline"

    def wait_for_device(self, timeout_sec: float = 90.0, poll_sec: float = 2.0) -> bool:
        end = time.time() + timeout_sec
        self.connect()
        while time.time() < end:
            if self.get_state() == "device":
                return True
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
        local = Path(local_path)
        local.parent.mkdir(parents=True, exist_ok=True)
        # Preferred path: stream screenshot directly to stdout.
        result = self.runner.run(self._adb("exec-out", "screencap", "-p"), timeout=15)
        if result.returncode == 0:
            # CommandRunner decodes stdout, which is not suitable for binary. Run raw for this one.
            try:
                import subprocess

                completed = subprocess.run(
                    [str(self.adb_path), "-s", self.serial, "exec-out", "screencap", "-p"],
                    capture_output=True,
                    timeout=15,
                )
                if completed.returncode == 0 and completed.stdout:
                    local.write_bytes(completed.stdout)
                    return True
            except Exception:
                pass
        # Fallback path: write to device and pull.
        index = self.index
        if index is None:
            try:
                port = int(self.serial.rsplit(":", 1)[1])
                index = max(0, (port - 5555) // 2)
            except (IndexError, ValueError):
                index = 0
        remote = f"/sdcard/tmp_screen_{index}.png"
        if not self.shell("screencap", "-p", remote, timeout=12).ok:
            return False
        pulled = self.runner.run([self.adb_path, "-s", self.serial, "pull", remote, local], timeout=15)
        return pulled.ok and local.exists() and local.stat().st_size > 0

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
