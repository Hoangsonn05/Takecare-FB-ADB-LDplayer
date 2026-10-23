from typing import Any

from .constants import (
    GOLIKE_REPORT_DEFAULT_DESCRIPTION,
    GOLIKE_REPORT_DEFAULT_ERROR_TYPE,
    GOLIKE_REPORT_PROVIDER,
    GOLIKE_REPORT_TYPE,
)
from .exceptions import GoLikeResponseError, UnsupportedJobError
from .mappings import MAPPING_BY_TYPE, REACTION_ACTIONS
from .models import FacebookAccountContext, NormalizedJob


def _positive_int(value: int | str, field: str) -> int:
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} không hợp lệ.") from exc
    if result <= 0:
        raise ValueError(f"{field} không hợp lệ.")
    return result


def build_complete_job_payload(
    *, job: NormalizedJob, account: FacebookAccountContext
) -> dict[str, Any]:
    if not job.object_id:
        raise GoLikeResponseError("Job thiếu object_id.")
    mapping = MAPPING_BY_TYPE.get(job.provider_type)
    if mapping is None:
        raise UnsupportedJobError(f"Chưa có mapping submit cho {job.provider_type}.")
    api_reaction = (job.reaction or mapping.reaction or job.action.value).lower()
    submit_type = (
        api_reaction if job.action in REACTION_ACTIONS else mapping.submit_type
    )
    payload: dict[str, Any] = {
        "object_id": job.object_id,
        "job_id": _positive_int(job.id, "job_id"),
        "type": submit_type,
        "uid": str(account.facebook_uid).strip(),
        "users_fb_account_id": _positive_int(
            account.provider_account_id, "users_fb_account_id"
        ),
        "users_advertising_id": _positive_int(job.id, "users_advertising_id"),
        "message": None,
    }
    if not payload["uid"]:
        raise ValueError("facebook_uid không được để trống.")
    if job.action in REACTION_ACTIONS:
        payload["reaction"] = api_reaction
    return payload


def build_report_payload(
    *,
    users_advertising_id: int | str,
    facebook_uid: str,
    description: str = GOLIKE_REPORT_DEFAULT_DESCRIPTION,
    error_type: int = GOLIKE_REPORT_DEFAULT_ERROR_TYPE,
    comment: str | None = None,
) -> dict[str, Any]:
    advertising_id = _positive_int(users_advertising_id, "users_advertising_id")
    uid = str(facebook_uid).strip()
    if not uid:
        raise ValueError("facebook_uid không được để trống.")
    text = str(description or "").strip() or GOLIKE_REPORT_DEFAULT_DESCRIPTION
    return {
        "description": text,
        "users_advertising_id": advertising_id,
        "type": GOLIKE_REPORT_TYPE,
        "fb_id": uid,
        "error_type": int(error_type),
        "provider": GOLIKE_REPORT_PROVIDER,
        "comment": comment,
    }
