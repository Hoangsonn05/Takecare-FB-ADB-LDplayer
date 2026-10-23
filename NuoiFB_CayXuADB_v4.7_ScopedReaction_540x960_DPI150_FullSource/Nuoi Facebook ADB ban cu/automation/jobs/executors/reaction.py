from __future__ import annotations

import time

from integrations.golike_api import JobAction

from ..action_verifier import (
    ActionVerificationStatus,
    ActionVerifier,
)
from ..config import JobAutomationConfig
from ..detectors.common import CapturedFrame, SearchResult, frame_hash
from ..detectors.feed_like import FeedLikeLocator
from ..detectors.reaction_panel import ReactionPanelLocator
from ..detectors.reel_like import ReelLikeLocator
from ..detectors.reaction_resolver import ReactionStateStatus
from ..failure_codes import FailureCode
from ..models import ContentKind, JobExecutionContext
from .base import ExecutorResult

_ACTION_MAP = {
    JobAction.LIKE: "like",
    JobAction.LOVE: "love",
    JobAction.CARE: "thuongthuong",
    JobAction.HAHA: "haha",
    JobAction.WOW: "wow",
    JobAction.SAD: "sad",
    JobAction.ANGRY: "angry",
}


def api_action_to_internal(action: JobAction) -> str:
    try:
        return _ACTION_MAP[action]
    except KeyError as exc:
        raise ValueError(f"Unsupported reaction action: {action.value}") from exc


class ReactionExecutor:
    def __init__(
        self,
        *,
        config: JobAutomationConfig,
        feed_locator: FeedLikeLocator,
        reel_locator: ReelLikeLocator,
        panel_locator: ReactionPanelLocator,
        verifier: ActionVerifier | None = None,
        sleep_interruptible=None,
    ) -> None:
        self.config = config
        self.feed_locator = feed_locator
        self.reel_locator = reel_locator
        self.panel_locator = panel_locator
        self.verifier = verifier or ActionVerifier()
        self.sleep_interruptible = sleep_interruptible

    def _sleep(self, stop_event, seconds: float) -> None:
        if self.sleep_interruptible:
            self.sleep_interruptible(seconds)
        else:
            stop_event.wait(max(0.0, float(seconds)))

    @staticmethod
    def _capture(device) -> CapturedFrame:
        image = device.capture_screen_image()
        return CapturedFrame(image, device.dump_ui_xml_text(), frame_hash(image))

    def _locate_like(self, context: JobExecutionContext, device, stop_event):
        if context.content_kind is ContentKind.FEED:
            return self.feed_locator.locate_with_scroll(
                device,
                max_swipes=self.config.feed_max_search_swipes,
                swipe_duration_ms=self.config.feed_search_swipe_duration_ms,
                stop_event=stop_event,
                capture=lambda: self._capture(device),
                settle=lambda seconds: self._sleep(stop_event, seconds),
            )
        if context.content_kind is ContentKind.REEL:
            frame = self._capture(device)
            return SearchResult(
                self.reel_locator.locate_once(frame.image, frame.ui_xml),
                frame,
                0,
            )
        return None

    def _reacquire(self, context: JobExecutionContext, frame: CapturedFrame):
        if context.content_kind is ContentKind.FEED:
            return self.feed_locator.locate_once(frame.image, frame.ui_xml)
        return self.reel_locator.locate_once(frame.image, frame.ui_xml)

    @staticmethod
    def _debug_bundle(
        before: CapturedFrame,
        resolution,
        *,
        panel_frame: CapturedFrame | None = None,
        after_image=None,
        after_xml: str = "",
        verification=None,
    ) -> dict:
        return {
            "before_image": before.image,
            "before_xml": before.ui_xml,
            "after_long_press_image": panel_frame.image if panel_frame else None,
            "after_long_press_xml": panel_frame.ui_xml if panel_frame else "",
            "panel_image": panel_frame.image if panel_frame else None,
            "panel_xml": panel_frame.ui_xml if panel_frame else "",
            "after_action_image": after_image,
            "after_action_xml": after_xml,
            "resolution": resolution.as_evidence() if resolution else {},
            "candidate_rejections": list(getattr(resolution, "candidate_evidence", []) or []),
            "verification": verification or {},
        }

    def execute(self, context: JobExecutionContext, device, stop_event) -> ExecutorResult:
        if context.action_verified:
            return ExecutorResult(
                False,
                False,
                FailureCode.ACTION_RESULT_UNKNOWN,
                "Action was already verified; refusing to execute it again",
            )
        if stop_event.is_set():
            return ExecutorResult(False, False, FailureCode.STOP_REQUESTED, "Stop requested")
        search = self._locate_like(context, device, stop_event)
        if search is None or search.location is None or search.frame is None:
            code = (
                FailureCode.REEL_LIKE_BUTTON_NOT_FOUND
                if context.content_kind is ContentKind.REEL
                else FailureCode.LIKE_BUTTON_NOT_FOUND_AFTER_SCROLL
            )
            image = search.frame.image if search and search.frame else None
            return ExecutorResult(
                False,
                False,
                code,
                "Like button was not found with verified geometry",
                {"image": image},
            )
        original = search.location
        fresh = self._capture(device)
        reacquired = self._reacquire(context, fresh)
        if reacquired is None or not original.same_action_scope(
            reacquired,
            self.config.stale_coordinate_tolerance_px,
        ):
            return ExecutorResult(
                False,
                False,
                FailureCode.ACTION_RESULT_UNKNOWN,
                "Reaction action scope changed before tap; action was not executed",
                {
                    "image": fresh.image,
                    "details": [
                        {"original_resolution": original.as_evidence()},
                        {"refreshed_resolution": reacquired.as_evidence() if reacquired else None},
                    ],
                },
            )
        context.detected_coordinate = (reacquired.x, reacquired.y)
        internal_action = api_action_to_internal(context.job.action)
        before_state = self.verifier.read_state(fresh.ui_xml, reacquired)
        if (
            before_state.status is ReactionStateStatus.ACTIVE
            and before_state.active_reaction == internal_action
        ):
            context.action_verified = True
            return ExecutorResult(
                True,
                True,
                message=f"Action {internal_action} already active",
                evidence={
                    "details": [
                        {"result": "ALREADY_ACTIVE"},
                        {"resolution": reacquired.as_evidence()},
                        {"pre_state": list(before_state.evidence)},
                    ]
                },
            )
        if before_state.status is ReactionStateStatus.UNKNOWN:
            return ExecutorResult(
                False,
                False,
                FailureCode.ACTION_RESULT_UNKNOWN,
                "Current reaction state is ambiguous; no tap was executed",
                {
                    "image": fresh.image,
                    "details": [
                        {"resolution": reacquired.as_evidence()},
                        {"pre_state": list(before_state.evidence)},
                    ],
                    "reaction_debug": self._debug_bundle(fresh, reacquired),
                },
            )

        panel_was_open = False
        needs_picker = internal_action != "like" or (
            before_state.status is ReactionStateStatus.ACTIVE
            and before_state.active_reaction != internal_action
        )
        if not needs_picker:
            command = device.tap(reacquired.x, reacquired.y)
            if not command.ok:
                return ExecutorResult(
                    False,
                    False,
                    FailureCode.ACTION_COMMAND_FAILED,
                    f"ADB tap failed: {command.brief()}",
                    {"image": fresh.image},
                )
        else:
            press = device.long_press(
                reacquired.x,
                reacquired.y,
                duration_ms=self.config.long_press_duration_ms,
            )
            if not press.ok:
                return ExecutorResult(
                    False,
                    False,
                    FailureCode.ACTION_COMMAND_FAILED,
                    f"ADB long press failed: {press.brief()}",
                    {"image": fresh.image},
                )
            deadline = time.monotonic() + self.config.panel_open_timeout_sec
            target = None
            panel_frame = None
            panel_resolution = None
            while time.monotonic() < deadline and not stop_event.is_set():
                self._sleep(stop_event, 0.25)
                panel_frame = self._capture(device)
                panel_resolution = self.panel_locator.resolve_panel(
                    before_image=fresh.image,
                    before_ui_xml=fresh.ui_xml,
                    after_image=panel_frame.image,
                    after_ui_xml=panel_frame.ui_xml,
                    content_kind=context.content_kind,
                    like_location=reacquired,
                )
                if panel_resolution is None:
                    continue
                panel_was_open = True
                target = self.panel_locator.locate(
                    panel_frame.image,
                    panel_frame.ui_xml,
                    content_kind=context.content_kind,
                    internal_action=internal_action,
                    like_location=reacquired,
                    panel_resolution=panel_resolution,
                )
                if target:
                    break
            if not panel_was_open:
                return ExecutorResult(
                    False,
                    False,
                    FailureCode.REACTION_PANEL_NOT_OPENED,
                    "Reaction panel could not be verified",
                    {
                        "image": panel_frame.image if panel_frame else fresh.image,
                        "details": [
                            {"resolution": reacquired.as_evidence()},
                            {"reason": "no_new_anchor_bound_panel"},
                        ],
                        "reaction_debug": self._debug_bundle(
                            fresh,
                            reacquired,
                            panel_frame=panel_frame,
                        ),
                    },
                )
            if target is None:
                return ExecutorResult(
                    False,
                    False,
                    FailureCode.REACTION_TARGET_NOT_FOUND,
                    f"Reaction target {internal_action} was not verified in the panel",
                    {
                        "image": panel_frame.image if panel_frame else fresh.image,
                        "details": [
                            {"resolution": reacquired.as_evidence()},
                            {
                                "panel": {
                                    "bounds": panel_resolution.panel_bounds if panel_resolution else None,
                                    "source": panel_resolution.source if panel_resolution else None,
                                    "options": sorted(panel_resolution.options) if panel_resolution else [],
                                }
                            },
                        ],
                        "reaction_debug": self._debug_bundle(
                            fresh,
                            reacquired,
                            panel_frame=panel_frame,
                        ),
                    },
                )
            command = device.tap(target.x, target.y)
            if not command.ok:
                return ExecutorResult(
                    False,
                    False,
                    FailureCode.ACTION_COMMAND_FAILED,
                    f"ADB reaction tap failed: {command.brief()}",
                    {"image": panel_frame.image if panel_frame else fresh.image},
                )
        self._sleep(stop_event, self.config.action_settle_sec)
        verification = self.verifier.verify(
            device=device,
            before_image=fresh.image,
            before_ui_xml=fresh.ui_xml,
            coordinate=(reacquired.x, reacquired.y),
            content_kind=context.content_kind,
            internal_action=internal_action,
            timeout_sec=self.config.verify_action_timeout_sec,
            stop_event=stop_event,
            panel_was_open=panel_was_open,
            panel_locator=self.panel_locator,
            like_location=reacquired,
            settle=lambda seconds: self._sleep(stop_event, seconds),
            reacquire_action=lambda image, ui_xml: self._reacquire(
                context,
                CapturedFrame(image, ui_xml, frame_hash(image)),
            ),
            coordinate_tolerance_px=self.config.stale_coordinate_tolerance_px,
            before_state=before_state,
        )
        if verification.confirmed:
            context.action_verified = True
            return ExecutorResult(
                True,
                True,
                message=f"Action {internal_action} verified",
                evidence={
                    "details": [
                        {"resolution": reacquired.as_evidence()},
                        {"verification": verification.evidence},
                        {
                            "requested_reaction": internal_action,
                            "detected_active_reaction": verification.detected_active_reaction,
                        },
                    ]
                },
            )
        if verification.status is ActionVerificationStatus.REACTION_TYPE_MISMATCH:
            code = FailureCode.REACTION_TYPE_MISMATCH
        elif verification.status is ActionVerificationStatus.UNKNOWN:
            code = FailureCode.ACTION_RESULT_UNKNOWN
        else:
            code = FailureCode.ACTION_NOT_CONFIRMED
        return ExecutorResult(
            False,
            False,
            code,
            f"Action verification failed: {verification.status.value}",
            {
                "image": verification.after_image,
                "details": [
                    {"resolution": reacquired.as_evidence()},
                    {"verification": verification.evidence},
                    {
                        "requested_reaction": internal_action,
                        "detected_active_reaction": verification.detected_active_reaction,
                    },
                ],
                "reaction_debug": self._debug_bundle(
                    fresh,
                    reacquired,
                    after_image=verification.after_image,
                    after_xml=verification.after_ui_xml,
                    verification={
                        "status": verification.status.value,
                        "requested_reaction": internal_action,
                        "detected_active_reaction": verification.detected_active_reaction,
                        "evidence": verification.evidence,
                    },
                ),
            },
        )
