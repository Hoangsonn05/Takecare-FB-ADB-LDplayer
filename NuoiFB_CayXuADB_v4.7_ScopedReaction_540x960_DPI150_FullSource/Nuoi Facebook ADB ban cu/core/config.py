from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List
import json

from .paths import app_path

DEFAULT_LD_BASE = r"D:\MMO\QuanlyLDPlayer\LD9Mod-32bit\LD9Mod"
FACEBOOK_PACKAGE_NAME = "com.facebook.katana"

VALID_REACTIONS = ["like", "love", "thuongthuong", "haha", "wow", "sad", "angry"]
ALLOWED_REACTIONS = VALID_REACTIONS
REACTION_TEMPLATE_FILES = {
    "like": "like.png",
    "love": "love.jpg",
    "thuongthuong": "thuongthuong.jpg",
    "haha": "haha.jpg",
    "wow": "wow.jpg",
    "sad": "sad.jpg",
    "angry": "angry.jpg",
}


@dataclass
class RuntimeConfig:
    name: str = "runtime"
    run_minutes: int = 30
    max_posts: int = 0
    reaction_enabled: bool = False
    max_reactions: int = 20
    reaction_probability_percent: int = 10
    selected_reactions: List[str] = field(default_factory=list)
    template_dir: str = "resources/templates"
    confidence_like: float = 0.60
    confidence_reaction: float = 0.60
    reel_like_template: str = "nutlikereel.png"
    reel_like_confidence: float = 0.55
    reaction_debug: bool = True
    # Display profile used by CV geometry. Defaults match the user's LDPlayer.
    screen_width_px: int = 540
    screen_height_px: int = 960
    screen_dpi: int = 150
    x_min: int = 200
    x_max: int = 340
    down_y1_min: int = 700
    down_y1_max: int = 800
    down_y2_min: int = 220
    down_y2_max: int = 320
    up_y1_min: int = 220
    up_y1_max: int = 320
    up_y2_min: int = 700
    up_y2_max: int = 800
    duration_min_ms: int = 350
    duration_max_ms: int = 450
    # Delay sau moi lan cuon xuong, dung nhu thoi gian xem bai viet.
    delay_min_sec: float = 2.0
    delay_max_sec: float = 3.0
    reel_delay_min_sec: float = 2.0
    reel_delay_max_sec: float = 4.0
    reverse_scroll_percent: int = 25
    swipes_per_post_min: int = 1
    swipes_per_post_max: int = 2
    occasional_extra_swipe_percent: int = 8
    launch_app: bool = True
    stop_on_adb_offline: bool = True
    max_consecutive_errors: int = 5
    ld_boot_timeout_sec: int = 90
    ld_settle_sec: int = 10
    restart_ld_on_adb_offline: bool = True
    adb_recovery_restarts: int = 1
    facebook_open_timeout_sec: int = 45
    facebook_settle_sec: int = 10
    worker_start_stagger_sec: int = 5
    verbose_action_log: bool = False
    # --- Kich ban chay ---
    run_newfeed: bool = True
    run_reel: bool = False
    run_notifications: bool = False
    script_order: str = "newfeed_then_reel"  # "newfeed_then_reel" | "reel_then_newfeed" | "random"
    reel_run_minutes: int = 0
    notification_tab_template: str = "thongbao.jpg"
    notification_count: int = 0
    notification_read_min_sec: float = 4.0
    notification_read_max_sec: float = 8.0
    notification_max_search_swipes: int = 3
    notification_load_sec: float = 3.0
    # --- Cam xuc Reel (rieng biet voi Newfeed) ---
    reel_reaction_enabled: bool = False
    reel_reaction_probability_percent: int = 10
    # --- Template dieu huong (Reel/Home tab) ---
    reel_tab_template: str = "reel_tab.png"
    home_tab_template: str = "home_tab.png"
    nav_template_threshold: float = 0.70
    auto_quit_ld_after_run: bool = False

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "RuntimeConfig":
        base = RuntimeConfig()
        allowed = set(asdict(base).keys())
        clean = {k: v for k, v in data.items() if k in allowed}
        if "selected_reactions" in clean:
            clean["selected_reactions"] = list(clean["selected_reactions"] or [])
        return RuntimeConfig(**clean)

    def validate(self) -> None:
        if self.run_minutes < 1:
            raise ValueError("run_minutes must be >= 1")
        if self.max_posts < 0:
            raise ValueError("max_posts must be >= 0")
        if self.max_reactions < 0:
            raise ValueError("max_reactions must be >= 0")
        if not 0 <= self.reaction_probability_percent <= 100:
            raise ValueError("reaction_probability_percent must be 0..100")
        invalid = [name for name in self.selected_reactions if name not in VALID_REACTIONS]
        if invalid:
            raise ValueError(f"selected_reactions contains invalid value(s): {', '.join(invalid)}")
        if not 0 <= self.reverse_scroll_percent <= 100:
            raise ValueError("reverse_scroll_percent must be 0..100")
        if self.swipes_per_post_min < 1:
            raise ValueError("swipes_per_post_min must be >= 1")
        if self.swipes_per_post_max < self.swipes_per_post_min:
            raise ValueError("swipes_per_post_max must be >= swipes_per_post_min")
        if not 0 <= self.occasional_extra_swipe_percent <= 100:
            raise ValueError("occasional_extra_swipe_percent must be 0..100")
        if self.delay_min_sec < 0:
            raise ValueError("delay_min_sec must be >= 0")
        if self.delay_max_sec < 0:
            raise ValueError("delay_max_sec must be >= 0")
        if self.reel_delay_min_sec < 0:
            raise ValueError("reel_delay_min_sec must be >= 0")
        if self.reel_delay_max_sec < 0:
            raise ValueError("reel_delay_max_sec must be >= 0")
        if not 0.0 <= self.reel_like_confidence <= 1.0:
            raise ValueError("reel_like_confidence must be 0.0..1.0")
        if self.screen_width_px < 240:
            raise ValueError("screen_width_px must be >= 240")
        if self.screen_height_px < 320:
            raise ValueError("screen_height_px must be >= 320")
        if not 72 <= self.screen_dpi <= 640:
            raise ValueError("screen_dpi must be 72..640")
        for low, high, name in [
            (self.x_min, self.x_max, "x"),
            (self.down_y1_min, self.down_y1_max, "down_y1"),
            (self.down_y2_min, self.down_y2_max, "down_y2"),
            (self.up_y1_min, self.up_y1_max, "up_y1"),
            (self.up_y2_min, self.up_y2_max, "up_y2"),
            (self.duration_min_ms, self.duration_max_ms, "duration"),
            (self.delay_min_sec, self.delay_max_sec, "delay"),
            (self.reel_delay_min_sec, self.reel_delay_max_sec, "reel_delay"),
        ]:
            if low > high:
                raise ValueError(f"{name}_min must be <= {name}_max")
        if self.max_consecutive_errors < 1:
            raise ValueError("max_consecutive_errors must be >= 1")
        for value, name in [
            (self.ld_boot_timeout_sec, "ld_boot_timeout_sec"),
            (self.ld_settle_sec, "ld_settle_sec"),
            (self.adb_recovery_restarts, "adb_recovery_restarts"),
            (self.facebook_open_timeout_sec, "facebook_open_timeout_sec"),
            (self.facebook_settle_sec, "facebook_settle_sec"),
            (self.worker_start_stagger_sec, "worker_start_stagger_sec"),
        ]:
            if value < 0:
                raise ValueError(f"{name} must be >= 0")
        if self.reaction_enabled:
            if not self.selected_reactions:
                raise ValueError("selected_reactions must contain at least one reaction when reaction_enabled=True")
            template_dir = app_path(self.template_dir)
            if not template_dir.exists():
                raise ValueError(f"template_dir does not exist: {self.template_dir}")
            like_path = template_dir / REACTION_TEMPLATE_FILES["like"]
            if not like_path.exists():
                raise ValueError(f"Missing Like template: {like_path}")
            for reaction in self.selected_reactions:
                path = template_dir / REACTION_TEMPLATE_FILES[reaction]
                if not path.exists():
                    raise ValueError(f"Missing reaction template for {reaction}: {path}")
        # --- Validate Reel fields ---
        allowed_script_orders = {
            "newfeed_then_reel",
            "reel_then_newfeed",
            "newfeed_then_notifications",
            "notifications_then_newfeed",
            "reel_then_notifications",
            "notifications_then_reel",
            "newfeed_then_reel_then_notifications",
            "newfeed_then_notifications_then_reel",
            "reel_then_newfeed_then_notifications",
            "reel_then_notifications_then_newfeed",
            "notifications_then_newfeed_then_reel",
            "notifications_then_reel_then_newfeed",
            "random",
        }
        if self.script_order not in allowed_script_orders:
            raise ValueError("script_order contains an unsupported action order")
        if not self.run_newfeed and not self.run_reel and not self.run_notifications:
            raise ValueError("At least one action must be enabled")
        if self.reel_run_minutes < 0:
            raise ValueError("reel_run_minutes must be >= 0")
        if self.notification_count < 0:
            raise ValueError("notification_count must be >= 0")
        if self.notification_read_min_sec < 0 or self.notification_read_max_sec < 0:
            raise ValueError("notification read delay must be >= 0")
        if self.notification_read_min_sec > self.notification_read_max_sec:
            raise ValueError("notification_read_min_sec must be <= notification_read_max_sec")
        if self.notification_max_search_swipes < 0:
            raise ValueError("notification_max_search_swipes must be >= 0")
        if self.notification_load_sec < 0:
            raise ValueError("notification_load_sec must be >= 0")
        if not 0.0 <= self.nav_template_threshold <= 1.0:
            raise ValueError("nav_template_threshold must be 0.0..1.0")
        if not 0 <= self.reel_reaction_probability_percent <= 100:
            raise ValueError("reel_reaction_probability_percent must be 0..100")
        if self.run_reel and self.reel_reaction_enabled:
            if not self.selected_reactions:
                raise ValueError("selected_reactions must contain at least one reaction when reel_reaction_enabled=True")
            template_dir = app_path(self.template_dir)
            if not template_dir.exists():
                raise ValueError(f"template_dir does not exist: {self.template_dir}")
            reel_like_path = self._resolve_template_file(template_dir, self.reel_like_template)
            if reel_like_path is None:
                raise ValueError(f"Missing Reel like template: {template_dir / self.reel_like_template}")
            for reaction in self.selected_reactions:
                if reaction != "like":
                    path = template_dir / REACTION_TEMPLATE_FILES[reaction]
                    if not path.exists():
                        raise ValueError(f"Missing reaction template for {reaction}: {path}")
        if self.run_notifications:
            template_dir = app_path(self.template_dir)
            notification_path = self._resolve_template_file(template_dir, self.notification_tab_template)
            if notification_path is None:
                raise ValueError(f"Missing notification tab template: {self.notification_tab_template}")

    @staticmethod
    def _resolve_template_file(template_dir: Path, filename: str) -> Path | None:
        path = template_dir / filename
        if path.exists():
            return path
        stem = Path(filename).stem
        candidates = []
        for ext in (".png", ".jpg", ".jpeg"):
            candidates.append(template_dir / f"{stem}{ext}")
            candidates.append(template_dir / f"{filename}{ext}")
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return None


# Backward-compatible import name for older modules or CLI calls.
SwipeProfile = RuntimeConfig


@dataclass
class AppSettings:
    ld_base: str = DEFAULT_LD_BASE
    adb_port_base: int = 5555
    package_name: str = FACEBOOK_PACKAGE_NAME
    max_concurrent_devices: int = 4
    screenshots_dir: str = "screenshots"
    logs_dir: str = "logs"
    database_path: str = "data/runs.sqlite3"
    runtime_config: RuntimeConfig = field(default_factory=RuntimeConfig)
    recent_ld_ids: List[int] = field(default_factory=list)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> "AppSettings":
        raw_runtime = data.get("runtime_config")
        if raw_runtime is None:
            old_profiles = data.get("profiles", []) or []
            raw_runtime = old_profiles[0] if old_profiles else {}
        recent_ld_ids: List[int] = []
        for x in data.get("recent_ld_ids", []) or []:
            try:
                recent_ld_ids.append(int(x))
            except Exception:
                pass
        recent_ld_ids = list(dict.fromkeys(recent_ld_ids))
        settings = AppSettings(
            ld_base=data.get("ld_base", DEFAULT_LD_BASE) or DEFAULT_LD_BASE,
            adb_port_base=int(data.get("adb_port_base", 5555)),
            package_name=FACEBOOK_PACKAGE_NAME,
            max_concurrent_devices=int(data.get("max_concurrent_devices", 4)),
            screenshots_dir=data.get("screenshots_dir", "screenshots"),
            logs_dir=data.get("logs_dir", "logs"),
            database_path=data.get("database_path", "data/runs.sqlite3"),
            runtime_config=RuntimeConfig.from_dict(raw_runtime or {}),
            recent_ld_ids=recent_ld_ids,
        )
        settings.validate()
        return settings

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["package_name"] = FACEBOOK_PACKAGE_NAME
        return data

    def validate(self) -> None:
        if self.adb_port_base <= 0:
            raise ValueError("adb_port_base must be positive")
        if self.max_concurrent_devices < 1:
            raise ValueError("max_concurrent_devices must be >= 1")
        self.package_name = FACEBOOK_PACKAGE_NAME
        try:
            self.recent_ld_ids = list(dict.fromkeys(
                int(x) for x in self.recent_ld_ids if int(x) >= 0
            ))
        except Exception:
            self.recent_ld_ids = []
        self.runtime_config.validate()

    def profile_by_name(self, _name: str) -> RuntimeConfig:
        return self.runtime_config


def load_settings(path: str | Path) -> AppSettings:
    p = app_path(path)
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return AppSettings.from_dict(data)


def save_settings(settings: AppSettings, path: str | Path) -> None:
    settings.package_name = FACEBOOK_PACKAGE_NAME
    p = app_path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        json.dump(settings.to_dict(), f, ensure_ascii=False, indent=2)


def ensure_default_config(path: str | Path) -> AppSettings:
    p = app_path(path)
    if p.exists():
        return load_settings(p)
    settings = AppSettings()
    save_settings(settings, p)
    return settings
