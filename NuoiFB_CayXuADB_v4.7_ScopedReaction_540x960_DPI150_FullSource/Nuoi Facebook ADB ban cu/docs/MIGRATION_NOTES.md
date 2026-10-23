# Migration notes from the original one-file script

The uploaded source was a single CLI script that mixed LDPlayer control, ADB commands, scrolling logic, template matching, and terminal logging in one file. This project separates those responsibilities:

- `core/ldconsole.py`: LDPlayer executable control.
- `core/adb_device.py`: ADB device operations.
- `core/config.py`: JSON-backed settings/profile model.
- `automation/feed_runner.py`: one-device worker lifecycle.
- `automation/scheduler.py`: multi-device coordination.
- `gui/main_window.py`: PySide6 GUI.
- `storage/db.py`: SQLite event/history persistence.

The original social-platform engagement operations are not included. This package is intended as a generic owned-device/app QA automation base.
