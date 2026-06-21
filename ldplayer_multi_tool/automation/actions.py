from __future__ import annotations

from dataclasses import dataclass
from random import randint, uniform
from typing import Tuple

from core.config import RuntimeConfig


@dataclass
class SwipeCommand:
    x1: int
    y1: int
    x2: int
    y2: int
    duration_ms: int
    delay_sec: float
    label: str = "swipe"


def random_down_swipe(profile: RuntimeConfig) -> SwipeCommand:
    return SwipeCommand(
        x1=randint(profile.x_min, profile.x_max),
        y1=randint(profile.down_y1_min, profile.down_y1_max),
        x2=randint(profile.x_min, profile.x_max),
        y2=randint(profile.down_y2_min, profile.down_y2_max),
        duration_ms=randint(profile.duration_min_ms, profile.duration_max_ms),
        delay_sec=uniform(profile.delay_min_sec, profile.delay_max_sec),
        label="down",
    )


def random_up_swipe(profile: RuntimeConfig) -> SwipeCommand:
    return SwipeCommand(
        x1=randint(profile.x_min, profile.x_max),
        y1=randint(profile.up_y1_min, profile.up_y1_max),
        x2=randint(profile.x_min, profile.x_max),
        y2=randint(profile.up_y2_min, profile.up_y2_max),
        duration_ms=randint(profile.duration_min_ms, profile.duration_max_ms),
        delay_sec=uniform(profile.delay_min_sec, profile.delay_max_sec),
        label="up",
    )


def random_reel_down_swipe(profile: RuntimeConfig, screen_height: int | None = None) -> SwipeCommand:
    """
    Swipe sang Reel ke tiep. Dung bien do dai hon khoang 30% so voi Newfeed.
    Day van la thao tac vuot tu duoi len: y1 lon -> y2 nho.
    Khong sua random_down_swipe() Newfeed hien co.
    """
    h = int(screen_height or 960)
    x1 = randint(profile.x_min, profile.x_max)
    x2 = randint(profile.x_min, profile.x_max)
    # Bien do dai hon ~30%, clamp theo man hinh
    y1 = randint(int(h * 0.78), int(h * 0.88))
    y2 = randint(int(h * 0.10), int(h * 0.18))
    duration = randint(
        int(profile.duration_min_ms * 1.10),
        int(profile.duration_max_ms * 1.25),
    )
    return SwipeCommand(
        x1=x1,
        y1=y1,
        x2=x2,
        y2=y2,
        duration_ms=duration,
        delay_sec=uniform(profile.reel_delay_min_sec, profile.reel_delay_max_sec),
        label="reel_down",
    )
