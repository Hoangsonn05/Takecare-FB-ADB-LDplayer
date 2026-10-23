from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.adb_device import ADBDevice
from core.config import load_settings
from core.ldconsole import LDConsoleManager
from core.paths import app_path


def main() -> int:
    settings = load_settings(app_path("configs/default.json"))
    ld = LDConsoleManager(settings.ld_base, settings.adb_port_base)

    for index in [13, 14]:
        serial = ld.serial_for_index(index)
        dev = ADBDevice(ld.adb_path, serial, index=index)

        print("==== LD", index, serial, "====")
        print("ldconsole running:", ld.is_instance_running_by_ldconsole(index))
        print("adb connect:", dev.connect())
        print("adb state:", dev.get_state())
        print("adb devices:")
        print(dev.devices_list())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
