import os
from golike_api import GoLikeApiClient

with GoLikeApiClient(auth_token=os.environ["GOLIKE_AUTH_TOKEN"]) as client:
    account = client.get_facebook_accounts()[0]
    jobs = client.get_facebook_jobs(
        facebook_uid=account.facebook_uid,
        server=client.get_publisher_server(),
        low_job=0,
    )
    for job in jobs:
        print(job.id, job.provider_type, job.action.value)
