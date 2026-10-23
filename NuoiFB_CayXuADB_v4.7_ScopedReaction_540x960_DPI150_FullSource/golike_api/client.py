from __future__ import annotations

import random
import time
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any
from urllib.parse import urljoin

import requests

from .auth import GoLikeAuthContext
from .constants import (
    DEFAULT_MAX_RETRIES,
    DEFAULT_TIMEOUT_SECONDS,
    GOLIKE_BASE_URL,
    GOLIKE_COMPLETE_JOBS_PATH,
    GOLIKE_DEFAULT_PUBLISHER_SERVER,
    GOLIKE_FB_ACCOUNTS_PATH,
    GOLIKE_GATEWAY_SIGNING_KEY_FIELD,
    GOLIKE_GET_JOBS_PATH,
    GOLIKE_PUBLISHER_SERVER_PATH,
    GOLIKE_REPORT_DEFAULT_DESCRIPTION,
    GOLIKE_REPORT_DEFAULT_ERROR_TYPE,
    GOLIKE_REPORT_PATH,
    GOLIKE_VERIFY_PATH,
)
from .exceptions import (
    GoLikeApiError,
    GoLikeAuthenticationError,
    GoLikeRateLimitError,
    GoLikeResponseError,
    UnsupportedJobError,
)
from .gauth import compact_json, decode_signing_key
from .mappings import JobAction
from .models import (
    CompleteJobResult,
    FacebookAccountContext,
    FacebookAccountInventory,
    GoLikeFacebookAccount,
    GoLikeIdentity,
    NormalizedJob,
    ReportJobResult,
)
from .parsers import (
    normalize_job,
    parse_complete_result,
    parse_facebook_account_inventory,
    parse_identity,
    parse_report_result,
)
from .payloads import build_complete_job_payload, build_report_payload

_SIGNED_PATHS = frozenset(
    {
        GOLIKE_FB_ACCOUNTS_PATH,
        GOLIKE_PUBLISHER_SERVER_PATH,
        GOLIKE_GET_JOBS_PATH,
        GOLIKE_COMPLETE_JOBS_PATH,
        GOLIKE_REPORT_PATH,
    }
)


class GoLikeApiClient:
    def __init__(
        self,
        *,
        auth_token: str,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        max_retries: int = DEFAULT_MAX_RETRIES,
        device_id: str | None = None,
    ) -> None:
        self.auth_context = GoLikeAuthContext(
            token=auth_token, device_id=device_id or ""
        )
        self.timeout_seconds = float(timeout_seconds)
        self.max_retries = max(0, int(max_retries))
        self.session = requests.Session()
        self.task_server = GOLIKE_DEFAULT_PUBLISHER_SERVER
        self.config = SimpleNamespace(
            base_url=GOLIKE_BASE_URL,
            get_job_path=GOLIKE_GET_JOBS_PATH,
            submit_job_path=GOLIKE_COMPLETE_JOBS_PATH,
            report_job_path=GOLIKE_REPORT_PATH,
        )

    def __enter__(self) -> "GoLikeApiClient":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.auth_context.clear_signing_key()
        self.session.close()

    def verify_auth(self) -> GoLikeIdentity:
        """Validate the token and bootstrap the Gateway signing context.

        The current web client exposes the 32-byte Gateway signing material in
        ``data.firebase_id`` from ``/api/users/me``.  Despite the historical
        field name, that value is the key consumed by the ``cA`` g-auth
        algorithm.  It is kept only in memory and never returned by this
        method or written to configuration.
        """

        identity, key, exp = self._load_gateway_identity()
        self.auth_context.username = identity.username
        self.auth_context.user_id = identity.user_id
        self._set_signing_key(key, exp)
        return identity

    def _load_gateway_identity(self) -> tuple[GoLikeIdentity, str, int | None]:
        payload = self._request("GET", GOLIKE_VERIFY_PATH, require_signature=False)
        identity = parse_identity(payload)
        key, exp = self._extract_signing_key(payload)
        if not key:
            raise GoLikeAuthenticationError(
                "Phản hồi /api/users/me thiếu firebase_id dùng làm Gateway signing_key."
            )
        return identity, key, exp

    def get_facebook_account_inventory(
        self, *, limit: int = 200
    ) -> FacebookAccountInventory:
        return parse_facebook_account_inventory(
            self._request(
                "GET",
                GOLIKE_FB_ACCOUNTS_PATH,
                params={"limit": int(limit)},
                require_signature=True,
            )
        )

    def get_facebook_accounts(self, *, limit: int = 200) -> list[GoLikeFacebookAccount]:
        return list(self.get_facebook_account_inventory(limit=limit).accounts)

    def refresh_signing_key(self, *, force: bool = False) -> str:
        """Refresh the Gateway key from ``/api/users/me``.

        Do not use ``/api/v1/security/session`` here.  That endpoint belongs to
        the separate Node security-token flow and its key is derived with a
        different HKDF contract.
        """

        now = int(time.time())
        if not force and self.auth_context.signing_key:
            exp = self.auth_context.signing_key_exp
            if exp is None or exp - 30 > now:
                return self.auth_context.signing_key

        identity, key, exp = self._load_gateway_identity()
        self.auth_context.username = identity.username
        self.auth_context.user_id = identity.user_id
        self._set_signing_key(key, exp)
        return key

    ensure_signing_key = refresh_signing_key

    def get_publisher_server(self) -> str:
        try:
            payload = self._request("GET", GOLIKE_PUBLISHER_SERVER_PATH)
            value = payload.get("data")
            if (
                payload.get("success") is True
                and isinstance(value, str)
                and value.strip()
            ):
                self.task_server = value.strip()
        except GoLikeAuthenticationError:
            raise
        except GoLikeApiError:
            if not self.task_server:
                self.task_server = GOLIKE_DEFAULT_PUBLISHER_SERVER
        return self.task_server

    resolve_task_server = get_publisher_server

    def get_facebook_jobs(
        self, *, facebook_uid: str, server: str, low_job: int = 0
    ) -> list[NormalizedJob]:
        uid = str(facebook_uid or "").strip()
        if not uid:
            raise GoLikeResponseError("Thiếu Facebook UID khi lấy job.")
        try:
            low_job_mode = int(low_job)
        except (TypeError, ValueError) as exc:
            raise GoLikeResponseError("low_job phải là 0 hoặc 1.") from exc
        if low_job_mode not in (0, 1):
            raise GoLikeResponseError("low_job phải là 0 hoặc 1.")
        params: dict[str, Any] = {
            "fb_id": uid,
            "server": str(server).strip(),
            "low_job": low_job_mode,
        }
        payload = self._request("GET", GOLIKE_GET_JOBS_PATH, params=params)
        if payload.get("success") is not True or int(payload.get("status", 0)) != 200:
            raise GoLikeResponseError(
                str(payload.get("message") or "API từ chối Get Job.")
            )
        data = payload.get("data")
        if data in (None, [], {}):
            return []
        if not isinstance(data, list):
            raise GoLikeResponseError("Dữ liệu Get Job không phải list JSON.")
        jobs: list[NormalizedJob] = []
        for raw in data:
            if not isinstance(raw, Mapping):
                continue
            try:
                jobs.append(normalize_job(raw))
            except (UnsupportedJobError, GoLikeResponseError):
                continue
        return jobs

    def get_jobs_for_facebook_account(
        self, *, facebook_uid: str, server: str | None = None, low_job: int = 0
    ) -> list[NormalizedJob]:
        return self.get_facebook_jobs(
            facebook_uid=facebook_uid,
            server=server or self.task_server,
            low_job=low_job,
        )

    def build_golike_submit_payload(
        self, *, job: NormalizedJob, facebook_uid: str, provider_account_id: str | int
    ) -> dict[str, Any]:
        return build_complete_job_payload(
            job=job,
            account=FacebookAccountContext(str(facebook_uid), str(provider_account_id)),
        )

    build_complete_job_payload = build_golike_submit_payload

    def complete_facebook_job(
        self, *, job: NormalizedJob, account: FacebookAccountContext
    ) -> CompleteJobResult:
        body = build_complete_job_payload(job=job, account=account)
        payload = self._request("POST", GOLIKE_COMPLETE_JOBS_PATH, json_body=body)
        result = parse_complete_result(payload)
        self._validate_submit_identity(payload, body)
        return result

    def submit_job_context(
        self, *, job: NormalizedJob, facebook_uid: str, provider_account_id: str | int
    ) -> dict[str, Any]:
        return dict(
            self.complete_facebook_job(
                job=job,
                account=FacebookAccountContext(
                    str(facebook_uid), str(provider_account_id)
                ),
            ).raw
        )

    def report_facebook_job(
        self,
        *,
        users_advertising_id: int | str,
        facebook_uid: str,
        description: str = GOLIKE_REPORT_DEFAULT_DESCRIPTION,
        error_type: int = GOLIKE_REPORT_DEFAULT_ERROR_TYPE,
        comment: str | None = None,
    ) -> ReportJobResult:
        body = build_report_payload(
            users_advertising_id=users_advertising_id,
            facebook_uid=facebook_uid,
            description=description,
            error_type=error_type,
            comment=comment,
        )
        return parse_report_result(
            self._request("POST", GOLIKE_REPORT_PATH, json_body=body)
        )

    @staticmethod
    def _validate_submit_identity(
        response: Mapping[str, Any], expected: Mapping[str, Any]
    ) -> None:
        data = response.get("data")
        if not isinstance(data, Mapping):
            return
        checks = {
            "fb_id": expected["uid"],
            "object_id": expected["object_id"],
            "type": expected["type"],
            "users_fb_account_id": expected["users_fb_account_id"],
        }
        for field, wanted in checks.items():
            if field in data and str(data[field]) != str(wanted):
                raise GoLikeResponseError(f"SUBMIT_RESPONSE_MISMATCH: {field}")

    @staticmethod
    def _extract_signing_key(
        payload: Mapping[str, Any],
    ) -> tuple[str | None, int | None]:
        """Read the Gateway signing key without exposing it to callers.

        Production currently returns the value as ``data.firebase_id``.  The
        legacy ``signing_key`` name is accepted only for compatible fixtures
        and future server migrations.
        """

        candidates: list[Mapping[str, Any]] = []
        data = payload.get("data")
        if isinstance(data, Mapping):
            candidates.append(data)
        candidates.append(payload)

        for item in candidates:
            key = item.get(GOLIKE_GATEWAY_SIGNING_KEY_FIELD) or item.get(
                "signing_key"
            )
            if not key:
                continue
            exp_raw = item.get("exp") or item.get("expires_at")
            try:
                exp = int(exp_raw) if exp_raw is not None else None
            except (TypeError, ValueError):
                exp = None
            return str(key).strip(), exp
        return None, None

    def _set_signing_key(self, key: str, exp: int | None) -> None:
        decode_signing_key(key)
        self.auth_context.signing_key = str(key).strip()
        self.auth_context.signing_key_exp = exp

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Mapping[str, Any] | None = None,
        require_signature: bool | None = None,
    ) -> dict[str, Any]:
        url = urljoin(GOLIKE_BASE_URL.rstrip("/") + "/", path.lstrip("/"))
        method = method.upper()
        signed = (
            path in _SIGNED_PATHS if require_signature is None else require_signature
        )
        body_text = compact_json(json_body) if json_body is not None else ""
        last_error: Exception | None = None
        signature_refreshed = False

        for attempt in range(self.max_retries + 1):
            if signed:
                self.refresh_signing_key(force=False)
            headers = self.auth_context.headers_for_request(
                method,
                url,
                params,
                body_text,
                require_signature=signed,
            )
            try:
                response = self.session.request(
                    method=method,
                    url=url,
                    params=params,
                    data=body_text if json_body is not None else None,
                    headers=headers,
                    timeout=self.timeout_seconds,
                    verify=True,
                )
                if response.status_code == 401:
                    raise GoLikeAuthenticationError(
                        "AuthToken hết hạn hoặc không hợp lệ: HTTP 401"
                    )
                if response.status_code == 403:
                    if signed and not signature_refreshed:
                        signature_refreshed = True
                        self.auth_context.clear_signing_key()
                        self.refresh_signing_key(force=True)
                        continue
                    detail = (
                        "Request có g-auth vẫn bị từ chối: HTTP 403"
                        if signed
                        else "Xác thực bị từ chối: HTTP 403"
                    )
                    raise GoLikeAuthenticationError(detail)
                if response.status_code == 429:
                    if attempt >= self.max_retries:
                        raise GoLikeRateLimitError("API giới hạn tần suất request.")
                    time.sleep(self._retry_after(response, attempt))
                    continue
                if 500 <= response.status_code < 600:
                    if attempt >= self.max_retries:
                        raise GoLikeResponseError(
                            f"Lỗi máy chủ API: HTTP {response.status_code}"
                        )
                    time.sleep(self._backoff(attempt))
                    continue
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise GoLikeResponseError("API không trả JSON hợp lệ.") from exc
                if not isinstance(payload, dict):
                    raise GoLikeResponseError("JSON phản hồi không phải object.")
                return payload
            except (
                requests.Timeout,
                requests.ConnectionError,
                requests.HTTPError,
            ) as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(self._backoff(attempt))
        raise GoLikeApiError(f"Request thất bại sau khi retry: {last_error}")

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(2**attempt, 16) + random.uniform(0, 0.5)

    @classmethod
    def _retry_after(cls, response: requests.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After")
        try:
            return max(float(value), 0.0) if value else cls._backoff(attempt)
        except ValueError:
            return cls._backoff(attempt)


GoLikeClient = GoLikeApiClient
GoLikeTaskApiClient = GoLikeApiClient


def create_golike_client(auth_token: str) -> GoLikeApiClient:
    return GoLikeApiClient(auth_token=auth_token)


def select_next_supported_job(
    jobs: Sequence[NormalizedJob],
    enabled_actions: set[JobAction],
    skipped_until: Mapping[JobAction, float],
) -> NormalizedJob | None:
    now = time.time()
    return next(
        (
            job
            for job in jobs
            if job.action in enabled_actions
            and float(skipped_until.get(job.action, 0)) <= now
        ),
        None,
    )
