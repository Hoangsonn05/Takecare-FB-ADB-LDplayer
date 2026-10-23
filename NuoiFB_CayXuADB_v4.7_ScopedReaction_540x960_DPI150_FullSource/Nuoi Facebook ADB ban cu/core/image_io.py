from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np


def imread_unicode(path: str | Path, flags: int = cv2.IMREAD_COLOR):
    """
    Unicode-safe image reader for Windows paths containing Vietnamese/non-ASCII chars.
    Do not use cv2.imread(str(path)) directly in this project.
    """
    p = Path(path)
    if not p.exists() or p.stat().st_size <= 0:
        return None

    data = np.fromfile(str(p), dtype=np.uint8)
    if data.size == 0:
        return None

    return cv2.imdecode(data, flags)


def image_is_readable(path: str | Path) -> bool:
    img = imread_unicode(path, cv2.IMREAD_COLOR)
    return img is not None
