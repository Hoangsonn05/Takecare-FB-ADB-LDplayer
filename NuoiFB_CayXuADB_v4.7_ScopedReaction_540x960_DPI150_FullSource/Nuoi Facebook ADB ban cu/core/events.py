from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional
import time


@dataclass
class AutomationEvent:
    """Event emitted by device workers and consumed by GUI/CLI/storage."""

    index: int
    type: str
    level: str = "INFO"
    status: Optional[str] = None
    message: str = ""
    runtime_sec: float = 0.0
    posts_processed: int = 0
    swipe_count: int = 0
    reaction_count: int = 0
    screenshot_count: int = 0
    error_count: int = 0
    data: Optional[Dict[str, Any]] = None
    ts: float = 0.0

    def __post_init__(self) -> None:
        if not self.ts:
            self.ts = time.time()

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
