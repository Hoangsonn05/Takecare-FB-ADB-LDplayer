from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch
import threading

import numpy as np

from automation.actions import SwipeCommand
from automation.feed_runner import FeedRunner
from automation.safe_swipe import SwipeObservation, observe_swipe_ui, verified_feed_movement
from core.config import AppSettings, RuntimeConfig


def observation(state="READY_FEED", y=600, image=None):
    return SwipeObservation(
        state=state,
        image=np.zeros((960, 540, 3), dtype=np.uint8) if image is None else image,
        anchors={"post-title": y, "post-body": y + 50},
        selected_tab=0 if state == "READY_FEED" else None,
    )


def feed_xml(content_rows, *, fixed_header=True):
    rows = "".join(
        f'<node package="com.facebook.katana" class="android.view.ViewGroup" '
        f'text="{label}" bounds="[0,{y}][540,{y + 30}]" />'
        for label, y in content_rows
    )
    header = (
        '<node package="com.facebook.katana" class="android.widget.Button" '
        'text="Fixed home navigation" bounds="[0,70][90,105]" />'
        '<node package="com.facebook.katana" class="android.widget.Button" '
        'text="Fixed page heading" bounds="[90,70][270,105]" />'
    ) if fixed_header else ""
    return (
        '<hierarchy><node package="com.facebook.katana" class="android.widget.FrameLayout" bounds="[0,0][540,960]">'
        '<node package="com.facebook.katana" class="androidx.recyclerview.widget.RecyclerView" '
        'scrollable="true" bounds="[0,23][540,960]">' + rows + '</node>' + header + '</node></hierarchy>'
    )


def observed_xml(xml, image=None):
    device = SimpleNamespace(
        get_state=lambda: "device", is_package_foreground=lambda _package: True,
        capture_screen_image=lambda: np.zeros((960, 540, 3), dtype=np.uint8) if image is None else image,
        dump_ui_xml_text=lambda: xml,
    )
    return observe_swipe_ui(device, "com.facebook.katana")


class FakeEvent:
    def __init__(self, on_wait=None):
        self.flag = False
        self.on_wait = on_wait

    def is_set(self):
        return self.flag

    def set(self):
        self.flag = True

    def wait(self, seconds):
        if self.on_wait:
            self.on_wait(seconds)
        return self.flag


class SafeSwipeTests(TestCase):
    def setUp(self):
        self.clock = [0.0]
        self.stop = FakeEvent(lambda seconds: self.clock.__setitem__(0, self.clock[0] + seconds))
        self.pause = FakeEvent(lambda _seconds: self.pause.__setattr__("flag", False))
        config = RuntimeConfig(reaction_enabled=False, reverse_scroll_percent=0)
        settings = AppSettings(ld_base="C:/unused", runtime_config=config)
        self.runner = FeedRunner(17, settings, config, self.stop, self.pause)
        self.runner.ld.verified_adb_serial_for_index = Mock(return_value="emulator-5588")
        self.runner.device = SimpleNamespace(
            index=17, serial="emulator-5588", get_state=Mock(return_value="device"),
            is_package_foreground=Mock(return_value=True),
            swipe=Mock(return_value=SimpleNamespace(ok=True)),
        )
        self.runner._save_swipe_screen = Mock()
        self.runner._sleep_interruptible = Mock()
        self.cmd = SwipeCommand(250, 750, 250, 270, 400, 0, "down")
        self.monotonic = patch("automation.feed_runner.time.monotonic", side_effect=lambda: self.clock[0])
        self.monotonic.start()
        self.addCleanup(self.monotonic.stop)

    def test_adb_success_but_no_confirmed_scroll_stops_without_counter(self):
        with patch("automation.feed_runner.observe_swipe_ui", return_value=observation()):
            self.assertFalse(self.runner._perform_swipe(self.cmd))
        self.assertEqual(self.runner.device.swipe.call_count, 1)
        self.assertEqual(self.runner.stats.swipe_count, 0)
        self.assertEqual(self.runner.stats.status, "WAITING_USER")

    def test_verified_feed_scroll_increments_only_after_ui_moves(self):
        changed = np.full((960, 540, 3), 30, dtype=np.uint8)
        with patch("automation.feed_runner.observe_swipe_ui", side_effect=[
            observation(), observation(), observation(y=300, image=changed),
        ]):
            self.assertTrue(self.runner._perform_swipe(self.cmd))
        self.assertEqual(self.runner.stats.swipe_count, 1)

    def test_video_viewer_blocks_second_swipe(self):
        with patch("automation.feed_runner.observe_swipe_ui", side_effect=[
            observation(), observation(), observation("VIDEO_VIEWER_OPENED"),
        ]):
            self.assertFalse(self.runner._perform_swipe(self.cmd))
        self.assertFalse(self.runner._perform_swipe(self.cmd))
        self.assertEqual(self.runner.device.swipe.call_count, 1)
        self.assertEqual(self.runner.stats.swipe_count, 0)

    def test_adb_timeout_is_error_even_below_consecutive_threshold(self):
        self.runner.device.swipe.return_value = SimpleNamespace(ok=False, brief=lambda: "timeout")
        with patch("automation.feed_runner.observe_swipe_ui", side_effect=[observation(), observation()]):
            self.assertFalse(self.runner._perform_swipe(self.cmd))
        self.assertEqual(self.runner.stats.status, "ERROR")
        self.assertEqual(self.runner.stats.swipe_count, 0)

    def test_loading_and_screenshot_error_send_no_more_commands(self):
        for state in ("UI_STILL_LOADING", "SCREEN_UNKNOWN"):
            with self.subTest(state=state):
                runner = self.runner
                runner._swipe_blocked = False
                runner.device.swipe.reset_mock()
                with patch("automation.feed_runner.observe_swipe_ui", return_value=observation(state)):
                    self.assertFalse(runner._perform_swipe(self.cmd))
                runner.device.swipe.assert_not_called()

    def test_identical_screenshot_after_xml_movement_is_not_verified(self):
        with patch("automation.feed_runner.observe_swipe_ui", side_effect=[
            observation(), observation(), observation(y=300),
        ]):
            self.assertFalse(self.runner._perform_swipe(self.cmd))
        self.assertEqual(self.runner.stats.swipe_count, 0)

    def test_stop_while_polling_prevents_next_action(self):
        calls = [observation(), observation(), observation()]
        def probe(*_args):
            item = calls.pop(0) if calls else observation()
            if not calls:
                self.stop.set()
            return item
        with patch("automation.feed_runner.observe_swipe_ui", side_effect=probe):
            self.assertFalse(self.runner._perform_swipe(self.cmd))
        self.assertFalse(self.runner._perform_swipe(self.cmd))
        self.assertEqual(self.runner.device.swipe.call_count, 1)

    def test_pause_resumes_with_fresh_observations(self):
        self.pause.set()
        self.stop.on_wait = lambda seconds: (self.clock.__setitem__(0, self.clock[0] + seconds), setattr(self.pause, "flag", False))
        changed = np.full((960, 540, 3), 30, dtype=np.uint8)
        probe = Mock(side_effect=[observation(), observation(), observation(y=300, image=changed)])
        with patch("automation.feed_runner.observe_swipe_ui", probe):
            self.assertTrue(self.runner._perform_swipe(self.cmd))
        self.assertEqual(probe.call_count, 3)

    def test_concurrent_swipe_is_rejected(self):
        self.runner._swipe_lock.acquire()
        try:
            self.assertFalse(self.runner._perform_swipe(self.cmd))
        finally:
            self.runner._swipe_lock.release()
        self.runner.device.swipe.assert_not_called()

    def test_reverse_scroll_failure_never_sends_return_swipe(self):
        self.runner.random_swipe_up = Mock(return_value=self.cmd)
        self.runner.random_swipe_down = Mock(return_value=self.cmd)
        self.runner._perform_swipe = Mock(return_value=False)
        self.assertFalse(self.runner._do_reverse_scroll())
        self.assertEqual(self.runner._perform_swipe.call_count, 1)

    def test_failed_post_does_not_increment_posts_processed(self):
        self.runner.config.run_minutes = 1
        self.runner._perform_swipe = Mock(return_value=False)
        self.runner.maybe_react_to_current_post = Mock()
        self.runner.run_newfeed_loop()
        self.assertEqual(self.runner.stats.posts_processed, 0)

    def test_autoplay_frame_change_is_not_scroll_evidence(self):
        before = observation()
        after = observation(image=np.full((960, 540, 3), 80, dtype=np.uint8))
        self.assertFalse(verified_feed_movement(before, after, -1))

    def test_structural_feed_observation_uses_xml_anchors(self):
        xml = '''<hierarchy>
        <node package="com.facebook.katana" class="android.widget.FrameLayout" bounds="[0,0][540,960]">
          <node package="com.facebook.katana" class="androidx.recyclerview.widget.RecyclerView" scrollable="true" bounds="[0,23][540,960]">
            <node package="com.facebook.katana" class="android.view.ViewGroup" text="Stable post title" bounds="[0,500][540,550]" />
            <node package="com.facebook.katana" class="android.view.ViewGroup" text="Stable post description" bounds="[0,550][540,600]" />
          </node>
          <node package="com.facebook.katana" class="android.view.View" selected="true" bounds="[0,64][90,105]" />
          <node package="com.facebook.katana" class="android.view.View" selected="false" bounds="[90,64][180,105]" />
          <node package="com.facebook.katana" class="android.view.View" selected="false" bounds="[180,64][270,105]" />
          <node package="com.facebook.katana" class="android.view.View" selected="false" bounds="[270,64][360,105]" />
        </node></hierarchy>'''
        device = SimpleNamespace(
            get_state=lambda: "device", is_package_foreground=lambda _package: True,
            capture_screen_image=lambda: np.zeros((960, 540, 3), dtype=np.uint8),
            dump_ui_xml_text=lambda: xml,
        )
        result = observe_swipe_ui(device, "com.facebook.katana")
        self.assertEqual(result.state, "READY_FEED")
        self.assertEqual(result.selected_tab, 0)
        self.assertEqual(len(result.anchors), 2)

    def test_wrong_app_never_becomes_ready(self):
        device = SimpleNamespace(get_state=lambda: "device", is_package_foreground=lambda _package: False)
        self.assertEqual(observe_swipe_ui(device, "com.facebook.katana").state, "SCREEN_UNKNOWN")

    def test_navigation_does_not_update_tab_after_unverified_tap(self):
        self.runner.current_tab = "reel"
        self.runner._capture_nav_screen = Mock(return_value=Path("screen.png"))
        self.runner.nav_matcher.find_top_nav_icon = Mock(return_value=(
            SimpleNamespace(x=45, y=85, template_name="home_tab.png"), 0.95,
        ))
        self.runner.device.tap = Mock(return_value=SimpleNamespace(ok=True))
        reel = observation("READY_REEL")
        reel.selected_tab = 1
        with patch("automation.feed_runner.observe_swipe_ui", return_value=reel):
            self.assertFalse(self.runner.enter_home_tab())
        self.assertEqual(self.runner.current_tab, "reel")
        self.assertEqual(self.runner.device.tap.call_count, 1)

    def test_feed_anchors_cross_old_top_cutoff_and_verify_scroll(self):
        before = observed_xml(feed_xml([
            ("Unique article author", 330), ("Unique article description", 390),
        ]))
        after = observed_xml(feed_xml([
            ("Unique article author", 30), ("Unique article description", 90),
            ("Next article author", 830), ("Next article description", 880),
        ]))
        self.assertEqual(after.state, "READY_FEED")
        self.assertTrue(verified_feed_movement(before, after, -1))

    def test_feed_anchors_remain_near_top_and_verify_only_real_motion(self):
        before = observed_xml(feed_xml([
            ("Top article author", 110), ("Top article description", 130),
        ]))
        after = observed_xml(feed_xml([
            ("Top article author", 35), ("Top article description", 55),
        ]))
        self.assertEqual(before.state, "READY_FEED")
        self.assertEqual(after.state, "READY_FEED")
        self.assertTrue(verified_feed_movement(before, after, -1))

    def test_fixed_header_is_not_a_feed_anchor_or_scroll_proof(self):
        before = observed_xml(feed_xml([
            ("Unchanged article author", 300), ("Unchanged article description", 360),
        ]))
        after = observed_xml(feed_xml([
            ("Unchanged article author", 300), ("Unchanged article description", 360),
        ], fixed_header=True), image=np.ones((960, 540, 3), dtype=np.uint8))
        self.assertEqual(len(before.anchors), 2)
        self.assertFalse(verified_feed_movement(before, after, -1))

    def test_replaced_post_without_shared_anchors_stays_unverified(self):
        before = observed_xml(feed_xml([
            ("Old article author", 300), ("Old article description", 360),
        ]))
        after = observed_xml(feed_xml([
            ("New article author", 300), ("New article description", 360),
        ]))
        self.assertEqual(after.state, "READY_FEED")
        self.assertFalse(verified_feed_movement(before, after, -1))

    def test_repeated_labels_in_multiple_posts_are_not_unique_anchors(self):
        snapshot = observed_xml(feed_xml([
            ("Repeated article title", 300), ("Repeated article title", 600),
            ("Only unique description", 350),
        ]))
        self.assertEqual(len(snapshot.anchors), 1)
        self.assertEqual(snapshot.state, "SCREEN_UNKNOWN")

    def test_xml_failure_and_stale_observation_do_not_send_swipe(self):
        self.assertEqual(observed_xml("").state, "SCREEN_UNKNOWN")
        stale = observation()
        stale.observed_at = -10
        with patch("automation.feed_runner.observe_swipe_ui", side_effect=[observation(), stale]):
            self.assertFalse(self.runner._perform_swipe(self.cmd))
        self.runner.device.swipe.assert_not_called()


if __name__ == "__main__":
    import unittest
    unittest.main()
