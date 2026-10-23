from __future__ import annotations

import cv2
import numpy as np

from core.template_matcher import MatchResult, TemplateMatcher

from .common import LocateResult, frame_hash, make_scope_signature, union_bounds
from .ui_tree import UiTree


class ReelLikeLocator:
    """Locate the first icon in Facebook's vertical Reel action rail."""

    def __init__(
        self,
        matcher: TemplateMatcher,
        template_filename: str,
        threshold: float,
    ) -> None:
        self.matcher = matcher
        self.template_filename = template_filename
        self.threshold = float(threshold)

    @staticmethod
    def _remove_long_vertical_lines(mask):
        height, width = mask.shape[:2]
        if height < 5 or width < 2:
            return mask
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT,
            (1, max(5, int(height * 0.35))),
        )
        lines = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
        return cv2.bitwise_and(mask, cv2.bitwise_not(lines))

    def _vertical_bright_groups(
        self,
        image,
        candidate: MatchResult,
    ) -> list[dict[str, float]]:
        gray = image if len(image.shape) == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        height, width = gray.shape[:2]
        icon_px = float(self.matcher.ui_px(24))
        candidate_height = max(candidate.height, int(icon_px))
        half_width = max(int(icon_px * 1.45), int(candidate_height * 1.05))
        x1 = max(0, int(candidate.x - half_width))
        x2 = min(width, int(candidate.x + half_width + 1))
        y0 = max(0, int(candidate.y - icon_px * 1.25))
        strip = gray[y0:, x1:x2]
        if strip.size == 0:
            return []

        # Reel action glyphs and counters are rendered bright over the video.
        # A fixed lower bound avoids the video background becoming foreground;
        # the percentile raises the threshold on unusually bright frames.
        threshold_value = int(max(175, min(235, np.percentile(strip, 92))))
        mask = cv2.threshold(strip, threshold_value, 255, cv2.THRESH_BINARY)[1]
        if np.count_nonzero(mask) > mask.size * 0.38:
            # Bright videos can overwhelm a pure luminance mask. Keep only
            # high-contrast edges in that case.
            mask = cv2.Canny(strip, 70, 170)
        mask = self._remove_long_vertical_lines(mask)

        row_activity = np.count_nonzero(mask, axis=1)
        active = row_activity >= max(1, int(mask.shape[1] * 0.015))
        merge_gap = max(1, int(icon_px * 0.12))
        runs: list[tuple[int, int]] = []
        start: int | None = None
        last_active: int | None = None
        for row, is_active in enumerate(active):
            if is_active:
                if start is None:
                    start = row
                last_active = row
            elif (
                start is not None
                and last_active is not None
                and row - last_active > merge_gap
            ):
                runs.append((start, last_active))
                start = None
                last_active = None
        if start is not None and last_active is not None:
            runs.append((start, last_active))

        groups: list[dict[str, float]] = []
        minimum_icon_height = max(5, int(icon_px * 0.62))
        maximum_icon_height = max(minimum_icon_height, int(icon_px * 1.75))
        minimum_icon_width = max(5, int(icon_px * 0.48))
        maximum_icon_width = max(minimum_icon_width, int(icon_px * 2.45))
        for start_row, end_row in runs:
            region = mask[start_row : end_row + 1]
            ys, xs = np.nonzero(region)
            if xs.size == 0:
                continue
            group_height = end_row - start_row + 1
            group_width = int(xs.max() - xs.min() + 1)
            if not minimum_icon_height <= group_height <= maximum_icon_height:
                continue
            if not minimum_icon_width <= group_width <= maximum_icon_width:
                continue
            center_x = x1 + float(xs.mean())
            center_y = y0 + float((start_row + end_row) / 2.0)
            groups.append(
                {
                    "center_x": center_x,
                    "center_y": center_y,
                    "height": float(group_height),
                    "width": float(group_width),
                    "pixels": float(xs.size),
                }
            )
        return sorted(groups, key=lambda item: item["center_y"])

    def _validate_vertical_action_rail(
        self,
        image,
        candidate: MatchResult,
    ) -> dict | None:
        height, width = image.shape[:2]
        rail_crop = self.matcher.is_narrow_rail_crop(image)
        if not rail_crop and candidate.x < width * 0.62:
            return None

        groups = self._vertical_bright_groups(image, candidate)
        if len(groups) < 3:
            return None
        icon_px = float(self.matcher.ui_px(24))
        center_tolerance = max(icon_px * 0.78, candidate.height * 0.78)
        candidate_group: dict[str, float] | None = None
        nearest_distance = float("inf")
        for group in groups:
            distance = abs(group["center_y"] - candidate.y)
            if distance <= center_tolerance and distance < nearest_distance:
                candidate_group = group
                nearest_distance = distance
        if candidate_group is None:
            return None
        if abs(candidate_group["center_x"] - candidate.x) > icon_px * 1.05:
            return None

        below = [
            group
            for group in groups
            if group["center_y"] > candidate_group["center_y"] + icon_px * 0.80
        ]
        if len(below) < 2:
            return None
        second, third = below[0], below[1]
        for group in (second, third):
            if abs(group["center_x"] - candidate_group["center_x"]) > icon_px * 1.20:
                return None

        first_gap = second["center_y"] - candidate_group["center_y"]
        second_gap = third["center_y"] - second["center_y"]
        minimum_gap = icon_px * 1.55
        maximum_gap = icon_px * 4.80
        if not minimum_gap <= first_gap <= maximum_gap:
            return None
        if not minimum_gap <= second_gap <= maximum_gap:
            return None
        if max(first_gap, second_gap) / max(1.0, min(first_gap, second_gap)) > 1.65:
            return None

        # The Like candidate must be the first icon of the repeating action
        # sequence. Share and Save candidates have fewer than two icon groups
        # below them and are rejected by the checks above.
        def group_bounds(group):
            half_w = max(4, int(group["width"] / 2))
            half_h = max(4, int(group["height"] / 2))
            return (
                int(group["center_x"]) - half_w,
                int(group["center_y"]) - half_h,
                int(group["center_x"]) + half_w,
                int(group["center_y"]) + half_h,
            )

        return {
            "reaction_bounds": candidate.bounds,
            "comment_bounds": group_bounds(second),
            "share_bounds": group_bounds(third),
            "evidence": [
                f"reel_like_y={candidate_group['center_y']:.1f}",
                f"reel_comment_y={second['center_y']:.1f}",
                f"reel_share_y={third['center_y']:.1f}",
            ],
        }

    def locate_once(self, image, ui_xml: str = "") -> LocateResult | None:
        if image is None:
            return None
        height, width = image.shape[:2]
        tree = UiTree.parse(ui_xml)
        layout = tree.find_action_layout(
            "vertical",
            screen_width=width,
            screen_height=height,
        )
        if layout is not None:
            node = layout.reaction
            x, y = node.center
            cluster = layout.cluster_bounds
            scope = tree.common_ancestor_bounds(
                [layout.reaction, layout.comment, layout.share]
            )
            if (
                scope[2] <= scope[0]
                or scope[3] <= scope[1]
                or (scope[2] - scope[0]) > width * 0.45
                or (scope[3] - scope[1]) > height * 0.70
            ):
                scope = cluster
            signature = make_scope_signature(
                "reel",
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
                content_kind="reel",
                reaction_bounds=node.bounds,
                comment_bounds=layout.comment.bounds,
                share_bounds=layout.share.bounds,
                action_cluster_bounds=cluster,
                scope_bounds=scope,
                scope_signature=signature,
                candidate_evidence=[{"accepted": "vertical_ui_action_cluster"}],
            )

        try:
            candidates, _best_score, roi = (
                self.matcher.find_reel_like_button_candidates_image(
                    image,
                    template_filename=self.template_filename,
                    threshold=self.threshold,
                )
            )
        except Exception:
            candidates, roi = [], (0, 0, 0, 0)

        for candidate in candidates:
            if candidate.confidence < self.threshold:
                continue
            validation = self._validate_vertical_action_rail(image, candidate)
            if validation is None:
                continue
            reaction_bounds = validation["reaction_bounds"]
            comment_bounds = validation["comment_bounds"]
            share_bounds = validation["share_bounds"]
            cluster = union_bounds(reaction_bounds, comment_bounds, share_bounds)
            signature = make_scope_signature(
                "reel",
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
                "reel_template_canny",
                roi,
                ["clean_thumb_icon", "vertical_action_sequence", *validation["evidence"]],
                frame_hash(image),
                content_kind="reel",
                reaction_bounds=reaction_bounds,
                comment_bounds=comment_bounds,
                share_bounds=share_bounds,
                action_cluster_bounds=cluster,
                scope_bounds=cluster,
                scope_signature=signature,
                candidate_evidence=[
                    {"candidate": candidate.bounds, "template_score": candidate.confidence},
                    {"accepted": "vertical_visual_action_cluster"},
                ],
            )
        return None
