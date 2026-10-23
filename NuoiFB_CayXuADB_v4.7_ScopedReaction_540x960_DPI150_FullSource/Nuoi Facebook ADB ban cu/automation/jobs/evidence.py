from __future__ import annotations

import json
from pathlib import Path
import re
import time
import xml.etree.ElementTree as ET

import cv2

from core.paths import app_path

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


class FailureEvidenceStore:
    def __init__(self, directory: str = "screenshots/job_failures") -> None:
        self.directory = app_path(directory)

    def save(
        self,
        image,
        *,
        ld_index: int,
        job_id: str,
        label: str,
        retention: int,
    ) -> str:
        if image is None or retention <= 0:
            return ""
        self.directory.mkdir(parents=True, exist_ok=True)
        safe_job = _SAFE_NAME.sub("_", str(job_id or "unknown"))[:48]
        safe_label = _SAFE_NAME.sub("_", str(label or "failure"))[:32]
        path = self.directory / (
            f"ld_{int(ld_index)}_job_{safe_job}_{safe_label}_{int(time.time() * 1000)}.png"
        )
        ok, encoded = cv2.imencode(".png", image)
        if not ok:
            return ""
        encoded.tofile(str(path))
        self.prune(retention)
        return str(path)

    def save_timeline(
        self,
        timeline,
        *,
        ld_index: int,
        job_id: str,
        label: str,
        retention: int,
    ) -> str:
        if not timeline or retention <= 0:
            return ""
        self.directory.mkdir(parents=True, exist_ok=True)
        safe_job = _SAFE_NAME.sub("_", str(job_id or "unknown"))[:48]
        safe_label = _SAFE_NAME.sub("_", str(label or "failure"))[:32]
        path = self.directory / (
            f"ld_{int(ld_index)}_job_{safe_job}_{safe_label}_"
            f"{int(time.time() * 1000)}.json"
        )
        try:
            path.write_text(
                json.dumps(
                    {"timeline": list(timeline)},
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )
        except OSError:
            return ""
        self.prune(retention)
        return str(path)

    @staticmethod
    def _sanitized_xml(value: str) -> str:
        """Keep geometry/state while removing unrelated post/account text."""

        try:
            root = ET.fromstring(str(value or ""))
        except ET.ParseError:
            return ""
        allowed_words = (
            "like", "thích", "love", "yêu thích", "care", "thương thương",
            "haha", "wow", "sad", "buồn", "angry", "phẫn nộ", "comment",
            "bình luận", "share", "chia sẻ",
        )
        for element in root.iter():
            for attribute in ("text", "content-desc"):
                current = str(element.attrib.get(attribute, ""))
                folded = current.casefold()
                if current and not any(word in folded for word in allowed_words):
                    element.attrib[attribute] = ""
            for attribute in list(element.attrib):
                if attribute not in {
                    "text", "content-desc", "resource-id", "bounds", "clickable",
                    "selected", "checked", "enabled", "class", "package",
                }:
                    element.attrib.pop(attribute, None)
        return ET.tostring(root, encoding="unicode")

    def save_reaction_bundle(
        self,
        bundle: dict,
        *,
        ld_index: int,
        job_id: str,
        retention: int,
    ) -> str:
        if not bundle or retention <= 0:
            return ""
        safe_job = _SAFE_NAME.sub("_", str(job_id or "unknown"))[:48]
        folder = self.directory / (
            f"ld_{int(ld_index)}_job_{safe_job}_reaction_{int(time.time() * 1000)}"
        )
        folder.mkdir(parents=True, exist_ok=True)
        image_names = ("before", "after_long_press", "panel", "after_action")
        xml_names = ("before", "after_long_press", "panel", "after_action")
        json_names = ("resolution", "verification", "candidate_rejections")
        wrote = False
        for name in image_names:
            image = bundle.get(f"{name}_image")
            if image is None:
                continue
            ok, encoded = cv2.imencode(".png", image)
            if ok:
                encoded.tofile(str(folder / f"{name}.png"))
                wrote = True
        for name in xml_names:
            xml_text = self._sanitized_xml(bundle.get(f"{name}_xml", ""))
            if xml_text:
                (folder / f"{name}.xml").write_text(xml_text, encoding="utf-8")
                wrote = True
        for name in json_names:
            payload = bundle.get(name)
            if payload is None:
                continue
            (folder / f"{name}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                encoding="utf-8",
            )
            wrote = True
        bundles = sorted(
            (item for item in self.directory.glob("*_reaction_*") if item.is_dir()),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old in bundles[int(retention) :]:
            for child in old.iterdir():
                try:
                    child.unlink()
                except OSError:
                    pass
            try:
                old.rmdir()
            except OSError:
                pass
        return str(folder) if wrote else ""

    def prune(self, retention: int) -> None:
        if retention < 0 or not self.directory.exists():
            return
        for pattern in ("*.png", "*.json"):
            files = sorted(
                self.directory.glob(pattern),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
            for path in files[int(retention) :]:
                try:
                    path.unlink()
                except OSError:
                    pass
