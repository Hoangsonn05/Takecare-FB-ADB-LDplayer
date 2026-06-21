from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.adb_device import ADBDevice
from core.config import ensure_default_config
from core.image_io import image_is_readable
from core.ldconsole import LDConsoleManager
from core.paths import app_path
from core.template_matcher import TemplateMatcher


def main() -> int:
    parser = argparse.ArgumentParser(description="Dry-run reaction screenshot and Like matching for one LDPlayer.")
    parser.add_argument("--index", type=int, default=4)
    parser.add_argument("--tap", action="store_true", help="Tap the matched Like coordinate. Default is dry-run only.")
    args = parser.parse_args()

    settings = ensure_default_config(app_path("configs/default.json"))
    manager = LDConsoleManager(settings.ld_base, settings.adb_port_base)
    device = ADBDevice(manager.adb_path, manager.serial_for_index(args.index), index=args.index)

    device.connect()
    path = app_path(f"screenshots/probe_ld_{args.index}.png")
    ok = device.screenshot(path)
    size = path.stat().st_size if path.exists() else 0
    readable = image_is_readable(path)

    print(f"screenshot: {path}")
    print(f"size: {size}")
    print(f"image_readable: {str(readable).lower()}")

    if not ok or not readable:
        return 1

    cfg = settings.runtime_config
    matcher = TemplateMatcher(app_path(cfg.template_dir), cfg.confidence_like, cfg.confidence_reaction)
    match, best = matcher.find_like_button(path)
    print(f"like_best_confidence: {best:.3f}")
    if match is None:
        print("like_match: none")
        return 2

    print(f"like_match: x={match.x}, y={match.y}")
    if args.tap:
        result = device.tap(match.x, match.y)
        print(f"tap: {'ok' if result.ok else result.brief()}")
        return 0 if result.ok else 3

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
