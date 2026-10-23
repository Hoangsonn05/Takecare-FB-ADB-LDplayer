from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from core.image_io import imread_unicode


@dataclass
class NotificationRow:
    x: int
    y: int
    box: tuple[int, int, int, int]
    unread: bool
    score: float = 0.0


class NotificationDetector:
    def detect_rows(self, screenshot_path: str | Path) -> list[NotificationRow]:
        img = imread_unicode(screenshot_path, cv2.IMREAD_COLOR)
        if img is None:
            return []

        h, w = img.shape[:2]
        y_start = int(h * 0.16)
        y_end = int(h * 0.95)
        if y_end <= y_start:
            return []

        unread_bands = self._detect_unread_bands(img, y_start, y_end)
        row_centers = self._detect_menu_centers(img, y_start, y_end)

        rows = self._merge_rows(w, h, unread_bands, row_centers)
        if rows:
            return rows
        return self._fallback_rows(w, h, y_start)

    def _detect_unread_bands(
        self,
        img: np.ndarray,
        y_start: int,
        y_end: int,
    ) -> list[tuple[int, int, float]]:
        region = img[y_start:y_end, :]
        b, g, r = cv2.split(region)
        blue_mask = (b > 210) & (g > 210) & (r > 180) & ((b.astype(int) - r.astype(int)) > 8)
        row_score = blue_mask.mean(axis=1)
        return self._bands_from_projection(row_score, y_start, threshold=0.18, min_height=18, merge_gap=18)

    def _detect_menu_centers(self, img: np.ndarray, y_start: int, y_end: int) -> list[int]:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        h, w = gray.shape[:2]
        x1 = int(w * 0.88)
        x2 = int(w * 0.99)
        right_roi = gray[y_start:y_end, x1:x2]
        dark = right_roi < 80
        proj = dark.mean(axis=1)
        bands = self._bands_from_projection(proj, y_start, threshold=0.015, min_height=2, merge_gap=18)

        centers: list[int] = []
        for y1, y2, _score in bands:
            center = int((y1 + y2) / 2)
            if int(h * 0.18) <= center <= int(h * 0.94):
                centers.append(center)
        return self._dedupe_centers(centers, min_gap=45)

    @staticmethod
    def _bands_from_projection(
        projection: np.ndarray,
        offset_y: int,
        threshold: float,
        min_height: int,
        merge_gap: int,
    ) -> list[tuple[int, int, float]]:
        raw: list[tuple[int, int, float]] = []
        start: int | None = None
        for i, value in enumerate(projection):
            if float(value) > threshold:
                if start is None:
                    start = i
            elif start is not None:
                if i - start >= min_height:
                    raw.append((offset_y + start, offset_y + i, float(projection[start:i].mean())))
                start = None
        if start is not None and len(projection) - start >= min_height:
            raw.append((offset_y + start, offset_y + len(projection), float(projection[start:].mean())))

        if not raw:
            return []

        merged: list[tuple[int, int, float]] = []
        cur_y1, cur_y2, cur_score = raw[0]
        for y1, y2, score in raw[1:]:
            if y1 - cur_y2 <= merge_gap:
                cur_score = max(cur_score, score)
                cur_y2 = y2
            else:
                merged.append((cur_y1, cur_y2, cur_score))
                cur_y1, cur_y2, cur_score = y1, y2, score
        merged.append((cur_y1, cur_y2, cur_score))
        return merged

    def _merge_rows(
        self,
        w: int,
        h: int,
        unread_bands: list[tuple[int, int, float]],
        row_centers: list[int],
    ) -> list[NotificationRow]:
        centers = list(row_centers)
        for y1, y2, _score in unread_bands:
            center = int((y1 + y2) / 2)
            if not any(abs(center - existing) <= 35 for existing in centers):
                centers.append(center)
        centers = self._dedupe_centers(sorted(centers), min_gap=45)

        rows: list[NotificationRow] = []
        for center in centers:
            if center < int(h * 0.17) or center > int(h * 0.94):
                continue
            unread = False
            score = 0.0
            box_y1 = max(0, center - 38)
            box_y2 = min(h, center + 38)
            for y1, y2, band_score in unread_bands:
                if abs(center - int((y1 + y2) / 2)) <= 35 or (y1 <= center <= y2):
                    unread = True
                    score = max(score, band_score)
                    box_y1 = max(0, min(box_y1, y1))
                    box_y2 = min(h, max(box_y2, y2))
            rows.append(
                NotificationRow(
                    x=int(w * 0.50),
                    y=int(center),
                    box=(0, int(box_y1), w, int(box_y2)),
                    unread=unread,
                    score=score,
                )
            )
        return sorted(rows, key=lambda row: row.y)

    @staticmethod
    def _dedupe_centers(centers: list[int], min_gap: int) -> list[int]:
        deduped: list[int] = []
        for center in sorted(centers):
            if not deduped or center - deduped[-1] >= min_gap:
                deduped.append(center)
        return deduped

    @staticmethod
    def _fallback_rows(w: int, h: int, y_start: int) -> list[NotificationRow]:
        rows: list[NotificationRow] = []
        step = 88
        y = y_start + 60
        while y < h - 60:
            rows.append(
                NotificationRow(
                    x=int(w * 0.50),
                    y=int(y),
                    box=(0, max(0, y - 38), w, min(h, y + 38)),
                    unread=False,
                    score=0.0,
                )
            )
            y += step
        return rows
