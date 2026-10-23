from __future__ import annotations

import base64
import getpass
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import quote

from .exceptions import GoLikeAuthenticationError
from .gauth import body_to_text, build_g_auth, normalize_signature_path


class AuthTokenMissingError(GoLikeAuthenticationError):
    pass


def normalize_auth_token(value: str) -> str:
    token = str(value or "").strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise AuthTokenMissingError("Thiếu AuthToken GoLike.")
    return token


def _stable_default_device_id() -> str:
    seed = f"golike:{uuid.getnode()}:{getpass.getuser()}"
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def normalize_device_id(value: str | None = None) -> str:
    candidate = str(value or "").strip()
    if not candidate:
        return _stable_default_device_id()
    return str(uuid.UUID(candidate))


def build_timestamp_header(timestamp: int | float | None = None) -> str:
    raw = str(int(time.time() if timestamp is None else timestamp)).encode("ascii")
    for _ in range(3):
        raw = base64.b64encode(raw)
    return raw.decode("ascii")


@dataclass(slots=True)
class GoLikeAuthContext:
    token: str = field(repr=False)
    username: str = ""
    device_id: str = ""
    user_id: str = ""
    signing_key: str = field(default="", repr=False)
    signing_key_exp: int | None = None

    def __post_init__(self) -> None:
        self.token = normalize_auth_token(self.token)
        self.device_id = normalize_device_id(self.device_id)

    @property
    def can_sign(self) -> bool:
        return bool(self.signing_key and self.user_id and self.device_id)

    def clear_signing_key(self) -> None:
        self.signing_key = ""
        self.signing_key_exp = None

    def base_headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json;charset=utf-8",
            "Authorization": f"Bearer {self.token}",
            "t": build_timestamp_header(),
            "g-device-id": self.device_id,
        }
        if self.username:
            headers["g-username"] = quote(self.username, safe="")
        return headers

    def headers_for_request(
        self,
        method: str,
        url: str,
        _params: Mapping[str, Any] | None,
        body: Mapping[str, Any] | str | None,
        *,
        require_signature: bool = False,
    ) -> Mapping[str, str]:
        headers = self.base_headers()
        if require_signature:
            if not self.can_sign:
                raise GoLikeAuthenticationError(
                    "Thiếu signing_key hoặc GoLike user ID để tạo g-auth."
                )
            headers["g-auth"] = build_g_auth(
                signing_key=self.signing_key,
                device_id=self.device_id,
                user_id=self.user_id,
                method=method,
                path=normalize_signature_path(url),
                body=body_to_text(body),
            )
        return headers
