from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence, Union
import subprocess

ArgSeq = Sequence[Union[str, int, Path]]


@dataclass
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def brief(self) -> str:
        text = self.stdout.strip() or self.stderr.strip()
        return text[:500]


class CommandRunner:
    """Small subprocess wrapper with timeout, normalized args, and no shell=True."""

    def __init__(self, default_timeout: float = 20.0):
        self.default_timeout = default_timeout

    @staticmethod
    def normalize(args: ArgSeq) -> list[str]:
        return [str(a) for a in args]

    def run(
        self,
        args: ArgSeq,
        timeout: Optional[float] = None,
        cwd: Optional[Union[str, Path]] = None,
        env: Optional[Mapping[str, str]] = None,
        input_bytes: Optional[bytes] = None,
    ) -> CommandResult:
        norm = self.normalize(args)
        try:
            completed = subprocess.run(
                norm,
                input=input_bytes,
                capture_output=True,
                timeout=self.default_timeout if timeout is None else timeout,
                cwd=str(cwd) if cwd else None,
                env=dict(env) if env else None,
            )
            stdout = completed.stdout.decode("utf-8", errors="replace")
            stderr = completed.stderr.decode("utf-8", errors="replace")
            return CommandResult(norm, completed.returncode, stdout, stderr)
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or b"").decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else str(exc.stdout or "")
            stderr = (exc.stderr or b"").decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr or "")
            return CommandResult(norm, 124, stdout, stderr, timed_out=True)
        except FileNotFoundError as exc:
            return CommandResult(norm, 127, "", str(exc))
        except Exception as exc:  # defensive boundary for GUI workers
            return CommandResult(norm, 1, "", repr(exc))
