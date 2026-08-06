from __future__ import annotations

import configparser
import json
import os
import queue
import random
import re
import subprocess
import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

try:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tkinter.scrolledtext import ScrolledText
except ImportError as exc:  # pragma: no cover - only on Python builds without Tk
    raise SystemExit("Tkinter não está instalado. Use o instalador oficial do Python para Windows.") from exc

APP_NAME = "Universal MUGEN Bot"
APP_VERSION = "0.1.0"

MATCH_START_PATTERNS = (
    "Match loop init",
    "Loading match assets",
    "Entering versus screen",
    "Gameflow 11",
)
MATCH_END_PATTERNS = (
    "End of match loop",
    "Entering victory screen",
    "Match loop deinit",
)
CRASH_PATTERNS = (
    "Error loading",
    "Can't load",
    "Could not open",
    "Invalid state controller",
    "Error parsing",
)


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "UniversalMugenBot"
    return Path.home() / ".universal_mugen_bot"


def read_text_safely(path: Path) -> str:
    data = path.read_bytes()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def normalize_path_text(value: str) -> str:
    value = value.strip().strip('"').replace("\\", "/")
    value = re.sub(r"/+", "/", value)
    while value.startswith("./"):
        value = value[2:]
    return value


def strip_inline_comment(line: str) -> str:
    # MUGEN DEF files use ';' for comments. Paths containing ';' are extremely rare.
    return line.split(";", 1)[0].strip()


def find_case_insensitive(base: Path, relative: str) -> Optional[Path]:
    current = base
    for part in normalize_path_text(relative).split("/"):
        if not part or part == ".":
            continue
        if part == "..":
            current = current.parent
            continue
        direct = current / part
        if direct.exists():
            current = direct
            continue
        if not current.is_dir():
            return None
        lowered = part.casefold()
        match = next((p for p in current.iterdir() if p.name.casefold() == lowered), None)
        if match is None:
            return None
        current = match
    return current


def parse_loose_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False, allow_no_value=True)
    parser.optionxform = str.lower
    text = read_text_safely(path)
    try:
        parser.read_string(text)
    except configparser.Error:
        # Some screenpacks have duplicate or malformed lines. Keep only ordinary key=value lines.
        cleaned: list[str] = []
        has_section = False
        for raw in text.splitlines():
            line = raw.strip()
            if line.startswith("[") and line.endswith("]"):
                cleaned.append(line)
                has_section = True
            elif has_section and "=" in line and not line.startswith(";"):
                cleaned.append(raw)
        parser.read_string("\n".join(cleaned))
    return parser


@dataclass(frozen=True)
class CharacterEntry:
    command_path: str
    display_name: str
    source: str


@dataclass(frozen=True)
class StageEntry:
    command_path: str
    display_name: str
    source: str


@dataclass
class GameProfile:
    game_dir: str = ""
    executable: str = ""
    engine: str = "unknown"
    log_file: str = "mugen.log"
    config_file: str = ""
    system_file: str = ""
    select_file: str = ""
    launch_style: str = "auto"
    rounds: int = 1
    ai_level: int = 8
    delay_seconds: float = 2.0
    startup_timeout: int = 35
    match_timeout: int = 900
    scan_binary: bool = True
    characters: list[str] = field(default_factory=list)
    stages: list[str] = field(default_factory=list)
    disabled_characters: list[str] = field(default_factory=list)


class ProfileStore:
    def __init__(self) -> None:
        self.path = app_data_dir() / "profiles.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def load(self, game_dir: Path) -> Optional[GameProfile]:
        raw = self.load_all().get(str(game_dir.resolve()).casefold())
        if not raw:
            return None
        try:
            return GameProfile(**raw)
        except TypeError:
            return None

    def save(self, profile: GameProfile) -> None:
        all_profiles = self.load_all()
        key = str(Path(profile.game_dir).resolve()).casefold()
        all_profiles[key] = asdict(profile)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(all_profiles, indent=2, ensure_ascii=False), encoding="utf-8")
        temp.replace(self.path)


class EngineDetector:
    EXE_EXCLUDES = {
        "unins000.exe",
        "uninstall.exe",
        "mugenwatcher.exe",
        "universalmugenbot.exe",
        "universalmugenbot-debug.exe",
    }

    def __init__(self, game_dir: Path, logger: Callable[[str], None]) -> None:
        self.game_dir = game_dir.resolve()
        self.log = logger

    def detect(self) -> GameProfile:
        exe = self._find_executable()
        log_file = self._find_log_file()
        config_file = self._find_config_file()
        engine = self._detect_engine(exe, log_file)
        system_file = self._find_system_file(config_file)
        select_file = self._find_select_file(system_file)
        return GameProfile(
            game_dir=str(self.game_dir),
            executable=str(exe) if exe else "",
            engine=engine,
            log_file=str(log_file.relative_to(self.game_dir)) if log_file else "mugen.log",
            config_file=str(config_file.relative_to(self.game_dir)) if config_file else "",
            system_file=str(system_file.relative_to(self.game_dir)) if system_file else "",
            select_file=str(select_file.relative_to(self.game_dir)) if select_file else "",
            scan_binary=True,
        )

    def _find_executable(self) -> Optional[Path]:
        candidates: list[tuple[int, Path]] = []
        has_hook = any((self.game_dir / name).exists() for name in ("MugenhookSettings.ini", "MugenHookSettings.ini"))
        has_elecbyte = (self.game_dir / "Elecbyte.MUGEN.libs").exists()
        for path in self.game_dir.glob("*.exe"):
            if path.name.casefold() in self.EXE_EXCLUDES:
                continue
            name = path.name.casefold()
            score = 0
            if name == "ikemen_go.exe":
                score += 120
            elif name in {"mugen.exe", "winmugen.exe"}:
                score += 110
            elif "ikemen" in name:
                score += 100
            elif "mugen" in name:
                score += 90
            else:
                score += 25
            if has_hook:
                score += 35
            if has_elecbyte:
                score += 25
            try:
                size = path.stat().st_size
                if size > 5_000_000:
                    score += 15
                if size > 500_000_000:
                    score += 20
            except OSError:
                pass
            candidates.append((score, path))
        if not candidates:
            # Some packs put the executable one directory below the selected folder.
            for path in self.game_dir.glob("*/*.exe"):
                if path.name.casefold() not in self.EXE_EXCLUDES:
                    candidates.append((10, path))
        if not candidates:
            return None
        candidates.sort(key=lambda pair: (pair[0], pair[1].stat().st_size), reverse=True)
        selected = candidates[0][1]
        self.log(f"Executável detectado: {selected.name}")
        return selected

    def _find_log_file(self) -> Optional[Path]:
        common = [self.game_dir / "mugen.log", self.game_dir / "ikemen.log"]
        for path in common:
            if path.exists():
                return path
        logs = sorted(self.game_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
        return logs[0] if logs else None

    def _find_config_file(self) -> Optional[Path]:
        common = [
            self.game_dir / "data" / "mugen.cfg",
            self.game_dir / "mugen.cfg",
            self.game_dir / "data" / "config.json",
            self.game_dir / "save" / "config.json",
        ]
        for path in common:
            if path.exists():
                return path
        matches = list(self.game_dir.glob("**/mugen.cfg"))
        return min(matches, key=lambda p: len(p.parts)) if matches else None

    def _detect_engine(self, exe: Optional[Path], log_file: Optional[Path]) -> str:
        name = exe.name.casefold() if exe else ""
        if "ikemen" in name:
            return "IKEMEN GO"
        if any((self.game_dir / n).exists() for n in ("MugenhookSettings.ini", "MugenHookSettings.ini")):
            return "MUGEN 1.1 + MugenHook"
        if log_file and log_file.exists():
            head = read_text_safely(log_file)[:2500].casefold()
            if "ikemen" in head:
                return "IKEMEN GO"
            match = re.search(r"m\.u\.g\.e\.n ver ([^\r\n]+)", head, re.IGNORECASE)
            if match:
                return f"MUGEN {match.group(1).strip()}"
        if name in {"mugen.exe", "winmugen.exe"} or "mugen" in name:
            return "MUGEN"
        return "MUGEN personalizado"

    def _find_system_file(self, config_file: Optional[Path]) -> Optional[Path]:
        if config_file and config_file.suffix.casefold() == ".cfg":
            try:
                parser = parse_loose_ini(config_file)
                motif = ""
                for section in ("Options", "Config"):
                    if parser.has_section(section):
                        motif = parser.get(section, "motif", fallback="").strip()
                        if motif:
                            break
                if motif:
                    for base in (self.game_dir, config_file.parent, self.game_dir / "data"):
                        found = find_case_insensitive(base, motif)
                        if found and found.is_file():
                            return found
            except OSError:
                pass
        common = [
            self.game_dir / "data" / "system.def",
            self.game_dir / "data" / "MKP" / "system.def",
            self.game_dir / "Default" / "System.def",
            self.game_dir / "system.def",
        ]
        for path in common:
            if path.exists():
                return path
        matches = list(self.game_dir.glob("**/[Ss]ystem.def"))
        return min(matches, key=lambda p: len(p.parts)) if matches else None

    def _find_select_file(self, system_file: Optional[Path]) -> Optional[Path]:
        if system_file and system_file.exists():
            try:
                parser = parse_loose_ini(system_file)
                if parser.has_section("Files"):
                    select_name = parser.get("Files", "select", fallback="select.def").strip()
                    for base in (system_file.parent, self.game_dir / "data", self.game_dir):
                        found = find_case_insensitive(base, select_name)
                        if found and found.is_file():
                            return found
            except OSError:
                pass
        common = [
            self.game_dir / "data" / "select.def",
            self.game_dir / "data" / "MKP" / "select.def",
            self.game_dir / "select.def",
        ]
        for path in common:
            if path.exists():
                return path
        matches = list(self.game_dir.glob("**/[Ss]elect.def"))
        return min(matches, key=lambda p: len(p.parts)) if matches else None

