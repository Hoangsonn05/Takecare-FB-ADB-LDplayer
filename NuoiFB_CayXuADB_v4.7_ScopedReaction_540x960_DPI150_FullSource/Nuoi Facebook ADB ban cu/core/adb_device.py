from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import re
import threading
import time

import cv2
import numpy as np

from .command_runner import BinaryCommandResult, CommandRunner, CommandResult

# Intent.FLAG_ACTIVITY_NEW_TASK | CLEAR_TOP | SINGLE_TOP.
FACEBOOK_ROUTE_ACTIVITY_FLAGS = "0x34000000"


@dataclass
class ScreenSize:
    width: int
    height: int


@dataclass(slots=True)
class ActivitySnapshot:
    package_name: str = ""
    activity_line: str = ""
    intent_data: str = ""
    task_id: str = ""
    resumed: bool = False


class ADBDevice:
    def __init__(self, adb_path: str | Path, serial: str, runner: Optional[CommandRunner] = None, index: Optional[int] = None):
        self.adb_path = Path(adb_path)
        self.serial = serial
        self.runner = runner or CommandRunner()
        self.index = index
        self._package_help_text: str | None = None
        self._am_help_checked = False
        self.last_view_activity_query_args: list[str] = []

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

    def wait_for_device(
        self,
        timeout_sec: float = 90.0,
        poll_sec: float = 2.0,
        stop_event: threading.Event | None = None,
    ) -> bool:
        end = time.time() + timeout_sec
        offline_hits = 0

        self.connect()
        while time.time() < end and not (stop_event and stop_event.is_set()):
            state = self.get_state()
            if state == "device":
                return True

            if state == "offline":
                offline_hits += 1
                if offline_hits in {2, 4, 6}:
                    self.disconnect()
                    if stop_event and stop_event.wait(1.0):
                        return False
                    self.connect()
                else:
                    self.connect()
            else:
                self.connect()

            if stop_event:
                if stop_event.wait(poll_sec):
                    return False
            else:
                time.sleep(poll_sec)
        return False

    def wait_for_boot_completed(
        self,
        timeout_sec: int = 90,
        poll_sec: int = 2,
        stop_event: threading.Event | None = None,
    ) -> bool:
        end = time.time() + timeout_sec
        while time.time() < end and not (stop_event and stop_event.is_set()):
            if self.get_state() == "device":
                result = self.shell("getprop", "sys.boot_completed", timeout=5)
                if result.ok and result.stdout.strip() == "1":
                    return True
            self.connect()
            if stop_event:
                if stop_event.wait(poll_sec):
                    return False
            else:
                time.sleep(poll_sec)
        return False

    def shell(self, *args: str | int | Path, timeout: float = 10.0) -> CommandResult:
        return self.runner.run(self._adb("shell", *args), timeout=timeout)

    def exec_out(self, *args: str | int | Path, timeout: float = 15.0) -> BinaryCommandResult:
        return self.runner.run_bytes(self._adb("exec-out", *args), timeout=timeout)

    def tap(self, x: int, y: int) -> CommandResult:
        return self.shell("input", "tap", int(x), int(y), timeout=8)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int) -> CommandResult:
        return self.shell("input", "swipe", int(x1), int(y1), int(x2), int(y2), int(duration_ms), timeout=10)

    def long_press(self, x: int, y: int, duration_ms: int = 900) -> CommandResult:
        return self.swipe(x, y, x, y, duration_ms)

    def back(self) -> CommandResult:
        return self.shell("input", "keyevent", "4", timeout=8)

    def force_stop(self, package_name: str) -> CommandResult:
        return self.shell("am", "force-stop", package_name, timeout=10)

    def open_app_with_monkey(self, package_name: str) -> bool:
        result = self.shell("monkey", "-p", package_name, "-c", "android.intent.category.LAUNCHER", "1", timeout=15)
        return result.ok

    def is_package_installed(self, package_name: str) -> bool:
        result = self.shell("pm", "list", "packages", package_name, timeout=10)
        if not result.ok:
            return False
        return f"package:{package_name}" in {line.strip() for line in result.stdout.splitlines()}

    def package_version_code(self, package_name: str) -> str:
        result = self.shell("dumpsys", "package", package_name, timeout=12)
        if not result.ok:
            return ""
        match = re.search(r"(?m)^\s*versionCode=(\d+)", result.stdout)
        return match.group(1) if match else ""

    def android_sdk_version(self) -> str:
        result = self.shell("getprop", "ro.build.version.sdk", timeout=8)
        return result.stdout.strip() if result.ok else ""

    def package_version_name(self, package_name: str) -> str:
        result = self.shell("dumpsys", "package", package_name, timeout=12)
        if not result.ok:
            return ""
        match = re.search(r"(?m)^\s*versionName=([^\s]+)", result.stdout)
        return match.group(1).strip() if match else ""

    @staticmethod
    def _parse_package_components(output: str, package_name: str) -> list[str]:
        pattern = re.compile(
            rf"(?<![A-Za-z0-9_.$])({re.escape(package_name)}/(?:\.[A-Za-z0-9_.$]+|[A-Za-z0-9_.$]+))"
        )
        return list(dict.fromkeys(match.group(1) for match in pattern.finditer(output)))

    def _package_help(self) -> str:
        if self._package_help_text is None:
            result = self.shell("cmd", "package", "help", timeout=10)
            self._package_help_text = f"{result.stdout}\n{result.stderr}"
        return self._package_help_text

    def _query_intent_activities(
        self,
        *,
        action: str,
        category: str,
        package_name: str,
        user_id: int,
        url: str = "",
        remember_view_command: bool = False,
    ) -> list[str]:
        help_text = self._package_help().casefold()
        supports_components = "--components" in help_text
        common = [
            "--user",
            str(user_id),
            "-a",
            action,
            "-c",
            category,
        ]
        if url:
            common.extend(["-d", url])
        common.extend(["-p", package_name])

        query_options = ["--brief"]
        if supports_components:
            query_options.append("--components")
        attempts = [
            ["cmd", "package", "query-activities", *query_options, *common],
        ]
        if supports_components:
            attempts.append(
                ["cmd", "package", "query-activities", "--brief", *common]
            )
        attempts.append(
            ["cmd", "package", "resolve-activity", "--brief", *common]
        )

        for args in attempts:
            if remember_view_command:
                self.last_view_activity_query_args = list(args)
            result = self.shell(*args, timeout=15)
            components = self._parse_package_components(
                f"{result.stdout}\n{result.stderr}",
                package_name,
            )
            if components:
                return components
            output = f"{result.stdout}\n{result.stderr}".casefold()
            if result.ok and not any(
                marker in output
                for marker in ("unknown command", "unknown option", "error:")
            ):
                return []
        return []

    def query_view_activities(
        self,
        url: str,
        package_name: str,
        user_id: int = 0,
    ) -> list[str]:
        return self._query_intent_activities(
            action="android.intent.action.VIEW",
            category="android.intent.category.BROWSABLE",
            url=url,
            package_name=package_name,
            user_id=user_id,
            remember_view_command=True,
        )

    def query_launcher_activities(
        self,
        package_name: str,
        user_id: int = 0,
    ) -> list[str]:
        return self._query_intent_activities(
            action="android.intent.action.MAIN",
            category="android.intent.category.LAUNCHER",
            package_name=package_name,
            user_id=user_id,
        )

    def launch_explicit_view_activity(
        self,
        *,
        component: str,
        url: str,
        user_id: int = 0,
    ) -> CommandResult:
        if not self._am_help_checked:
            self.shell("am", "help", timeout=10)
            self._am_help_checked = True
        return self.shell(
            "am",
            "start",
            "-W",
            "--user",
            str(user_id),
            "-n",
            component,
            "-a",
            "android.intent.action.VIEW",
            "-c",
            "android.intent.category.BROWSABLE",
            "-d",
            url,
            "-f",
            FACEBOOK_ROUTE_ACTIVITY_FLAGS,
            timeout=25,
        )

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

    @staticmethod
    def _component_from_line(line: str) -> tuple[str, str]:
        match = re.search(
            r"\b([A-Za-z0-9_.$]+)/(?:[A-Za-z0-9_.$]+|\.[A-Za-z0-9_.$]+)",
            line,
        )
        if not match:
            return "", ""
        return match.group(1), match.group(0)

    @staticmethod
    def _intent_data_from_lines(lines: list[str]) -> str:
        patterns = (
            re.compile(r"\bdat=([^\s}]+)"),
            re.compile(r"\bdata=([^\s}]+)"),
            re.compile(r"\bmData=([^\s}]+)"),
        )
        for line in lines:
            for pattern in patterns:
                match = pattern.search(line)
                if match:
                    return match.group(1).strip()
        return ""

    @classmethod
    def _parse_top_activity_output(cls, output: str) -> ActivitySnapshot:
        lines = output.splitlines()
        activity_index = -1
        activity_line = ""
        package_name = ""
        for index, line in enumerate(lines):
            stripped = line.strip()
            if not (
                stripped.startswith("ACTIVITY ")
                or "topResumedActivity" in stripped
                or "mResumedActivity" in stripped
                or "ResumedActivity" in stripped
            ):
                continue
            package, _component = cls._component_from_line(stripped)
            if package:
                activity_index = index
                activity_line = stripped
                package_name = package
                break
        if activity_index < 0:
            return ActivitySnapshot()

        task_match = re.search(
            r"(?:\bt|task(?:Id)?[=:#\s]+)(\d+)\b",
            activity_line,
            flags=re.IGNORECASE,
        )
        task_id = task_match.group(1) if task_match else ""
        block = [activity_line]
        base_indent = len(lines[activity_index]) - len(lines[activity_index].lstrip())
        for line in lines[activity_index + 1 :]:
            stripped = line.strip()
            indent = len(line) - len(line.lstrip())
            if not stripped:
                if len(block) > 1:
                    break
                continue
            if (
                indent <= base_indent
                and (
                    stripped.startswith("ACTIVITY ")
                    or stripped.startswith("TASK ")
                    or stripped.startswith("* Task{")
                    or stripped.startswith("Task{")
                )
            ):
                break
            block.append(line)
        return ActivitySnapshot(
            package_name=package_name,
            activity_line=activity_line,
            intent_data=cls._intent_data_from_lines(block),
            task_id=task_id,
            resumed=True,
        )

    @classmethod
    def _parse_resumed_activity_output(cls, output: str) -> ActivitySnapshot:
        lines = output.splitlines()
        resumed_indices = [
            index
            for index, line in enumerate(lines)
            if (
                "topResumedActivity" in line
                or "mResumedActivity" in line
                or "ResumedActivity" in line
            )
        ]
        if not resumed_indices:
            return ActivitySnapshot()
        resumed_index = resumed_indices[-1]
        resumed_line = lines[resumed_index].strip()
        package_name, _component = cls._component_from_line(resumed_line)
        record_match = re.search(r"ActivityRecord\{([0-9A-Fa-f]+)", resumed_line)
        task_match = re.search(
            r"(?:\bt|task(?:Id)?[=:#\s]+)(\d+)\b",
            resumed_line,
            flags=re.IGNORECASE,
        )
        task_id = task_match.group(1) if task_match else ""

        block: list[str] = [resumed_line]
        if record_match:
            record_token = f"ActivityRecord{{{record_match.group(1)}"
            record_indices = [
                index
                for index, line in enumerate(lines)
                if index != resumed_index and record_token in line
            ]
            if record_indices:
                record_index = record_indices[-1]
                block = [lines[record_index]]
                base_indent = len(lines[record_index]) - len(lines[record_index].lstrip())
                for line in lines[record_index + 1 :]:
                    stripped = line.strip()
                    indent = len(line) - len(line.lstrip())
                    if not stripped:
                        continue
                    if (
                        indent <= base_indent
                        and (
                            stripped.startswith("* Hist #")
                            or stripped.startswith("* Task{")
                            or stripped.startswith("Task{")
                        )
                    ):
                        break
                    block.append(line)

        return ActivitySnapshot(
            package_name=package_name,
            activity_line=resumed_line,
            intent_data=cls._intent_data_from_lines(block),
            task_id=task_id,
            resumed=bool(package_name),
        )

    def activity_snapshot(self) -> ActivitySnapshot:
        top = self.shell("dumpsys", "activity", "top", timeout=10)
        if top.ok:
            snapshot = self._parse_top_activity_output(top.stdout)
            if snapshot.resumed:
                return snapshot
        activities = self.shell("dumpsys", "activity", "activities", timeout=10)
        if activities.ok:
            return self._parse_resumed_activity_output(activities.stdout)
        return ActivitySnapshot()

    def current_activity_line(self) -> str:
        return self.activity_snapshot().activity_line

    def current_intent_data(self) -> str:
        return self.activity_snapshot().intent_data

    def wait_for_package_foreground(
        self,
        package_name: str,
        timeout_sec: int = 45,
        poll_sec: int = 2,
        stop_event: threading.Event | None = None,
    ) -> bool:
        deadline = time.time() + timeout_sec
        while time.time() < deadline and not (stop_event and stop_event.is_set()):
            if self.is_package_foreground(package_name):
                return True
            if stop_event:
                if stop_event.wait(poll_sec):
                    return False
            else:
                time.sleep(poll_sec)
        return False

    def dump_ui_xml_text(self) -> str:
        remote = "/sdcard/ld_tool_window_dump.xml"
        dumped = self.shell("uiautomator", "dump", "--compressed", remote, timeout=15)
        if not dumped.ok:
            dumped = self.shell("uiautomator", "dump", remote, timeout=15)
        if not dumped.ok:
            return ""
        result = self.exec_out("cat", remote, timeout=10)
        if not result.ok:
            return ""
        return result.stdout.decode("utf-8", errors="replace")

    def capture_screen_image(self):
        """Capture the current screen directly into an OpenCV BGR image."""
        result = self.exec_out("screencap", "-p", timeout=20)
        if not result.ok or not result.stdout:
            return None
        image = cv2.imdecode(np.frombuffer(result.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
        return image

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
