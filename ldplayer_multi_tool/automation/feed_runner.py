from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from random import choice, randint, uniform
import random as _random
from typing import Callable, Optional
import threading
import time

from core.adb_device import ADBDevice
from core.config import AppSettings, RuntimeConfig
from core.events import AutomationEvent
from core.ldconsole import LDConsoleManager
from core.nav_matcher import NavMatcher
from core.paths import app_path
from core.template_matcher import TemplateMatcher
from .actions import SwipeCommand, random_down_swipe, random_reel_down_swipe, random_up_swipe

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
        self.reel_matcher: TemplateMatcher | None = None
        self.screen_width: int | None = None
        self.screen_height: int | None = None
        self.nav_matcher = NavMatcher(
            self._resolve_template_dir(runtime_config.template_dir),
            threshold=runtime_config.nav_template_threshold,
        )

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
        """Chi log khi verbose_action_log=True, khong spam panel mac dinh."""
        if getattr(self.config, "verbose_action_log", False):
            self.emit("action", message, status="RUNNING")

    def _perform_swipe(self, command: SwipeCommand, delay_override: float | None = None) -> bool:
        result = self.device.swipe(command.x1, command.y1, command.x2, command.y2, command.duration_ms)
        msg = f"{command.label}: ({command.x1},{command.y1}) -> ({command.x2},{command.y2}) | {command.duration_ms}ms"
        if not result.ok:
            return self._handle_result(False, f"Swipe failed: {msg}; {result.brief()}")
        self.stats.swipe_count += 1
        # Khong log tung swipe vao panel — chi cap nhat counter va debug neu verbose
        self.log_action_debug(msg)
        # Emit event toi gian de cap nhat counter tren bang ma khong spam log
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
        # delay_sec la thoi gian xem bai sau khi cuon xuong
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

    def _do_reverse_scroll(self) -> bool:
        """Cuon len nhe roi cuon xuong hoan tra. Goi sau view delay cua down swipe chinh."""
        if self.stop_event.is_set():
            return False
        self.log_action_debug("Reverse scroll triggered")
        # Cuon len nhe — delay override ngan, khong dung thoi gian xem bai
        if not self._perform_swipe(self.random_swipe_up(), delay_override=uniform(0.5, 1.2)):
            return False
        if self.stop_event.is_set():
            return False
        # Cuon xuong hoan tra — delay override ngan
        if not self._perform_swipe(self.random_swipe_down(), delay_override=uniform(0.8, 1.8)):
            return False
        return not self.stop_event.is_set()

    def maybe_reverse_scroll(self) -> bool:
        """Xac suat thuc hien reverse scroll theo config. Tra True neu khong can dung."""
        if self.config.reverse_scroll_percent <= 0:
            return True
        if randint(1, 100) > self.config.reverse_scroll_percent:
            return True
        return self._do_reverse_scroll()

    def _screenshot_path(self) -> Path:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        return self.screenshot_dir / f"ld_{self.index}_{int(time.time() * 1000)}.png"

    def _resolve_template_dir(self, template_dir: str) -> Path:
        p = Path(template_dir)
        if p.is_absolute():
            return p
        return Path(__file__).resolve().parents[1] / p

    def _capture_screenshot(self) -> Path | None:
        path = self._screenshot_path()
        if self.device.screenshot(path):
            self.stats.screenshot_count += 1
            self.emit("screenshot", f"Saved screenshot: {path}")
            return path
        self._handle_result(False, "Screenshot failed")
        return None

    def _capture_nav_screen(self, tag: str) -> Path | None:
        """Chup screenshot cho dieu huong (Reel/Home tab). Khong spam log."""
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.screenshot_dir / f"nav_ld_{self.index}_{tag}_{int(time.time() * 1000)}.png"
        ok = self.device.screenshot(path)
        if not ok or not path.exists():
            self.stats.error_count += 1
            self.emit("error", f"Nav screenshot failed: {path}", level="ERROR")
            return None
        self.stats.screenshot_count += 1
        return path

    def _capture_reaction_screen(self, tag: str) -> Path | None:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.screenshot_dir / f"reaction_ld_{self.index}_{tag}_{int(time.time() * 1000)}.png"
        ok = self.device.screenshot(path)
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        label = "Reaction panel screenshot" if tag == "panel" else "Reaction screenshot"

        if ok and exists and size > 0:
            self.stats.screenshot_count += 1
            self.emit("screenshot", f"{label} saved: {path} ({size} bytes)", status=self.stats.status)
            return path

        self.stats.error_count += 1
        if exists:
            self.emit(
                "error",
                f"Reaction screenshot unreadable after capture: {path} ({size} bytes). "
                "Check screenshot bytes, adb pull result, and Unicode-safe image loader.",
                level="ERROR",
            )
        else:
            self.emit("error", f"Reaction screenshot failed: {path}", level="ERROR")
        return None

    def _reaction_debug(self, message: str) -> None:
        if getattr(self.config, "reaction_debug", True):
            self.emit("reaction", f"Reaction debug: {message}", status=self.stats.status)

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

    def _ensure_ldplayer_started(self) -> bool:
        """
        Kiem tra va khoi dong LDPlayer instance theo index bang ldconsole.
        Khong yeu cau ADB truoc khi quyet dinh launch.
        Return True neu da chay hoac da gui lenh launch thanh cong.
        """
        self.emit("status", f"Checking LDPlayer instance {self.index}", status="LAUNCHING")

        running = self.ld.is_instance_running_by_ldconsole(self.index)

        if running is True:
            self.emit("status", "LDPlayer already running by ldconsole", status="LAUNCHING")
            return True

        if running is False:
            self.emit("status", f"LDPlayer index {self.index} is not running; launching", status="LAUNCHING")
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
            self._sleep_interruptible(4.0)
            return True

        if self.ld.looks_like_usage_output(result):
            self._handle_result(
                False,
                (
                    "LDPlayer launch command returned Usage/help output. "
                    "This usually means ldconsole arguments are wrong. "
                    f"Args={result.args}; output={result.brief()}"
                ),
            )
            return False

        running_after_fail = self.ld.is_instance_running_by_ldconsole(self.index)
        if running_after_fail is True:
            self.emit(
                "status",
                f"LDPlayer launch returned non-zero but instance is running by ldconsole: {result.brief()}",
                level="WARN",
                status="LAUNCHING",
            )
            self._sleep_interruptible(3.0)
            return True

        self._handle_result(False, f"LDPlayer launch failed: args={result.args}; output={result.brief()}")
        return False

    def _adb_not_ready_details(self) -> str:
        adb_list = self.device.devices_list().strip()[:500]
        running_state = self.ld.is_instance_running_by_ldconsole(self.index)
        return (
            f"ADB device not ready after LD launch: {self.device.serial}; "
            f"ldconsole_running={running_state}; "
            f"adb_devices={adb_list}"
        )

    def _restart_ld_after_adb_not_ready(self) -> bool | None:
        restarts = max(0, int(getattr(self.config, "adb_recovery_restarts", 0)))
        if not getattr(self.config, "restart_ld_on_adb_offline", True) or restarts <= 0:
            return None

        self.emit(
            "status",
            f"ADB not ready for {self.device.serial}; restarting LDPlayer index {self.index} once",
            level="WARN",
            status="CONNECTING_ADB",
        )
        self.ld.quit(self.index)
        self._sleep_interruptible(5.0)
        if self.stop_event.is_set():
            return False

        launch = self.ld.launch(self.index)
        if not launch.ok:
            self._handle_result(False, f"LDPlayer relaunch failed after ADB offline: args={launch.args}; output={launch.brief()}")
            return False

        self._sleep_interruptible(8.0)
        if self.stop_event.is_set():
            return False

        if self.device.wait_for_device(timeout_sec=self.config.ld_boot_timeout_sec, poll_sec=2):
            self.emit("status", "ADB connected after LDPlayer restart", status="CONNECTING_ADB")
            return True

        adb_list = self.device.devices_list().strip()[:500]
        running_state = self.ld.is_instance_running_by_ldconsole(self.index)
        self._handle_result(
            False,
            (
                f"ADB still offline after LDPlayer restart: {self.device.serial}; "
                f"ldconsole_running={running_state}; "
                f"adb_devices={adb_list}"
            ),
        )
        return False

    def bootstrap(self) -> bool:
        """
        Prepare LDPlayer and Facebook before running automation.
        Return True only when executable paths, LDPlayer, ADB, Android boot,
        and Facebook foreground checks have succeeded.
        Flow: check paths -> check/launch LD by ldconsole -> wait ADB -> boot -> app
        """
        self.emit("status", "Checking LDPlayer executable paths", status="CHECKING_PATHS")
        if self.ld.ld_base != self.ld.raw_ld_base:
            self.emit("status", f"Resolved LDPlayer base: {self.ld.ld_base}", status="CHECKING_PATHS")
        paths_ok, missing = self.ld.validate_paths()
        if not paths_ok:
            self._handle_result(False, "Missing executable(s): " + "; ".join(missing))
            self.emit("status", "Set LDPlayer base to the folder containing ldconsole.exe, ld.exe and adb.exe", level="ERROR", status="ERROR")
            return False

        # 1. Launch/check LD bang ldconsole truoc, khong yeu cau ADB
        if not self._ensure_ldplayer_started():
            return False

        # 2. Sau khi da quyet dinh launch/running, moi connect ADB
        self.emit("status", f"ADB serial: {self.device.serial}", status="CONNECTING_ADB")
        self.emit("status", "Waiting for ADB device", status="CONNECTING_ADB")
        if not self.device.wait_for_device(timeout_sec=self.config.ld_boot_timeout_sec, poll_sec=2):
            recovered = self._restart_ld_after_adb_not_ready()
            if recovered is None:
                self._handle_result(False, self._adb_not_ready_details())
                return False
            if recovered is False:
                return False
        else:
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
            self.screen_width = size.width
            self.screen_height = size.height
            self.emit("status", f"Screen size: {size.width}x{size.height}")

        if self.config.launch_app and self.settings.package_name:
            if not self._open_facebook():
                return False
        else:
            self.emit("status", "App launch skipped")

        if self.config.reaction_enabled:
            self.matcher = TemplateMatcher(
                self._resolve_template_dir(self.config.template_dir),
                self.config.confidence_like,
                self.config.confidence_reaction,
            )
        if self.config.reel_reaction_enabled:
            self.reel_matcher = TemplateMatcher(
                self._resolve_template_dir(self.config.template_dir),
                self.config.reel_like_confidence,
                self.config.confidence_reaction,
            )
        return True

    def maybe_react_to_current_post(self) -> bool:
        if not self.config.reaction_enabled:
            return False
        if self.stats.reaction_count >= self.config.max_reactions:
            self._reaction_debug("skipped: max reactions reached")
            return False
        if not self.config.selected_reactions:
            self.emit("reaction", "Reaction skipped: no selected reactions", level="WARN")
            return False
        roll = randint(1, 100)
        if roll > self.config.reaction_probability_percent:
            self._reaction_debug(f"skipped: probability roll {roll}")
            return False
        self.emit("reaction", "Reaction attempt started", status="RUNNING")
        if self.matcher is None:
            self.matcher = TemplateMatcher(
                self._resolve_template_dir(self.config.template_dir),
                self.config.confidence_like,
                self.config.confidence_reaction,
            )

        first = self._capture_reaction_screen("before")
        if first is None:
            return False
        try:
            like_match, like_best = self.matcher.find_like_button(first)
        except Exception as exc:
            self.stats.error_count += 1
            self.emit("reaction", f"Like template error: {exc}", level="ERROR")
            return False
        self.emit("reaction", f"Like best confidence={like_best:.3f} threshold={self.config.confidence_like:.3f}")
        if like_match is None:
            self.emit(
                "reaction",
                f"Like button not found. best={like_best:.3f}, threshold={self.config.confidence_like:.3f}",
                level="WARN",
            )
            return False

        reaction_name = choice(self.config.selected_reactions)
        self.emit("reaction", f"Reaction selected: {reaction_name}")
        if reaction_name == "like":
            tap = self.device.tap(like_match.x, like_match.y)
            if not tap.ok:
                self.stats.error_count += 1
                self.emit("reaction", f"Reaction tap failed: {tap.brief()}", level="ERROR")
                return False
            self.stats.reaction_count += 1
            self.emit("reaction", f"Reaction sent: like at ({like_match.x},{like_match.y})", status="RUNNING")
            return True

        duration_ms = randint(1100, 1500)
        panel_threshold = max(float(self.config.confidence_reaction), 0.75)
        self.emit("reaction", f"Opening reaction panel by long press Like for {duration_ms}ms", status="RUNNING")
        press = self.device.long_press(like_match.x, like_match.y, duration_ms=duration_ms)
        if not press.ok:
            self.stats.error_count += 1
            self.emit("reaction", f"Open reaction panel failed: {press.brief()}", level="ERROR")
            return False
        self._sleep_interruptible(uniform(1.0, 1.5))

        second = self._capture_reaction_screen("panel")
        if second is None:
            return False
        try:
            reaction_match, reaction_best, roi = self.matcher.find_reaction_in_panel(
                screenshot_path=second,
                reaction_name=reaction_name,
                like_x=like_match.x,
                like_y=like_match.y,
                screen_width=self.screen_width,
                screen_height=self.screen_height,
                threshold=panel_threshold,
            )
        except Exception as exc:
            self.stats.error_count += 1
            self.emit("reaction", f"Reaction template error: {reaction_name}: {exc}", level="ERROR")
            return False
        self.emit(
            "reaction",
            f"{reaction_name} panel best confidence={reaction_best:.3f}, threshold={panel_threshold:.3f}, roi={roi}",
        )
        if reaction_match is None:
            self.emit(
                "reaction",
                f"Reaction icon not found in panel: {reaction_name}. best={reaction_best:.3f}, "
                f"threshold={panel_threshold:.3f}, roi={roi}",
                level="WARN",
            )
            return False

        tap = self.device.tap(reaction_match.x, reaction_match.y)
        if not tap.ok:
            self.stats.error_count += 1
            self.emit("reaction", f"Reaction tap failed: {tap.brief()}", level="ERROR")
            return False
        self.stats.reaction_count += 1
        self.emit("reaction", f"Reaction sent: {reaction_name} at ({reaction_match.x},{reaction_match.y})", status="RUNNING")
        return True

    def maybe_react_to_current_reel(self) -> bool:
        """
        Tha cam xuc cho Reel hien tai.
        Tim Like o COT PHAI man hinh (khac Newfeed).
        Giu nguyen co che long press va tim reaction panel.
        """
        if not self.config.reel_reaction_enabled:
            return False
        if self.stats.reaction_count >= self.config.max_reactions:
            self._reaction_debug("reel: skipped: max reactions reached")
            return False
        if not self.config.selected_reactions:
            self.emit("reaction", "Reel reaction skipped: no selected reactions", level="WARN")
            return False
        roll = randint(1, 100)
        if roll > self.config.reel_reaction_probability_percent:
            self._reaction_debug(f"reel: skipped: probability roll {roll}")
            return False

        self.emit("reaction", "Reel reaction attempt started", status="RUNNING")
        if self.reel_matcher is None:
            self.reel_matcher = TemplateMatcher(
                self._resolve_template_dir(self.config.template_dir),
                self.config.reel_like_confidence,
                self.config.confidence_reaction,
            )

        first = self._capture_reaction_screen("reel_before")
        if first is None:
            return False
        try:
            # Dung find_reel_like_button — tim icon o cot phai (khac find_like_button Newfeed)
            like_match, like_best, like_roi = self.reel_matcher.find_reel_like_button(
                screenshot_path=first,
                template_filename=self.config.reel_like_template,
                threshold=self.config.reel_like_confidence,
            )
        except Exception as exc:
            self.stats.error_count += 1
            self.emit("reaction", f"Reel like template error: {exc}", level="ERROR")
            return False
        self.emit(
            "reaction",
            f"Reel like best confidence={like_best:.3f} "
            f"threshold={self.config.reel_like_confidence:.3f} "
            f"roi={like_roi} template={self.config.reel_like_template}",
        )
        if like_match is None:
            self.emit(
                "reaction",
                f"Reel like button not found. best={like_best:.3f}, "
                f"threshold={self.config.reel_like_confidence:.3f}, "
                f"roi={like_roi}, template={self.config.reel_like_template}",
                level="WARN",
            )
            return False

        reaction_name = choice(self.config.selected_reactions)
        self.emit("reaction", f"Reel reaction selected: {reaction_name}")
        if reaction_name == "like":
            tap = self.device.tap(like_match.x, like_match.y)
            if not tap.ok:
                self.stats.error_count += 1
                self.emit("reaction", f"Reel reaction tap failed: {tap.brief()}", level="ERROR")
                return False
            self.stats.reaction_count += 1
            self.emit("reaction", f"Reel reaction sent: like at ({like_match.x},{like_match.y})", status="RUNNING")
            return True

        # Long press de mo panel cam xuc — giu nguyen co che nhu Newfeed
        duration_ms = randint(1100, 1500)
        panel_threshold = max(float(self.config.confidence_reaction), 0.75)
        self.emit("reaction", f"Reel: opening reaction panel by long press Like for {duration_ms}ms", status="RUNNING")
        press = self.device.long_press(like_match.x, like_match.y, duration_ms=duration_ms)
        if not press.ok:
            self.stats.error_count += 1
            self.emit("reaction", f"Reel: open reaction panel failed: {press.brief()}", level="ERROR")
            return False
        self._sleep_interruptible(uniform(1.0, 1.5))

        second = self._capture_reaction_screen("reel_panel")
        if second is None:
            return False
        try:
            # Tim reaction trong panel — dung chung find_reaction_in_panel (panel giong nhau)
            reaction_match, reaction_best, roi = self.reel_matcher.find_reaction_in_panel(
                screenshot_path=second,
                reaction_name=reaction_name,
                like_x=like_match.x,
                like_y=like_match.y,
                screen_width=self.screen_width,
                screen_height=self.screen_height,
                threshold=panel_threshold,
            )
        except Exception as exc:
            self.stats.error_count += 1
            self.emit("reaction", f"Reel reaction template error: {reaction_name}: {exc}", level="ERROR")
            return False
        self.emit(
            "reaction",
            f"Reel {reaction_name} panel best confidence={reaction_best:.3f}, threshold={panel_threshold:.3f}, roi={roi}",
        )
        if reaction_match is None:
            self.emit(
                "reaction",
                f"Reel reaction icon not found in panel: {reaction_name}. best={reaction_best:.3f}, "
                f"threshold={panel_threshold:.3f}, roi={roi}",
                level="WARN",
            )
            return False

        tap = self.device.tap(reaction_match.x, reaction_match.y)
        if not tap.ok:
            self.stats.error_count += 1
            self.emit("reaction", f"Reel reaction tap failed: {tap.brief()}", level="ERROR")
            return False
        self.stats.reaction_count += 1
        self.emit("reaction", f"Reel reaction sent: {reaction_name} at ({reaction_match.x},{reaction_match.y})", status="RUNNING")
        return True

    def reveal_top_nav(self) -> bool:
        """Cuon len 1 lan de thanh dieu huong Facebook hien ra."""
        self.emit("status", "Reveal top navigation", status=self.stats.status)
        cmd = random_up_swipe(self.config)
        if not self._perform_swipe(cmd, delay_override=uniform(1.0, 1.5)):
            return False
        self._sleep_interruptible(uniform(0.8, 1.2))
        return not self.stop_event.is_set()

    def _tap_nav_icon(self, template_filename: str, tab_label: str) -> bool:
        """reveal -> screenshot -> find template -> tap. Dung chung cho Reel va Home."""
        if not self.reveal_top_nav():
            return False
        screen = self._capture_nav_screen(tab_label)
        if screen is None:
            return False
        try:
            match, best = self.nav_matcher.find_top_nav_icon(screen, template_filename)
        except Exception as exc:
            self._handle_result(False, f"Find {tab_label} tab failed: {exc}")
            return False
        self.emit("status", f"{tab_label} tab best confidence={best:.3f} threshold={self.config.nav_template_threshold:.3f}", status=self.stats.status)
        if match is None:
            self._handle_result(False, f"{tab_label} tab not found. best={best:.3f}, threshold={self.config.nav_template_threshold:.3f}")
            return False
        tap = self.device.tap(match.x, match.y)
        if not tap.ok:
            self._handle_result(False, f"Tap {tab_label} tab failed: {tap.brief()}")
            return False
        self._sleep_interruptible(uniform(2.0, 3.0))
        return not self.stop_event.is_set()

    def enter_reel_tab(self) -> bool:
        self.emit("status", "Entering Reel tab", status="OPENING_REEL")
        ok = self._tap_nav_icon(self.config.reel_tab_template, "Reel")
        if ok:
            self.emit("status", "Reel ready", status="RUNNING")
        return ok

    def enter_home_tab(self) -> bool:
        self.emit("status", "Entering Home tab", status="OPENING_HOME")
        ok = self._tap_nav_icon(self.config.home_tab_template, "Home")
        if ok:
            self.emit("status", "Home ready", status="RUNNING")
        return ok

    def run_newfeed_loop(self) -> None:
        """Loop Newfeed goc — giu nguyen logic reaction, reverse scroll, view delay."""
        deadline = time.time() + self.config.run_minutes * 60
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
            self.stats.posts_processed += 1
            self.log_action_debug(f"Post started: {self.stats.posts_processed}")
            self.emit_cb(
                AutomationEvent(
                    index=self.index, type="counter", level="DEBUG",
                    status=self.stats.status, message="",
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
            swipe_ok = True
            for _ in range(swipes_needed):
                if self.stop_event.is_set() or time.time() >= deadline:
                    swipe_ok = False
                    break
                if not self._perform_swipe(self.random_swipe_down()):
                    swipe_ok = False
                    break
            if not swipe_ok:
                break
            if not self.stop_event.is_set() and time.time() < deadline:
                if not self.maybe_reverse_scroll():
                    break

    def run_reel_loop(self) -> None:
        """Loop Reel: swipe dai hon, co reaction rieng, khong reverse scroll."""
        minutes = self.config.reel_run_minutes or self.config.run_minutes
        deadline = time.time() + minutes * 60
        self.emit("status", f"Reel loop started for {minutes} min", status="RUNNING")
        while time.time() < deadline and not self.stop_event.is_set():
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.5)
            if self.config.stop_on_adb_offline and self.device.get_state() != "device":
                if not self._handle_result(False, "ADB state is not device"):
                    break
                self.device.connect()
                self._sleep_interruptible(2)
                continue
            # Thu reaction truoc khi swipe (dung rieng find_reel_like_button)
            self.maybe_react_to_current_reel()
            if self.stop_event.is_set():
                break
            cmd = random_reel_down_swipe(self.config, self.screen_height)
            if not self._perform_swipe(cmd):
                break

    def _emit_final_status(self) -> None:
        if self.stop_event.is_set():
            self.emit("status", "Stopped", status="STOPPED")
        elif self.stats.status == "ERROR":
            self.emit("status", "Stopped after errors", status="ERROR")
        else:
            self.emit("status", "Done", status="DONE")

    def _quit_ldplayer_after_done(self) -> None:
        if not getattr(self.config, "auto_quit_ld_after_run", False):
            return
        if self.stats.status != "DONE" or self.stop_event.is_set():
            return
        self.emit("status", "Completed config; closing LDPlayer", status="DONE")
        result = self.ld.quit(self.index)
        if result.ok:
            self.emit("status", "LDPlayer quit command sent after completed config", status="DONE")
            return
        self.emit(
            "status",
            f"LDPlayer auto quit failed after completed config: args={result.args}; output={result.brief()}",
            level="WARN",
            status="DONE",
        )

    def run(self) -> RunnerStats:
        self.stats = RunnerStats(status="STARTING")
        self.emit("status", "Worker started", status="STARTING")
        if not self.bootstrap():
            self.emit("status", "Bootstrap failed", level="ERROR", status="ERROR")
            return self.stats

        self.stats.started_at = time.time()
        self.emit("status", "Runtime config started", status="RUNNING")

        run_nf = self.config.run_newfeed
        run_rl = self.config.run_reel

        # Giai quyet thu tu kich ban, ke ca random
        order = self.config.script_order
        if order == "random" and run_nf and run_rl:
            order = _random.choice(["newfeed_then_reel", "reel_then_newfeed"])
            self.emit("status", f"Random script order selected: {order}", status="RUNNING")

        if run_nf and run_rl:
            if order == "newfeed_then_reel":
                self.run_newfeed_loop()
                if not self.stop_event.is_set():
                    if self.enter_reel_tab() and not self.stop_event.is_set():
                        self.run_reel_loop()
            else:  # reel_then_newfeed
                if self.enter_reel_tab() and not self.stop_event.is_set():
                    self.run_reel_loop()
                if not self.stop_event.is_set():
                    if self.enter_home_tab() and not self.stop_event.is_set():
                        self.run_newfeed_loop()
        elif run_rl:
            if self.enter_reel_tab() and not self.stop_event.is_set():
                self.run_reel_loop()
        else:  # chi Newfeed
            self.run_newfeed_loop()

        self._emit_final_status()
        self._quit_ldplayer_after_done()
        return self.stats
