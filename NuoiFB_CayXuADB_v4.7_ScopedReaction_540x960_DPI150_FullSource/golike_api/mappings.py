"""Backward-compatible exports for the canonical job type registry."""

from .job_types import (  # noqa: F401
    JOB_MAPPINGS,
    MAPPING_BY_TYPE,
    REACTION_ACTIONS,
    JobAction,
    JobMapping,
    NormalizedJobType,
    canonical_provider_type,
    mapping_for_provider_type,
    normalize_job_type,
)
