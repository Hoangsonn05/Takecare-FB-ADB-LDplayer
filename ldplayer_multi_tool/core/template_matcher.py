from __future__ import annotations

from pathlib import Path

from core.config import REACTION_TEMPLATE_FILES
from core.paths import app_path


class TemplateMatcher:
    def __init__(self, template_dir: str, confidence_like: float, confidence_reaction: float):
        try:
            import cv2
        except ImportError as exc:
            raise RuntimeError("OpenCV is required for template matching. Install opencv-python.") from exc

        self.cv2 = cv2
        self.template_dir = app_path(template_dir)
        self.confidence_like = float(confidence_like)
        self.confidence_reaction = float(confidence_reaction)
        self._cache: dict[str, object] = {}

    def _load_template(self, name: str):
        if name not in REACTION_TEMPLATE_FILES:
            raise ValueError(f"Unknown template name: {name}")
        if name in self._cache:
            return self._cache[name]
        path = self.template_dir / REACTION_TEMPLATE_FILES[name]
        if not path.exists():
            raise FileNotFoundError(f"Template file not found: {path}")
        template = self.cv2.imread(str(path), self.cv2.IMREAD_COLOR)
        if template is None:
            raise ValueError(f"OpenCV could not read template: {path}")
        self._cache[name] = template
        return template

    def _find(self, screenshot_path: str, template_name: str, threshold: float) -> tuple[int, int] | None:
        screenshot = self.cv2.imread(str(screenshot_path), self.cv2.IMREAD_COLOR)
        if screenshot is None:
            raise ValueError(f"OpenCV could not read screenshot: {screenshot_path}")
        template = self._load_template(template_name)
        result = self.cv2.matchTemplate(screenshot, template, self.cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = self.cv2.minMaxLoc(result)
        if max_val < threshold:
            return None
        height, width = template.shape[:2]
        return max_loc[0] + width // 2, max_loc[1] + height // 2

    def find_like_button(self, screenshot_path: str) -> tuple[int, int] | None:
        return self._find(screenshot_path, "like", self.confidence_like)

    def find_reaction(self, screenshot_path: str, reaction_name: str) -> tuple[int, int] | None:
        return self._find(screenshot_path, reaction_name, self.confidence_reaction)
