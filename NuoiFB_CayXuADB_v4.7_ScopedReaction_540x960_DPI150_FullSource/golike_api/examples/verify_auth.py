import os
from golike_api import GoLikeApiClient

with GoLikeApiClient(auth_token=os.environ["GOLIKE_AUTH_TOKEN"]) as client:
    identity = client.verify_auth()
    print(identity.user_id, identity.username, identity.coin)
