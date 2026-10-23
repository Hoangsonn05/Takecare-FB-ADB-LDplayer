"""Canonical GoLike job classification used by every API adapter and worker."""

from dataclasses import dataclass
from enum import Enum


class NormalizedJobType(str, Enum):
    REACTION = "reaction"
    FOLLOW = "follow"
    LIKE_PAGE = "like_page"
    UNKNOWN = "unknown"


class JobAction(str, Enum):
    LIKE = "like"
    LOVE = "love"
    CARE = "care"
    HAHA = "haha"
    WOW = "wow"
    SAD = "sad"
    ANGRY = "angry"
    FOLLOW = "follow"
    LIKE_PAGE = "like_page"


REACTION_ACTIONS = frozenset(
    {
        JobAction.LIKE,
        JobAction.LOVE,
        JobAction.CARE,
        JobAction.HAHA,
        JobAction.WOW,
        JobAction.SAD,
        JobAction.ANGRY,
    }
)


@dataclass(slots=True, frozen=True)
class JobMapping:
    provider_type: str
    normalized_type: NormalizedJobType
    action: JobAction
    submit_type: str
    reaction: str | None = None


JOB_MAPPINGS = (
    JobMapping("like", NormalizedJobType.REACTION, JobAction.LIKE, "like", "like"),
    JobMapping(
        "facebook_like_v1",
        NormalizedJobType.REACTION,
        JobAction.LIKE,
        "facebook_like_v1",
        "like",
    ),
    JobMapping("love", NormalizedJobType.REACTION, JobAction.LOVE, "love", "love"),
    JobMapping("care", NormalizedJobType.REACTION, JobAction.CARE, "care", "care"),
    JobMapping("haha", NormalizedJobType.REACTION, JobAction.HAHA, "haha", "haha"),
    JobMapping("wow", NormalizedJobType.REACTION, JobAction.WOW, "wow", "wow"),
    JobMapping("sad", NormalizedJobType.REACTION, JobAction.SAD, "sad", "sad"),
    JobMapping("angry", NormalizedJobType.REACTION, JobAction.ANGRY, "angry", "angry"),
    JobMapping("follow", NormalizedJobType.FOLLOW, JobAction.FOLLOW, "follow"),
    JobMapping(
        "follow_corona_0", NormalizedJobType.FOLLOW, JobAction.FOLLOW, "follow"
    ),
    JobMapping(
        "like_page", NormalizedJobType.LIKE_PAGE, JobAction.LIKE_PAGE, "like_page"
    ),
    JobMapping(
        "like_page_corona_0",
        NormalizedJobType.LIKE_PAGE,
        JobAction.LIKE_PAGE,
        "like_page",
    ),
)


def canonical_provider_type(value: object) -> str:
    """Normalize spelling only; never guess an unknown job from substrings."""
    return str(value or "").strip().casefold().replace("-", "_").replace(" ", "_")


MAPPING_BY_TYPE = {item.provider_type: item for item in JOB_MAPPINGS}


def mapping_for_provider_type(value: object) -> JobMapping | None:
    return MAPPING_BY_TYPE.get(canonical_provider_type(value))


def normalize_job_type(value: object) -> NormalizedJobType:
    mapping = mapping_for_provider_type(value)
    return mapping.normalized_type if mapping else NormalizedJobType.UNKNOWN


def is_action_compatible(
    normalized_type: NormalizedJobType, action: JobAction
) -> bool:
    if normalized_type is NormalizedJobType.REACTION:
        return action in REACTION_ACTIONS
    if normalized_type is NormalizedJobType.FOLLOW:
        return action is JobAction.FOLLOW
    if normalized_type is NormalizedJobType.LIKE_PAGE:
        return action is JobAction.LIKE_PAGE
    return False
