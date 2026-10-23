"""GoLike ``g-auth`` request signature compatible with the current web client.

The implementation mirrors the browser contract discovered in the production
bundle: HKDF-SHA256 -> AES-256-GCM over a compact JSON envelope.  A fresh IV
and nonce are generated for every request, therefore signatures are never
reused or persisted.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

G_AUTH_SALT = "glk-gauth-v3-2026q3"
G_AUTH_INFO = "aes-gcm-key"

_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


def compact_json(value: Mapping[str, Any] | Any) -> str:
    """Serialize exactly like ``JSON.stringify`` for supported API payloads."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def body_to_text(body: Mapping[str, Any] | str | None) -> str:
    if body is None:
        return ""
    if isinstance(body, str):
        return body
    return compact_json(body)


def base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def base64url_decode(value: str) -> bytes:
    text = str(value or "").strip()
    padding = "=" * ((4 - len(text) % 4) % 4)
    return base64.urlsafe_b64decode(text + padding)


def decode_signing_key(value: str) -> bytes:
    """Decode a 32-byte signing key supplied as hex or Base64URL."""

    text = str(value or "").strip()
    if not text:
        raise ValueError("signing_key rỗng hoặc không phải chuỗi.")

    attempts: list[str] = []
    if len(text) % 2 == 0 and _HEX_RE.fullmatch(text):
        try:
            decoded = bytes.fromhex(text)
            attempts.append(f"hex:{len(decoded)}")
            if len(decoded) == 32:
                return decoded
        except ValueError:
            attempts.append("hex:error")

    try:
        decoded = base64url_decode(text)
        attempts.append(f"b64url:{len(decoded)}")
        if len(decoded) == 32:
            return decoded
    except (ValueError, base64.binascii.Error):
        attempts.append("b64url:error")

    raise ValueError(
        "signing_key phải giải mã thành đúng 32 byte (AES-256); " + ", ".join(attempts)
    )


def derive_aes_key(signing_key: str) -> bytes:
    material = decode_signing_key(signing_key)
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=G_AUTH_SALT.encode("utf-8"),
        info=G_AUTH_INFO.encode("utf-8"),
    ).derive(material)


def normalize_signature_path(url_or_path: str) -> str:
    text = str(url_or_path or "").strip()
    if not text:
        return "/"
    parsed = urlsplit(text)
    path = parsed.path if parsed.scheme or parsed.netloc else text.split("?", 1)[0]
    path = path.split("#", 1)[0]
    return path if path.startswith("/") else f"/{path}"


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _coerce_user_id(value: str | int) -> str | int:
    text = str(value).strip()
    if text.isdigit():
        return int(text)
    if not text:
        raise ValueError("Thiếu GoLike user ID để tạo g-auth.")
    return text


def build_g_auth(
    *,
    signing_key: str,
    device_id: str,
    user_id: str | int,
    method: str,
    path: str,
    body: Mapping[str, Any] | str | None = None,
    timestamp_ms: int | None = None,
    nonce: bytes | None = None,
    iv: bytes | None = None,
) -> str:
    """Create one production-compatible ``g-auth`` header value.

    ``timestamp_ms``, ``nonce`` and ``iv`` exist for deterministic tests only.
    Production callers must leave them unset.
    """

    device = str(device_id or "").strip()
    if not device:
        raise ValueError("Thiếu g-device-id để tạo g-auth.")

    method_value = str(method or "GET").upper()
    path_value = normalize_signature_path(path)
    body_text = body_to_text(body)
    body_hash = sha256_hex(body_text)
    now_ms = int(time.time() * 1000) if timestamp_ms is None else int(timestamp_ms)
    nonce_bytes = os.urandom(16) if nonce is None else bytes(nonce)
    iv_bytes = os.urandom(12) if iv is None else bytes(iv)
    if len(nonce_bytes) != 16:
        raise ValueError("Nonce g-auth phải dài 16 byte.")
    if len(iv_bytes) != 12:
        raise ValueError("IV AES-GCM phải dài 12 byte.")

    checksum_source = f"{now_ms}:{device}:{body_hash}:{G_AUTH_SALT}"
    envelope = {
        "t": now_ms,
        "x": base64url_encode(nonce_bytes),
        "d": device,
        "u": _coerce_user_id(user_id),
        "n": method_value,
        "k": path_value,
        "q": body_hash,
        "r": sha256_hex(checksum_source)[:16],
    }
    plaintext = compact_json(envelope).encode("utf-8")
    ciphertext_and_tag = AESGCM(derive_aes_key(signing_key)).encrypt(
        iv_bytes, plaintext, None
    )
    return base64url_encode(iv_bytes + ciphertext_and_tag)


def decode_g_auth_for_test(value: str, signing_key: str) -> dict[str, Any]:
    """Decrypt a generated token for unit tests and diagnostics."""

    raw = base64url_decode(value)
    if len(raw) < 28:
        raise ValueError("g-auth quá ngắn.")
    plaintext = AESGCM(derive_aes_key(signing_key)).decrypt(raw[:12], raw[12:], None)
    decoded = json.loads(plaintext.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Envelope g-auth không phải object.")
    return decoded
