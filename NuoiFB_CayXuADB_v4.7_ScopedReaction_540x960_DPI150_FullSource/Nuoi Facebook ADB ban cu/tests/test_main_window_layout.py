from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QGroupBox, QTabWidget

from core.config import ALLOWED_REACTIONS, AppSettings, load_settings, save_settings
from gui.main_window import MainWindow


class MainWindowLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.config_path = Path(self.temp_dir.name) / "config.json"
        settings = AppSettings(database_path=str(Path(self.temp_dir.name) / "runs.sqlite3"))
        save_settings(settings, self.config_path)
        self.window = MainWindow(self.config_path)
        self.window.show()
        self.app.processEvents()

    def tearDown(self) -> None:
        self.window.timer.stop()
        self.window.auto_job_tab.shutdown()
        self.window.hide()
        self.window.deleteLater()
        self.app.processEvents()
        self.temp_dir.cleanup()

    def test_layout_at_common_window_sizes(self) -> None:
        run_tabs = next(tab for tab in self.window.findChildren(QTabWidget) if tab.tabText(0) == "Kich ban")
        self.assertEqual([run_tabs.tabText(i) for i in range(run_tabs.count())],
                         ["Kich ban", "Newfeed", "Cam xuc", "Reel", "Thong bao"])
        self.assertEqual(self.window.main_tabs.count(), 2)
        self.assertEqual(self.window.table.columnCount(), 12)
        self.assertEqual(self.window.observation_splitter.count(), 2)
        self.assertIs(self.window.observation_splitter.widget(0), self.window.table)
        self.assertIs(self.window.observation_splitter.widget(1), self.window.log)
        self.assertEqual(set(self.window.reaction_checks), set(ALLOWED_REACTIONS))

        for width, height in [(1366, 768), (1600, 900)]:
            with self.subTest(size=(width, height)):
                self.window.resize(width, height)
                self.app.processEvents()
                self.assertEqual(self.window.size().width(), width)
                self.assertEqual(self.window.size().height(), height)
                self.assertTrue(self.window.save_config_btn.isVisible())
                self.assertTrue(self.window.auto_job_tab.isVisible() is False)
                self.assertGreater(self.window.table.horizontalScrollBar().maximum(), 0)
                self.assertGreater(self.window.table.height(), self.window.log.height())
                for button in (self.window.scan_btn, self.window.clear_btn, self.window.connect_btn,
                               self.window.start_btn, self.window.stop_btn, self.window.pause_btn,
                               self.window.resume_btn):
                    self.assertTrue(button.isVisible(), button.text())
                    self.assertIsInstance(button.parentWidget(), QGroupBox)

        run_tabs.setCurrentIndex(2)
        self.app.processEvents()
        for checkbox in self.window.reaction_checks.values():
            self.assertTrue(checkbox.isVisible(), checkbox.text())
        self.assertEqual(len({id(c.parentWidget()) for c in self.window.reaction_checks.values()}), 1)
        before = self.window.observation_splitter.sizes()
        self.window.observation_splitter.moveSplitter(before[0] - 50, 1)
        self.app.processEvents()
        self.assertNotEqual(self.window.observation_splitter.sizes(), before)

    def test_save_config_round_trip(self) -> None:
        self.window.max_posts_spin.setValue(17)
        self.window.reaction_enabled_check.setChecked(True)
        self.window.reaction_checks["like"].setChecked(True)
        self.window.save_config_btn.click()
        saved = load_settings(self.config_path)
        self.assertEqual(saved.runtime_config.max_posts, 17)
        self.assertEqual(saved.runtime_config.selected_reactions, ["like"])
        self.assertTrue(saved.runtime_config.reaction_enabled)


if __name__ == "__main__":
    unittest.main()
