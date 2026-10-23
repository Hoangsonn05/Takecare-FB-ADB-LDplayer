from __future__ import annotations

from dataclasses import dataclass, field

import cv2
import numpy as np

from core.template_matcher import TemplateMatcher

from ..models import ContentKind
from .common import LocateResult, frame_hash, union_bounds
from .reaction_resolver import REACTION_LABELS, canonical_reaction
from .ui_tree import UiTree


@dataclass(slots=True)
class ReactionPanelResolution:
    panel_bounds: tuple[int, int, int, int]
    anchor_bounds: tuple[int, int, int, int]
    content_kind: ContentKind
    options: dict[str, LocateResult] = field(default_factory=dict)
    source: str = ""
    confidence: float = 0.0
    frame_id: str = ""
    visual_centers: list[tuple[int, int]] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)


class ReactionPanelLocator:
    """Bind a newly opened reaction picker to one resolved action anchor."""

    def __init__(self, matcher: TemplateMatcher) -> None:
        self.matcher = matcher

    @staticmethod
    def _panel_roi(
        image,
        content_kind: ContentKind,
        like_location: LocateResult,
    ) -> tuple[int, int, int, int]:
        height, width = image.shape[:2]
        anchor = getattr(like_location, "reaction_bounds", ()) or (
            max(0, like_location.x - 30),
            max(0, like_location.y - 30),
            min(width, like_location.x + 30),
            min(height, like_location.y + 30),
        )
        ax1, ay1, ax2, _ay2 = anchor
        if content_kind is ContentKind.REEL:
            return (
                0,
                max(0, ay1 - int(height * 0.36)),
                width,
                max(1, min(height, ay1 + int(height * 0.08))),
            )
        return (
            max(0, ax1 - int(width * 0.25)),
            max(0, ay1 - int(height * 0.28)),
            min(width, ax2 + int(width * 0.75)),
            max(1, ay1 - max(4, int(height * 0.008))),
        )

    @staticmethod
    def _circle_centers(
        image,
        roi_box: tuple[int, int, int, int],
    ) -> list[tuple[int, int]]:
        x1, y1, x2, y2 = roi_box
        roi = image[y1:y2, x1:x2]
        if roi.size == 0:
            return []
        hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
        mask = cv2.threshold(hsv[:, :, 1], 55, 255, cv2.THRESH_BINARY)[1]
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[tuple[int, int, int]] = []
        rh, rw = roi.shape[:2]
        for contour in contours:
            area = cv2.contourArea(contour)
            bx, by, bw, bh = cv2.boundingRect(contour)
            if area < max(40, rw * rh * 0.0005):
                continue
            if not 0.55 <= bw / max(1, bh) <= 1.55:
                continue
            if not (8 <= bw <= max(90, int(rw * 0.20))):
                continue
            candidates.append((x1 + bx + bw // 2, y1 + by + bh // 2, max(bw, bh)))
        if len(candidates) < 5:
            return []
        median_y = int(np.median([item[1] for item in candidates]))
        median_size = int(np.median([item[2] for item in candidates]))
        row = [item for item in candidates if abs(item[1] - median_y) <= max(20, int(median_size * 0.65))]
        row.sort(key=lambda item: item[0])
        deduped: list[tuple[int, int, int]] = []
        for item in row:
            if not deduped or item[0] - deduped[-1][0] >= max(10, item[2] // 3):
                deduped.append(item)
        if not 5 <= len(deduped) <= 8:
            return []
        gaps = [deduped[index + 1][0] - deduped[index][0] for index in range(len(deduped) - 1)]
        mean_gap = float(np.mean(gaps)) if gaps else 0.0
        if mean_gap <= 0 or max(abs(gap - mean_gap) for gap in gaps) > mean_gap * 0.58:
            return []
        return [(x, y) for x, y, _size in deduped]

    @staticmethod
    def _point_in(box: tuple[int, int, int, int], point: tuple[int, int]) -> bool:
        return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]

    def _xml_options(
        self,
        ui_xml: str,
        roi: tuple[int, int, int, int],
        image,
    ) -> dict[str, LocateResult]:
        tree = UiTree.parse(ui_xml)
        options: dict[str, LocateResult] = {}
        seen_bounds: set[tuple[int, int, int, int]] = set()
        roi_width = max(1, roi[2] - roi[0])
        roi_height = max(1, roi[3] - roi[1])
        for node in tree.nodes_in_bounds(roi, require_clickable=True):
            reaction = ""
            for value in (node.text, node.content_desc, node.resource_id, node.label):
                reaction = canonical_reaction(value, allow_state_phrase=False)
                if reaction:
                    break
            if not reaction or reaction in options or node.bounds in seen_bounds:
                continue
            if node.width > roi_width * 0.30 or node.height > max(120, roi_height * 0.85):
                continue
            seen_bounds.add(node.bounds)
            options[reaction] = LocateResult(
                *node.center,
                0.98,
                "panel_ui_label",
                node.bounds,
                [f"exact_label:{reaction}"],
                frame_hash(image),
                content_kind="panel",
                reaction_bounds=node.bounds,
                scope_bounds=roi,
                candidate_evidence=[{"accepted": "exact_clickable_panel_label"}],
            )
        return options

    def resolve_panel(
        self,
        *,
        before_image,
        before_ui_xml: str,
        after_image,
        after_ui_xml: str,
        content_kind: ContentKind,
        like_location: LocateResult,
    ) -> ReactionPanelResolution | None:
        if after_image is None:
            return None
        roi = self._panel_roi(after_image, content_kind, like_location)
        options = self._xml_options(after_ui_xml, roi, after_image)
        before_options = self._xml_options(before_ui_xml, roi, before_image) if before_image is not None else {}
        new_labels = set(options) - set(before_options)
        centers = self._circle_centers(after_image, roi)
        before_centers = self._circle_centers(before_image, roi) if before_image is not None else []
        visual_is_new = bool(centers and (not before_centers or len(centers) != len(before_centers)))

        if len(options) >= 3 and (new_labels or not before_ui_xml.strip()):
            panel_bounds = union_bounds(*(item.roi for item in options.values()))
            return ReactionPanelResolution(
                panel_bounds,
                getattr(like_location, "reaction_bounds", ()) or getattr(like_location, "roi", (0, 0, 0, 0)),
                content_kind,
                options=options,
                source="ui_hierarchy_delta",
                confidence=0.99 if new_labels else 0.92,
                frame_id=frame_hash(after_image),
                visual_centers=centers,
                evidence=[f"options={sorted(options)}", f"new_options={sorted(new_labels)}"],
            )
        if centers and (visual_is_new or before_image is None):
            gaps = [centers[index + 1][0] - centers[index][0] for index in range(len(centers) - 1)]
            padding = max(18, int(np.median(gaps) * 0.55)) if gaps else 24
            panel_bounds = (
                max(roi[0], centers[0][0] - padding),
                max(roi[1], min(point[1] for point in centers) - padding),
                min(roi[2], centers[-1][0] + padding),
                min(roi[3], max(point[1] for point in centers) + padding),
            )
            return ReactionPanelResolution(
                panel_bounds,
                getattr(like_location, "reaction_bounds", ()) or getattr(like_location, "roi", (0, 0, 0, 0)),
                content_kind,
                options=options,
                source="visual_panel_delta" if before_image is not None else "visual_panel_geometry",
                confidence=0.91 if visual_is_new else 0.82,
                frame_id=frame_hash(after_image),
                visual_centers=centers,
                evidence=[f"visual_options={len(centers)}", f"roi={roi}", f"temporal_new={visual_is_new}"],
            )
        return None

    def locate(
        self,
        image,
        ui_xml: str,
        *,
        content_kind: ContentKind,
        internal_action: str,
        like_location: LocateResult,
        panel_resolution: ReactionPanelResolution | None = None,
        before_image=None,
        before_ui_xml: str = "",
    ) -> LocateResult | None:
        if image is None or internal_action not in REACTION_LABELS:
            return None
        resolution = panel_resolution or self.resolve_panel(
            before_image=before_image,
            before_ui_xml=before_ui_xml,
            after_image=image,
            after_ui_xml=ui_xml,
            content_kind=content_kind,
            like_location=like_location,
        )
        if resolution is None:
            return None
        if internal_action in resolution.options:
            return resolution.options[internal_action]

        try:
            match, score = self.matcher.find_reaction_in_roi_image(
                image,
                internal_action,
                resolution.panel_bounds,
                threshold=0.72,
            )
        except Exception:
            match, score = None, -1.0
        if match is None:
            return None
        nearest = min(
            (
                ((match.x - x) ** 2 + (match.y - y) ** 2) ** 0.5
                for x, y in resolution.visual_centers
            ),
            default=float("inf"),
        )
        height, width = image.shape[:2]
        if not self._point_in(resolution.panel_bounds, (match.x, match.y)):
            return None
        if resolution.visual_centers and nearest > max(22, min(width, height) * 0.055):
            return None
        return LocateResult(
            match.x,
            match.y,
            float(score),
            "panel_template_validated",
            match.bounds,
            ["anchor_bound_panel", f"reaction_template:{internal_action}", f"nearest_option={nearest:.1f}"],
            frame_hash(image),
            content_kind="panel",
            reaction_bounds=match.bounds,
            scope_bounds=resolution.panel_bounds,
            candidate_evidence=[
                {"template_score": float(score)},
                {"panel_source": resolution.source, "panel_confidence": resolution.confidence},
            ],
        )

    def panel_is_open(
        self,
        image,
        ui_xml: str,
        *,
        content_kind: ContentKind,
        like_location: LocateResult,
        before_image=None,
        before_ui_xml: str = "",
    ) -> bool:
        return self.resolve_panel(
            before_image=before_image,
            before_ui_xml=before_ui_xml,
            after_image=image,
            after_ui_xml=ui_xml,
            content_kind=content_kind,
            like_location=like_location,
        ) is not None


__all__ = ["ReactionPanelLocator", "ReactionPanelResolution"]
