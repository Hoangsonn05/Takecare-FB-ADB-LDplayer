from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
import time

import cv2
import numpy as np

from .detectors.common import LocateResult
from .detectors.reaction_resolver import (
    REACTION_LABELS,
    ReactionState,
    ReactionStateResolver,
    ReactionStateStatus,
)
from .models import ContentKind


class ActionVerificationStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    REACTION_TYPE_MISMATCH = "REACTION_TYPE_MISMATCH"
    UNKNOWN = "UNKNOWN"


@dataclass(slots=True)
class ActionVerification:
    status: ActionVerificationStatus
    confidence: float
    evidence: list[str]
    after_image: object = None
    after_ui_xml: str = ""
    requested_reaction: str = ""
    detected_active_reaction: str = ""

    @property
    def confirmed(self) -> bool:
        return self.status is ActionVerificationStatus.CONFIRMED


def coordinates_stable(
    first: tuple[int, int],
    second: tuple[int, int],
    tolerance_px: int,
) -> bool:
    dx = int(first[0]) - int(second[0])
    dy = int(first[1]) - int(second[1])
    return dx * dx + dy * dy <= int(tolerance_px) ** 2


class ActionVerifier:
    def __init__(self, state_resolver: ReactionStateResolver | None = None) -> None:
        self.state_resolver = state_resolver or ReactionStateResolver()

    def read_state(self, ui_xml: str, anchor: LocateResult) -> ReactionState:
        return self.state_resolver.read(ui_xml, anchor)

    @staticmethod
    def _gray(image):
        if image is None:
            return None
        return image if len(image.shape) == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    @classmethod
    def _change_metrics(cls, before, after, bounds) -> tuple[float, float, float]:
        first = cls._gray(before)
        second = cls._gray(after)
        if first is None or second is None or first.shape != second.shape:
            return 0.0, 1.0, 1.0
        height, width = first.shape[:2]
        x1, y1, x2, y2 = bounds
        x1, x2 = max(0, x1), min(width, x2)
        y1, y2 = max(0, y1), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            return 0.0, 0.0, 0.0
        diff = cv2.absdiff(first, second).astype(np.float32) / 255.0
        local = float(np.mean(diff[y1:y2, x1:x2]))
        global_change = float(np.mean(diff))
        mask = np.ones(diff.shape, dtype=bool)
        mask[y1:y2, x1:x2] = False
        outside = float(np.mean(diff[mask])) if np.any(mask) else 0.0
        return local, outside, global_change

    @staticmethod
    def _visual_reaction(image, anchor: LocateResult, panel_locator) -> tuple[str, float, list[str]]:
        matcher = getattr(panel_locator, "matcher", None)
        if image is None or matcher is None:
            return "", 0.0, []
        roi = anchor.reaction_bounds or anchor.roi
        scores: list[tuple[float, str]] = []
        for reaction in REACTION_LABELS:
            try:
                match, score = matcher.find_reaction_in_roi_image(
                    image,
                    reaction,
                    roi,
                    threshold=0.0,
                )
            except Exception:
                match, score = None, -1.0
            if match is not None:
                scores.append((float(score), reaction))
        scores.sort(reverse=True)
        if not scores:
            return "", 0.0, []
        best_score, best_reaction = scores[0]
        runner_up = scores[1][0] if len(scores) > 1 else -1.0
        evidence = [f"visual_best={best_reaction}:{best_score:.3f}", f"visual_runner_up={runner_up:.3f}"]
        if best_score < 0.76 or best_score - runner_up < 0.07:
            return "", best_score, evidence
        return best_reaction, best_score, evidence

    def verify(
        self,
        *,
        device,
        before_image,
        before_ui_xml: str,
        coordinate: tuple[int, int],
        content_kind: ContentKind,
        internal_action: str,
        timeout_sec: float,
        stop_event,
        panel_was_open: bool,
        panel_locator,
        like_location: LocateResult,
        settle: Callable[[float], object] | None = None,
        reacquire_action: Callable[[object, str], LocateResult | None] | None = None,
        coordinate_tolerance_px: int = 28,
        before_state: ReactionState | None = None,
    ) -> ActionVerification:
        """Confirm only when the active reaction equals the requested reaction."""

        settle_cb = settle or (lambda seconds: stop_event.wait(seconds))
        deadline = time.monotonic() + max(0.1, float(timeout_sec))
        observations = 0
        visual_streak = 0
        last_visual = ""
        last_image = None
        last_xml = ""
        best_local = 0.0
        best_outside = 0.0
        best_global = 0.0
        evidence = [f"requested={internal_action}"]
        if before_state is not None:
            evidence.extend(f"before:{item}" for item in before_state.evidence)

        while time.monotonic() < deadline and not stop_event.is_set():
            settle_cb(0.25)
            last_image = device.capture_screen_image()
            last_xml = device.dump_ui_xml_text()
            observations += 1

            current = None
            if reacquire_action is not None and last_image is not None:
                try:
                    current = reacquire_action(last_image, last_xml)
                except Exception:
                    current = None
            if current is None or not like_location.same_action_scope(
                current,
                coordinate_tolerance_px,
            ):
                evidence.append(f"observation_{observations}:scope_changed_or_missing")
                continue

            state = self.read_state(last_xml, current)
            evidence.extend(f"observation_{observations}:{item}" for item in state.evidence)
            if state.status is ReactionStateStatus.ACTIVE:
                if state.active_reaction == internal_action:
                    return ActionVerification(
                        ActionVerificationStatus.CONFIRMED,
                        state.confidence,
                        [*evidence, "strong_scoped_ui_match"],
                        last_image,
                        last_xml,
                        internal_action,
                        state.active_reaction,
                    )
                return ActionVerification(
                    ActionVerificationStatus.REACTION_TYPE_MISMATCH,
                    state.confidence,
                    [*evidence, f"actual={state.active_reaction}"],
                    last_image,
                    last_xml,
                    internal_action,
                    state.active_reaction,
                )

            local, outside, global_change = self._change_metrics(
                before_image,
                last_image,
                current.reaction_bounds or current.roi,
            )
            best_local = max(best_local, local)
            best_outside = max(best_outside, outside)
            best_global = max(best_global, global_change)
            visual, visual_confidence, visual_evidence = self._visual_reaction(
                last_image,
                current,
                panel_locator,
            )
            evidence.extend(f"observation_{observations}:{item}" for item in visual_evidence)
            localized = local >= 0.018 and outside <= max(0.025, local * 0.62)
            if visual and localized:
                visual_streak = visual_streak + 1 if visual == last_visual else 1
                last_visual = visual
                if visual_streak >= 2:
                    status = (
                        ActionVerificationStatus.CONFIRMED
                        if visual == internal_action
                        else ActionVerificationStatus.REACTION_TYPE_MISMATCH
                    )
                    return ActionVerification(
                        status,
                        min(0.94, visual_confidence),
                        [*evidence, "two_stable_scoped_visual_matches", f"actual={visual}"],
                        last_image,
                        last_xml,
                        internal_action,
                        visual,
                    )
            else:
                visual_streak = 0
                last_visual = ""

            if panel_was_open and panel_locator.panel_is_open(
                last_image,
                last_xml,
                content_kind=content_kind,
                like_location=current,
            ):
                evidence.append(f"observation_{observations}:panel_still_open")

        if observations == 0 or last_image is None:
            status = ActionVerificationStatus.UNKNOWN
            evidence.append("no_post_action_frame")
        else:
            status = ActionVerificationStatus.UNKNOWN
            evidence.extend(
                [
                    "requested_reaction_not_proven",
                    f"best_local_change={best_local:.3f}",
                    f"best_outside_change={best_outside:.3f}",
                    f"best_global_change={best_global:.3f}",
                    "pixel_change_is_diagnostic_only",
                ]
            )
        return ActionVerification(
            status,
            best_local,
            evidence,
            last_image,
            last_xml,
            internal_action,
            "",
        )
