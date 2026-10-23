from __future__ import annotations

from pathlib import Path
import sys

# Keep one canonical API package at the repository root. This adapter makes it
# importable when app.py is launched from the tool subdirectory and when
# PyInstaller is given the repository root through --paths.
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from golike_api import (  # noqa: E402,F401
    CompleteJobResult,
    FacebookAccountContext,
    FacebookAccountInventory,
    GoLikeApiClient,
    GoLikeApiError,
    GoLikeAuthenticationError,
    GoLikeFacebookAccount,
    GoLikeIdentity,
    GoLikeRateLimitError,
    GoLikeResponseError,
    JobAction,
    NormalizedJob,
    NormalizedJobType,
    REACTION_ACTIONS,
    ReportJobResult,
    UnsupportedJobError,
    redact_sensitive_data,
)

__all__ = [
    "CompleteJobResult",
    "FacebookAccountContext",
    "FacebookAccountInventory",
    "GoLikeApiClient",
    "GoLikeApiError",
    "GoLikeAuthenticationError",
    "GoLikeFacebookAccount",
    "GoLikeIdentity",
    "GoLikeRateLimitError",
    "GoLikeResponseError",
    "JobAction",
    "NormalizedJob",
    "NormalizedJobType",
    "REACTION_ACTIONS",
    "ReportJobResult",
    "UnsupportedJobError",
    "redact_sensitive_data",
]
