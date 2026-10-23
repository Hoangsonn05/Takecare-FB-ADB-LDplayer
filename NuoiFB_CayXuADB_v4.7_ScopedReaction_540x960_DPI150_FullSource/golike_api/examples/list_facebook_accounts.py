import os
from golike_api import GoLikeApiClient

with GoLikeApiClient(auth_token=os.environ["GOLIKE_AUTH_TOKEN"]) as client:
    for account in client.get_facebook_accounts():
        print(account.provider_account_id, account.facebook_uid, account.facebook_name)
