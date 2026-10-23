from collections.abc import Mapping
from typing import Any

from .exceptions import (
    GoLikeAuthenticationError,
    GoLikeResponseError,
    UnsupportedJobError,
)
from .job_types import (
    REACTION_ACTIONS,
    NormalizedJobType,
    canonical_provider_type,
    mapping_for_provider_type,
)
from .models import (
    CompleteJobResult,
    FacebookAccountInventory,
    GoLikeFacebookAccount,
    GoLikeIdentity,
    NormalizedJob,
    ReportJobResult,
)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_identity(payload: Mapping[str, Any]) -> GoLikeIdentity:
    if payload.get("status") not in (200, "200") or payload.get("success") is not True:
        raise GoLikeAuthenticationError(
            str(payload.get("message") or "GoLike từ chối xác thực.")
        )
    data = payload.get("data")
    if not isinstance(data, Mapping):
        raise GoLikeResponseError("Phản hồi xác thực thiếu object data.")
    user_id = str(data.get("id") or "").strip()
    username = str(data.get("username") or "").strip()
    if not user_id or not username:
        raise GoLikeResponseError("Phản hồi xác thực thiếu id hoặc username.")
    coin_raw = data.get("coin")
    try:
        coin = float(coin_raw) if coin_raw is not None else None
    except (TypeError, ValueError):
        coin = None
    safe_data = dict(data)
    safe_data.pop("firebase_id", None)
    safe_data.pop("signing_key", None)
    return GoLikeIdentity(user_id, username, coin, safe_data)


def parse_facebook_account_inventory(
    payload: Mapping[str, Any],
) -> FacebookAccountInventory:
    outer = payload.get("data")
    items = (
        outer
        if isinstance(outer, list)
        else outer.get("data")
        if isinstance(outer, Mapping)
        else None
    )
    if not isinstance(items, list):
        raise GoLikeResponseError(
            "Phản hồi account thiếu list tại data hoặc data.data."
        )
    accounts: list[GoLikeFacebookAccount] = []
    inactive = banned = invalid = 0
    for raw in items:
        if not isinstance(raw, Mapping):
            invalid += 1
            continue
        account_id = str(raw.get("id") or "").strip()
        uid = str(raw.get("fb_id") or "").strip()
        if not account_id or not uid:
            invalid += 1
            continue
        status = _int(raw.get("status"))
        is_banned = bool(_int(raw.get("is_banned")))
        if is_banned:
            banned += 1
            continue
        if status != 1:
            inactive += 1
            continue
        jobs = raw.get("jobs_can_work")
        accounts.append(
            GoLikeFacebookAccount(
                account_id,
                uid,
                str(raw.get("name") or raw.get("fb_name") or "").strip(),
                status,
                False,
                dict(jobs) if isinstance(jobs, Mapping) else {},
                dict(raw),
            )
        )
    return FacebookAccountInventory(
        len(items), len(accounts), inactive, banned, invalid, tuple(accounts)
    )


def parse_facebook_accounts(payload: Mapping[str, Any]) -> list[GoLikeFacebookAccount]:
    return list(parse_facebook_account_inventory(payload).accounts)


def normalize_job(raw: Mapping[str, Any]) -> NormalizedJob:
    provider_type = canonical_provider_type(raw.get("type"))
    mapping = mapping_for_provider_type(provider_type)
    if mapping is None:
        raise UnsupportedJobError(
            f"Chưa có mapping cho job type: {provider_type or '<empty>'}"
        )
    job_id = raw.get("id") or raw.get("job_id")
    if job_id in (None, ""):
        raise GoLikeResponseError("Job không có id.")
    reaction_raw = raw.get("reaction")
    if mapping.normalized_type is NormalizedJobType.REACTION:
        reaction = (
            str(reaction_raw).strip().casefold()
            if reaction_raw not in (None, "")
            else mapping.reaction
        )
        allowed_reactions = {action.value for action in REACTION_ACTIONS}
        if reaction not in allowed_reactions:
            raise GoLikeResponseError(
                f"Reaction không hợp lệ cho {provider_type}: {reaction!r}"
            )
    else:
        reaction = None
    reward = (
        raw.get("reward") if raw.get("reward") is not None else raw.get("fix_coin_job")
    )
    return NormalizedJob(
        str(job_id),
        str(raw["object_id"]) if raw.get("object_id") is not None else None,
        str(raw["link"]) if raw.get("link") else None,
        provider_type,
        mapping.action,
        reaction,
        reward,
        dict(raw),
    )


def parse_complete_result(payload: Mapping[str, Any]) -> CompleteJobResult:
    status = _int(payload.get("status"))
    success = payload.get("success") is True
    if status != 200 or not success:
        raise GoLikeResponseError(
            str(payload.get("message") or "API từ chối Submit Job.")
        )
    data = payload.get("data")
    reward = data.get("prices") if isinstance(data, Mapping) else None
    return CompleteJobResult(
        status, success, payload.get("message"), reward, dict(payload)
    )


def parse_report_result(payload: Mapping[str, Any]) -> ReportJobResult:
    status = _int(payload.get("status"))
    success = payload.get("success") is True
    if status != 200 or not success:
        raise GoLikeResponseError(
            str(payload.get("message") or "API từ chối Report Job.")
        )
    skip = payload.get("skip") if isinstance(payload.get("skip"), bool) else None
    return ReportJobResult(
        status,
        success,
        skip,
        payload.get("message"),
        payload.get("server"),
        dict(payload),
    )
