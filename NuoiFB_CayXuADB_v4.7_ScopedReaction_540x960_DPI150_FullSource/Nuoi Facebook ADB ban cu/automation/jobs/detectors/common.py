from __future__ import annotations

from dataclasses import dataclass, field
import hashlib

import cv2
import numpy as np


def frame_hash(image) -> str:
    if image is None:
        return ""
    gray = image if len(image.shape) == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (16, 16), interpolation=cv2.INTER_AREA)
    return hashlib.sha256(small.tobytes()).hexdigest()[:20]


def perceptual_difference(first, second) -> float:
    if first is None or second is None:
        return 0.0
    first_gray = first if len(first.shape) == 2 else cv2.cvtColor(first, cv2.COLOR_BGR2GRAY)
    second_gray = second if len(second.shape) == 2 else cv2.cvtColor(second, cv2.COLOR_BGR2GRAY)
    a = cv2.resize(first_gray, (64, 64), interpolation=cv2.INTER_AREA)
    b = cv2.resize(second_gray, (64, 64), interpolation=cv2.INTER_AREA)
    return float(np.mean(cv2.absdiff(a, b)) / 255.0)


@dataclass(slots=True)
class CapturedFrame:
    image: object
    ui_xml: str
    frame_id: str


@dataclass(slots=True)
class LocateResult:
    x: int
    y: int
    confidence: float
    detector_source: str
    roi: tuple[int, int, int, int]
    supporting_anchors: list[str] = field(default_factory=list)
    screen_hash: str = ""
    content_kind: str = "unknown"
    reaction_bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    comment_bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    share_bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    action_cluster_bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    scope_bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    scope_signature: str = ""
    candidate_evidence: list[dict] = field(default_factory=list)

    @property
    def frame_id(self) -> str:
        return self.screen_hash

    def same_action_scope(self, other: object, tolerance_px: int) -> bool:
        if not isinstance(other, LocateResult):
            return False
        if self.content_kind != other.content_kind:
            return False
        if not self.scope_signature or self.scope_signature != other.scope_signature:
            return False
        dx = int(self.x) - int(other.x)
        dy = int(self.y) - int(other.y)
        if dx * dx + dy * dy > int(tolerance_px) ** 2:
            return False
        return _bounds_shape_close(
            self.action_cluster_bounds,
            other.action_cluster_bounds,
            tolerance_px,
        )

    def as_evidence(self) -> dict:
        return {
            "content_kind": self.content_kind,
            "reaction_bounds": self.reaction_bounds,
            "comment_bounds": self.comment_bounds,
            "share_bounds": self.share_bounds,
            "action_cluster_bounds": self.action_cluster_bounds,
            "scope_bounds": self.scope_bounds,
            "source": self.detector_source,
            "confidence": self.confidence,
            "frame_id": self.frame_id,
            "scope_signature": self.scope_signature,
            "candidate_evidence": list(self.candidate_evidence),
        }


@dataclass(slots=True)
class SearchResult:
    location: LocateResult | None
    frame: CapturedFrame | None
    swipes: int = 0
    stopped_for_unchanged_frame: bool = False


def union_bounds(*bounds: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    valid = [item for item in bounds if item and item[2] > item[0] and item[3] > item[1]]
    if not valid:
        return (0, 0, 0, 0)
    return (
        min(item[0] for item in valid),
        min(item[1] for item in valid),
        max(item[2] for item in valid),
        max(item[3] for item in valid),
    )


def make_scope_signature(
    content_kind: str,
    reaction_bounds: tuple[int, int, int, int],
    comment_bounds: tuple[int, int, int, int],
    share_bounds: tuple[int, int, int, int],
    scope_bounds: tuple[int, int, int, int],
    labels: list[str] | tuple[str, ...] = (),
) -> str:
    """Hash relative structure, not absolute coordinates, so small UI shifts survive."""

    sx1, sy1, sx2, sy2 = scope_bounds
    sw = max(1, sx2 - sx1)
    sh = max(1, sy2 - sy1)

    def relative(box):
        x1, y1, x2, y2 = box
        return tuple(
            round(value, 2)
            for value in (
                (x1 - sx1) / sw,
                (y1 - sy1) / sh,
                (x2 - x1) / sw,
                (y2 - y1) / sh,
            )
        )

    payload = repr(
        (
            str(content_kind),
            relative(reaction_bounds),
            relative(comment_bounds),
            relative(share_bounds),
            tuple(sorted(str(label).casefold().strip() for label in labels if label)),
        )
    )
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()[:24]


def _bounds_shape_close(first, second, tolerance_px: int) -> bool:
    if not first or not second:
        return False
    fw = int(first[2]) - int(first[0])
    fh = int(first[3]) - int(first[1])
    sw = int(second[2]) - int(second[0])
    sh = int(second[3]) - int(second[1])
    tolerance = max(4, int(tolerance_px))
    return abs(fw - sw) <= tolerance * 2 and abs(fh - sh) <= tolerance * 2
