"""Controlled LD-17 Newfeed probe. Run only after the external preflight gates pass."""

from __future__ import annotations

import argparse
import threading

from automation.feed_runner import FeedRunner
from core.adb_device import ADBDevice
from core.config import AppSettings, RuntimeConfig
from core.resource_locks import GLOBAL_RESOURCE_LOCKS


TARGET_INDEX = 17
EXPECTED_SERIAL = "emulator-5588"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-swipes", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.max_swipes <= 3:
        parser.error("--max-swipes must be 1..3")

    config = RuntimeConfig(
        reaction_enabled=False, reel_reaction_enabled=False,
        run_newfeed=True, run_reel=False, run_notifications=False,
        reverse_scroll_percent=0, auto_quit_ld_after_run=False,
    )
    settings = AppSettings(runtime_config=config)
    stop = threading.Event()
    pause = threading.Event()
    owner = "safe-swipe-ld17-harness"
    if not GLOBAL_RESOURCE_LOCKS.claim_ld(TARGET_INDEX, owner):
        print("BLOCKED: LD-17 already claimed in this process")
        return 2
    try:
        runner = FeedRunner(
            TARGET_INDEX, settings, config, stop, pause,
            emit=lambda event: print(event.type, event.status, event.message),
        )
        serial = runner.ld.verified_adb_serial_for_index(TARGET_INDEX)
        if serial != EXPECTED_SERIAL:
            print("BLOCKED: unexpected serial", serial)
            return 2
        runner.device = ADBDevice(runner.ld.adb_path, serial, index=TARGET_INDEX)
        if runner.device.get_state() != "device" or not runner.device.is_package_foreground(settings.package_name):
            print("BLOCKED: ADB or Facebook is not ready")
            return 2
        print("BOUND", TARGET_INDEX, serial)
        for number in range(1, args.max_swipes + 1):
            if stop.is_set() or runner._swipe_blocked:
                break
            print("ATTEMPT", number)
            if not runner._perform_swipe(runner.random_swipe_down()):
                break
        print("RESULT", runner.stats.status, "VERIFIED_SWIPES", runner.stats.swipe_count)
        return 0 if runner.stats.swipe_count > 0 and not runner._swipe_blocked else 1
    finally:
        GLOBAL_RESOURCE_LOCKS.release_ld(TARGET_INDEX, owner)


if __name__ == "__main__":
    raise SystemExit(main())
