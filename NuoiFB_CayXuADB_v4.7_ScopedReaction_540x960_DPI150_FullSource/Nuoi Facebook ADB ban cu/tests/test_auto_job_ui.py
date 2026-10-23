from __future__ import annotations

import os
from types import SimpleNamespace
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from automation.jobs.models import DeviceAccountBinding
from core.config import AppSettings, RuntimeConfig
from gui.tabs.auto_job_tab import AutoJobTab


class AutoJobUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        settings = AppSettings(
            ld_base=".",
            runtime_config=RuntimeConfig(
                run_minutes=1,
                run_newfeed=True,
                run_reel=False,
                run_notifications=False,
            ),
        )
        self.tab = AutoJobTab(settings)

    def tearDown(self) -> None:
        self.tab.shutdown()
        self.tab.deleteLater()
        self.app.processEvents()

    def test_compact_layout_controls_and_config_mapping(self) -> None:
        self.tab.resize(1366, 700)
        self.tab.show()
        self.app.processEvents()
        self.assertEqual(self.tab.monitor_tabs.count(), 2)
        self.assertEqual(self.tab.top_splitter.count(), 2)
        self.assertFalse(self.tab.start_btn.isEnabled())
        self.assertTrue(self.tab.start_btn.isVisible())
        self.assertTrue(self.tab.stop_btn.isVisible())
        self.tab.job_route_timeout_spin.setValue(42.0)
        self.tab.feed_swipes_spin.setValue(3)
        self.tab.screen_width_spin.setValue(540)
        self.tab.screen_height_spin.setValue(960)
        self.tab.screen_dpi_spin.setValue(150)
        self.assertTrue(self.tab.low_job_fallback_check.isChecked())
        self.assertTrue(self.tab.low_job_fallback_spin.isEnabled())
        self.tab.low_job_fallback_spin.setValue(5)
        config = self.tab._config_from_ui()
        self.assertEqual(config.job_route_timeout_sec, 42.0)
        self.assertEqual(config.feed_max_search_swipes, 3)
        self.assertEqual(config.screen_width_px, 540)
        self.assertEqual(config.screen_height_px, 960)
        self.assertEqual(config.screen_dpi, 150)
        self.assertTrue(config.low_job_fallback_enabled)
        self.assertEqual(config.low_job_fallback_after_empty, 5)
        self.tab.low_job_fallback_check.setChecked(False)
        self.assertFalse(self.tab.low_job_fallback_spin.isEnabled())
        disabled = self.tab._config_from_ui()
        self.assertFalse(disabled.low_job_fallback_enabled)
        self.assertFalse(hasattr(self.tab, "deeplink_retry_spin"))
        self.assertFalse(hasattr(self.tab, "deeplink_retry_delay_spin"))

    def test_start_requires_auth_and_binding(self) -> None:
        self.tab.auth_snapshot = SimpleNamespace()
        self.tab._update_controls()
        self.assertFalse(self.tab.start_btn.isEnabled())
        self.tab.controller.bind(
            DeviceAccountBinding(
                ld_index=1,
                adb_serial="localhost:5557",
                facebook_uid="123456789",
                provider_account_id="99",
                facebook_name="Tester",
            )
        )
        self.tab._render_bindings()
        self.assertTrue(self.tab.start_btn.isEnabled())

    def test_scan_result_keeps_ld_placeholder_selected(self) -> None:
        self.tab.scan_generation = 7
        instances = [
            SimpleNamespace(index=2, name="LD-2", running=True),
            SimpleNamespace(index=4, name="LD-4", running=False),
        ]
        self.tab._scan_finished(7, (SimpleNamespace(), instances), None)
        self.assertEqual(self.tab.ld_combo.count(), 3)
        self.assertEqual(self.tab.ld_combo.currentIndex(), 0)
        self.assertIsNone(self.tab.ld_combo.currentData())


if __name__ == "__main__":
    unittest.main()
