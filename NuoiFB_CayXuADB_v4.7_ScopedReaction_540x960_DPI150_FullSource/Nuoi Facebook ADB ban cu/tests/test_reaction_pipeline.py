from __future__ import annotations

from types import SimpleNamespace
import threading
import tempfile
import unittest
from pathlib import Path

import numpy as np

from automation.jobs.action_verifier import ActionVerificationStatus, ActionVerifier
from automation.jobs.detectors.common import (
    CapturedFrame,
    LocateResult,
    SearchResult,
    make_scope_signature,
)
from automation.jobs.detectors.feed_like import FeedLikeLocator
from automation.jobs.detectors.reaction_panel import ReactionPanelLocator
from automation.jobs.detectors.reaction_resolver import (
    ReactionStateResolver,
    ReactionStateStatus,
    canonical_reaction,
)
from automation.jobs.executors.reaction import ReactionExecutor
from automation.jobs.failure_codes import FailureCode
from automation.jobs.evidence import FailureEvidenceStore
from automation.jobs.models import ContentKind
from core.paths import app_path
from core.template_matcher import TemplateMatcher
from core.template_matcher import MatchResult
from integrations.golike_api import JobAction


def anchor(y: int = 500, signature: str = "") -> LocateResult:
    reaction = (40, y, 140, y + 50)
    comment = (200, y, 330, y + 50)
    share = (390, y, 530, y + 50)
    scope = (40, y, 530, y + 50)
    return LocateResult(
        90,
        y + 25,
        0.98,
        "ui_hierarchy",
        reaction,
        ["Comment", "Share"],
        "frame-a",
        content_kind="feed",
        reaction_bounds=reaction,
        comment_bounds=comment,
        share_bounds=share,
        action_cluster_bounds=scope,
        scope_bounds=scope,
        scope_signature=signature
        or make_scope_signature("feed", reaction, comment, share, scope, ("comment", "share")),
    )


def scoped_xml(active: str = "", *, selected_outside: str = "") -> str:
    selected = "true" if active else "false"
    label = active.title() if active else "Like"
    outside = (
        f'<node text="{selected_outside.title()}" bounds="[40,100][140,150]" '
        'clickable="true" selected="true" checked="false" />'
        if selected_outside
        else ""
    )
    return f"""<hierarchy>
      {outside}
      <node text="{label}" bounds="[40,500][140,550]" clickable="true" selected="{selected}" checked="false" />
      <node text="Comment" bounds="[200,500][330,550]" clickable="true" selected="false" checked="false" />
      <node text="Share" bounds="[390,500][530,550]" clickable="true" selected="false" checked="false" />
    </hierarchy>"""


class _Command:
    ok = True

    @staticmethod
    def brief():
        return "ok"


class _Device:
    def __init__(self, image, xml):
        self.image = image
        self.xml = xml
        self.taps = 0
        self.long_presses = 0

    def capture_screen_image(self):
        return self.image.copy()

    def dump_ui_xml_text(self):
        return self.xml

    def tap(self, *_args):
        self.taps += 1
        return _Command()

    def long_press(self, *_args, **_kwargs):
        self.long_presses += 1
        return _Command()


class ReactionPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matcher = TemplateMatcher(app_path("resources/templates"), 0.55, 0.60, 540, 960, 150)

    def test_vietnamese_and_english_labels_are_canonical(self):
        self.assertEqual(canonical_reaction("Yêu thích"), "love")
        self.assertEqual(canonical_reaction("Thương thương"), "thuongthuong")
        self.assertEqual(canonical_reaction("Remove Angry"), "angry")
        self.assertEqual(canonical_reaction("42 lượt cảm xúc"), "")

    def test_selected_state_is_limited_to_resolved_anchor(self):
        state = ReactionStateResolver().read(
            scoped_xml("", selected_outside="wow"),
            anchor(),
        )
        self.assertIs(state.status, ReactionStateStatus.NONE)
        self.assertEqual(state.active_reaction, "")

    def test_exact_active_reaction_is_read_from_anchor(self):
        state = ReactionStateResolver().read(scoped_xml("haha"), anchor())
        self.assertIs(state.status, ReactionStateStatus.ACTIVE)
        self.assertEqual(state.active_reaction, "haha")

    def test_clickable_ancestor_that_covers_whole_screen_is_rejected(self):
        xml = """<hierarchy><node bounds="[0,0][540,960]" clickable="true">
          <node text="Like" bounds="[60,500][120,530]" clickable="false" />
          <node text="Comment" bounds="[220,500][300,530]" clickable="false" />
          <node text="Share" bounds="[420,500][500,530]" clickable="false" />
        </node></hierarchy>"""
        tree = __import__("automation.jobs.detectors.ui_tree", fromlist=["UiTree"]).UiTree.parse(xml)
        self.assertIsNone(tree.find_action_layout("horizontal", screen_width=540, screen_height=960))

    def test_multiple_posts_choose_center_target_not_upper_post(self):
        def row(y):
            return (
                f'<node text="Like" bounds="[40,{y}][140,{y+50}]" clickable="true" />'
                f'<node text="Comment" bounds="[200,{y}][330,{y+50}]" clickable="true" />'
                f'<node text="Share" bounds="[390,{y}][530,{y+50}]" clickable="true" />'
            )
        xml = f"<hierarchy>{row(80)}{row(500)}</hierarchy>"
        image = np.zeros((960, 540, 3), dtype=np.uint8)
        result = FeedLikeLocator(self.matcher, 0.55).locate_once(image, xml)
        self.assertIsNotNone(result)
        self.assertEqual(result.y, 525)

    def test_scope_signature_survives_small_shift_but_not_scope_change(self):
        first = anchor()
        shifted = anchor(y=512, signature=first.scope_signature)
        changed = anchor(y=512, signature="different")
        self.assertTrue(first.same_action_scope(shifted, 28))
        self.assertFalse(first.same_action_scope(changed, 28))

    def test_scope_signature_uses_nearby_post_identity(self):
        def post(author):
            return f"""<hierarchy><node bounds="[0,300][540,620]" clickable="false">
              <node text="{author}" resource-id="author_name" bounds="[30,320][220,360]" clickable="false" />
              <node text="Like" bounds="[40,500][140,550]" clickable="true" />
              <node text="Comment" bounds="[200,500][330,550]" clickable="true" />
              <node text="Share" bounds="[390,500][530,550]" clickable="true" />
            </node></hierarchy>"""
        image = np.zeros((960, 540, 3), dtype=np.uint8)
        locator = FeedLikeLocator(self.matcher, 0.55)
        first = locator.locate_once(image, post("Author A"))
        second = locator.locate_once(image, post("Author B"))
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.scope_signature, second.scope_signature)

    def test_anchor_bounds_stay_inside_540_by_960(self):
        image = np.zeros((960, 540, 3), dtype=np.uint8)
        result = FeedLikeLocator(self.matcher, 0.55).locate_once(image, scoped_xml())
        self.assertIsNotNone(result)
        for value, limit in zip(result.reaction_bounds, (540, 960, 540, 960)):
            self.assertGreaterEqual(value, 0)
            self.assertLessEqual(value, limit)

    def test_panel_requires_temporal_or_structural_binding_to_anchor(self):
        before = np.zeros((960, 540, 3), dtype=np.uint8)
        after = before.copy()
        options = "".join(
            f'<node text="{name}" bounds="[{20+i*70},420][{75+i*70},475]" clickable="true" />'
            for i, name in enumerate(("Like", "Love", "Care", "Haha", "Wow", "Sad", "Angry"))
        )
        locator = ReactionPanelLocator(self.matcher)
        resolution = locator.resolve_panel(
            before_image=before,
            before_ui_xml=scoped_xml(),
            after_image=after,
            after_ui_xml=f"<hierarchy>{options}</hierarchy>",
            content_kind=ContentKind.FEED,
            like_location=anchor(),
        )
        self.assertIsNotNone(resolution)
        self.assertEqual(set(resolution.options), {"like", "love", "thuongthuong", "haha", "wow", "sad", "angry"})

    def test_panel_far_from_anchor_is_rejected(self):
        image = np.zeros((960, 540, 3), dtype=np.uint8)
        options = "".join(
            f'<node text="{name}" bounds="[{20+i*70},80][{75+i*70},130]" clickable="true" />'
            for i, name in enumerate(("Like", "Love", "Care", "Haha", "Wow", "Sad", "Angry"))
        )
        resolution = ReactionPanelLocator(self.matcher).resolve_panel(
            before_image=image,
            before_ui_xml=scoped_xml(),
            after_image=image,
            after_ui_xml=f"<hierarchy>{options}</hierarchy>",
            content_kind=ContentKind.FEED,
            like_location=anchor(),
        )
        self.assertIsNone(resolution)

    def test_visual_panel_does_not_use_reversed_reel_index(self):
        image = __import__("core.image_io", fromlist=["imread_unicode"]).imread_unicode(
            app_path("resources/templates/domcamxucreel.png")
        )
        locator = ReactionPanelLocator(self.matcher)
        like = SimpleNamespace(x=313, y=93)
        love = locator.locate(
            image,
            "",
            content_kind=ContentKind.REEL,
            internal_action="love",
            like_location=like,
        )
        ambiguous_care = locator.locate(
            image,
            "",
            content_kind=ContentKind.REEL,
            internal_action="thuongthuong",
            like_location=like,
        )
        self.assertIsNotNone(love)
        self.assertEqual(love.detector_source, "panel_template_validated")
        self.assertIsNone(ambiguous_care)

    def test_requested_haha_but_actual_wow_is_mismatch(self):
        image = np.zeros((960, 540, 3), dtype=np.uint8)
        device = _Device(image, scoped_xml("wow"))
        current = anchor()
        verification = ActionVerifier().verify(
            device=device,
            before_image=image,
            before_ui_xml=scoped_xml(),
            coordinate=(current.x, current.y),
            content_kind=ContentKind.FEED,
            internal_action="haha",
            timeout_sec=0.2,
            stop_event=threading.Event(),
            panel_was_open=False,
            panel_locator=SimpleNamespace(panel_is_open=lambda *_args, **_kwargs: False, matcher=None),
            like_location=current,
            settle=lambda _seconds: None,
            reacquire_action=lambda _image, _xml: current,
        )
        self.assertIs(verification.status, ActionVerificationStatus.REACTION_TYPE_MISMATCH)
        self.assertEqual(verification.detected_active_reaction, "wow")

    def test_requested_love_but_actual_like_is_mismatch(self):
        image = np.zeros((960, 540, 3), dtype=np.uint8)
        device = _Device(image, scoped_xml("like"))
        current = anchor()
        verification = ActionVerifier().verify(
            device=device,
            before_image=image,
            before_ui_xml=scoped_xml(),
            coordinate=(current.x, current.y),
            content_kind=ContentKind.FEED,
            internal_action="love",
            timeout_sec=0.2,
            stop_event=threading.Event(),
            panel_was_open=True,
            panel_locator=SimpleNamespace(panel_is_open=lambda *_args, **_kwargs: False, matcher=None),
            like_location=current,
            settle=lambda _seconds: None,
            reacquire_action=lambda _image, _xml: current,
        )
        self.assertIs(verification.status, ActionVerificationStatus.REACTION_TYPE_MISMATCH)
        self.assertEqual(verification.detected_active_reaction, "like")

    def test_visual_confirmation_requires_two_stable_exact_frames(self):
        before = np.zeros((960, 540, 3), dtype=np.uint8)
        after = before.copy()
        after[500:550, 40:140] = 255
        current = anchor()

        class Matcher:
            def find_reaction_in_roi_image(self, _image, reaction, _roi, threshold=0.0):
                if reaction != "love":
                    return None, 0.20
                return MatchResult(90, 525, 0.91, "love", 40, 40), 0.91

        verification = ActionVerifier().verify(
            device=_Device(after, ""),
            before_image=before,
            before_ui_xml="",
            coordinate=(current.x, current.y),
            content_kind=ContentKind.FEED,
            internal_action="love",
            timeout_sec=0.15,
            stop_event=threading.Event(),
            panel_was_open=False,
            panel_locator=SimpleNamespace(matcher=Matcher(), panel_is_open=lambda *_args, **_kwargs: False),
            like_location=current,
            settle=lambda _seconds: None,
            reacquire_action=lambda _image, _xml: current,
        )
        self.assertIs(verification.status, ActionVerificationStatus.CONFIRMED)
        self.assertIn("two_stable_scoped_visual_matches", verification.evidence)

    def test_already_like_never_taps_again(self):
        image = np.zeros((960, 540, 3), dtype=np.uint8)
        device = _Device(image, scoped_xml("like"))
        current = anchor()

        class Feed:
            def locate_with_scroll(self, *_args, **_kwargs):
                return SearchResult(current, CapturedFrame(image, device.xml, "frame-a"))

            def locate_once(self, *_args, **_kwargs):
                return current

        config = SimpleNamespace(
            feed_max_search_swipes=0,
            feed_search_swipe_duration_ms=400,
            stale_coordinate_tolerance_px=28,
            long_press_duration_ms=900,
            panel_open_timeout_sec=0.1,
            action_settle_sec=0.0,
            verify_action_timeout_sec=0.1,
        )
        executor = ReactionExecutor(
            config=config,
            feed_locator=Feed(),
            reel_locator=SimpleNamespace(),
            panel_locator=ReactionPanelLocator(self.matcher),
        )
        context = SimpleNamespace(
            action_verified=False,
            content_kind=ContentKind.FEED,
            job=SimpleNamespace(action=JobAction.LIKE),
            detected_coordinate=None,
        )
        result = executor.execute(context, device, threading.Event())
        self.assertTrue(result.success)
        self.assertTrue(context.action_verified)
        self.assertEqual(device.taps, 0)

    def test_ambiguous_pre_state_refuses_to_tap(self):
        image = np.zeros((960, 540, 3), dtype=np.uint8)
        device = _Device(image, "")
        current = anchor()

        class Feed:
            def locate_with_scroll(self, *_args, **_kwargs):
                return SearchResult(current, CapturedFrame(image, "", "frame-a"))

            def locate_once(self, *_args, **_kwargs):
                return current

        config = SimpleNamespace(
            feed_max_search_swipes=0,
            feed_search_swipe_duration_ms=400,
            stale_coordinate_tolerance_px=28,
            long_press_duration_ms=900,
            panel_open_timeout_sec=0.1,
            action_settle_sec=0.0,
            verify_action_timeout_sec=0.1,
        )
        result = ReactionExecutor(
            config=config,
            feed_locator=Feed(),
            reel_locator=SimpleNamespace(),
            panel_locator=ReactionPanelLocator(self.matcher),
        ).execute(
            SimpleNamespace(
                action_verified=False,
                content_kind=ContentKind.FEED,
                job=SimpleNamespace(action=JobAction.LIKE),
                detected_coordinate=None,
            ),
            device,
            threading.Event(),
        )
        self.assertEqual(result.failure_code, FailureCode.ACTION_RESULT_UNKNOWN)
        self.assertEqual(device.taps, 0)

    def test_long_press_without_new_panel_fails_without_option_tap(self):
        image = np.zeros((960, 540, 3), dtype=np.uint8)
        device = _Device(image, scoped_xml())
        current = anchor()

        class Feed:
            def locate_with_scroll(self, *_args, **_kwargs):
                return SearchResult(current, CapturedFrame(image, device.xml, "frame-a"))

            def locate_once(self, *_args, **_kwargs):
                return current

        config = SimpleNamespace(
            feed_max_search_swipes=0,
            feed_search_swipe_duration_ms=400,
            stale_coordinate_tolerance_px=28,
            long_press_duration_ms=900,
            panel_open_timeout_sec=0.01,
            action_settle_sec=0.0,
            verify_action_timeout_sec=0.1,
        )
        result = ReactionExecutor(
            config=config,
            feed_locator=Feed(),
            reel_locator=SimpleNamespace(),
            panel_locator=ReactionPanelLocator(self.matcher),
            sleep_interruptible=lambda _seconds: None,
        ).execute(
            SimpleNamespace(
                action_verified=False,
                content_kind=ContentKind.FEED,
                job=SimpleNamespace(action=JobAction.LOVE),
                detected_coordinate=None,
            ),
            device,
            threading.Event(),
        )
        self.assertEqual(result.failure_code, FailureCode.REACTION_PANEL_NOT_OPENED)
        self.assertEqual(device.long_presses, 1)
        self.assertEqual(device.taps, 0)

    def test_dpi_scaling_uses_configured_density(self):
        at_150 = TemplateMatcher(app_path("resources/templates"), screen_dpi=150)
        at_300 = TemplateMatcher(app_path("resources/templates"), screen_dpi=300)
        self.assertEqual(at_150.ui_px(24), 22)
        self.assertEqual(at_300.ui_px(24), 45)

    def test_failure_bundle_writes_named_sanitized_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            store = FailureEvidenceStore()
            store.directory = Path(directory)
            image = np.zeros((20, 20, 3), dtype=np.uint8)
            xml = """<hierarchy><node text="Private post text" bounds="[0,0][20,10]" />
              <node text="Like" bounds="[0,10][20,20]" clickable="true" selected="false" />
            </hierarchy>"""
            folder = Path(
                store.save_reaction_bundle(
                    {
                        "before_image": image,
                        "before_xml": xml,
                        "resolution": {"content_kind": "feed"},
                        "verification": {"status": "UNKNOWN"},
                        "candidate_rejections": [{"reason": "not_scope"}],
                    },
                    ld_index=1,
                    job_id="job",
                    retention=3,
                )
            )
            self.assertTrue((folder / "before.png").is_file())
            self.assertTrue((folder / "before.xml").is_file())
            self.assertTrue((folder / "resolution.json").is_file())
            sanitized = (folder / "before.xml").read_text(encoding="utf-8")
            self.assertNotIn("Private post text", sanitized)
            self.assertIn("Like", sanitized)


if __name__ == "__main__":
    unittest.main()
