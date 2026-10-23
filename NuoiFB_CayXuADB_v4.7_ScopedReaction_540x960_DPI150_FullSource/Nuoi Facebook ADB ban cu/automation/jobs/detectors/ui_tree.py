from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata
import xml.etree.ElementTree as ET

_BOUNDS = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def normalize_label(value: str) -> str:
    text = unicodedata.normalize("NFD", str(value or "").casefold())
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    return " ".join(text.split())


_ACTION_TERMS = {
    "reaction": ("like", "thich", "react", "bay to cam xuc", "cam xuc"),
    "comment": ("comment", "binh luan"),
    "share": ("share", "chia se"),
    "save": ("save", "luu"),
    "more": ("more", "khac"),
}
_LEADING_COUNT = re.compile(r"^[\s\d.,]+(?:k|m|tr|nghin|trieu)?\b")


def _contains_term(label: str, term: str) -> bool:
    if not label or not term:
        return False
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", label) is not None


@dataclass(slots=True, frozen=True)
class UiNode:
    text: str
    content_desc: str
    resource_id: str
    bounds: tuple[int, int, int, int]
    selected: bool
    checked: bool
    clickable: bool
    source_bounds: tuple[int, int, int, int] = (0, 0, 0, 0)
    depth: int = 0
    element_id: int = -1
    parent_id: int = -1
    tap_element_id: int = -1

    @property
    def center(self) -> tuple[int, int]:
        x1, y1, x2, y2 = self.bounds
        return int((x1 + x2) / 2), int((y1 + y2) / 2)

    @property
    def label(self) -> str:
        return " ".join(
            item for item in (self.text, self.content_desc, self.resource_id) if item
        )

    @property
    def width(self) -> int:
        return max(0, self.bounds[2] - self.bounds[0])

    @property
    def height(self) -> int:
        return max(0, self.bounds[3] - self.bounds[1])

    @property
    def area(self) -> int:
        return self.width * self.height


@dataclass(slots=True, frozen=True)
class ActionLayout:
    reaction: UiNode
    comment: UiNode
    share: UiNode

    @property
    def labels(self) -> list[str]:
        return [self.comment.label, self.share.label]

    @property
    def cluster_bounds(self) -> tuple[int, int, int, int]:
        boxes = (self.reaction.bounds, self.comment.bounds, self.share.bounds)
        return (
            min(box[0] for box in boxes),
            min(box[1] for box in boxes),
            max(box[2] for box in boxes),
            max(box[3] for box in boxes),
        )


@dataclass(slots=True, frozen=True)
class _ClickableContext:
    bounds: tuple[int, int, int, int]
    selected: bool
    checked: bool
    element_id: int


class UiTree:
    def __init__(
        self,
        nodes: list[UiNode],
        *,
        parent_by_id: dict[int, int] | None = None,
        bounds_by_id: dict[int, tuple[int, int, int, int]] | None = None,
    ) -> None:
        self.nodes = nodes
        self.parent_by_id = parent_by_id or {}
        self.bounds_by_id = bounds_by_id or {}

    @classmethod
    def parse(cls, xml_text: str) -> "UiTree":
        if not str(xml_text or "").strip():
            return cls([])
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return cls([])

        nodes: list[UiNode] = []
        parent_by_id: dict[int, int] = {}
        bounds_by_id: dict[int, tuple[int, int, int, int]] = {}
        next_id = 0

        def walk(
            element: ET.Element,
            clickable_ancestor: _ClickableContext | None,
            depth: int,
            parent_id: int,
        ) -> None:
            nonlocal next_id
            element_id = next_id
            next_id += 1
            parent_by_id[element_id] = parent_id
            match = _BOUNDS.fullmatch(element.attrib.get("bounds", ""))
            own_bounds = (
                tuple(int(value) for value in match.groups())
                if match is not None
                else None
            )
            own_selected = element.attrib.get("selected") == "true"
            own_checked = element.attrib.get("checked") == "true"
            own_clickable = element.attrib.get("clickable") == "true"
            if own_bounds is not None:
                bounds_by_id[element_id] = own_bounds

            effective_clickable = clickable_ancestor
            if own_clickable and own_bounds is not None:
                effective_clickable = _ClickableContext(
                    own_bounds,
                    own_selected,
                    own_checked,
                    element_id,
                )

            if own_bounds is not None:
                tap_bounds = (
                    effective_clickable.bounds
                    if effective_clickable is not None
                    else own_bounds
                )
                nodes.append(
                    UiNode(
                        text=element.attrib.get("text", ""),
                        content_desc=element.attrib.get("content-desc", ""),
                        resource_id=element.attrib.get("resource-id", ""),
                        bounds=tap_bounds,
                        selected=own_selected
                        or bool(effective_clickable and effective_clickable.selected),
                        checked=own_checked
                        or bool(effective_clickable and effective_clickable.checked),
                        clickable=effective_clickable is not None,
                        source_bounds=own_bounds,
                        depth=depth,
                        element_id=element_id,
                        parent_id=parent_id,
                        tap_element_id=(
                            effective_clickable.element_id
                            if effective_clickable is not None
                            else -1
                        ),
                    )
                )

            for child in element:
                walk(child, effective_clickable, depth + 1, element_id)

        walk(root, None, 0, -1)
        return cls(nodes, parent_by_id=parent_by_id, bounds_by_id=bounds_by_id)

    @staticmethod
    def _valid_tap_node(node: UiNode, width: int, height: int, vertical: bool) -> bool:
        if not node.clickable or node.width < 8 or node.height < 8:
            return False
        if node.bounds[0] < 0 or node.bounds[1] < 0:
            return False
        if vertical:
            return node.width <= width * 0.34 and node.height <= max(150, height * 0.18)
        return node.width <= width * 0.48 and node.height <= max(120, height * 0.16)

    def find_any(self, labels: set[str]) -> list[UiNode]:
        normalized = {normalize_label(label) for label in labels}
        found: list[UiNode] = []
        for node in self.nodes:
            haystack = normalize_label(node.label)
            if any(_contains_term(haystack, label) for label in normalized if label):
                found.append(node)
        return found

    def action_nodes(self, action: str, *, allow_counts: bool) -> list[UiNode]:
        terms = _ACTION_TERMS.get(str(action or "").casefold())
        if not terms:
            return []

        best_by_tap_bounds: dict[tuple[int, int, int, int], UiNode] = {}
        for node in self.nodes:
            label = normalize_label(node.label)
            if not any(_contains_term(label, term) for term in terms):
                continue
            if not allow_counts and _LEADING_COUNT.match(label):
                continue
            current = best_by_tap_bounds.get(node.bounds)
            if current is None:
                best_by_tap_bounds[node.bounds] = node
                continue
            current_score = (
                current.clickable,
                current.selected or current.checked,
                -current.source_bounds[2] + current.source_bounds[0],
                current.depth,
            )
            candidate_score = (
                node.clickable,
                node.selected or node.checked,
                -node.source_bounds[2] + node.source_bounds[0],
                node.depth,
            )
            if candidate_score > current_score:
                best_by_tap_bounds[node.bounds] = node

        return list(best_by_tap_bounds.values())

    def find_action_layout(
        self,
        orientation: str,
        *,
        screen_width: int,
        screen_height: int,
    ) -> ActionLayout | None:
        """Return a semantic Like/Comment/Share layout using relative geometry.

        Label children are mapped to their nearest clickable ancestor during
        parsing, so the returned coordinate always belongs to an actionable
        node rather than a text or counter node.
        """

        width = max(1, int(screen_width))
        height = max(1, int(screen_height))
        vertical = str(orientation).casefold() == "vertical"
        allow_counts = vertical
        reactions = self.action_nodes("reaction", allow_counts=allow_counts)
        comments = self.action_nodes("comment", allow_counts=allow_counts)
        shares = self.action_nodes("share", allow_counts=allow_counts)
        candidates: list[ActionLayout] = []

        for reaction in reactions:
            if not self._valid_tap_node(reaction, width, height, vertical):
                continue
            rx, ry = reaction.center
            if vertical:
                if rx < width * 0.62:
                    continue
                for comment in comments:
                    if not self._valid_tap_node(comment, width, height, vertical) or comment.bounds == reaction.bounds:
                        continue
                    cx, cy = comment.center
                    if cy <= ry or abs(cx - rx) > width * 0.12:
                        continue
                    for share in shares:
                        if not self._valid_tap_node(share, width, height, vertical):
                            continue
                        if share.bounds in {reaction.bounds, comment.bounds}:
                            continue
                        sx, sy = share.center
                        if sy <= cy or abs(sx - rx) > width * 0.12:
                            continue
                        first_gap = cy - ry
                        second_gap = sy - cy
                        if first_gap < height * 0.035 or second_gap < height * 0.035:
                            continue
                        if max(first_gap, second_gap) / max(
                            1.0, min(first_gap, second_gap)
                        ) > 2.20:
                            continue
                        candidates.append(ActionLayout(reaction, comment, share))
            else:
                if rx > width * 0.45:
                    continue
                for comment in comments:
                    if not self._valid_tap_node(comment, width, height, vertical) or comment.bounds == reaction.bounds:
                        continue
                    cx, cy = comment.center
                    row_tolerance = max(
                        height * 0.025,
                        (reaction.height + comment.height) * 0.40,
                    )
                    if cx <= rx or abs(cy - ry) > row_tolerance:
                        continue
                    for share in shares:
                        if not self._valid_tap_node(share, width, height, vertical):
                            continue
                        if share.bounds in {reaction.bounds, comment.bounds}:
                            continue
                        sx, sy = share.center
                        share_tolerance = max(
                            height * 0.025,
                            (reaction.height + share.height) * 0.40,
                        )
                        if sx <= cx or abs(sy - ry) > share_tolerance:
                            continue
                        if sx - rx < width * 0.42:
                            continue
                        first_gap = cx - rx
                        second_gap = sx - cx
                        if max(first_gap, second_gap) / max(
                            1.0, min(first_gap, second_gap)
                        ) > 4.00:
                            continue
                        candidates.append(ActionLayout(reaction, comment, share))

        if not candidates:
            return None
        if vertical:
            return min(
                candidates,
                key=lambda layout: (
                    layout.reaction.center[1],
                    abs(layout.comment.center[0] - layout.reaction.center[0])
                    + abs(layout.share.center[0] - layout.reaction.center[0]),
                    layout.reaction.area,
                ),
            )
        return min(
            candidates,
            key=lambda layout: (
                abs(layout.reaction.center[1] - height * 0.55),
                abs(layout.comment.center[1] - layout.reaction.center[1])
                + abs(layout.share.center[1] - layout.reaction.center[1]),
                layout.reaction.area,
            ),
        )

    def common_ancestor_bounds(self, nodes: list[UiNode]) -> tuple[int, int, int, int]:
        ids = [node.tap_element_id for node in nodes if node.tap_element_id >= 0]
        if not ids:
            return (0, 0, 0, 0)

        def lineage(element_id: int) -> list[int]:
            result = []
            current = element_id
            while current >= 0 and current not in result:
                result.append(current)
                current = self.parent_by_id.get(current, -1)
            return result

        first_lineage = lineage(ids[0])
        other_sets = [set(lineage(item)) for item in ids[1:]]
        for candidate in first_lineage:
            if all(candidate in values for values in other_sets):
                return self.bounds_by_id.get(candidate, (0, 0, 0, 0))
        return (0, 0, 0, 0)

    def nodes_in_bounds(
        self,
        bounds: tuple[int, int, int, int],
        *,
        require_clickable: bool = False,
    ) -> list[UiNode]:
        x1, y1, x2, y2 = bounds
        if x2 <= x1 or y2 <= y1:
            return []
        result = []
        for node in self.nodes:
            cx, cy = node.center
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                if not require_clickable or node.clickable:
                    result.append(node)
        return result

    def scope_identity_tokens(
        self,
        bounds: tuple[int, int, int, int],
        *,
        limit: int = 16,
    ) -> list[str]:
        """Return stable, non-empty semantic tokens near an action scope."""

        tokens: list[str] = []
        action_terms = {term for values in _ACTION_TERMS.values() for term in values}
        for node in self.nodes_in_bounds(bounds):
            for raw in (node.resource_id, node.content_desc, node.text):
                value = normalize_label(raw)
                if not value or _LEADING_COUNT.match(value):
                    continue
                if any(_contains_term(value, term) for term in action_terms):
                    continue
                value = value[:80]
                if value not in tokens:
                    tokens.append(value)
                if len(tokens) >= max(1, int(limit)):
                    return tokens
        return tokens
