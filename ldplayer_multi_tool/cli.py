from __future__ import annotations

import argparse
import queue
import time

from automation.scheduler import AutomationScheduler
from core.config import FACEBOOK_PACKAGE_NAME, ensure_default_config, load_settings


def parse_indices(text: str) -> list[int]:
    out: list[int] = []
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        out.append(int(part))
    return list(dict.fromkeys(out))


def main() -> None:
    parser = argparse.ArgumentParser(description="LDPlayer Facebook feed automation")
    parser.add_argument("--config", default="configs/default.json")
    parser.add_argument("--indices", required=True, help="Comma-separated LDPlayer indexes, e.g. 0,1,4")
    parser.add_argument("--profile", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--ld-base", default=None)
    # Kept hidden for old command compatibility; the value is ignored because Facebook is hardcoded.
    parser.add_argument("--package-name", default=None, help=argparse.SUPPRESS)
    args = parser.parse_args()

    ensure_default_config(args.config)
    settings = load_settings(args.config)
    if args.ld_base:
        settings.ld_base = args.ld_base
    settings.package_name = FACEBOOK_PACKAGE_NAME

    event_queue: queue.Queue = queue.Queue()
    scheduler = AutomationScheduler(settings, event_queue)
    scheduler.start(parse_indices(args.indices), settings.runtime_config)

    try:
        while scheduler.active_indices():
            try:
                event = event_queue.get(timeout=0.5)
                print(f"[{time.strftime('%H:%M:%S')}] [LD-{event.index}] [{event.level}] [{event.status}] {event.message}")
            except queue.Empty:
                pass
            scheduler.finalize_finished_run_if_any()
    except KeyboardInterrupt:
        print("Stopping...")
        scheduler.stop_all()
        while scheduler.active_indices():
            time.sleep(0.5)
    finally:
        scheduler.finalize_finished_run_if_any()


if __name__ == "__main__":
    main()
