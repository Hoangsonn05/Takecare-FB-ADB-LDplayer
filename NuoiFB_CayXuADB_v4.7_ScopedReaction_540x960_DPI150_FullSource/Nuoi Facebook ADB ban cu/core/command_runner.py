from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional, Sequence, Union
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


@dataclass
class BinaryCommandResult:
    args: list[str]
    returncode: int
    stdout: bytes
    stderr: bytes
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def brief(self) -> str:
        raw = self.stdout or self.stderr
        return raw[:500].decode("utf-8", errors="replace")


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
        result = self.run_bytes(
            args,
            timeout=timeout,
            cwd=cwd,
            env=env,
            input_bytes=input_bytes,
        )
        return CommandResult(
            args=result.args,
            returncode=result.returncode,
            stdout=result.stdout.decode("utf-8", errors="replace"),
            stderr=result.stderr.decode("utf-8", errors="replace"),
            timed_out=result.timed_out,
        )

    def run_bytes(
        self,
        args: ArgSeq,
        timeout: Optional[float] = None,
        cwd: Optional[Union[str, Path]] = None,
        env: Optional[Mapping[str, str]] = None,
        input_bytes: Optional[bytes] = None,
    ) -> BinaryCommandResult:
        """Run a command without shell expansion and preserve binary stdout."""
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
            return BinaryCommandResult(
                norm,
                completed.returncode,
                completed.stdout or b"",
                completed.stderr or b"",
            )
        except subprocess.TimeoutExpired as exc:
            stdout = exc.stdout if isinstance(exc.stdout, bytes) else str(exc.stdout or "").encode()
            stderr = exc.stderr if isinstance(exc.stderr, bytes) else str(exc.stderr or "").encode()
            return BinaryCommandResult(norm, 124, stdout or b"", stderr or b"", timed_out=True)
        except FileNotFoundError as exc:
            return BinaryCommandResult(norm, 127, b"", str(exc).encode("utf-8", errors="replace"))
        except Exception as exc:  # defensive boundary for GUI workers
            return BinaryCommandResult(norm, 1, b"", repr(exc).encode("utf-8", errors="replace"))
