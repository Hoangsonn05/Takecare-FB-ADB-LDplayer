# GoLike live API package

Self-contained Python package using the standard library, `requests` and `cryptography`.

```bash
pip install requests cryptography
```

```python
from golike_api import GoLikeApiClient, FacebookAccountContext

client = GoLikeApiClient(auth_token="JWT")
identity = client.verify_auth()
accounts = client.get_facebook_accounts()
server = client.get_publisher_server()
jobs = client.get_facebook_jobs(
    facebook_uid=accounts[0].facebook_uid,
    server=server,
    low_job=0,
)
result = client.complete_facebook_job(
    job=jobs[0],
    account=FacebookAccountContext(
        facebook_uid=accounts[0].facebook_uid,
        provider_account_id=accounts[0].provider_account_id,
    ),
)
report = client.report_facebook_job(
    users_advertising_id=jobs[0].id,
    facebook_uid=accounts[0].facebook_uid,
)
```

Live endpoints are centralized in `constants.py`. Report uses `POST /api/report/send`
with exactly this captured body:

```json
{
  "description": "Tôi không muốn làm Job này",
  "users_advertising_id": "JOB_ID",
  "type": "ads",
  "fb_id": "FACEBOOK_UID",
  "error_type": 0,
  "provider": "facebook",
  "comment": null
}
```

`type="ads"` identifies the advertising report object; it is not the job action.
Dynamic authorization headers are generated at runtime. `verify_auth()` calls
`GET /api/users/me`, reads `data.firebase_id` as the Gateway signing key, and keeps
it only in memory. The current frontend removes that field from the public user
object and stores it as `state.signing_key`. The separate
`/api/v1/security/session` key is not used by this package because it belongs to a
different security-token signature flow.

Each Accounts/Server/Get/Submit/Report request gets a fresh `g-auth` value generated
with HKDF-SHA256 and AES-256-GCM over a compact JSON envelope containing method,
pathname, body hash, user ID, device ID, timestamp and nonce. Query parameters are
not included in the signed pathname, while POST body hashing uses exactly the
compact JSON string sent on the wire.

Never persist or log tokens, signing keys, cookies, `g-auth`, or full headers. Submit
and Report examples require an explicit `--confirm-live` flag.
