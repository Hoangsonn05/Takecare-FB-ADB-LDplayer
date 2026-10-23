from dataclasses import dataclass, field
from typing import Any, Mapping

from .mappings import JobAction


@dataclass(slots=True, frozen=True)
class GoLikeIdentity:
    user_id: str
    username: str
    coin: int | float | None
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True, frozen=True)
class GoLikeFacebookAccount:
    provider_account_id: str
    facebook_uid: str
    facebook_name: str
    status: int
    is_banned: bool
    jobs_can_work: Mapping[str, Any]
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)


@dataclass(slots=True, frozen=True)
class FacebookAccountInventory:
    total: int
    active: int
    inactive: int
    banned: int
    invalid: int
    accounts: tuple[GoLikeFacebookAccount, ...]


@dataclass(slots=True)
class NormalizedJob:
    id: str
    object_id: str | None
    link: str | None
    provider_type: str
    action: JobAction
    reaction: str | None
    reward: int | float | None
    raw: dict[str, Any]


@dataclass(slots=True, frozen=True)
class FacebookAccountContext:
    facebook_uid: str
    provider_account_id: str


@dataclass(slots=True, frozen=True)
class CompleteJobResult:
    status: int
    success: bool
    message: str | None
    reward: int | float | None
    raw: Mapping[str, Any]


@dataclass(slots=True, frozen=True)
class ReportJobResult:
    status: int
    success: bool
    skip: bool | None
    message: str | None
    server: str | None
    raw: Mapping[str, Any]
