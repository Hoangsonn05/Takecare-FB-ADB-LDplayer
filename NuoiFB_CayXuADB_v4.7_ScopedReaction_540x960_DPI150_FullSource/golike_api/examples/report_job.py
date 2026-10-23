import argparse
import os
from golike_api import GoLikeApiClient

parser = argparse.ArgumentParser()
parser.add_argument("job_id")
parser.add_argument("facebook_uid")
parser.add_argument("--confirm-live", action="store_true")
args = parser.parse_args()
if not args.confirm_live:
    raise SystemExit("Refusing live Report without --confirm-live")
with GoLikeApiClient(auth_token=os.environ["GOLIKE_AUTH_TOKEN"]) as client:
    print(
        client.report_facebook_job(
            users_advertising_id=args.job_id, facebook_uid=args.facebook_uid
        )
    )
