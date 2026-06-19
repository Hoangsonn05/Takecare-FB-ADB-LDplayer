from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> int:
    try:
        from PySide6.QtGui import QFont
        from PySide6.QtWidgets import QApplication
        from core.paths import PROJECT_ROOT, app_path, ensure_runtime_dirs
        from gui.main_window import MainWindow
    except ImportError as exc:
        print("PySide6 is not installed. Install with: pip install -r requirements.txt")
        print(f"Import error: {exc}")
        return 1

    # Anchor all relative config/data paths inside the tool folder.
    os.chdir(PROJECT_ROOT)
    ensure_runtime_dirs()

    app = QApplication(sys.argv)
    app.setFont(QFont("Segoe UI", 9))
    window = MainWindow(config_path=app_path("configs/default.json"))
    window.resize(1200, 720)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
