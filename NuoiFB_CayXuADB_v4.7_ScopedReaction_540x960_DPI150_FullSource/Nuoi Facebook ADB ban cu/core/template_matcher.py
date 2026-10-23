from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from core.config import REACTION_TEMPLATE_FILES
from core.image_io import imread_unicode


@dataclass
class MatchResult:
    x: int
    y: int
    confidence: float
    template_name: str
    width: int = 0
    height: int = 0
    scale: float = 1.0

    @property
    def bounds(self) -> tuple[int, int, int, int]:
        half_width = max(1, int(self.width) // 2)
        half_height = max(1, int(self.height) // 2)
        return (
            int(self.x) - half_width,
            int(self.y) - half_height,
            int(self.x) + half_width,
            int(self.y) + half_height,
        )


class TemplateMatcher:
    """OpenCV matcher shared by the legacy runner and Auto Job detectors.

    The supplied templates were captured on a 540x960, 150 DPI LDPlayer.
    Screen dimensions are used for relative ROIs while DPI controls expected
    icon scale. Cropped reference images are detected automatically and are not
    forced through a full-screen ROI.
    """

    TEMPLATE_FILES = REACTION_TEMPLATE_FILES
    TEMPLATE_BASE_DPI = 150
    LIKE_SCALES = (0.65, 0.75, 0.85, 0.95, 1.00, 1.05, 1.10, 1.20, 1.30, 1.40)
    PANEL_REACTION_SCALES = (0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.30)

    def __init__(
        self,
        template_dir: str | Path,
        confidence_like: float = 0.6,
        confidence_reaction: float = 0.6,
        screen_width_px: int = 540,
        screen_height_px: int = 960,
        screen_dpi: int = 150,
    ):
        self.template_dir = Path(template_dir)
        self.confidence_like = float(confidence_like)
        self.confidence_reaction = float(confidence_reaction)
        self.screen_width_px = max(1, int(screen_width_px))
        self.screen_height_px = max(1, int(screen_height_px))
        self.screen_dpi = max(1, int(screen_dpi))
        self._cache: dict[str, object] = {}

    def ui_px(self, dp: float) -> int:
        """Convert Android dp to pixels using the configured emulator DPI."""

        return max(1, int(round(float(dp) * self.screen_dpi / 160.0)))

    def _search_scales(self, base_scales: tuple[float, ...]) -> tuple[float, ...]:
        density_scale = self.screen_dpi / float(self.TEMPLATE_BASE_DPI)
        values = {
            round(max(0.35, min(2.50, scale * density_scale)), 3)
            for scale in base_scales
        }
        return tuple(sorted(values))

    def is_narrow_rail_crop(self, image) -> bool:
        gray = self._as_gray(image)
        if gray is None:
            return False
        height, width = gray.shape[:2]
        crop_width_limit = max(self.ui_px(96), int(self.screen_width_px * 0.28))
        return width <= crop_width_limit and height >= width * 1.5

    def _load_template(self, name: str):
        if name not in self.TEMPLATE_FILES:
            raise ValueError(f"Unknown template name: {name}")

        if name in self._cache:
            return self._cache[name]

        path = self.template_dir / self.TEMPLATE_FILES[name]
        if not path.exists():
            raise FileNotFoundError(f"Missing template: {path}")

        img = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError(
                f"Cannot read template with Unicode-safe OpenCV loader: {path}"
            )

        self._cache[name] = img
        return img

    def _load_template_by_filename(self, filename: str):
        cache_key = f"file:{filename}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        path = self.template_dir / filename
        if not path.exists():
            stem = Path(filename).stem
            for ext in (".png", ".jpg", ".jpeg"):
                candidates = (
                    self.template_dir / f"{stem}{ext}",
                    self.template_dir / f"{filename}{ext}",
                )
                existing = next((candidate for candidate in candidates if candidate.exists()), None)
                if existing is not None:
                    path = existing
                    break
            else:
                raise FileNotFoundError(f"Missing template: {self.template_dir / filename}")

        img = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError(
                f"Cannot read template with Unicode-safe OpenCV loader: {path}"
            )

        self._cache[cache_key] = img
        return img

    @staticmethod
    def _as_gray(image):
        if image is None:
            return None
        if len(image.shape) == 2:
            return image
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    @staticmethod
    def _sparse_otsu_foreground(gray):
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

    @staticmethod
    def _remove_long_horizontal_lines(binary, width_ratio: float):
        height, width = binary.shape[:2]
        if width < 4 or height < 2:
            return binary
        kernel_width = max(3, int(round(width * float(width_ratio))))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_width, 1))
        lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        return cv2.bitwise_and(binary, cv2.bitwise_not(lines))

    @staticmethod
    def _remove_long_vertical_lines(binary, height_ratio: float = 0.35):
        height, width = binary.shape[:2]
        if height < 4 or width < 2:
            return binary
        kernel_height = max(5, int(round(height * float(height_ratio))))
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, kernel_height))
        lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        return cv2.bitwise_and(binary, cv2.bitwise_not(lines))

    @staticmethod
    def _crop_to_foreground(image, *, padding: int = 2):
        points = cv2.findNonZero(image)
        if points is None:
            return image
        x, y, width, height = cv2.boundingRect(points)
        pad = max(0, int(padding))
        return image[
            max(0, y - pad) : min(image.shape[0], y + height + pad),
            max(0, x - pad) : min(image.shape[1], x + width + pad),
        ]

    def _prepare_feed_like_template(self, template_gray):
        cache_key = "prepared:feed_like"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        foreground = self._sparse_otsu_foreground(template_gray)
        foreground = self._remove_long_horizontal_lines(foreground, 0.55)
        foreground = self._crop_to_foreground(foreground, padding=self.ui_px(2))
        self._cache[cache_key] = foreground
        return foreground

    def _prepare_feed_screen(self, screen_gray):
        foreground = self._sparse_otsu_foreground(screen_gray)
        return self._remove_long_horizontal_lines(foreground, 0.28)

    def _prepare_reel_like_template(self, template_gray):
        """Extract only the thumb icon and discard its dynamic counter."""

        cache_key = "prepared:reel_like:" + str(template_gray.shape)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        height, width = template_gray.shape[:2]
        edges = cv2.Canny(template_gray, 50, 150)
        activity = np.count_nonzero(edges, axis=1)
        active = activity >= max(2, int(width * 0.04))
        runs: list[tuple[int, int]] = []
        start: int | None = None
        for row, is_active in enumerate(active):
            if is_active and start is None:
                start = row
            elif not is_active and start is not None:
                runs.append((start, row - 1))
                start = None
        if start is not None:
            runs.append((start, height - 1))

        icon_band = template_gray
        minimum_height = max(3, int(height * 0.15))
        for start_row, end_row in runs:
            if end_row - start_row + 1 < minimum_height:
                continue
            padding = max(1, int(height * 0.03))
            icon_band = template_gray[
                max(0, start_row - padding) : min(height, end_row + padding + 1),
                :,
            ]
            break

        icon_edges = cv2.Canny(icon_band, 50, 150)
        points = cv2.findNonZero(icon_edges)
        if points is not None:
            x, y, icon_width, icon_height = cv2.boundingRect(points)
            padding = self.ui_px(2)
            icon_band = icon_band[
                max(0, y - padding) : min(icon_band.shape[0], y + icon_height + padding),
                max(0, x - padding) : min(icon_band.shape[1], x + icon_width + padding),
            ]

        self._cache[cache_key] = icon_band
        return icon_band

    @staticmethod
    def _resize_template(template, scale: float, *, binary: bool):
        interpolation = cv2.INTER_AREA if scale < 1.0 else cv2.INTER_CUBIC
        resized = cv2.resize(
            template,
            None,
            fx=float(scale),
            fy=float(scale),
            interpolation=interpolation,
        )
        if binary:
            resized = cv2.threshold(resized, 127, 255, cv2.THRESH_BINARY)[1]
        return resized

    @staticmethod
    def _extract_local_candidates(
        score_map,
        *,
        origin_x: int,
        origin_y: int,
        template_name: str,
        template_width: int,
        template_height: int,
        scale: float,
        minimum_score: float,
        count: int,
    ) -> list[MatchResult]:
        if score_map is None or score_map.size == 0:
            return []
        working = score_map.copy()
        found: list[MatchResult] = []
        suppress_x = max(3, int(template_width * 0.55))
        suppress_y = max(3, int(template_height * 0.65))
        for _ in range(max(1, int(count))):
            _, max_value, _, max_location = cv2.minMaxLoc(working)
            score = float(max_value)
            if score < float(minimum_score):
                break
            center_x = int(origin_x + max_location[0] + template_width / 2)
            center_y = int(origin_y + max_location[1] + template_height / 2)
            found.append(
                MatchResult(
                    x=center_x,
                    y=center_y,
                    confidence=score,
                    template_name=template_name,
                    width=int(template_width),
                    height=int(template_height),
                    scale=float(scale),
                )
            )
            x1 = max(0, max_location[0] - suppress_x)
            y1 = max(0, max_location[1] - suppress_y)
            x2 = min(working.shape[1], max_location[0] + suppress_x + 1)
            y2 = min(working.shape[0], max_location[1] + suppress_y + 1)
            working[y1:y2, x1:x2] = -1.0
        return found

    @staticmethod
    def _deduplicate_candidates(
        candidates: list[MatchResult],
        *,
        max_candidates: int,
    ) -> list[MatchResult]:
        selected: list[MatchResult] = []
        for candidate in sorted(candidates, key=lambda item: item.confidence, reverse=True):
            duplicate = False
            for existing in selected:
                radius = max(
                    5.0,
                    min(
                        max(candidate.width, candidate.height),
                        max(existing.width, existing.height),
                    )
                    * 0.70,
                )
                distance = float(np.hypot(candidate.x - existing.x, candidate.y - existing.y))
                if distance <= radius:
                    duplicate = True
                    break
            if duplicate:
                continue
            selected.append(candidate)
            if len(selected) >= max(1, int(max_candidates)):
                break
        return selected

    def _find(
        self,
        screenshot_path: str | Path,
        name: str,
        threshold: float,
    ) -> tuple[MatchResult | None, float]:
        screen = imread_unicode(screenshot_path, cv2.IMREAD_GRAYSCALE)
        if screen is None:
            raise ValueError(
                f"Cannot read screenshot with Unicode-safe OpenCV loader: {screenshot_path}"
            )

        template = self._load_template(name)
        template_height, template_width = template.shape[:2]
        screen_height, screen_width = screen.shape[:2]
        if template_height >= screen_height or template_width >= screen_width:
            return None, -1.0

        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_value, _, max_location = cv2.minMaxLoc(result)

        if max_value < threshold:
            return None, float(max_value)

        x = int(max_location[0] + template_width / 2)
        y = int(max_location[1] + template_height / 2)
        return (
            MatchResult(
                x=x,
                y=y,
                confidence=float(max_value),
                template_name=name,
                width=template_width,
                height=template_height,
            ),
            float(max_value),
        )

    def find_like_button(self, screenshot_path: str | Path) -> tuple[MatchResult | None, float]:
        screen = imread_unicode(screenshot_path, cv2.IMREAD_GRAYSCALE)
        if screen is None:
            raise ValueError(
                f"Cannot read screenshot with Unicode-safe OpenCV loader: {screenshot_path}"
            )
        return self.find_like_button_image(screen)

    def find_like_button_candidates_image(
        self,
        image,
        *,
        minimum_score: float | None = None,
        max_candidates: int = 16,
    ) -> tuple[list[MatchResult], float, tuple[int, int, int, int]]:
        screen = self._as_gray(image)
        if screen is None:
            raise ValueError("Cannot read in-memory screenshot")

        height, width = screen.shape[:2]
        short_horizontal_crop = height <= max(self.ui_px(84), int(self.screen_height_px * 0.12))
        roi_y1 = 0 if short_horizontal_crop else int(height * 0.06)
        roi_y2 = height if short_horizontal_crop else int(height * 0.995)
        roi_x1 = 0
        roi_x2 = min(width, int(width * 0.68))
        roi_box = (roi_x1, roi_y1, roi_x2, roi_y2)
        if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
            return [], -1.0, roi_box

        prepared_screen = self._prepare_feed_screen(screen)
        roi = prepared_screen[roi_y1:roi_y2, roi_x1:roi_x2]
        template = self._prepare_feed_like_template(self._load_template("like"))
        floor = (
            max(0.30, self.confidence_like - 0.28)
            if minimum_score is None
            else max(-1.0, float(minimum_score))
        )

        candidates: list[MatchResult] = []
        best_score = -1.0
        for scale in self._search_scales(self.LIKE_SCALES):
            resized = self._resize_template(template, scale, binary=True)
            template_height, template_width = resized.shape[:2]
            if template_height >= roi.shape[0] or template_width >= roi.shape[1]:
                continue
            result = cv2.matchTemplate(roi, resized, cv2.TM_CCOEFF_NORMED)
            _, max_value, _, _ = cv2.minMaxLoc(result)
            best_score = max(best_score, float(max_value))
            candidates.extend(
                self._extract_local_candidates(
                    result,
                    origin_x=roi_x1,
                    origin_y=roi_y1,
                    template_name="like",
                    template_width=template_width,
                    template_height=template_height,
                    scale=scale,
                    minimum_score=floor,
                    count=4,
                )
            )

        return (
            self._deduplicate_candidates(candidates, max_candidates=max_candidates),
            best_score,
            roi_box,
        )

    def find_like_button_image(self, image) -> tuple[MatchResult | None, float]:
        candidates, best_score, _ = self.find_like_button_candidates_image(image)
        for candidate in candidates:
            if candidate.confidence >= self.confidence_like:
                return candidate, best_score
        return None, best_score

    def find_reel_like_button(
        self,
        screenshot_path: str | Path,
        template_filename: str = "nutlikereel.png",
        threshold: float = 0.55,
    ) -> tuple[MatchResult | None, float, tuple[int, int, int, int]]:
        screen = imread_unicode(screenshot_path, cv2.IMREAD_GRAYSCALE)
        if screen is None:
            raise ValueError(
                f"Cannot read screenshot with Unicode-safe OpenCV loader: {screenshot_path}"
            )
        return self.find_reel_like_button_image(
            screen,
            template_filename=template_filename,
            threshold=threshold,
        )

    def find_reel_like_button_candidates_image(
        self,
        image,
        *,
        template_filename: str = "nutlikereel.png",
        threshold: float = 0.55,
        max_candidates: int = 20,
    ) -> tuple[list[MatchResult], float, tuple[int, int, int, int]]:
        screen = self._as_gray(image)
        if screen is None:
            raise ValueError("Cannot read in-memory screenshot")

        height, width = screen.shape[:2]
        rail_crop = self.is_narrow_rail_crop(screen)
        roi_x1 = 0 if rail_crop else int(width * 0.58)
        roi_x2 = width
        roi_y1 = 0 if rail_crop else int(height * 0.03)
        roi_y2 = height if rail_crop else int(height * 0.985)
        roi_box = (roi_x1, roi_y1, roi_x2, roi_y2)
        if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
            return [], -1.0, roi_box

        roi = screen[roi_y1:roi_y2, roi_x1:roi_x2]
        template = self._prepare_reel_like_template(
            self._load_template_by_filename(template_filename)
        )
        roi_edges = cv2.Canny(roi, 50, 150)
        roi_edges = self._remove_long_vertical_lines(roi_edges, 0.35)
        floor = max(0.20, float(threshold) - 0.32)

        candidates: list[MatchResult] = []
        best_score = -1.0
        for scale in self._search_scales(self.LIKE_SCALES):
            resized = self._resize_template(template, scale, binary=False)
            template_height, template_width = resized.shape[:2]
            if template_height >= roi.shape[0] or template_width >= roi.shape[1]:
                continue
            resized_edges = cv2.Canny(resized, 50, 150)
            if np.count_nonzero(resized_edges) < 8:
                continue
            result = cv2.matchTemplate(roi_edges, resized_edges, cv2.TM_CCOEFF_NORMED)
            _, max_value, _, _ = cv2.minMaxLoc(result)
            best_score = max(best_score, float(max_value))
            candidates.extend(
                self._extract_local_candidates(
                    result,
                    origin_x=roi_x1,
                    origin_y=roi_y1,
                    template_name=template_filename,
                    template_width=template_width,
                    template_height=template_height,
                    scale=scale,
                    minimum_score=floor,
                    count=5,
                )
            )

        return (
            self._deduplicate_candidates(candidates, max_candidates=max_candidates),
            best_score,
            roi_box,
        )

    def find_reel_like_button_image(
        self,
        image,
        template_filename: str = "nutlikereel.png",
        threshold: float = 0.55,
    ) -> tuple[MatchResult | None, float, tuple[int, int, int, int]]:
        candidates, best_score, roi_box = self.find_reel_like_button_candidates_image(
            image,
            template_filename=template_filename,
            threshold=threshold,
        )
        for candidate in candidates:
            if candidate.confidence >= float(threshold):
                return candidate, best_score, roi_box
        return None, best_score, roi_box

    def find_reaction_in_roi_image(
        self,
        image,
        reaction_name: str,
        roi_box: tuple[int, int, int, int],
        threshold: float = 0.72,
    ) -> tuple[MatchResult | None, float]:
        screen = self._as_gray(image)
        if screen is None:
            raise ValueError("Cannot read in-memory screenshot")
        height, width = screen.shape[:2]
        x1, y1, x2, y2 = roi_box
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(width, int(x2)), min(height, int(y2))
        if x2 <= x1 or y2 <= y1:
            return None, -1.0
        roi = screen[y1:y2, x1:x2]
        template = self._load_template(reaction_name)
        best_score = -1.0
        best_location = None
        best_size = None
        for scale in self._search_scales(self.PANEL_REACTION_SCALES):
            resized = self._resize_template(template, scale, binary=False)
            template_height, template_width = resized.shape[:2]
            if template_height >= roi.shape[0] or template_width >= roi.shape[1]:
                continue
            result = cv2.matchTemplate(roi, resized, cv2.TM_CCOEFF_NORMED)
            _, max_value, _, max_location = cv2.minMaxLoc(result)
            if float(max_value) > best_score:
                best_score = float(max_value)
                best_location = max_location
                best_size = (template_width, template_height)
        if best_location is None or best_size is None or best_score < float(threshold):
            return None, best_score
        template_width, template_height = best_size
        return (
            MatchResult(
                x=int(x1 + best_location[0] + template_width / 2),
                y=int(y1 + best_location[1] + template_height / 2),
                confidence=best_score,
                template_name=reaction_name,
                width=template_width,
                height=template_height,
            ),
            best_score,
        )

    def find_reaction(
        self,
        screenshot_path: str | Path,
        reaction_name: str,
    ) -> tuple[MatchResult | None, float]:
        return self._find(screenshot_path, reaction_name, self.confidence_reaction)

    def find_reaction_in_panel(
        self,
        screenshot_path: str | Path,
        reaction_name: str,
        like_x: int,
        like_y: int,
        screen_width: int | None = None,
        screen_height: int | None = None,
        threshold: float = 0.75,
    ) -> tuple[MatchResult | None, float, tuple[int, int, int, int]]:
        screen = imread_unicode(screenshot_path, cv2.IMREAD_GRAYSCALE)
        if screen is None:
            raise ValueError(
                f"Cannot read screenshot with Unicode-safe OpenCV loader: {screenshot_path}"
            )

        height, width = screen.shape[:2]
        if screen_width:
            width = min(width, int(screen_width))
        if screen_height:
            height = min(height, int(screen_height))

        roi_x1 = max(0, int(like_x) - self.ui_px(85))
        roi_x2 = min(width, int(like_x) + self.ui_px(460))
        roi_y1 = max(0, int(like_y) - self.ui_px(205))
        roi_y2 = max(0, int(like_y) - self.ui_px(27))
        roi_box = (roi_x1, roi_y1, roi_x2, roi_y2)

        if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
            return None, -1.0, roi_box

        match, best_score = self.find_reaction_in_roi_image(
            screen,
            reaction_name,
            roi_box,
            threshold=max(float(threshold), 0.75),
        )
        return match, best_score, roi_box
