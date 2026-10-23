from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import threading
import unittest

import cv2
import numpy as np

from automation.jobs.action_verifier import (
    ActionVerificationStatus,
    ActionVerifier,
    coordinates_stable,
)
from automation.jobs.detectors import (
    CapturedFrame,
    FeedLikeLocator,
    ReactionPanelLocator,
    ReelLikeLocator,
    ScreenClassifier,
)
from automation.jobs.detectors.ui_tree import UiTree
from automation.jobs.models import ContentKind
from core.image_io import imread_unicode
from core.paths import app_path
from core.template_matcher import TemplateMatcher


def feed_ui_xml(width: int = 546, y: int = 52) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="Thích" content-desc="Like" resource-id="" bounds="[50,{y-20}][130,{y+20}]" clickable="true" selected="false" checked="false" />
  <node text="Bình luận" content-desc="Comment" resource-id="" bounds="[210,{y-20}][320,{y+20}]" clickable="true" selected="false" checked="false" />
  <node text="Chia sẻ" content-desc="Share" resource-id="" bounds="[{width-130},{y-20}][{width-20},{y+20}]" clickable="true" selected="false" checked="false" />
</hierarchy>"""


def reel_ui_xml(width: int = 352) -> str:
    x1, x2 = width - 58, width - 8
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<hierarchy>
  <node text="" content-desc="42 cảm xúc" resource-id="" bounds="[{x1},60][{x2},100]" clickable="true" selected="false" checked="false" />
  <node text="42 cảm xúc" content-desc="" resource-id="" bounds="[{x1},101][{x2},121]" clickable="false" selected="false" checked="false" />
  <node text="" content-desc="2 bình luận" resource-id="" bounds="[{x1},130][{x2},170]" clickable="true" selected="false" checked="false" />
  <node text="" content-desc="Chia sẻ, 5 lượt chia sẻ" resource-id="" bounds="[{x1},195][{x2},235]" clickable="true" selected="false" checked="false" />
  <node text="" content-desc="Lưu" resource-id="" bounds="[{x1},250][{x2},290]" clickable="true" selected="false" checked="false" />
</hierarchy>"""


class FakeSwipeDevice:
    def __init__(self) -> None:
        self.swipes = 0

    def swipe(self, *_args):
        self.swipes += 1
        return SimpleNamespace(ok=True)


class DetectorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.template_dir = app_path("resources/templates")
        cls.matcher = TemplateMatcher(cls.template_dir, 0.55, 0.60)

    def test_feed_action_row_with_ui_and_reference_image(self) -> None:
        image = imread_unicode(self.template_dir / "domnutlike.png")
        locator = FeedLikeLocator(self.matcher, 0.55)
        result = locator.locate_once(image, feed_ui_xml())
        self.assertIsNotNone(result)
        self.assertEqual(result.detector_source, "ui_hierarchy")
        self.assertLess(result.x, image.shape[1] // 2)

    def test_feed_reference_image_without_xml_uses_like_template(self) -> None:
        image = imread_unicode(self.template_dir / "domnutlike.png")
        result = FeedLikeLocator(self.matcher, 0.55).locate_once(image, "")
        self.assertIsNotNone(result)
        self.assertEqual(result.detector_source, "template_fallback")
        self.assertLess(result.x, image.shape[1] * 0.35)

    def test_feed_does_not_tap_generic_horizontal_summary(self) -> None:
        summary = np.full((700, 400, 3), 255, dtype=np.uint8)
        for x in (15, 170, 330):
            cv2.rectangle(summary, (x, 520), (x + 35, 550), (0, 0, 0), 2)
        result = FeedLikeLocator(self.matcher, 0.55).locate_once(summary, "")
        self.assertIsNone(result)

    def test_reel_right_rail_reuses_canny_matcher(self) -> None:
        image = imread_unicode(self.template_dir / "domcamxucreel.png")
        locator = ReelLikeLocator(self.matcher, "nutlikereel.png", 0.55)
        result = locator.locate_once(image, "")
        self.assertIsNotNone(result)
        self.assertEqual(result.detector_source, "reel_template_canny")
        self.assertGreater(result.x, image.shape[1] * 0.70)
        self.assertGreaterEqual(result.confidence, 0.55)
        self.assertLess(result.y, image.shape[0] // 3)

    def test_reel_ui_accepts_reaction_count_and_chooses_top_node(self) -> None:
        image = np.zeros((354, 352, 3), dtype=np.uint8)
        result = ReelLikeLocator(
            self.matcher,
            "nutlikereel.png",
            0.55,
        ).locate_once(image, reel_ui_xml())
        self.assertIsNotNone(result)
        self.assertEqual(result.detector_source, "ui_hierarchy")
        self.assertEqual(result.y, 80)

    def test_reel_locator_rejects_feed_action_row(self) -> None:
        image = imread_unicode(self.template_dir / "domnutlike.png")
        result = ReelLikeLocator(
            self.matcher,
            "nutlikereel.png",
            0.55,
        ).locate_once(image, "")
        self.assertIsNone(result)

    def test_feed_reaction_panel_template_is_structurally_validated(self) -> None:
        panel_image = imread_unicode(self.template_dir / "camxuc.png")
        canvas = np.full((400, 500, 3), 245, dtype=np.uint8)
        canvas[100 : 100 + panel_image.shape[0], 40 : 40 + panel_image.shape[1]] = panel_image
        like = SimpleNamespace(x=90, y=230)
        locator = ReactionPanelLocator(self.matcher)
        result = locator.locate(
            canvas,
            "",
            content_kind=ContentKind.FEED,
            internal_action="love",
            like_location=like,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.detector_source, "panel_template_validated")

    def test_reel_reaction_panel_template_is_structurally_validated(self) -> None:
        image = imread_unicode(self.template_dir / "domcamxucreel.png")
        like = SimpleNamespace(x=313, y=93)
        locator = ReactionPanelLocator(self.matcher)
        result = locator.locate(
            image,
            "",
            content_kind=ContentKind.REEL,
            internal_action="love",
            like_location=like,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.detector_source, "panel_template_validated")
        self.assertTrue(
            locator.panel_is_open(
                image,
                "",
                content_kind=ContentKind.REEL,
                like_location=like,
            )
        )

    def test_url_and_layout_classifier(self) -> None:
        classifier = ScreenClassifier()
        feed_image = imread_unicode(self.template_dir / "domnutlike.png")
        feed = classifier.classify(
            url="https://www.facebook.com/user/posts/1",
            ui_xml=feed_ui_xml(),
            image=feed_image,
        )
        self.assertEqual(feed.kind, ContentKind.FEED)
        self.assertTrue(feed.has_layout_signal)
        reel_image = imread_unicode(self.template_dir / "domcamxucreel.png")
        reel = classifier.classify(
            url="https://www.facebook.com/reel/1",
            ui_xml=reel_ui_xml(),
            image=reel_image,
        )
        self.assertEqual(reel.kind, ContentKind.REEL)
        self.assertTrue(reel.has_layout_signal)

    def test_long_post_scrolls_until_like_appears(self) -> None:
        locator = FeedLikeLocator(self.matcher, 0.55)
        blank1 = np.zeros((700, 400, 3), dtype=np.uint8)
        blank2 = np.full((700, 400, 3), 20, dtype=np.uint8)
        reference = imread_unicode(self.template_dir / "domnutlike.png")
        final = np.full((700, 400, 3), 240, dtype=np.uint8)
        resized = cv2.resize(reference, (400, 54))
        final[560:614] = resized
        frames = [
            CapturedFrame(blank1, "", ""),
            CapturedFrame(blank2, "", ""),
            CapturedFrame(final, feed_ui_xml(width=400, y=587), ""),
        ]
        device = FakeSwipeDevice()

        def capture():
            return frames.pop(0)

        result = locator.locate_with_scroll(
            device,
            max_swipes=3,
            swipe_duration_ms=450,
            stop_event=threading.Event(),
            capture=capture,
        )
        self.assertIsNotNone(result.location)
        self.assertEqual(result.swipes, 2)
        self.assertEqual(device.swipes, 2)

    def test_feed_search_never_swipes_more_than_three_times(self) -> None:
        locator = FeedLikeLocator(self.matcher, 0.55)
        frames = [
            CapturedFrame(
                np.full((700, 400, 3), value, dtype=np.uint8),
                "",
                f"frame-{value}",
            )
            for value in (0, 20, 40, 60)
        ]
        device = FakeSwipeDevice()
        capture_calls = 0

        def capture():
            nonlocal capture_calls
            capture_calls += 1
            return frames.pop(0)

        result = locator.locate_with_scroll(
            device,
            max_swipes=3,
            swipe_duration_ms=450,
            stop_event=threading.Event(),
            capture=capture,
        )
        self.assertIsNone(result.location)
        self.assertEqual(result.swipes, 3)
        self.assertEqual(device.swipes, 3)
        self.assertEqual(capture_calls, 4)


    def test_uploaded_feed_crop_uses_structural_first_action(self) -> None:
        image = imread_unicode(
            app_path("resources/job_references/feed/feed_action_row_540x40.png")
        )
        matcher = TemplateMatcher(self.template_dir, 0.60, 0.60, 540, 960, 150)
        result = FeedLikeLocator(matcher, 0.60).locate_once(image, "")
        self.assertIsNotNone(result)
        self.assertLess(result.x, image.shape[1] * 0.25)
        self.assertGreater(result.confidence, 0.80)
        self.assertTrue(any("feed_row_comment" in item for item in result.supporting_anchors))

    def test_uploaded_reel_crop_selects_top_icon(self) -> None:
        image = imread_unicode(
            app_path("resources/job_references/reel/reel_action_rail_50x208.png")
        )
        matcher = TemplateMatcher(self.template_dir, 0.60, 0.60, 540, 960, 150)
        result = ReelLikeLocator(matcher, "nutlikereel.png", 0.55).locate_once(image, "")
        self.assertIsNotNone(result)
        self.assertLess(result.y, image.shape[0] * 0.25)
        self.assertGreaterEqual(result.confidence, 0.55)

    def test_reel_crop_without_like_does_not_fall_through_to_save(self) -> None:
        image = imread_unicode(
            app_path("resources/job_references/reel/reel_action_rail_50x208.png")
        )
        image[:60, :] = 0
        matcher = TemplateMatcher(self.template_dir, 0.60, 0.60, 540, 960, 150)
        result = ReelLikeLocator(matcher, "nutlikereel.png", 0.55).locate_once(image, "")
        self.assertIsNone(result)

    def test_ui_label_uses_nearest_clickable_ancestor(self) -> None:
        xml = """<hierarchy>
          <node bounds="[40,500][150,550]" clickable="true" selected="false" checked="false">
            <node text="Thích" content-desc="Like" bounds="[70,510][110,535]" clickable="false" selected="false" checked="false" />
          </node>
          <node bounds="[200,500][330,550]" clickable="true" selected="false" checked="false">
            <node text="Bình luận" content-desc="Comment" bounds="[220,510][300,535]" clickable="false" selected="false" checked="false" />
          </node>
          <node bounds="[390,500][530,550]" clickable="true" selected="false" checked="false">
            <node text="Chia sẻ" content-desc="Share" bounds="[410,510][500,535]" clickable="false" selected="false" checked="false" />
          </node>
        </hierarchy>"""
        layout = UiTree.parse(xml).find_action_layout(
            "horizontal", screen_width=540, screen_height=960
        )
        self.assertIsNotNone(layout)
        self.assertEqual(layout.reaction.bounds, (40, 500, 150, 550))
        self.assertTrue(layout.reaction.clickable)

    def test_pixel_change_alone_never_confirms_action(self) -> None:
        before = np.zeros((200, 200, 3), dtype=np.uint8)
        after = before.copy()
        after[80:120, 80:120] = 255

        class Device:
            def capture_screen_image(self):
                return after

            def dump_ui_xml_text(self):
                return ""

        class Panel:
            def panel_is_open(self, *_args, **_kwargs):
                return False

        verification = ActionVerifier().verify(
            device=Device(),
            before_image=before,
            before_ui_xml="",
            coordinate=(100, 100),
            content_kind=ContentKind.FEED,
            internal_action="like",
            timeout_sec=0.12,
            stop_event=threading.Event(),
            panel_was_open=False,
            panel_locator=Panel(),
            like_location=SimpleNamespace(x=100, y=100),
            settle=lambda _seconds: None,
        )
        self.assertIsNot(verification.status, ActionVerificationStatus.CONFIRMED)

    def test_stale_coordinate_is_rejected(self) -> None:
        self.assertTrue(coordinates_stable((100, 100), (110, 110), 20))
        self.assertFalse(coordinates_stable((100, 100), (160, 100), 20))


if __name__ == "__main__":
    unittest.main()
