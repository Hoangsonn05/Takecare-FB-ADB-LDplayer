from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import Enum
import re
from urllib.parse import urlsplit

import cv2
import numpy as np

from core.adb_device import ActivitySnapshot
from core.config import FACEBOOK_PACKAGE_NAME

from .detectors.common import perceptual_difference
from .detectors.screen_classifier import ScreenClassifier
from .detectors.ui_tree import UiTree, normalize_label
from .models import ContentKind

_BLOCKED_TEXT = (
    "log in",
    "login",
    "đăng nhập",
    "something went wrong",
    "đã xảy ra lỗi",
    "page isn't available",
    "trang này hiện không hiển thị",
    "content isn't available",
    "nội dung này hiện không khả dụng",
)
_LOADING_TEXT = (
    "loading",
    "đang tải",
    "please wait",
    "vui lòng chờ",
    "progressbar",
)
_HOME_LABELS = {"home", "trang chủ", "news feed", "bảng tin"}


class NavigationProbeState(str, Enum):
    ROUTER_ACTIVITY = "router_activity"
    LOADING = "loading"
    TARGET_READY = "target_ready"
    RETURNED_HOME = "returned_home"
    WRONG_APP = "wrong_app"
    BLOCKED = "blocked"
    TERMINAL_ERROR = "terminal_error"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class NavigationBaseline:
    image: object | None
    ui_xml: str
    activity: ActivitySnapshot


@dataclass(slots=True)
class NavigationProbe:
    state: NavigationProbeState
    confidence: float
    evidence: list[str] = field(default_factory=list)
    activity: str = ""
    intent_data: str = ""
    observed_content_kind: ContentKind = ContentKind.UNKNOWN
    image: object | None = None
    ui_xml: str = ""
    foreground: bool = False
    screen_not_blank: bool = False
    layout_match: bool = False
    intent_match: bool = False
    home_detected: bool = False
    router_activity: bool = False
    route_transition_observed: bool = False


@dataclass(slots=True)
class NavigationVerification:
    """Backward-compatible one-shot result for diagnostics outside JobRunner."""

    verified: bool
    confidence: float
    evidence: list[str] = field(default_factory=list)
    actual_activity: str = ""
    actual_intent_data: str = ""
    expected_content_kind: ContentKind = ContentKind.UNKNOWN
    observed_content_kind: ContentKind = ContentKind.UNKNOWN


class NavigationVerifier:
    def __init__(
        self,
        classifier: ScreenClassifier | None = None,
        package_name: str = FACEBOOK_PACKAGE_NAME,
    ) -> None:
        self.classifier = classifier or ScreenClassifier()
        self.package_name = package_name

    @staticmethod
    def _not_blank(image) -> bool:
        if image is None:
            return False
        gray = image if len(image.shape) == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        return float(np.std(gray)) >= 7.0

    @staticmethod
    def _activity_line_has_component(activity_line: str, component: str) -> bool:
        package, separator, class_name = str(component or "").partition("/")
        if not separator or not package or not class_name:
            return False
        full_class = (
            f"{package}{class_name}" if class_name.startswith(".") else class_name
        )
        short_class = (
            f".{full_class[len(package) + 1:]}"
            if full_class.startswith(f"{package}.")
            else class_name
        )
        return any(
            token in str(activity_line or "")
            for token in {
                f"{package}/{class_name}",
                f"{package}/{full_class}",
                f"{package}/{short_class}",
            }
        )

    @staticmethod
    def _intent_matches(actual: str, expected_url: str, object_id: str | None) -> bool:
        actual_text = str(actual or "").casefold()
        if not actual_text:
            return False
        expected = urlsplit(expected_url)
        path = expected.path.rstrip("/").casefold()
        if path and path != "/" and path in actual_text:
            return True
        return bool(object_id and str(object_id).casefold() in actual_text)

    @staticmethod
    def _xml_identity_matches(
        ui_xml: str,
        expected_url: str,
        object_id: str | None,
    ) -> bool:
        xml = str(ui_xml or "").casefold()
        if not xml:
            return False
        if object_id and str(object_id).casefold() in xml:
            return True
        path_parts = [
            part
            for part in urlsplit(expected_url).path.casefold().split("/")
            if len(part) >= 5
            and (len(part) >= 8 or any(char.isdigit() for char in part))
            and part not in {"posts", "videos", "reels", "story.php"}
        ]
        return any(part in xml for part in path_parts)

    @staticmethod
    def _normalized_xml(xml_text: str) -> str:
        text = re.sub(r'bounds="\[[^"]+\]"', 'bounds=""', str(xml_text or ""))
        text = re.sub(r"\s+", " ", text).strip().casefold()
        return text[:50000]

    @classmethod
    def _xml_similarity(cls, first: str, second: str) -> float:
        a = cls._normalized_xml(first)
        b = cls._normalized_xml(second)
        if not a or not b:
            return 0.0
        return SequenceMatcher(None, a, b, autojunk=True).ratio()

    @staticmethod
    def _selected_home_marker(ui_xml: str) -> bool:
        tree = UiTree.parse(ui_xml)
        normalized_labels = {normalize_label(label) for label in _HOME_LABELS}
        for node in tree.nodes:
            if not (node.selected or node.checked):
                continue
            label = normalize_label(node.label)
            if any(marker in label for marker in normalized_labels):
                return True
        return False

    def capture_baseline(self, device) -> NavigationBaseline:
        return NavigationBaseline(
            image=device.capture_screen_image(),
            ui_xml=device.dump_ui_xml_text(),
            activity=device.activity_snapshot(),
        )

    def is_facebook_home(
        self,
        *,
        baseline: NavigationBaseline,
        image,
        ui_xml: str,
        snapshot: ActivitySnapshot,
        intent_match: bool,
    ) -> tuple[bool, list[str], float, float]:
        difference = perceptual_difference(baseline.image, image)
        xml_similarity = self._xml_similarity(baseline.ui_xml, ui_xml)
        selected_home = self._selected_home_marker(ui_xml)
        intent = str(snapshot.intent_data or "")
        parsed_intent = urlsplit(intent) if intent else None
        root_intent = bool(
            parsed_intent
            and (parsed_intent.hostname or "").casefold().endswith("facebook.com")
            and parsed_intent.path in {"", "/"}
        )
        same_as_baseline = bool(
            baseline.image is not None
            and image is not None
            and difference < 0.018
            and (not baseline.ui_xml or not ui_xml or xml_similarity >= 0.82)
        )
        home = bool(
            not intent_match
            and (
                same_as_baseline
                or root_intent
                or selected_home
            )
        )
        evidence = [
            f"home_selected={selected_home}",
            f"baseline_diff={difference:.3f}",
            f"baseline_xml_similarity={xml_similarity:.3f}",
            f"home_root_intent={root_intent}",
        ]
        return home, evidence, difference, xml_similarity

    def _snapshot(self, device) -> ActivitySnapshot:
        if hasattr(device, "activity_snapshot"):
            return device.activity_snapshot()
        activity = device.current_activity_line()
        intent = device.current_intent_data()
        package = self.package_name if self.package_name in activity else ""
        return ActivitySnapshot(
            package_name=package,
            activity_line=activity,
            intent_data=intent,
            resumed=bool(activity),
        )

    def probe(
        self,
        *,
        device,
        open_result,
        expected_url: str,
        object_id: str | None,
        baseline: NavigationBaseline,
        raw_job: dict,
        elapsed_sec: float,
        initial_grace_sec: float,
    ) -> NavigationProbe:
        image = device.capture_screen_image()
        ui_xml = device.dump_ui_xml_text()
        snapshot = self._snapshot(device)
        expected_kind = self.classifier.classify_url(expected_url)
        classification = self.classifier.classify(
            url=expected_url,
            raw_job=raw_job,
            ui_xml=ui_xml,
            image=image,
        )
        command_accepted = bool(
            getattr(
                open_result,
                "command_accepted",
                getattr(open_result, "ok", False)
                and getattr(open_result, "status_ok", False),
            )
        )
        foreground = bool(
            snapshot.resumed and snapshot.package_name == self.package_name
        )
        if not foreground and not snapshot.package_name:
            foreground = bool(device.is_package_foreground(self.package_name))
        activity_ok = self.package_name in snapshot.activity_line
        not_blank = self._not_blank(image)
        intent_match = self._intent_matches(
            snapshot.intent_data,
            expected_url,
            object_id,
        )
        xml_identity = self._xml_identity_matches(ui_xml, expected_url, object_id)
        lowered_xml = str(ui_xml or "").casefold()
        blocked = any(marker in lowered_xml for marker in _BLOCKED_TEXT)
        loading_marker = any(marker in lowered_xml for marker in _LOADING_TEXT)
        layout_match = bool(
            expected_kind is not ContentKind.UNKNOWN
            and classification.kind is expected_kind
            and classification.has_layout_signal
        )
        home, home_evidence, changed, xml_similarity = self.is_facebook_home(
            baseline=baseline,
            image=image,
            ui_xml=ui_xml,
            snapshot=snapshot,
            intent_match=bool(intent_match or xml_identity),
        )
        identity = bool(intent_match or xml_identity)
        resolved_component = str(
            getattr(open_result, "resolved_component", "") or ""
        )
        launched_activity = str(
            getattr(open_result, "launched_activity", "") or ""
        )
        router_activity = bool(
            snapshot.activity_line
            and any(
                self._activity_line_has_component(
                    snapshot.activity_line,
                    component,
                )
                for component in (resolved_component, launched_activity)
            )
        )
        loading_surface = bool(
            foreground and (not not_blank or not ui_xml.strip() or loading_marker)
        )
        route_transition_observed = bool(
            router_activity or (loading_surface and not home)
        )
        evidence = [
            f"am_start_status_ok={getattr(open_result, 'status_ok', False)}",
            f"command_accepted={command_accepted}",
            f"foreground={foreground}",
            f"activity_package={activity_ok}",
            f"screen_not_blank={not_blank}",
            f"intent_match={intent_match}",
            f"xml_identity_match={xml_identity}",
            f"layout_match={layout_match}",
            f"loading_marker={loading_marker}",
            f"router_activity={router_activity}",
            f"route_transition_observed={route_transition_observed}",
            f"content_kind={classification.kind.value}",
            *home_evidence,
            *classification.evidence,
        ]

        if not command_accepted:
            state = NavigationProbeState.TERMINAL_ERROR
        elif blocked:
            state = NavigationProbeState.BLOCKED
        elif router_activity:
            state = NavigationProbeState.ROUTER_ACTIVITY
        elif elapsed_sec < initial_grace_sec and (
            not foreground or not not_blank or not layout_match or home
        ):
            state = NavigationProbeState.LOADING
        elif snapshot.package_name and snapshot.package_name != self.package_name:
            state = NavigationProbeState.WRONG_APP
        elif not foreground:
            state = NavigationProbeState.WRONG_APP
        elif not activity_ok:
            state = NavigationProbeState.LOADING
        elif home:
            state = NavigationProbeState.RETURNED_HOME
        elif not not_blank or not ui_xml.strip() or loading_marker:
            state = NavigationProbeState.LOADING
        elif not layout_match:
            state = NavigationProbeState.LOADING
        elif identity or changed >= 0.025 or xml_similarity < 0.70:
            state = NavigationProbeState.TARGET_READY
        else:
            state = NavigationProbeState.UNKNOWN

        confidence = 0.0
        confidence += 0.18 if command_accepted else 0.0
        confidence += 0.22 if foreground and activity_ok else 0.0
        confidence += 0.12 if not_blank else 0.0
        confidence += 0.20 if identity else 0.0
        confidence += 0.20 if layout_match else 0.0
        confidence += min(0.08, changed)
        if home or blocked:
            confidence = min(confidence, 0.45)
        return NavigationProbe(
            state=state,
            confidence=min(confidence, 1.0),
            evidence=evidence,
            activity=snapshot.activity_line,
            intent_data=snapshot.intent_data,
            observed_content_kind=classification.kind,
            image=image,
            ui_xml=ui_xml,
            foreground=foreground,
            screen_not_blank=not_blank,
            layout_match=layout_match,
            intent_match=identity,
            home_detected=home,
            router_activity=router_activity,
            route_transition_observed=route_transition_observed,
        )

    def verify(
        self,
        *,
        device,
        open_result,
        expected_url: str,
        object_id: str | None,
        before_image,
        after_image,
        ui_xml: str,
        raw_job: dict,
    ) -> NavigationVerification:
        """Compatibility adapter; JobRunner uses probe() with stable polling."""
        baseline = NavigationBaseline(
            before_image,
            "",
            ActivitySnapshot(),
        )

        verifier = self

        class _CapturedDevice:
            def capture_screen_image(self):
                return after_image

            def dump_ui_xml_text(self):
                return ui_xml

            def activity_snapshot(self):
                return verifier._snapshot(device)

            def is_package_foreground(self, package_name):
                return device.is_package_foreground(package_name)

        probe = self.probe(
            device=_CapturedDevice(),
            open_result=open_result,
            expected_url=expected_url,
            object_id=object_id,
            baseline=baseline,
            raw_job=raw_job,
            elapsed_sec=999.0,
            initial_grace_sec=0.0,
        )
        return NavigationVerification(
            verified=probe.state is NavigationProbeState.TARGET_READY,
            confidence=probe.confidence,
            evidence=probe.evidence,
            actual_activity=probe.activity,
            actual_intent_data=probe.intent_data,
            expected_content_kind=self.classifier.classify_url(expected_url),
            observed_content_kind=probe.observed_content_kind,
        )
