from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import load_settings
from core.ldconsole import LDConsoleManager
from core.paths import app_path


def main() -> int:
    settings = load_settings(app_path("configs/default.json"))
    ld = LDConsoleManager(settings.ld_base, settings.adb_port_base)

    index = 4

    print("ld_base:", ld.ld_base)
    print("paths:", ld.validate_paths())

    for args in [
        [ld.ldconsole_path, "list2"],
        [ld.ldconsole_path, "isrunning", "--index", str(index)],
        [ld.ldconsole_path, "launch", "--index", str(index)],
    ]:
        result = ld.runner.run(args, timeout=30, cwd=ld.ld_base)
        print("ARGS:", result.args)
        print("RETURN:", result.returncode)
        print("STDOUT:", result.stdout[:500])
        print("STDERR:", result.stderr[:500])
        print("---")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
