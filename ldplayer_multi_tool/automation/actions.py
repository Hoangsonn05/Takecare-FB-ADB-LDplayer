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
