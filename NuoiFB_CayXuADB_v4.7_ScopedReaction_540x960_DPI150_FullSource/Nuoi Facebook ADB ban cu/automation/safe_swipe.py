"""Read-only UI observations used by the Nuoi FB swipe gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import re
import time
from xml.etree import ElementTree

import numpy as np


_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


@dataclass
class SwipeObservation:
    state: str
    image: np.ndarray | None = None
    anchors: dict[str, int] = field(default_factory=dict)
    selected_tab: int | None = None
    reel_identity: str = ""
    observed_at: float = field(default_factory=lambda: time.monotonic())


def _bounds(node):
    match = _BOUNDS.fullmatch(node.attrib.get("bounds", ""))
    return tuple(map(int, match.groups())) if match else None


def observe_swipe_ui(device, package: str) -> SwipeObservation:
    """Require structural UI evidence; moving video pixels never prove a scroll."""
    if device.get_state() != "device":
        return SwipeObservation("ADB_ERROR")
    if not device.is_package_foreground(package):
        return SwipeObservation("SCREEN_UNKNOWN")
    image = device.capture_screen_image()
    if image is None or image.ndim != 3 or image.size == 0:
        return SwipeObservation("SCREEN_UNKNOWN")
    xml = device.dump_ui_xml_text()
    if not xml:
        return SwipeObservation("SCREEN_UNKNOWN", image)
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return SwipeObservation("SCREEN_UNKNOWN", image)

    height, width = image.shape[:2]
    nodes = list(root.iter("node"))
    if not nodes or any(n.attrib.get("package") not in (None, "", package) for n in nodes):
        return SwipeObservation("SCREEN_UNKNOWN", image)

    nav = []
    for node in nodes:
        box = _bounds(node)
        if (box and node.attrib.get("class", "").endswith(".View")
                and 0.04 * height <= box[1] < 0.16 * height
                and 0.10 * width <= box[2] - box[0] <= 0.22 * width):
            nav.append(node)
    nav.sort(key=lambda node: _bounds(node)[0])
    selected = [i for i, node in enumerate(nav) if node.attrib.get("selected") == "true"]
    selected_tab = selected[0] if len(nav) >= 4 and len(selected) == 1 else None

    lists = []
    for node in nodes:
        box = _bounds(node)
        if (node.attrib.get("class", "").endswith("RecyclerView")
                and node.attrib.get("scrollable") == "true" and box
                and box[2] - box[0] >= 0.8 * width
                and box[3] - box[1] >= 0.65 * height):
            lists.append(node)
    feed_list = max(lists, key=lambda node: _bounds(node)[3] - _bounds(node)[1]) if lists else None

    anchors = {}
    if feed_list is not None:
        feed_box = _bounds(feed_list)
        occurrences = {}
        for node in feed_list.iter("node"):
            box = _bounds(node)
            label = (node.attrib.get("text", "") + "|" + node.attrib.get("content-desc", "")).strip("|")
            if (box and len(label.strip()) >= 8
                    and box[0] < box[2] and box[1] < box[3]
                    and box[0] < width and box[2] > 0
                    and box[1] < height and box[3] > 0
                    and box[0] < feed_box[2] and box[2] > feed_box[0]
                    and box[1] < feed_box[3] and box[3] > feed_box[1]):
                key = sha256((node.attrib.get("class", "") + "|" + label).encode()).hexdigest()
                occurrences.setdefault(key, []).append(box[1])
        anchors = {key: positions[0] for key, positions in occurrences.items() if len(positions) == 1}

    if any(node.attrib.get("class", "").endswith("ProgressBar") for node in nodes):
        state = "UI_STILL_LOADING"
    elif any(node.attrib.get("class", "").endswith(("Dialog", "PopupWindow")) for node in nodes):
        state = "SCREEN_UNKNOWN"
    elif feed_list is not None and selected_tab in (None, 0) and len(anchors) >= 2:
        state = "READY_FEED"
    elif selected_tab == 1 and feed_list is None:
        state = "READY_REEL"
    else:
        labels = " ".join(
            (node.attrib.get("text", "") + " " + node.attrib.get("content-desc", "")).casefold()
            for node in nodes
        )
        has_video = any(word in labels for word in ("video", "reel", "watch"))
        has_back = any(word in labels for word in ("back", "quay lại", "đóng", "close"))
        state = "VIDEO_VIEWER_OPENED" if feed_list is None and selected_tab is None and has_video and has_back else "SCREEN_UNKNOWN"
    reel_labels = []
    if state == "READY_REEL":
        for node in nodes:
            box = _bounds(node)
            label = (node.attrib.get("text", "") + " " + node.attrib.get("content-desc", "")).strip()
            if box and box[0] < 0.7 * width and box[1] > 0.18 * height and len(label) >= 12:
                reel_labels.append(label)
    reel_identity = sha256("|".join(sorted(reel_labels)).encode()).hexdigest() if reel_labels else ""
    return SwipeObservation(state, image, anchors, selected_tab, reel_identity)


def verified_feed_movement(before: SwipeObservation, after: SwipeObservation, direction: int) -> bool:
    """Two stable content anchors must move together in the swipe direction."""
    if before.state != "READY_FEED" or after.state != "READY_FEED":
        return False
    shifts = [after.anchors[key] - old_y for key, old_y in before.anchors.items() if key in after.anchors]
    shifts = [shift for shift in shifts if shift * direction >= 55]
    return any(abs(a - b) <= 45 for i, a in enumerate(shifts) for b in shifts[i + 1:])


def verified_reel_transition(before: SwipeObservation, after: SwipeObservation) -> bool:
    return (before.state == after.state == "READY_REEL" and bool(before.reel_identity)
            and bool(after.reel_identity) and before.reel_identity != after.reel_identity)
