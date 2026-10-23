from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Iterable

from .golike_api import (
    CompleteJobResult,
    FacebookAccountContext,
    FacebookAccountInventory,
    GoLikeApiClient,
    GoLikeFacebookAccount,
    GoLikeIdentity,
    NormalizedJob,
    ReportJobResult,
)

_SENSITIVE_TEXT = re.compile(
    r"(?i)\b(authorization|auth[_-]?token|g-auth|signing_key|firebase_id|cookie)"
    r"\s*[:=]\s*([^\s,;]+)"
)
_BEARER_TEXT = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")


def sanitize_message(message: object, secrets: Iterable[str] = ()) -> str:
    text = str(message or "")
    text = _SENSITIVE_TEXT.sub(r"\1=[REDACTED]", text)
    text = _BEARER_TEXT.sub("Bearer [REDACTED]", text)
    for secret in secrets:
        value = str(secret or "")
        if value:
            text = text.replace(value, "[REDACTED]")
    return text[:1000]


@dataclass(slots=True, frozen=True)
class GoLikeAuthSnapshot:
    identity: GoLikeIdentity
    inventory: FacebookAccountInventory
    server: str


class GoLikeJobService:
    """Small facade shielding job automation from the API package layout."""

    def __init__(
        self,
        auth_token: str,
        *,
        timeout_seconds: float = 15.0,
        max_retries: int = 2,
    ) -> None:
        self._token = str(auth_token or "")
        self.client = GoLikeApiClient(
            auth_token=self._token,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )

    def close(self) -> None:
        self.client.close()
        self._token = ""

    def __enter__(self) -> "GoLikeJobService":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def authenticate(self) -> GoLikeAuthSnapshot:
        identity = self.client.verify_auth()
        inventory = self.client.get_facebook_account_inventory()
        server = self.client.get_publisher_server()
        return GoLikeAuthSnapshot(identity, inventory, server)

    def verify_auth(self) -> GoLikeIdentity:
        return self.client.verify_auth()

    def get_accounts(self) -> list[GoLikeFacebookAccount]:
        return self.client.get_facebook_accounts()

    def get_publisher_server(self) -> str:
        return self.client.get_publisher_server()

    def get_jobs(
        self,
        *,
        facebook_uid: str,
        server: str,
        low_job: int = 0,
    ) -> list[NormalizedJob]:
        return self.client.get_facebook_jobs(
            facebook_uid=facebook_uid,
            server=server,
            low_job=low_job,
        )

    def submit(
        self,
        *,
        job: NormalizedJob,
        facebook_uid: str,
        provider_account_id: str,
    ) -> CompleteJobResult:
        return self.client.complete_facebook_job(
            job=job,
            account=FacebookAccountContext(
                facebook_uid=facebook_uid,
                provider_account_id=provider_account_id,
            ),
        )

    def report(
        self,
        *,
        job_id: str,
        facebook_uid: str,
        description: str,
        error_type: int = 0,
    ) -> ReportJobResult:
        return self.client.report_facebook_job(
            users_advertising_id=job_id,
            facebook_uid=facebook_uid,
            description=description,
            error_type=error_type,
        )
