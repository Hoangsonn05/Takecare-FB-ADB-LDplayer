from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2

from core.config import REACTION_TEMPLATE_FILES
from core.image_io import imread_unicode


@dataclass
class MatchResult:
    x: int
    y: int
    confidence: float
    template_name: str


class TemplateMatcher:
    TEMPLATE_FILES = REACTION_TEMPLATE_FILES
    LIKE_SCALES = (0.60, 0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.30, 1.40)
    PANEL_REACTION_SCALES = (0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.30)

    def __init__(self, template_dir: str | Path, confidence_like: float = 0.6, confidence_reaction: float = 0.6):
        self.template_dir = Path(template_dir)
        self.confidence_like = float(confidence_like)
        self.confidence_reaction = float(confidence_reaction)
        self._cache = {}

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
            raise ValueError(f"Cannot read template with Unicode-safe OpenCV loader: {path}")

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
                for candidate in (self.template_dir / f"{stem}{ext}", self.template_dir / f"{filename}{ext}"):
                    if candidate.exists():
                        path = candidate
                        break
                if path.exists():
                    break
            else:
                raise FileNotFoundError(f"Missing template: {self.template_dir / filename}")

        img = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError(f"Cannot read template with Unicode-safe OpenCV loader: {path}")

        self._cache[cache_key] = img
        return img

    def _prepare_reel_like_template(self, template_gray):
        h, w = template_gray.shape[:2]
        if h > w * 1.25:
            return template_gray[: max(1, int(h * 0.62)), :]
        if h > w:
            return template_gray[: max(1, int(h * 0.70)), :]
        return template_gray

    def _find(self, screenshot_path: str | Path, name: str, threshold: float) -> tuple[MatchResult | None, float]:
        screen = imread_unicode(screenshot_path, cv2.IMREAD_GRAYSCALE)
        if screen is None:
            raise ValueError(f"Cannot read screenshot with Unicode-safe OpenCV loader: {screenshot_path}")

        template = self._load_template(name)
        th, tw = template.shape[:2]
        sh, sw = screen.shape[:2]
        if th >= sh or tw >= sw:
            return None, -1.0

        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val < threshold:
            return None, float(max_val)

        x = int(max_loc[0] + tw / 2)
        y = int(max_loc[1] + th / 2)
        return MatchResult(x=x, y=y, confidence=float(max_val), template_name=name), float(max_val)

    def find_like_button(self, screenshot_path: str | Path) -> tuple[MatchResult | None, float]:
        screen = imread_unicode(screenshot_path, cv2.IMREAD_GRAYSCALE)
        if screen is None:
            raise ValueError(f"Cannot read screenshot with Unicode-safe OpenCV loader: {screenshot_path}")

        h, _w = screen.shape[:2]
        y0 = int(h * 0.50)
        y1 = int(h * 0.88)
        roi = screen[y0:y1, :]
        template = self._load_template("like")

        best_score = -1.0
        best_point = None
        best_size = None

        for scale in self.LIKE_SCALES:
            resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            th, tw = resized.shape[:2]
            if th >= roi.shape[0] or tw >= roi.shape[1]:
                continue

            result = cv2.matchTemplate(roi, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if max_val > best_score:
                best_score = float(max_val)
                best_point = max_loc
                best_size = (tw, th)

        if best_point is None or best_size is None or best_score < self.confidence_like:
            return None, best_score

        x = int(best_point[0] + best_size[0] / 2)
        y = int(y0 + best_point[1] + best_size[1] / 2)
        return MatchResult(x=x, y=y, confidence=best_score, template_name="like"), best_score

    def find_reel_like_button(
        self,
        screenshot_path: str | Path,
        template_filename: str = "nutlikereel.png",
        threshold: float = 0.55,
    ) -> tuple[MatchResult | None, float, tuple[int, int, int, int]]:
        screen = imread_unicode(screenshot_path, cv2.IMREAD_GRAYSCALE)
        if screen is None:
            raise ValueError(f"Cannot read screenshot with Unicode-safe OpenCV loader: {screenshot_path}")

        h, w = screen.shape[:2]
        roi_x1 = int(w * 0.78)
        roi_x2 = w
        roi_y1 = int(h * 0.35)
        roi_y2 = int(h * 0.90)
        roi_box = (roi_x1, roi_y1, roi_x2, roi_y2)
        roi = screen[roi_y1:roi_y2, roi_x1:roi_x2]
        template = self._prepare_reel_like_template(self._load_template_by_filename(template_filename))
        roi_edges = cv2.Canny(roi, 50, 150)

        best_score = -1.0
        best_loc = None
        best_size = None

        for scale in self.LIKE_SCALES:
            resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            th, tw = resized.shape[:2]
            if th >= roi.shape[0] or tw >= roi.shape[1]:
                continue

            resized_edges = cv2.Canny(resized, 50, 150)
            result = cv2.matchTemplate(roi_edges, resized_edges, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if float(max_val) > best_score:
                best_score = float(max_val)
                best_loc = max_loc
                best_size = (tw, th)

        if best_loc is None or best_size is None or best_score < float(threshold):
            return None, best_score, roi_box

        x = int(roi_x1 + best_loc[0] + best_size[0] / 2)
        y = int(roi_y1 + best_loc[1] + best_size[1] / 2)
        return MatchResult(x=x, y=y, confidence=best_score, template_name=template_filename), best_score, roi_box

    def find_reaction(self, screenshot_path: str | Path, reaction_name: str) -> tuple[MatchResult | None, float]:
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
            raise ValueError(f"Cannot read screenshot with Unicode-safe OpenCV loader: {screenshot_path}")

        h, w = screen.shape[:2]
        if screen_width:
            w = min(w, int(screen_width))
        if screen_height:
            h = min(h, int(screen_height))

        roi_x1 = max(0, int(like_x) - 80)
        roi_x2 = min(w, int(like_x) + 430)
        roi_y1 = max(0, int(like_y) - 190)
        roi_y2 = max(0, int(like_y) - 25)
        roi_box = (roi_x1, roi_y1, roi_x2, roi_y2)

        if roi_x2 <= roi_x1 or roi_y2 <= roi_y1:
            return None, -1.0, roi_box

        roi = screen[roi_y1:roi_y2, roi_x1:roi_x2]
        template = self._load_template(reaction_name)

        best_score = -1.0
        best_loc = None
        best_size = None

        for scale in self.PANEL_REACTION_SCALES:
            resized = cv2.resize(template, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
            th, tw = resized.shape[:2]
            if th >= roi.shape[0] or tw >= roi.shape[1]:
                continue

            result = cv2.matchTemplate(roi, resized, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(result)
            if float(max_val) > best_score:
                best_score = float(max_val)
                best_loc = max_loc
                best_size = (tw, th)

        panel_threshold = max(float(threshold), 0.75)
        if best_loc is None or best_size is None or best_score < panel_threshold:
            return None, best_score, roi_box

        tw, th = best_size
        x = int(roi_x1 + best_loc[0] + tw / 2)
        y = int(roi_y1 + best_loc[1] + th / 2)
        return MatchResult(x=x, y=y, confidence=best_score, template_name=reaction_name), best_score, roi_box
