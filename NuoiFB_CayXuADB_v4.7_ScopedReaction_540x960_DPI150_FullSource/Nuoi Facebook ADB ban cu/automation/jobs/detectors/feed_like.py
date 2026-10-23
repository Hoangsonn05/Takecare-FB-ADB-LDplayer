from __future__ import annotations

from collections.abc import Callable
import threading

import cv2
import numpy as np

from core.template_matcher import MatchResult, TemplateMatcher

from .common import (
    CapturedFrame,
    LocateResult,
    SearchResult,
    frame_hash,
    make_scope_signature,
    union_bounds,
)
from .ui_tree import UiTree


class FeedLikeLocator:
    """Locate the first action in Facebook's horizontal Feed action row.

    UI hierarchy remains the strongest source. The OpenCV fallback first finds
    the cleaned Like icon/label template, then validates that the candidate is
    the left-most member of a three-part horizontal action row. Contours are
    supporting evidence only and never create an independent tap coordinate.
    """

    def __init__(self, matcher: TemplateMatcher, minimum_confidence: float = 0.60) -> None:
        self.matcher = matcher
        self.minimum_confidence = float(minimum_confidence)

    def _locate_ui(self, image, ui_xml: str) -> LocateResult | None:
        if image is None:
            return None
        height, width = image.shape[:2]
        tree = UiTree.parse(ui_xml)
        layout = tree.find_action_layout(
            "horizontal",
            screen_width=width,
            screen_height=height,
        )
        if layout is None:
            return None
        node = layout.reaction
        x, y = node.center
        cluster = layout.cluster_bounds
        scope = tree.common_ancestor_bounds(
            [layout.reaction, layout.comment, layout.share]
        )
        if (
            scope[2] <= scope[0]
            or scope[3] <= scope[1]
            or (scope[2] - scope[0]) > width * 1.01
            or (scope[3] - scope[1]) > height * 0.75
        ):
            scope = cluster
        signature = make_scope_signature(
            "feed",
            node.bounds,
            layout.comment.bounds,
            layout.share.bounds,
            scope,
            [*layout.labels, *tree.scope_identity_tokens(scope)],
        )
        return LocateResult(
            x,
            y,
            0.94,
            "ui_hierarchy",
            node.bounds,
            layout.labels,
            frame_hash(image),
            content_kind="feed",
            reaction_bounds=node.bounds,
            comment_bounds=layout.comment.bounds,
            share_bounds=layout.share.bounds,
            action_cluster_bounds=cluster,
            scope_bounds=scope,
            scope_signature=signature,
            candidate_evidence=[{"accepted": "horizontal_ui_action_cluster"}],
        )

    @staticmethod
    def _sparse_foreground(gray):
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        foreground = cv2.threshold(
            blurred,
            0,
            255,
            cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU,
        )[1]
        if np.count_nonzero(foreground) > foreground.size * 0.55:
            foreground = cv2.bitwise_not(foreground)
        return foreground

    def _horizontal_groups(
        self,
        image,
        candidate: MatchResult,
    ) -> list[dict[str, float]]:
        gray = image if len(image.shape) == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape[:2]
        candidate_height = max(candidate.height, self.matcher.ui_px(18))
        half_band = max(
            self.matcher.ui_px(25),
            int(candidate_height * 1.15),
            int(height * 0.025),
        )
        y1 = max(0, int(candidate.y - half_band))
        y2 = min(height, int(candidate.y + half_band + 1))
        band = self._sparse_foreground(gray[y1:y2, :])

        line_kernel_width = max(3, int(width * 0.28))
        line_kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (line_kernel_width, 1),
        )
        long_lines = cv2.morphologyEx(band, cv2.MORPH_OPEN, line_kernel)
        band = cv2.bitwise_and(band, cv2.bitwise_not(long_lines))

        activity = np.count_nonzero(band, axis=0)
        active = activity > 0
        merge_gap = max(self.matcher.ui_px(8), int(candidate_height * 0.45))
        raw_groups: list[tuple[int, int]] = []
        start: int | None = None
        last_active: int | None = None
        for column, is_active in enumerate(active):
            if is_active:
                if start is None:
                    start = column
                last_active = column
            elif (
                start is not None
                and last_active is not None
                and column - last_active > merge_gap
            ):
                raw_groups.append((start, last_active))
                start = None
                last_active = None
        if start is not None and last_active is not None:
            raw_groups.append((start, last_active))

        groups: list[dict[str, float]] = []
        minimum_width = max(self.matcher.ui_px(18), int(candidate_height * 0.65))
        maximum_width = max(minimum_width, int(width * 0.28))
        for start_x, end_x in raw_groups:
            group_width = end_x - start_x + 1
            if not minimum_width <= group_width <= maximum_width:
                continue
            region = band[:, start_x : end_x + 1]
            foreground_pixels = int(np.count_nonzero(region))
            if foreground_pixels < self.matcher.ui_px(14):
                continue
            density = foreground_pixels / float(max(1, region.size))
            row_coverage = float(
                np.mean(np.count_nonzero(region, axis=1) > 0)
            )
            # Screen shadows and crop borders tend to fill almost the entire band.
            if density > 0.58 or row_coverage > 0.88:
                continue
            groups.append(
                {
                    "x1": float(start_x),
                    "x2": float(end_x),
                    "center": float((start_x + end_x) / 2.0),
                    "width": float(group_width),
                    "density": density,
                    "y1": float(y1),
                    "y2": float(y2),
                }
            )
        return sorted(groups, key=lambda item: item["center"])

    def _validate_horizontal_action_row(
        self,
        image,
        candidate: MatchResult,
    ) -> dict | None:
        height, width = image.shape[:2]
        if candidate.x > width * 0.45:
            return None
        groups = self._horizontal_groups(image, candidate)
        if len(groups) < 3:
            return None

        candidate_width = max(candidate.width, self.matcher.ui_px(36))
        candidate_left = candidate.x - candidate_width / 2.0
        candidate_right = candidate.x + candidate_width / 2.0
        candidate_group: dict[str, float] | None = None
        candidate_overlap = 0.0
        for group in groups:
            overlap = max(
                0.0,
                min(candidate_right, group["x2"]) - max(candidate_left, group["x1"]),
            )
            normalized = overlap / max(1.0, min(candidate_width, group["width"]))
            if normalized > candidate_overlap:
                candidate_overlap = normalized
                candidate_group = group
        if candidate_group is None or candidate_overlap < 0.35:
            return None
        if candidate_group["center"] > width * 0.36:
            return None

        minimum_spacing = max(self.matcher.ui_px(70), width * 0.12)
        right_groups = [
            group
            for group in groups
            if group["center"] - candidate_group["center"] >= minimum_spacing
        ]
        if len(right_groups) < 2:
            return None
        comment_group, share_group = right_groups[0], right_groups[1]
        gap_one = comment_group["center"] - candidate_group["center"]
        gap_two = share_group["center"] - comment_group["center"]
        if gap_one <= 0 or gap_two <= 0:
            return None
        if max(gap_one, gap_two) / max(1.0, min(gap_one, gap_two)) > 1.85:
            return None
        if share_group["center"] - candidate_group["center"] < width * 0.45:
            return None

        candidate_group_width = candidate_group["width"]
        for group in (comment_group, share_group):
            ratio = group["width"] / max(1.0, candidate_group_width)
            if not 0.45 <= ratio <= 2.35:
                return None

        def group_bounds(group):
            return (
                int(group["x1"]),
                int(group["y1"]),
                int(group["x2"] + 1),
                int(group["y2"]),
            )

        return {
            "reaction_bounds": candidate.bounds,
            "comment_bounds": group_bounds(comment_group),
            "share_bounds": group_bounds(share_group),
            "evidence": [
                f"feed_row_like_x={candidate_group['center']:.1f}",
                f"feed_row_comment_x={comment_group['center']:.1f}",
                f"feed_row_share_x={share_group['center']:.1f}",
            ],
        }

    def locate_once(self, image, ui_xml: str = "") -> LocateResult | None:
        ui_result = self._locate_ui(image, ui_xml)
        if ui_result:
            return ui_result
        if image is None:
            return None

        try:
            candidates, _best_score, roi = self.matcher.find_like_button_candidates_image(
                image,
                minimum_score=max(0.30, self.minimum_confidence - 0.22),
            )
        except Exception:
            candidates, roi = [], (0, 0, 0, 0)

        for candidate in candidates:
            if candidate.confidence < self.minimum_confidence:
                continue
            validation = self._validate_horizontal_action_row(image, candidate)
            if validation is None:
                continue
            reaction_bounds = validation["reaction_bounds"]
            comment_bounds = validation["comment_bounds"]
            share_bounds = validation["share_bounds"]
            cluster = union_bounds(reaction_bounds, comment_bounds, share_bounds)
            signature = make_scope_signature(
                "feed",
                reaction_bounds,
                comment_bounds,
                share_bounds,
                cluster,
                ("comment", "share"),
            )
            return LocateResult(
                candidate.x,
                candidate.y,
                float(candidate.confidence),
                "template_fallback",
                roi,
                ["clean_like_icon_and_label", *validation["evidence"]],
                frame_hash(image),
                content_kind="feed",
                reaction_bounds=reaction_bounds,
                comment_bounds=comment_bounds,
                share_bounds=share_bounds,
                action_cluster_bounds=cluster,
                scope_bounds=cluster,
                scope_signature=signature,
                candidate_evidence=[
                    {"candidate": candidate.bounds, "template_score": candidate.confidence},
                    {"accepted": "horizontal_visual_action_cluster"},
                ],
            )
        return None

    def locate_with_scroll(
        self,
        device,
        *,
        max_swipes: int,
        swipe_duration_ms: int,
        stop_event: threading.Event,
        capture: Callable[[], CapturedFrame] | None = None,
        settle: Callable[[float], None] | None = None,
    ) -> SearchResult:
        capture_frame = capture or (
            lambda: CapturedFrame(
                image=device.capture_screen_image(),
                ui_xml=device.dump_ui_xml_text(),
                frame_id="",
            )
        )
        settle_cb = settle or (lambda _seconds: None)
        previous_hash = ""
        unchanged = 0
        last_frame: CapturedFrame | None = None
        for swipe_index in range(max(0, int(max_swipes)) + 1):
            if stop_event.is_set():
                return SearchResult(None, last_frame, max(0, swipe_index - 1))
            frame = capture_frame()
            if not frame.frame_id:
                frame.frame_id = frame_hash(frame.image)
            last_frame = frame
            location = self.locate_once(frame.image, frame.ui_xml)
            if location:
                return SearchResult(location, frame, swipe_index)
            if frame.frame_id and frame.frame_id == previous_hash:
                unchanged += 1
            else:
                unchanged = 0
            previous_hash = frame.frame_id
            if unchanged >= 2:
                return SearchResult(None, frame, swipe_index, True)
            if swipe_index >= max_swipes or frame.image is None:
                break
            height, width = frame.image.shape[:2]
            command = device.swipe(
                int(width * 0.50),
                int(height * 0.78),
                int(width * 0.50),
                int(height * 0.50),
                int(swipe_duration_ms),
            )
            if not command.ok:
                break
            settle_cb(1.0)
        return SearchResult(None, last_frame, max(0, int(max_swipes)))
