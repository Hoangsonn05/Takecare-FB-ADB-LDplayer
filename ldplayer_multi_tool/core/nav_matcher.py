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

    def _load_template(self, filename: str | Path):
        key = str(filename)
        if key in self._cache:
            return self._cache[key]
        path = Path(filename)
        if not path.is_absolute():
            path = self._resolve_template_file(str(filename))
        if not path.exists():
            raise FileNotFoundError(f"Missing nav template: {path}")
        img = imread_unicode(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise ValueError(f"Cannot read nav template: {path}")
        self._cache[key] = img
        return img

    def _resolve_template_file(self, filename: str) -> Path:
        path = self.template_dir / filename
        if path.exists():
            return path
        stem = Path(filename).stem
        for ext in (".png", ".jpg", ".jpeg"):
            for candidate in (self.template_dir / f"{stem}{ext}", self.template_dir / f"{filename}{ext}"):
                if candidate.exists():
                    return candidate
        return path

    def _candidate_template_paths(self, filename: str) -> list[Path]:
        paths: list[Path] = []
        primary = self._resolve_template_file(filename)
        if primary.exists():
            paths.append(primary)

        stem = Path(filename).stem
        if stem == "thongbao":
            for ext in (".png", ".jpg", ".jpeg"):
                candidate = self.template_dir / f"thongbaokhioreel{ext}"
                if candidate.exists() and candidate not in paths:
                    paths.append(candidate)
        return paths

    @staticmethod
    def _edge_image(gray):
        blurred = cv2.GaussianBlur(gray, (3, 3), 0)
        return cv2.Canny(blurred, 50, 150)

    @staticmethod
    def _best_template_match(roi, template) -> tuple[float, tuple[int, int] | None, tuple[int, int] | None]:
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
        return best_score, best_loc, best_size

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

        best_score: float = -1.0
        best_loc = None
        best_size = None
        best_template_name = filename

        templates = self._candidate_template_paths(filename)
        if not templates:
            templates = [self._resolve_template_file(filename)]

        for template_path in templates:
            template = self._load_template(template_path)
            score, loc, size = self._best_template_match(roi, template)
            if score > best_score:
                best_score = score
                best_loc = loc
                best_size = size
                best_template_name = template_path.name

        # Fallback theo hinh dang/canh de chiu duoc nen sang/toi cua Reel.
        edge_threshold = max(0.62, self.threshold - 0.10)
        if best_score < self.threshold:
            roi_edges = self._edge_image(roi)
            for template_path in templates:
                template = self._load_template(template_path)
                template_edges = self._edge_image(template)
                score, loc, size = self._best_template_match(roi_edges, template_edges)
                if score > best_score:
                    best_score = score
                    best_loc = loc
                    best_size = size
                    best_template_name = f"{template_path.name}:edge"

        threshold = edge_threshold if ":edge" in best_template_name else self.threshold
        if best_loc is None or best_size is None or best_score < threshold:
            return None, best_score

        tw, th = best_size
        cx = int(roi_x1 + best_loc[0] + tw / 2)
        cy = int(roi_y1 + best_loc[1] + th / 2)

        return NavMatchResult(
            x=cx,
            y=cy,
            confidence=best_score,
            template_name=best_template_name,
            roi=roi_box,
        ), best_score
