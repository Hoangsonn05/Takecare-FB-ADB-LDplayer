from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from .common import LocateResult
from .ui_tree import UiNode, UiTree, normalize_label


REACTION_LABELS = {
    "like": {"like", "thich"},
    "love": {"love", "yeu thich"},
    "thuongthuong": {"care", "thuong thuong"},
    "haha": {"haha"},
    "wow": {"wow"},
    "sad": {"sad", "buon"},
    "angry": {"angry", "phan no"},
}

_COUNT_WORDS = ("nguoi", "luot", "people", "others", "reactions", "cam xuc")
_REMOVE_WORDS = ("remove", "unlike", "bo ", "go ", "da ")


def canonical_reaction(label: str, *, allow_state_phrase: bool = True) -> str:
    value = normalize_label(label)
    if not value:
        return ""
    if any(word in value for word in _COUNT_WORDS) and not any(
        word in value for word in _REMOVE_WORDS
    ):
        return ""
    matches = []
    for reaction, labels in REACTION_LABELS.items():
        for candidate in labels:
            if re.search(rf"(?<!\w){re.escape(candidate)}(?!\w)", value):
                matches.append((len(candidate), reaction))
    if not matches:
        return ""
    matches.sort(reverse=True)
    reaction = matches[0][1]
    if allow_state_phrase:
        return reaction
    exact = value.strip(" .,:;!()[]")
    return reaction if exact in REACTION_LABELS[reaction] else ""


class ReactionStateStatus(str, Enum):
    NONE = "NONE"
    ACTIVE = "ACTIVE"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True, frozen=True)
class ReactionState:
    status: ReactionStateStatus
    active_reaction: str = ""
    confidence: float = 0.0
    evidence: tuple[str, ...] = ()


class ReactionStateResolver:
    """Read reaction state only from the resolved anchor/action scope."""

    @staticmethod
    def _anchor_nodes(tree: UiTree, anchor: LocateResult) -> list[UiNode]:
        bounds = anchor.reaction_bounds or anchor.roi
        nodes = tree.nodes_in_bounds(bounds)
        scoped = []
        sx1, sy1, sx2, sy2 = anchor.scope_bounds or anchor.action_cluster_bounds
        for node in nodes:
            x, y = node.center
            if sx2 > sx1 and sy2 > sy1 and not (sx1 <= x <= sx2 and sy1 <= y <= sy2):
                continue
            scoped.append(node)
        return scoped

    def read(self, ui_xml: str, anchor: LocateResult) -> ReactionState:
        tree = UiTree.parse(ui_xml)
        nodes = self._anchor_nodes(tree, anchor)
        if not nodes:
            return ReactionState(
                ReactionStateStatus.UNKNOWN,
                confidence=0.0,
                evidence=("anchor_has_no_ui_nodes",),
            )

        inactive_anchor_seen = False
        for node in nodes:
            reaction = canonical_reaction(node.label)
            if not reaction:
                continue
            if node.selected or node.checked:
                return ReactionState(
                    ReactionStateStatus.ACTIVE,
                    reaction,
                    0.99,
                    (f"selected_or_checked:{reaction}", f"bounds={node.bounds}"),
                )
            if node.clickable and node.bounds == anchor.reaction_bounds:
                inactive_anchor_seen = True

        if inactive_anchor_seen:
            return ReactionState(
                ReactionStateStatus.NONE,
                confidence=0.96,
                evidence=("scoped_clickable_anchor_unselected",),
            )
        return ReactionState(
            ReactionStateStatus.UNKNOWN,
            confidence=0.20,
            evidence=("scoped_state_ambiguous",),
        )


__all__ = [
    "REACTION_LABELS",
    "ReactionState",
    "ReactionStateResolver",
    "ReactionStateStatus",
    "canonical_reaction",
]
