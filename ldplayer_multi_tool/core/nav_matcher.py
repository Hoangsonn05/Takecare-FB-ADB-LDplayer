from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2

from core.image_io import imread_unicode


@dataclass
class NavMatchResult:
    x: int
    y: int
    confidence: float
    template_name: str
    roi: tuple[int, int, int, int]


class NavMatcher:
    """
    Tim icon dieu huong tren thanh top-nav cua Facebook bang template matching.
    Chi quet vung top 25% man hinh (noi chua thanh nav).
    Khong dung chung voi TemplateMatcher reaction.
    """

    def __init__(self, template_dir: str | Path, threshold: float = 0.70):
        self.template_dir = Path(template_dir)
        self.threshold = float(threshold)
        self._cache: dict[str, object] = {}

    def _load_template(self, filename: str):
        if filename in self._cache:
            return self._cache[filename]
        path = self.template_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"Missing nav template: {path}")
        img = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError(f"Cannot read nav template: {path}")
        self._cache[filename] = img
        return img

    def find_top_nav_icon(
        self,
        screenshot_path: str | Path,
        filename: str,
    ) -> tuple[NavMatchResult | None, float]:
        """
        Tim icon <filename> trong vung top 25% man hinh.
        Tra ve (NavMatchResult, best_score).
        Tra (None, best_score) neu khong du confidence.
        """
        screen = imread_unicode(screenshot_path, cv2.IMREAD_GRAYSCALE)
        if screen is None:
            raise ValueError(f"Cannot read screenshot: {screenshot_path}")

        h, w = screen.shape[:2]

        # Chi quet top nav — 25% tren cung
        roi_x1, roi_y1 = 0, 0
        roi_x2, roi_y2 = w, int(h * 0.25)
        roi_box = (roi_x1, roi_y1, roi_x2, roi_y2)
        roi = screen[roi_y1:roi_y2, roi_x1:roi_x2]

        template = self._load_template(filename)

        best_score: float = -1.0
        best_loc = None
        best_size = None

        for scale in (0.70, 0.80, 0.90, 1.00, 1.10, 1.20, 1.30):
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

        if best_loc is None or best_size is None or best_score < self.threshold:
            return None, best_score

        tw, th = best_size
        cx = int(roi_x1 + best_loc[0] + tw / 2)
        cy = int(roi_y1 + best_loc[1] + th / 2)

        return NavMatchResult(
            x=cx,
            y=cy,
            confidence=best_score,
            template_name=filename,
            roi=roi_box,
        ), best_score
