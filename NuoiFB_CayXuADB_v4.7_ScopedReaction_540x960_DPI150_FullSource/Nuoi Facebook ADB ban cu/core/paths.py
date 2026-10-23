from __future__ import annotations

from pathlib import Path

# Absolute project root. Keep runtime data/config/screenshots inside the tool
# directory regardless of the current working directory used to launch app.py.
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def app_path(path: str | Path) -> Path:
    """Resolve a user/config path against the tool root when it is relative."""
    p = Path(str(path).strip().strip('"')).expanduser()
    if p.is_absolute():
        return p
    return PROJECT_ROOT / p


def ensure_runtime_dirs() -> None:
    for rel in ("configs", "data", "logs", "screenshots", "resources", "resources/templates"):
        (PROJECT_ROOT / rel).mkdir(parents=True, exist_ok=True)
