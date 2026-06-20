from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from random import choice, randint, uniform
from typing import Callable, Optional
import threading
import time

from core.adb_device import ADBDevice
from core.config import AppSettings, RuntimeConfig
from core.events import AutomationEvent
from core.ldconsole import LDConsoleManager
from core.paths import app_path
from core.template_matcher import TemplateMatcher
from .actions import SwipeCommand, random_down_swipe, random_up_swipe

EventCallback = Callable[[AutomationEvent], None]


@dataclass
class RunnerStats:
    posts_processed: int = 0
    swipe_count: int = 0
    reaction_count: int = 0
    screenshot_count: int = 0
    error_count: int = 0
    started_at: float = 0.0
    status: str = "IDLE"

    @property
    def runtime_sec(self) -> float:
        return 0.0 if not self.started_at else max(0.0, time.time() - self.started_at)


class FeedRunner:
    def __init__(
        self,
        index: int,
        settings: AppSettings,
        runtime_config: RuntimeConfig,
        stop_event: threading.Event,
        pause_event: threading.Event,
        emit: Optional[EventCallback] = None,
    ) -> None:
        self.index = int(index)
        self.settings = settings
        self.config = runtime_config
        self.stop_event = stop_event
        self.pause_event = pause_event
        self.emit_cb = emit or (lambda event: None)
        self.ld = LDConsoleManager(settings.ld_base, settings.adb_port_base)
        self.settings.ld_base = str(self.ld.ld_base)
        self.device = ADBDevice(self.ld.adb_path, self.ld.serial_for_index(self.index), index=self.index)
        self.stats = RunnerStats()
        self.consecutive_errors = 0
        self.screenshot_dir = app_path(settings.screenshots_dir)
        self.matcher: TemplateMatcher | None = None

    def emit(self, type_: str, message: str, level: str = "INFO", status: Optional[str] = None) -> None:
        if status:
            self.stats.status = status
        self.emit_cb(
            AutomationEvent(
                index=self.index,
                type=type_,
                level=level,
                status=self.stats.status,
                message=message,
                runtime_sec=self.stats.runtime_sec,
                posts_processed=self.stats.posts_processed,
                swipe_count=self.stats.swipe_count,
                reaction_count=self.stats.reaction_count,
                screenshot_count=self.stats.screenshot_count,
                error_count=self.stats.error_count,
            )
        )

    def _sleep_interruptible(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end and not self.stop_event.is_set():
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.5)
            time.sleep(min(0.25, max(0.0, end - time.time())))

    def _handle_result(self, ok: bool, message: str) -> bool:
        if ok:
            self.consecutive_errors = 0
            return True
        self.stats.error_count += 1
        self.consecutive_errors += 1
        hard_error = self.consecutive_errors >= self.config.max_consecutive_errors
        self.emit("error", message, level="ERROR", status="ERROR" if hard_error else self.stats.status)
        if hard_error:
            self.stop_event.set()
            return False
        return True

    def log_action_debug(self, message: str) -> None:
        """Chỉ log khi verbose_action_log=True, không spam panel mặc định."""
        if getattr(self.config, "verbose_action_log", False):
            self.emit("action", message, status="RUNNING")

    def _perform_swipe(self, command: SwipeCommand, delay_override: float | None = None) -> bool:
        result = self.device.swipe(command.x1, command.y1, command.x2, command.y2, command.duration_ms)
        msg = f"{command.label}: ({command.x1},{command.y1}) -> ({command.x2},{command.y2}) | {command.duration_ms}ms"
        if not result.ok:
            return self._handle_result(False, f"Swipe failed: {msg}; {result.brief()}")
        self.stats.swipe_count += 1
        # Không log từng swipe vào panel — chỉ cập nhật counter và debug nếu verbose
        self.log_action_debug(msg)
        # Emit event tối giản để cập nhật counter trên bảng mà không spam log
        self.emit_cb(
            AutomationEvent(
                index=self.index,
                type="counter",
                level="DEBUG",
                status=self.stats.status,
                message="",
                runtime_sec=self.stats.runtime_sec,
                posts_processed=self.stats.posts_processed,
                swipe_count=self.stats.swipe_count,
                reaction_count=self.stats.reaction_count,
                screenshot_count=self.stats.screenshot_count,
                error_count=self.stats.error_count,
            )
        )
        self._sleep_interruptible(command.delay_sec if delay_override is None else delay_override)
        return True

    def random_swipe_down(self) -> SwipeCommand:
        return random_down_swipe(self.config)

    def random_swipe_up(self) -> SwipeCommand:
        return random_up_swipe(self.config)

    def choose_swipes_needed(self) -> int:
        low = max(1, int(self.config.swipes_per_post_min))
        high = max(low, int(self.config.swipes_per_post_max))
        n = randint(low, high)
        if self.config.occasional_extra_swipe_percent > 0 and randint(1, 100) <= self.config.occasional_extra_swipe_percent:
            n += 1
        return max(1, min(n, 3))

    def maybe_reverse_scroll(self) -> bool:
        if self.config.reverse_scroll_percent <= 0:
            return True
        if randint(1, 100) > self.config.reverse_scroll_percent:
            return True
        if self.stop_event.is_set():
            return False

        self.emit("action", "Reverse scroll", status="RUNNING")
        if not self._perform_swipe(self.random_swipe_up(), delay_override=uniform(0.6, 1.4)):
            return False
        if self.stop_event.is_set():
            return False
        if not self._perform_swipe(self.random_swipe_down(), delay_override=uniform(0.8, 1.8)):
            return False
        return not self.stop_event.is_set()

    def _screenshot_path(self) -> Path:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        return self.screenshot_dir / f"ld_{self.index}_{int(time.time() * 1000)}.png"

    def _capture_screenshot(self) -> Path | None:
        path = self._screenshot_path()
        if self.device.screenshot(path):
            self.stats.screenshot_count += 1
            self.emit("screenshot", f"Saved screenshot: {path}")
            return path
        self._handle_result(False, "Screenshot failed")
        return None

    def _open_facebook(self) -> bool:
        package = self.settings.package_name
        if not self.device.is_package_installed(package):
            self._handle_result(False, f"Facebook package not installed or cannot be launched: {package}")
            return False

        self.emit("status", f"Opening Facebook: {package}", status="OPENING_APP")
        max_attempts = 3
        for attempt in range(1, max_attempts + 1):
            app = self.ld.run_app(self.index, package)
            if not app.ok:
                self.emit("status", f"ldconsole runapp failed: {app.brief()}", level="WARN", status="OPENING_APP")
            self._sleep_interruptible(2.5)

            self.emit("status", "Checking Facebook foreground", status="WAITING_FACEBOOK")
            if self.device.is_package_foreground(package):
                self.emit("status", "Facebook foreground confirmed", status="WAITING_FACEBOOK")
                self.emit("status", f"Waiting Facebook settle {self.config.facebook_settle_sec}s", status="WAITING_FACEBOOK")
                self._sleep_interruptible(self.config.facebook_settle_sec)
                if self.stop_event.is_set():
                    return False
                self.emit("status", "Facebook ready", status="WAITING_FACEBOOK")
                return True

            self.emit("status", f"Facebook not foreground, retry {attempt}/{max_attempts}", level="WARN", status="WAITING_FACEBOOK")
            self.emit("status", "Opening Facebook with monkey fallback", status="OPENING_APP")
            if not self.device.open_app_with_monkey(package):
                self.emit("status", f"Facebook package not installed or cannot be launched: {package}", level="WARN", status="OPENING_APP")
            self._sleep_interruptible(2.5)

            self.emit("status", "Checking Facebook foreground", status="WAITING_FACEBOOK")
            if self.device.is_package_foreground(package):
                self.emit("status", "Facebook foreground confirmed", status="WAITING_FACEBOOK")
                self.emit("status", f"Waiting Facebook settle {self.config.facebook_settle_sec}s", status="WAITING_FACEBOOK")
                self._sleep_interruptible(self.config.facebook_settle_sec)
                if self.stop_event.is_set():
                    return False
                self.emit("status", "Facebook ready", status="WAITING_FACEBOOK")
                return True

        focused = self.device.foreground_activity_line(package)
        suffix = f"; last focused line: {focused}" if focused else ""
        self._handle_result(False, f"Facebook did not reach foreground{suffix}")
        self.stats.status = "ERROR"
        return False

    def bootstrap(self) -> bool:
        """
        Prepare LDPlayer and Facebook before running automation.
        Return True only when executable paths, LDPlayer, ADB, Android boot,
        and Facebook foreground checks have succeeded.
        """
        self.emit("status", "Checking LDPlayer executable paths", status="CHECKING_PATHS")
        if self.ld.ld_base != self.ld.raw_ld_base:
            self.emit("status", f"Resolved LDPlayer base: {self.ld.ld_base}", status="CHECKING_PATHS")
        paths_ok, missing = self.ld.validate_paths()
        if not paths_ok:
            self._handle_result(False, "Missing executable(s): " + "; ".join(missing))
            self.emit("status", "Set LDPlayer base to the folder containing ldconsole.exe, ld.exe and adb.exe", level="ERROR", status="ERROR")
            return False

        self.emit("status", f"ADB serial: {self.device.serial}", status="CONNECTING_ADB")
        self.device.connect()
        adb_ready = self.device.get_state() == "device"
        running = self.ld.is_instance_running(self.index)

        if running or adb_ready:
            self.emit("status", "LDPlayer already running", status="CONNECTING_ADB")
        else:
            self.emit("status", f"Launching LDPlayer index {self.index}", status="LAUNCHING")
            result = self.ld.launch(self.index)
            if not result.ok:
                self._handle_result(False, f"LDPlayer launch failed: {result.brief()}")
                return False
            self.emit("status", "LDPlayer launch command sent", status="LAUNCHING")

        self.emit("status", "Waiting for ADB device", status="CONNECTING_ADB")
        if not self.device.wait_for_device(timeout_sec=self.config.ld_boot_timeout_sec, poll_sec=2):
            self._handle_result(False, f"ADB device not ready: {self.device.serial}")
            return False
        self.emit("status", "ADB connected", status="CONNECTING_ADB")

        self.emit("status", "Waiting for Android boot completed", status="WAITING_BOOT")
        if not self.device.wait_for_boot_completed(timeout_sec=self.config.ld_boot_timeout_sec, poll_sec=2):
            self._handle_result(False, f"Android boot did not complete within {self.config.ld_boot_timeout_sec}s")
            return False
        self.emit("status", "Android boot completed", status="WAITING_BOOT")
        self._sleep_interruptible(self.config.ld_settle_sec)
        if self.stop_event.is_set():
            return False

        size = self.device.screen_size()
        if size:
            self.emit("status", f"Screen size: {size.width}x{size.height}")

        if self.config.launch_app and self.settings.package_name:
            if not self._open_facebook():
                return False
        else:
            self.emit("status", "App launch skipped")

        if self.config.reaction_enabled:
            self.matcher = TemplateMatcher(
                str(app_path(self.config.template_dir)),
                self.config.confidence_like,
                self.config.confidence_reaction,
            )
        return True

    def maybe_react_to_current_post(self) -> bool:
        if not self.config.reaction_enabled:
            return False
        if self.stats.reaction_count >= self.config.max_reactions:
            return False
        if not self.config.selected_reactions:
            return False
        if randint(1, 100) > self.config.reaction_probability_percent:
            return False
        if self.matcher is None:
            self.matcher = TemplateMatcher(
                str(app_path(self.config.template_dir)),
                self.config.confidence_like,
                self.config.confidence_reaction,
            )

        first = self._capture_screenshot()
        if first is None:
            return False
        try:
            like_pos = self.matcher.find_like_button(str(first))
        except Exception as exc:
            self._handle_result(False, f"Find Like failed: {exc}")
            return False
        if like_pos is None:
            self.emit("reaction", "Like button not found")
            return False

        reaction_name = choice(self.config.selected_reactions)
        if reaction_name == "like":
            tap = self.device.tap(like_pos[0], like_pos[1])
            if not tap.ok:
                self._handle_result(False, f"Tap Like failed: {tap.brief()}")
                return False
            self.stats.reaction_count += 1
            self.emit("reaction", "Reacted: like", status="RUNNING")
            return True

        result = self.device.long_press(like_pos[0], like_pos[1], duration_ms=1000)
        if not result.ok:
            self._handle_result(False, f"Long press Like failed: {result.brief()}")
            return False
        self._sleep_interruptible(randint(2000, 2500) / 1000)

        second = self._capture_screenshot()
        if second is None:
            return False
        try:
            reaction_pos = self.matcher.find_reaction(str(second), reaction_name)
        except Exception as exc:
            self._handle_result(False, f"Find reaction failed: {exc}")
            return False
        if reaction_pos is None:
            self.emit("reaction", f"Reaction not found: {reaction_name}")
            return False

        tap = self.device.tap(reaction_pos[0], reaction_pos[1])
        if not tap.ok:
            self._handle_result(False, f"Tap reaction failed: {tap.brief()}")
            return False
        self.stats.reaction_count += 1
        self.emit("reaction", f"Reacted: {reaction_name}", status="RUNNING")
        return True

    def run(self) -> RunnerStats:
        self.stats = RunnerStats(started_at=time.time(), status="STARTING")
        self.emit("status", "Worker started", status="STARTING")
        if not self.bootstrap():
            self.emit("status", "Bootstrap failed", level="ERROR", status="ERROR")
            return self.stats

        deadline = time.time() + self.config.run_minutes * 60
        self.emit("status", "Runtime config started", status="RUNNING")
        while time.time() < deadline and not self.stop_event.is_set():
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.5)

            if self.config.max_posts > 0 and self.stats.posts_processed >= self.config.max_posts:
                break

            if self.config.stop_on_adb_offline and self.device.get_state() != "device":
                if not self._handle_result(False, "ADB state is not device"):
                    break
                self.device.connect()
                self._sleep_interruptible(2)
                continue

            if not self.maybe_reverse_scroll():
                break

            self.stats.posts_processed += 1
            # Không log từng post vào panel — chỉ debug nếu verbose
            self.log_action_debug(f"Post started: {self.stats.posts_processed}")
            # Emit counter-only event để cập nhật cột Posts
            self.emit_cb(
                AutomationEvent(
                    index=self.index,
                    type="counter",
                    level="DEBUG",
                    status=self.stats.status,
                    message="",
                    runtime_sec=self.stats.runtime_sec,
                    posts_processed=self.stats.posts_processed,
                    swipe_count=self.stats.swipe_count,
                    reaction_count=self.stats.reaction_count,
                    screenshot_count=self.stats.screenshot_count,
                    error_count=self.stats.error_count,
                )
            )
            swipes_needed = self.choose_swipes_needed()
            self.maybe_react_to_current_post()

            for _ in range(swipes_needed):
                if self.stop_event.is_set() or time.time() >= deadline:
                    break
                if not self._perform_swipe(self.random_swipe_down()):
                    break

        if self.stop_event.is_set():
            self.emit("status", "Stopped", status="STOPPED")
        elif self.stats.status == "ERROR":
            self.emit("status", "Stopped after errors", status="ERROR")
        else:
            self.emit("status", "Done", status="DONE")
        return self.stats
