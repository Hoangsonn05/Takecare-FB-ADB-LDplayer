from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from random import choice, randint, uniform
import random as _random
from typing import Callable, Optional
import threading
import time
import numpy as np

from core.config import AppSettings, RuntimeConfig
from core.adb_device import ADBDevice
from core.events import AutomationEvent
from core.nav_matcher import NavMatcher
from core.paths import app_path
from core.template_matcher import TemplateMatcher
from .actions import SwipeCommand, random_down_swipe, random_reel_down_swipe, random_up_swipe
from .device_session import DeviceBootstrapService
from .safe_swipe import observe_swipe_ui, verified_feed_movement, verified_reel_transition

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
        self.stats = RunnerStats()
        self.bootstrap_service = DeviceBootstrapService(
            self.index,
            settings,
            runtime_config,
            stop_event,
            pause_event,
            emit=lambda type_, message, level, status: self.emit(
                type_, message, level=level, status=status
            ),
            require_existing=True,
        )
        self.ld = self.bootstrap_service.ld
        self.settings.ld_base = str(self.ld.ld_base)
        self.device = self.bootstrap_service.device
        self.consecutive_errors = 0
        self.screenshot_dir = app_path(settings.screenshots_dir)
        self.matcher: TemplateMatcher | None = None
        self.reel_matcher: TemplateMatcher | None = None
        self.screen_width: int | None = None
        self.screen_height: int | None = None
        self.current_tab: str = "unknown"
        self._swipe_lock = threading.Lock()
        self._swipe_blocked = False
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
        if not self._swipe_lock.acquire(blocking=False):
            return self._swipe_failure("CONCURRENT_SWIPE", "ERROR")
        try:
            return self._perform_swipe_locked(command, delay_override)
        finally:
            self._swipe_lock.release()

    def _swipe_failure(self, reason: str, status: str = "WAITING_USER") -> bool:
        self._swipe_blocked = True
        self.stats.error_count += 1
        self.emit("error", f"Swipe stopped: {reason}", level="ERROR", status=status)
        return False

    def _wait_swipe_ready(self) -> bool:
        while self.pause_event.is_set() and not self.stop_event.is_set():
            self.stop_event.wait(0.2)
        return not self.stop_event.is_set() and not self._swipe_blocked

    def _save_swipe_screen(self, observation, tag: str) -> None:
        if observation.image is None:
            return
        import cv2

        path = self.screenshot_dir / f"swipe_ld_{self.index}_{tag}_{time.time_ns()}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded, png = cv2.imencode(".png", observation.image)
        if encoded:
            path.write_bytes(png.tobytes())
            self.stats.screenshot_count += 1
            self.emit("screenshot", f"Swipe {tag}: {path}")

    def _perform_swipe_locked(self, command: SwipeCommand, delay_override: float | None) -> bool:
        expected = "READY_REEL" if command.label == "reel_down" else "READY_FEED"
        if not self._wait_swipe_ready():
            return False
        try:
            bound_serial = self.ld.verified_adb_serial_for_index(self.index)
        except RuntimeError:
            return self._swipe_failure("DEVICE_MAPPING_UNVERIFIED", "ERROR")
        if self.index != self.device.index or self.device.serial != bound_serial:
            return self._swipe_failure("DEVICE_MAPPING_UNVERIFIED", "ERROR")
        before = observe_swipe_ui(self.device, self.settings.package_name)
        if before.state != expected:
            self._save_swipe_screen(before, "before_blocked")
            return self._swipe_failure(before.state)
        if self.stop_event.wait(1.0):
            return False
        if not self._wait_swipe_ready():
            return False
        ready = observe_swipe_ui(self.device, self.settings.package_name)
        shared = [key for key in before.anchors if key in ready.anchors]
        if (ready.state != expected or
                (expected == "READY_FEED" and
                 (len(shared) < 2 or any(abs(ready.anchors[key] - before.anchors[key]) > 12 for key in shared))) or
                (expected == "READY_REEL" and ready.reel_identity != before.reel_identity)):
            self._save_swipe_screen(ready, "before_blocked")
            return self._swipe_failure("UI_NOT_STABLE")
        height, width = ready.image.shape[:2]
        min_end_y = 0.03 * height if expected == "READY_REEL" else 0.15 * height
        if not (0 <= command.x1 < width and 0 <= command.x2 < width and
                0.18 * height <= command.y1 < 0.95 * height and
                min_end_y <= command.y2 < 0.95 * height and
                command.y1 != command.y2 and 100 <= command.duration_ms <= 3000):
            return self._swipe_failure("INVALID_COORDINATES", "ERROR")
        if not self._wait_swipe_ready():
            return False
        if time.monotonic() - ready.observed_at > 3 or self.device.get_state() != "device" or not self.device.is_package_foreground(self.settings.package_name):
            return self._swipe_failure("OBSERVATION_STALE_OR_DEVICE_CHANGED", "ERROR")
        self._save_swipe_screen(ready, "before")
        if not self._wait_swipe_ready():
            return False
        if not self._wait_swipe_ready():
            return False
        if (time.monotonic() - ready.observed_at > 3 or
                self.device.get_state() != "device" or
                not self.device.is_package_foreground(self.settings.package_name)):
            return self._swipe_failure("OBSERVATION_STALE_OR_DEVICE_CHANGED", "ERROR")
        self.emit("swipe", f"COMMAND_SENT {command.label}")
        result = self.device.swipe(command.x1, command.y1, command.x2, command.y2, command.duration_ms)
        if not result.ok:
            return self._swipe_failure(f"ADB_ERROR {result.brief()}", "ERROR")
        self.emit("swipe", f"COMMAND_OK {command.label}")
        deadline = time.monotonic() + 15.0
        last = ready
        verified = False
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if not self._wait_swipe_ready():
                return False
            current = observe_swipe_ui(self.device, self.settings.package_name)
            last = current
            if current.state in {"ADB_ERROR", "SCREEN_UNKNOWN", "VIDEO_VIEWER_OPENED"}:
                break
            if current.state == expected:
                if expected == "READY_FEED":
                    direction = -1 if command.y1 > command.y2 else 1
                    verified = verified_feed_movement(ready, current, direction)
                else:
                    verified = verified_reel_transition(ready, current)
                if verified:
                    if (ready.image.shape == current.image.shape and
                            np.array_equal(ready.image, current.image)):
                        last.state = "SCREEN_UNKNOWN"
                        verified = False
                    break
            if self.stop_event.wait(min(1.5, max(0.0, deadline - time.monotonic()))):
                return False
        self._save_swipe_screen(last, "after")
        if not verified:
            reason = last.state if last.state != expected else "NO_CONFIRMED_SCROLL"
            reason = reason if time.monotonic() < deadline else f"TIMEOUT {reason}"
            return self._swipe_failure(reason, "ERROR" if last.state == "ADB_ERROR" else "WAITING_USER")
        self.stats.swipe_count += 1
        self.consecutive_errors = 0
        self.emit("swipe", "UI_VERIFIED VERIFIED_REEL_SCROLL" if expected == "READY_REEL" else "UI_VERIFIED VERIFIED_FEED_SCROLL")
        self._emit_counters()
        self._sleep_interruptible(command.delay_sec if delay_override is None else delay_override)
        return not self.stop_event.is_set()

    def _emit_counters(self) -> None:
        self.emit_cb(AutomationEvent(
            index=self.index, type="counter", level="DEBUG", status=self.stats.status, message="",
            runtime_sec=self.stats.runtime_sec, posts_processed=self.stats.posts_processed,
            swipe_count=self.stats.swipe_count, reaction_count=self.stats.reaction_count,
            screenshot_count=self.stats.screenshot_count, error_count=self.stats.error_count,
        ))

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

    def _capture_notification_screen(self, tag: str) -> Path | None:
        self.screenshot_dir.mkdir(parents=True, exist_ok=True)
        path = self.screenshot_dir / f"notification_ld_{self.index}_{tag}_{int(time.time() * 1000)}.png"
        ok = self.device.screenshot(path)
        if not ok or not path.exists():
            self.stats.error_count += 1
            self.emit("error", f"Notification screenshot failed: {path}", level="ERROR")
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
        return self.bootstrap_service.open_facebook()

    def _ensure_ldplayer_started(self) -> bool:
        return self.bootstrap_service.ensure_ldplayer_started()

    def _adb_not_ready_details(self) -> str:
        return self.bootstrap_service._adb_not_ready_details()

    def _restart_ld_after_adb_not_ready(self) -> bool | None:
        return self.bootstrap_service.restart_after_adb_not_ready()

    def bootstrap(self) -> bool:
        if self.stop_event.is_set():
            self.stats.status = "STOPPED"
            return False
        try:
            serial = self.ld.verified_adb_serial_for_index(self.index)
        except RuntimeError as exc:
            self.stats.error_count += 1
            self.emit("error", str(exc), level="ERROR", status="ERROR")
            return False
        self.bootstrap_service.device = ADBDevice(self.ld.adb_path, serial, index=self.index)
        self.device = self.bootstrap_service.device
        session = self.bootstrap_service.bootstrap()
        if session is None:
            self.stats.status = "ERROR" if not self.stop_event.is_set() else "STOPPED"
            return False
        self.ld = session.ld
        self.device = session.device
        if session.screen_size:
            self.screen_width = session.screen_size.width
            self.screen_height = session.screen_size.height
        self.current_tab = "unknown"

        if self.config.reaction_enabled:
            self.matcher = TemplateMatcher(
                self._resolve_template_dir(self.config.template_dir),
                self.config.confidence_like,
                self.config.confidence_reaction,
                screen_width_px=self.config.screen_width_px,
                screen_height_px=self.config.screen_height_px,
                screen_dpi=self.config.screen_dpi,
            )
        if self.config.reel_reaction_enabled:
            self.reel_matcher = TemplateMatcher(
                self._resolve_template_dir(self.config.template_dir),
                self.config.reel_like_confidence,
                self.config.confidence_reaction,
                screen_width_px=self.config.screen_width_px,
                screen_height_px=self.config.screen_height_px,
                screen_dpi=self.config.screen_dpi,
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
                screen_width_px=self.config.screen_width_px,
                screen_height_px=self.config.screen_height_px,
                screen_dpi=self.config.screen_dpi,
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
                screen_width_px=self.config.screen_width_px,
                screen_height_px=self.config.screen_height_px,
                screen_dpi=self.config.screen_dpi,
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

    def _tap_nav_icon(self, template_filename: str, tab_label: str, reveal: bool = True, fail_quietly: bool = False) -> bool:
        """reveal -> screenshot -> find template -> tap. Dung chung cho Reel va Home."""
        if reveal and not self.reveal_top_nav():
            return False
        screen = self._capture_nav_screen(tab_label)
        if screen is None:
            return False
        try:
            match, best = self.nav_matcher.find_top_nav_icon(screen, template_filename)
        except Exception as exc:
            if fail_quietly:
                self.emit("status", f"Find {tab_label} tab direct try failed: {exc}", level="WARN", status=self.stats.status)
                return False
            return self._swipe_failure(f"NAV_{tab_label}_MATCH_ERROR {exc}")
        if match is None:
            self.emit("status", f"{tab_label} tab best confidence={best:.3f} threshold={self.config.nav_template_threshold:.3f}", status=self.stats.status)
            if fail_quietly:
                return False
            return self._swipe_failure(f"NAV_{tab_label}_NOT_FOUND best={best:.3f}")
        self.emit(
            "status",
            f"{tab_label} tab best confidence={best:.3f} threshold={self.config.nav_template_threshold:.3f} template={match.template_name}",
            status=self.stats.status,
        )
        if not self._wait_swipe_ready() or self.device.get_state() != "device" or not self.device.is_package_foreground(self.settings.package_name):
            return self._swipe_failure(f"NAV_{tab_label}_DEVICE_NOT_READY")
        expected_tab = {"Home": 0, "Reel": 1, "Notifications": 4}[tab_label]
        before_nav = observe_swipe_ui(self.device, self.settings.package_name)
        if (before_nav.state in {"ADB_ERROR", "VIDEO_VIEWER_OPENED", "UI_STILL_LOADING"} or
                (before_nav.state == "SCREEN_UNKNOWN" and before_nav.selected_tab not in (0, 1, 4))):
            return self._swipe_failure(f"NAV_{tab_label}_{before_nav.state}")
        if before_nav.image is None or not (0 <= match.x < before_nav.image.shape[1] and 0 <= match.y < before_nav.image.shape[0]):
            return self._swipe_failure(f"NAV_{tab_label}_INVALID_COORDINATES")
        if not self._wait_swipe_ready():
            return False
        tap = self.device.tap(match.x, match.y)
        if not tap.ok:
            return self._swipe_failure(f"NAV_{tab_label}_ADB_ERROR {tap.brief()}", "ERROR")
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline and not self.stop_event.is_set():
            if not self._wait_swipe_ready():
                return False
            observed = observe_swipe_ui(self.device, self.settings.package_name)
            if observed.selected_tab == expected_tab:
                return True
            if observed.state in {"ADB_ERROR", "VIDEO_VIEWER_OPENED"}:
                break
            if self.stop_event.wait(1.5):
                return False
        return self._swipe_failure(f"NAV_{tab_label}_NOT_VERIFIED")

    def enter_reel_tab(self) -> bool:
        self.emit("status", "Entering Reel tab", status="OPENING_REEL")
        ok = self._tap_nav_icon(self.config.reel_tab_template, "Reel")
        if ok:
            self.emit("status", "Reel ready", status="RUNNING")
            self.current_tab = "reel"
        return ok

    def enter_home_tab(self) -> bool:
        self.emit("status", "Entering Home tab", status="OPENING_HOME")
        ok = self._tap_nav_icon(self.config.home_tab_template, "Home", reveal=self.current_tab != "reel")
        if ok:
            self.emit("status", "Home ready", status="RUNNING")
            self.current_tab = "newfeed"
        return ok

    def enter_notifications_tab(self, from_reel: bool = False) -> bool:
        self.emit("status", "Entering Notifications tab", status="OPENING_NOTIFICATIONS")
        reveal = not from_reel and self.current_tab != "reel"
        self.emit(
            "status",
            f"Notifications nav reveal={reveal} current_tab={self.current_tab} from_reel={from_reel}",
            status="OPENING_NOTIFICATIONS",
        )
        if reveal:
            ok = self._tap_nav_icon(
                self.config.notification_tab_template,
                "Notifications",
                reveal=False,
                fail_quietly=True,
            )
            if not ok:
                self.emit("status", "Notifications tab direct try failed; revealing top nav", status="OPENING_NOTIFICATIONS")
                ok = self._tap_nav_icon(
                    self.config.notification_tab_template,
                    "Notifications",
                    reveal=True,
                )
        else:
            ok = self._tap_nav_icon(
                self.config.notification_tab_template,
                "Notifications",
                reveal=False,
            )
        if not ok:
            return False
        self._sleep_interruptible(self.config.notification_load_sec)
        self.emit("status", "Notifications ready", status="RUNNING")
        self.current_tab = "notifications"
        return True

    def run_newfeed_loop(self) -> None:
        """Loop Newfeed goc — giu nguyen logic reaction, reverse scroll, view delay."""
        deadline = time.time() + self.config.run_minutes * 60
        while time.time() < deadline and not self.stop_event.is_set():
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.5)
            if self.stop_event.is_set() or self._swipe_blocked:
                break
            if self.config.max_posts > 0 and self.stats.posts_processed >= self.config.max_posts:
                break
            if self.config.stop_on_adb_offline and self.device.get_state() != "device":
                self._swipe_failure("ADB_ERROR offline", "ERROR")
                break
            self.log_action_debug(f"Post started: {self.stats.posts_processed + 1}")
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
            if self.stop_event.is_set() or self._swipe_blocked:
                break
            self.stats.posts_processed += 1
            self._emit_counters()

    def run_reel_loop(self) -> None:
        """Loop Reel: swipe dai hon, co reaction rieng, khong reverse scroll."""
        minutes = self.config.reel_run_minutes or self.config.run_minutes
        deadline = time.time() + minutes * 60
        self.emit("status", f"Reel loop started for {minutes} min", status="RUNNING")
        while time.time() < deadline and not self.stop_event.is_set():
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.5)
            if self.stop_event.is_set() or self._swipe_blocked:
                break
            if self.config.stop_on_adb_offline and self.device.get_state() != "device":
                self._swipe_failure("ADB_ERROR offline", "ERROR")
                break
            # Thu reaction truoc khi swipe (dung rieng find_reel_like_button)
            self.maybe_react_to_current_reel()
            if self.stop_event.is_set():
                break
            cmd = random_reel_down_swipe(self.config, self.screen_height)
            if not self._perform_swipe(cmd):
                break

    def run_notifications_loop(self) -> None:
        from core.notification_detector import NotificationDetector

        detector = NotificationDetector()
        target = self.config.notification_count or 10
        read_count = 0
        self.emit("status", f"Notification loop started, target={target}", status="RUNNING")

        while read_count < target and not self.stop_event.is_set():
            while self.pause_event.is_set() and not self.stop_event.is_set():
                time.sleep(0.5)

            rows = []
            unread_rows = []
            max_search = int(self.config.notification_max_search_swipes)
            for search_i in range(max_search + 1):
                screen = self._capture_notification_screen(f"list_{read_count}_{search_i}")
                if screen is None:
                    return

                rows = detector.detect_rows(screen)
                unread_rows = [row for row in rows if row.unread]
                self.emit(
                    "status",
                    f"Notifications detected: total={len(rows)}, unread={len(unread_rows)}, search={search_i}",
                    status="RUNNING",
                )

                if unread_rows:
                    break

                if search_i < max_search:
                    self.emit(
                        "status",
                        f"Searching more notifications: swipe {search_i + 1}/{max_search}",
                        status="RUNNING",
                    )
                    cmd = random_down_swipe(self.config)
                    if not self._perform_swipe(cmd, delay_override=1.0):
                        return

            candidates = unread_rows if unread_rows else rows
            if not candidates:
                self.emit("status", "No notification rows detected", level="WARN", status="RUNNING")
                break
            if not unread_rows:
                self.emit(
                    "status",
                    f"No unread notifications after {max_search} swipes; selecting random read notification",
                    status="RUNNING",
                )

            row = choice(candidates)
            self.emit(
                "status",
                f"Open notification at ({row.x},{row.y}), unread={row.unread}",
                status="RUNNING",
            )

            tap = self.device.tap(row.x, row.y)
            if not tap.ok:
                self.stats.error_count += 1
                self.emit("error", f"Tap notification failed: {tap.brief()}", level="ERROR")
                continue

            open_delay = uniform(4.0, 6.0)
            self.emit("status", f"Waiting notification content load {open_delay:.1f}s", status="RUNNING")
            self._sleep_interruptible(open_delay)
            read_delay = uniform(self.config.notification_read_min_sec, self.config.notification_read_max_sec)
            cmd = random_down_swipe(self.config)
            if not self._perform_swipe(cmd, delay_override=uniform(0.8, 1.4)):
                return
            self.emit("status", f"Reading notification content {read_delay:.1f}s", status="RUNNING")
            self._sleep_interruptible(read_delay)

            back = self.device.back()
            if not back.ok:
                self.stats.error_count += 1
                self.emit("error", f"Back from notification failed: {back.brief()}", level="ERROR")
                return

            self._sleep_interruptible(uniform(1.0, 1.8))
            read_count += 1
            self.emit("status", f"Notification read done: {read_count}/{target}", status="RUNNING")

    def _build_script_actions(self) -> list[str]:
        enabled: list[str] = []
        if self.config.run_newfeed:
            enabled.append("newfeed")
        if self.config.run_reel:
            enabled.append("reel")
        if self.config.run_notifications:
            enabled.append("notifications")

        if not enabled:
            return []
        if self.config.script_order == "random":
            actions = list(enabled)
            _random.shuffle(actions)
            self.emit("status", f"Random script order selected: {' -> '.join(actions)}", status="RUNNING")
            return actions

        order = self.config.script_order.split("_then_")
        actions = [action for action in order if action in enabled]
        for action in enabled:
            if action not in actions:
                actions.append(action)
        return actions

    def _emit_final_status(self) -> None:
        if self.stop_event.is_set():
            self.emit("status", "Stopped", status="STOPPED")
        elif self.stats.status in {"ERROR", "WAITING_USER"}:
            self.emit("status", "Stopped after unverified UI or errors", status=self.stats.status)
        else:
            self.emit("status", "Done", status="DONE")

    def _quit_ldplayer_after_done(self) -> None:
        if self.bootstrap_service.require_existing:
            return
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

        observed = observe_swipe_ui(self.device, self.settings.package_name)
        if observed.state != "READY_FEED":
            self._save_swipe_screen(observed, "bootstrap_blocked")
            self._swipe_failure(f"BOOTSTRAP_{observed.state}")
            return self.stats
        self.current_tab = "newfeed"

        self.stats.started_at = time.time()
        self.emit("status", "Runtime config started", status="RUNNING")

        previous_action: str | None = None
        for action in self._build_script_actions():
            if self.stop_event.is_set():
                break

            if action == "newfeed":
                if self.current_tab in {"reel", "notifications"}:
                    if not self.enter_home_tab():
                        break
                self.run_newfeed_loop()
                if self._swipe_blocked:
                    break
                previous_action = "newfeed"
            elif action == "reel":
                if self.current_tab != "reel":
                    if not self.enter_reel_tab():
                        break
                self.run_reel_loop()
                if self._swipe_blocked:
                    break
                previous_action = "reel"
            elif action == "notifications":
                from_reel = previous_action == "reel" or self.current_tab == "reel"
                if from_reel:
                    self.current_tab = "reel"
                if not self.enter_notifications_tab(from_reel=from_reel):
                    break
                self.run_notifications_loop()
                if self._swipe_blocked:
                    break
                previous_action = "notifications"

        self._emit_final_status()
        self._quit_ldplayer_after_done()
        return self.stats
