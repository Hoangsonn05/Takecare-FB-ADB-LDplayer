from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import csv
import io
import os
import re

from .command_runner import CommandRunner, CommandResult

LD_EXE_NAME = "ld.exe"
LDCONSOLE_EXE_NAME = "ldconsole.exe"
ADB_EXE_NAME = "adb.exe"
REQUIRED_EXES = (LDCONSOLE_EXE_NAME, LD_EXE_NAME, ADB_EXE_NAME)


def _contains_tools(path: Path) -> bool:
    return all((path / exe).exists() for exe in REQUIRED_EXES)


def resolve_ld_base_path(raw_base: str | Path, max_depth: int = 3, max_dirs: int = 500) -> Path:
    """Resolve the folder that contains ldconsole.exe, ld.exe and adb.exe."""
    raw = Path(str(raw_base).strip().strip('"')).expanduser()
    if raw.name.lower() in {name.lower() for name in REQUIRED_EXES}:
        raw = raw.parent

    candidates: list[Path] = [raw]
    common_children = [
        "LDPlayer9",
        "LDPlayer",
        "LD9",
        "LD9Mod",
        "LDPlayer4.0",
        "dnplayer",
    ]
    candidates.extend(raw / child for child in common_children)
    if raw.parent and raw.parent != raw:
        candidates.append(raw.parent)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key in seen:
            continue
        seen.add(key)
        if _contains_tools(candidate):
            return candidate

    if not raw.exists() or not raw.is_dir():
        return raw

    root_depth = len(raw.parts)
    visited = 0
    try:
        for current, dirs, _files in os.walk(raw):
            current_path = Path(current)
            depth = len(current_path.parts) - root_depth
            if depth > max_depth:
                dirs[:] = []
                continue
            visited += 1
            if visited > max_dirs:
                break
            if _contains_tools(current_path):
                return current_path
    except OSError:
        pass
    return raw


@dataclass
class LDInstance:
    index: int
    name: str = ""
    is_running: Optional[bool] = None
    raw: str = ""
    adb_serial: str = ""
    source: str = ""

    @property
    def running(self) -> Optional[bool]:
        return self.is_running


class LDConsoleManager:
    def __init__(self, ld_base: str | Path, adb_port_base: int = 5555, runner: Optional[CommandRunner] = None):
        self.raw_ld_base = Path(str(ld_base).strip().strip('"')).expanduser()
        self.ld_base = resolve_ld_base_path(self.raw_ld_base)
        self.adb_port_base = adb_port_base
        self.runner = runner or CommandRunner()

    @property
    def ldconsole_path(self) -> Path:
        return self.ld_base / LDCONSOLE_EXE_NAME

    @property
    def ld_path(self) -> Path:
        return self.ld_base / LD_EXE_NAME

    @property
    def adb_path(self) -> Path:
        return self.ld_base / ADB_EXE_NAME

    def tools_exist(self) -> bool:
        ok, _missing = self.validate_paths()
        return ok

    def validate_paths(self) -> tuple[bool, list[str]]:
        missing: list[str] = []
        for p in [self.ldconsole_path, self.ld_path, self.adb_path]:
            if not p.exists():
                missing.append(str(p))
        return not missing, missing

    def missing_paths(self) -> list[str]:
        _ok, missing = self.validate_paths()
        return missing

    def path_status_message(self) -> str:
        ok, missing = self.validate_paths()
        if ok:
            if self.ld_base != self.raw_ld_base:
                return f"LDPlayer tools resolved: {self.ld_base}"
            return f"LDPlayer tools found: {self.ld_base}"
        return "Missing LDPlayer executable(s): " + "; ".join(missing)

    def serial_for_index(self, index: int) -> str:
        return f"localhost:{self.adb_port_base + 2 * int(index)}"

    @staticmethod
    def _csv_parts(line: str) -> list[str]:
        try:
            return next(csv.reader(io.StringIO(line)))
        except Exception:
            return line.split(",")

    @staticmethod
    def _parse_running(parts: list[str]) -> Optional[bool]:
        """Best-effort parse for LDPlayer `list2` output.

        Common format:
        index,name,topWindowHandle,bindWindowHandle,androidStarted,pid,pidVBox
        A positive pid or androidStarted=1 means the instance is running.
        """
        for pid_pos in (5, 6):
            if len(parts) > pid_pos:
                try:
                    if int(str(parts[pid_pos]).strip()) > 0:
                        return True
                except ValueError:
                    pass
        if len(parts) >= 5:
            value = str(parts[4]).strip().lower()
            if value in {"1", "true", "yes", "running"}:
                return True
            if value in {"0", "", "false", "no", "stopped"}:
                return False
        return None

    def _parse_list2_output(self, text: str) -> list[LDInstance]:
        instances: list[LDInstance] = []
        for line in text.splitlines():
            raw = line.strip()
            if not raw:
                continue
            parts = [p.strip() for p in self._csv_parts(raw)]
            try:
                idx = int(parts[0])
            except (ValueError, IndexError):
                continue
            name = parts[1] if len(parts) > 1 and parts[1] else f"LD-{idx}"
            instances.append(
                LDInstance(
                    index=idx,
                    name=name,
                    is_running=self._parse_running(parts),
                    raw=raw,
                    adb_serial=self.serial_for_index(idx),
                    source="list2",
                )
            )
        return instances

    def _looks_like_usage_output(self, result: CommandResult) -> bool:
        text = f"{result.stdout}\n{result.stderr}".lower()
        return (
            "usage:" in text
            or "commands :" in text
            or "dnconsole <command>" in text
            or "command line management interface" in text
        )

    def looks_like_usage_output(self, result: CommandResult) -> bool:
        return self._looks_like_usage_output(result)

    def _parse_list_output(self, text: str) -> list[LDInstance]:
        """Parse `ldconsole list` as a fallback.

        Some LDPlayer builds return only names, others return index,name. This
        parser accepts both and merges the result with list2.
        """
        instances: list[LDInstance] = []
        implicit_index = 0
        for line in text.splitlines():
            raw = line.strip()
            if not raw:
                continue
            parts = [p.strip() for p in self._csv_parts(raw)]
            idx: int
            name: str
            try:
                idx = int(parts[0])
                name = parts[1] if len(parts) > 1 and parts[1] else f"LD-{idx}"
            except (ValueError, IndexError):
                idx = implicit_index
                name = raw
            instances.append(
                LDInstance(
                    index=idx,
                    name=name or f"LD-{idx}",
                    is_running=None,
                    raw=raw,
                    adb_serial=self.serial_for_index(idx),
                    source="list",
                )
            )
            implicit_index = max(implicit_index + 1, idx + 1)
        return instances

    @staticmethod
    def _extract_index_from_name(name: str) -> Optional[int]:
        lowered = name.lower()
        patterns = [
            r"(?:leidian|ldplayer|dnplayer|index|instance|player)[_\- ]*(\d+)",
            r"^(\d+)$",
        ]
        for pattern in patterns:
            match = re.search(pattern, lowered)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    return None
        return None

    @staticmethod
    def _extract_profile_name_from_text(text: str, fallback: str) -> str:
        # LDPlayer config files vary by version. Try common keys without relying
        # on a strict JSON schema.
        key_patterns = [
            r'"(?:basicSettings\.)?(?:name|title)"\s*[:=]\s*"([^"]+)"',
            r'"(?:playerName|instanceName|name)"\s*[:=]\s*"([^"]+)"',
            r'(?:name|title|playerName)\s*=\s*([^\r\n]+)',
        ]
        for pattern in key_patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                value = match.group(1).strip().strip('"').strip()
                if value:
                    return value
        return fallback

    def _candidate_vms_dirs(self) -> list[Path]:
        candidates = [
            self.ld_base / "vms",
            self.raw_ld_base / "vms",
            self.ld_base.parent / "vms",
            self.raw_ld_base.parent / "vms",
        ]
        if self.ld_base.parent.parent != self.ld_base.parent:
            candidates.append(self.ld_base.parent.parent / "vms")
        if self.raw_ld_base.parent.parent != self.raw_ld_base.parent:
            candidates.append(self.raw_ld_base.parent.parent / "vms")
        unique: list[Path] = []
        seen: set[str] = set()
        for path in candidates:
            key = str(path).lower()
            if key not in seen:
                seen.add(key)
                unique.append(path)
        return unique

    def _scan_filesystem_instances(self) -> list[LDInstance]:
        """Fallback scan for all instances recorded by LDMultiplayer.

        This is intentionally best-effort. It scans common LDPlayer `vms`
        locations so the GUI can show stopped/offline profiles even when
        `ldconsole list2` is incomplete on a given build.
        """
        found: dict[int, LDInstance] = {}
        max_files = 1500
        scanned = 0
        for vms_dir in self._candidate_vms_dirs():
            if not vms_dir.exists() or not vms_dir.is_dir():
                continue
            # First, directory names such as leidian0, leidian1, ...
            try:
                for child in vms_dir.iterdir():
                    if scanned > max_files:
                        break
                    scanned += 1
                    if child.is_dir():
                        idx = self._extract_index_from_name(child.name)
                        if idx is not None and idx not in found:
                            found[idx] = LDInstance(
                                index=idx,
                                name=f"LD-{idx}",
                                is_running=None,
                                raw=str(child),
                                adb_serial=self.serial_for_index(idx),
                                source="filesystem",
                            )
            except OSError:
                continue

            # Then scan shallow config files that often contain all profiles.
            try:
                for file_path in vms_dir.rglob("*"):
                    if scanned > max_files:
                        break
                    scanned += 1
                    if not file_path.is_file():
                        continue
                    if file_path.suffix.lower() not in {".config", ".ini", ".json", ".cfg"}:
                        continue
                    idx = self._extract_index_from_name(file_path.stem) or self._extract_index_from_name(file_path.parent.name)
                    if idx is None:
                        continue
                    fallback_name = found[idx].name if idx in found else f"LD-{idx}"
                    try:
                        if file_path.stat().st_size <= 512_000:
                            text = file_path.read_text(encoding="utf-8", errors="ignore")
                            name = self._extract_profile_name_from_text(text, fallback_name)
                        else:
                            name = fallback_name
                    except OSError:
                        name = fallback_name
                    found[idx] = LDInstance(
                        index=idx,
                        name=name,
                        is_running=found[idx].is_running if idx in found else None,
                        raw=str(file_path),
                        adb_serial=self.serial_for_index(idx),
                        source="filesystem",
                    )
            except OSError:
                continue
        return [found[i] for i in sorted(found)]

    @staticmethod
    def _merge_instances(primary: list[LDInstance], secondary: list[LDInstance]) -> list[LDInstance]:
        merged: dict[int, LDInstance] = {inst.index: inst for inst in primary}
        for inst in secondary:
            current = merged.get(inst.index)
            if current is None:
                merged[inst.index] = inst
                continue
            if (not current.name or current.name == f"LD-{current.index}") and inst.name:
                current.name = inst.name
            if current.is_running is None and inst.is_running is not None:
                current.is_running = inst.is_running
            current.adb_serial = current.adb_serial or inst.adb_serial
            current.raw = current.raw or inst.raw
            current.source = "+".join(filter(None, [current.source, inst.source]))
        return [merged[i] for i in sorted(merged)]

    def list_instances(self) -> list[LDInstance]:
        instances: list[LDInstance] = []
        if self.ldconsole_path.exists():
            result2 = self.runner.run([self.ldconsole_path, "list2"], timeout=20, cwd=self.ld_base)
            if result2.ok and not self._looks_like_usage_output(result2):
                instances = self._merge_instances(instances, self._parse_list2_output(result2.stdout))

            result1 = self.runner.run([self.ldconsole_path, "list"], timeout=20, cwd=self.ld_base)
            if result1.ok and not self._looks_like_usage_output(result1):
                instances = self._merge_instances(instances, self._parse_list_output(result1.stdout))

        # Always run filesystem fallback: it can reveal stopped LDMultiplayer
        # profiles that list/list2 omit on some LDPlayer builds.
        instances = self._merge_instances(instances, self._scan_filesystem_instances())
        return instances

    def get_instance(self, index: int) -> Optional[LDInstance]:
        index = int(index)
        for inst in self.list_instances():
            if inst.index == index:
                return inst
        return None

    def is_instance_running(self, index: int) -> bool:
        inst = self.get_instance(index)
        if inst is not None and inst.is_running is not None:
            return bool(inst.is_running)
        serial = self.serial_for_index(index)
        result = self.runner.run([self.adb_path, "-s", serial, "get-state"], timeout=5)
        return result.ok and result.stdout.strip() == "device"

    def is_instance_running_by_ldconsole(self, index: int) -> bool | None:
        """
        Kiem tra LD instance co dang chay khong, chi dua vao ldconsole/list2.
        Return:
        - True neu ldconsole/list2 xac nhan dang chay
        - False neu ldconsole/list2 xac nhan dang tat
        - None neu khong xac dinh duoc
        Khong fallback ADB trong ham nay.
        """
        result = self.runner.run(
            [self.ldconsole_path, "isrunning", "--index", str(int(index))],
            timeout=15,
            cwd=self.ld_base,
        )
        text = f"{result.stdout}\n{result.stderr}".strip().lower()

        if result.ok and not self._looks_like_usage_output(result):
            if text in {"1", "true", "yes", "running"}:
                return True
            if text in {"0", "false", "no", "stopped"}:
                return False
            if "not running" in text or "stopped" in text:
                return False
            if "running" in text:
                return True

        inst = self.get_instance(index)
        if inst is None:
            return None
        if inst.is_running is None:
            return None
        return bool(inst.is_running)

    def launch(self, index: int) -> CommandResult:
        return self.runner.run(
            [self.ldconsole_path, "launch", "--index", str(int(index))],
            timeout=40,
            cwd=self.ld_base,
        )

    def launch_if_needed(self, index: int) -> tuple[bool, CommandResult | None, str]:
        running = self.is_instance_running_by_ldconsole(index)
        if running is True:
            return True, None, "LDPlayer instance is already running"
        result = self.launch(index)
        if result.ok:
            return True, result, "LDPlayer launch command sent"
        if self._looks_like_usage_output(result):
            return False, result, "LDPlayer launch command returned Usage/help output"
        running_after_fail = self.is_instance_running_by_ldconsole(index)
        if running_after_fail is True:
            return True, result, "LDPlayer launch returned non-zero but instance is running by ldconsole"
        return False, result, "LDPlayer launch returned non-zero"

    def quit(self, index: int) -> CommandResult:
        return self.runner.run(
            [self.ldconsole_path, "quit", "--index", str(int(index))],
            timeout=20,
            cwd=self.ld_base,
        )

    def run_app(self, index: int, package_name: str) -> CommandResult:
        if not package_name:
            return CommandResult(["runapp"], 1, "", "package_name is empty")
        return self.runner.run(
            [
                self.ldconsole_path,
                "runapp",
                "--index",
                str(int(index)),
                "--packagename",
                package_name,
            ],
            timeout=25,
            cwd=self.ld_base,
        )
