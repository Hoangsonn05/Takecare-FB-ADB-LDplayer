from typing import Any

SENSITIVE_KEYS = {
    "authorization",
    "auth_token",
    "authtoken",
    "token",
    "g-auth",
    "cookie",
    "set-cookie",
    "access_token",
    "refresh_token",
    "password",
    "secret",
    "signing_key",
    "firebase_id",
    "device_token",
    "verify_code",
}


def redact_sensitive_data(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]"
            if str(key).casefold() in SENSITIVE_KEYS
            else redact_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive_data(item) for item in value]
    return value
