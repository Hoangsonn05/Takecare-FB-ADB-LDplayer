from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Any
from urllib.parse import urlsplit

import cv2

from ..models import ContentKind
from .ui_tree import UiTree

_FEED_PATH_MARKERS = (
    "/posts/",
    "/story.php",
    "/permalink.php",
    "/photo",
    "/videos/",
    "/watch/",
)


@dataclass(slots=True)
class ScreenClassification:
    kind: ContentKind
    confidence: float
    evidence: list[str] = field(default_factory=list)
    has_layout_signal: bool = False


class ScreenClassifier:
    @staticmethod
    def classify_url(url: str) -> ContentKind:
        parsed = urlsplit(str(url or ""))
        path = parsed.path.casefold()
        host = (parsed.hostname or "").casefold()
        if "/reel/" in path or "/reels/" in path or "/share/r/" in path:
            return ContentKind.REEL
        if (
            host == "fb.watch"
            or any(marker in path for marker in _FEED_PATH_MARKERS)
            or path.startswith(("/share/p/", "/share/v/"))
        ):
            return ContentKind.FEED
        return ContentKind.UNKNOWN

    @staticmethod
    def _raw_hint(raw_job: Mapping[str, Any] | None) -> ContentKind:
        if not raw_job:
            return ContentKind.UNKNOWN
        values = " ".join(str(raw_job.get(key, "")) for key in ("content_type", "format", "target_type"))
        lowered = values.casefold()
        if "reel" in lowered:
            return ContentKind.REEL
        if any(word in lowered for word in ("post", "feed", "photo", "video")):
            return ContentKind.FEED
        return ContentKind.UNKNOWN

    @staticmethod
    def _ui_hint(ui_xml: str) -> tuple[ContentKind, list[str]]:
        tree = UiTree.parse(ui_xml)
        bounds = [node.bounds for node in tree.nodes]
        if not bounds:
            return ContentKind.UNKNOWN, []
        width = max(bound[2] for bound in bounds)
        height = max(bound[3] for bound in bounds)
        if tree.find_action_layout(
            "vertical",
            screen_width=width,
            screen_height=height,
        ):
            return ContentKind.REEL, ["ui:right_action_rail"]
        if tree.find_action_layout(
            "horizontal",
            screen_width=width,
            screen_height=height,
        ):
            return ContentKind.FEED, ["ui:horizontal_action_row"]
        return ContentKind.UNKNOWN, []

    @staticmethod
    def _cv_hint(image) -> tuple[ContentKind, list[str]]:
        if image is None:
            return ContentKind.UNKNOWN, []
        gray = image if len(image.shape) == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        edges = cv2.Canny(gray, 60, 160)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        right_centers: list[tuple[int, int]] = []
        bottom_centers: list[tuple[int, int]] = []
        for contour in contours:
            x, y, cw, ch = cv2.boundingRect(contour)
            if not (8 <= cw <= w * 0.18 and 8 <= ch <= h * 0.12):
                continue
            center = (x + cw // 2, y + ch // 2)
            if center[0] >= int(w * 0.72):
                right_centers.append(center)
            if int(h * 0.48) <= center[1] <= int(h * 0.93):
                bottom_centers.append(center)
        if len(right_centers) >= 3:
            xs = [point[0] for point in right_centers]
            ys = sorted(point[1] for point in right_centers)
            if max(xs) - min(xs) <= max(45, int(w * 0.10)) and ys[-1] - ys[0] >= int(h * 0.15):
                return ContentKind.REEL, ["cv:vertical_right_rail"]
        if len(bottom_centers) >= 3:
            for y_band in range(int(h * 0.5), int(h * 0.92), max(10, int(h * 0.03))):
                row = [point for point in bottom_centers if abs(point[1] - y_band) <= max(18, int(h * 0.025))]
                if len(row) >= 3 and max(x for x, _ in row) - min(x for x, _ in row) >= int(w * 0.45):
                    return ContentKind.FEED, ["cv:horizontal_action_row"]
        return ContentKind.UNKNOWN, []

    def classify(
        self,
        *,
        url: str = "",
        raw_job: Mapping[str, Any] | None = None,
        ui_xml: str = "",
        image=None,
    ) -> ScreenClassification:
        hints = [("url", self.classify_url(url)), ("raw", self._raw_hint(raw_job))]
        ui_kind, ui_evidence = self._ui_hint(ui_xml)
        cv_kind, cv_evidence = self._cv_hint(image)
        evidence = [f"{source}:{kind.value}" for source, kind in hints if kind is not ContentKind.UNKNOWN]
        intent_kinds = [kind for _source, kind in hints if kind is not ContentKind.UNKNOWN]
        if intent_kinds and any(kind is not intent_kinds[0] for kind in intent_kinds[1:]):
            return ScreenClassification(ContentKind.UNKNOWN, 0.20, evidence + ["intent:conflict"], False)
        intent_kind = intent_kinds[0] if intent_kinds else ContentKind.UNKNOWN

        if ui_kind is not ContentKind.UNKNOWN:
            evidence.extend(ui_evidence)
            if intent_kind is not ContentKind.UNKNOWN and ui_kind is not intent_kind:
                return ScreenClassification(ContentKind.UNKNOWN, 0.25, evidence + ["ui:conflict"], True)
            kind = intent_kind if intent_kind is not ContentKind.UNKNOWN else ui_kind
            confidence = 0.90 if intent_kind is not ContentKind.UNKNOWN else 0.80
            return ScreenClassification(kind, confidence, evidence, True)

        if cv_kind is not ContentKind.UNKNOWN:
            evidence.extend(cv_evidence)
            if intent_kind is not ContentKind.UNKNOWN and cv_kind is not intent_kind:
                return ScreenClassification(
                    intent_kind,
                    0.55,
                    evidence + ["cv:weak_conflict_ignored"],
                    False,
                )
            kind = intent_kind if intent_kind is not ContentKind.UNKNOWN else cv_kind
            return ScreenClassification(kind, 0.75, evidence, True)

        if intent_kind is not ContentKind.UNKNOWN:
            return ScreenClassification(intent_kind, 0.55, evidence, False)
        return ScreenClassification(ContentKind.UNKNOWN, 0.0, evidence, False)
