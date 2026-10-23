import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--confirm-live", action="store_true")
args = parser.parse_args()
if not args.confirm_live:
    raise SystemExit("Refusing live Submit without --confirm-live")
raise SystemExit(
    "Load and inspect a concrete job/account before calling complete_facebook_job()."
)
