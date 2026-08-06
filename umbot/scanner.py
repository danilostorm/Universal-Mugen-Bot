from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from .core import (
    CharacterEntry,
    GameProfile,
    StageEntry,
    find_case_insensitive,
    normalize_path_text,
    read_text_safely,
    strip_inline_comment,
)

class RosterScanner:
    CHAR_LOG_RE = re.compile(r"(?:Loading character|Error reading character file:)\s+(chars[\\/].+?\.def)", re.IGNORECASE)
    STAGE_LOG_RE = re.compile(r"(?:Loading stage file|Stage file:)\s+(stages[\\/].+?\.def)", re.IGNORECASE)
    CHAR_BINARY_RE = re.compile(rb"(?i)(chars[\\/][A-Za-z0-9_ .!@#$%&'()+,=\-\[\]{}\\/]{1,220}?\.def)")
    STAGE_BINARY_RE = re.compile(rb"(?i)(stages[\\/][A-Za-z0-9_ .!@#$%&'()+,=\-\[\]{}\\/]{1,220}?\.def)")

    def __init__(self, profile: GameProfile, logger: Callable[[str], None]) -> None:
        self.profile = profile
        self.game_dir = Path(profile.game_dir)
        self.log = logger

    def scan(self, binary_scan: bool = True) -> tuple[list[CharacterEntry], list[StageEntry]]:
        chars: dict[str, CharacterEntry] = {}
        stages: dict[str, StageEntry] = {}

        def add_char(path: str, source: str) -> None:
            normalized = normalize_path_text(path)
            if not normalized or normalized.casefold() in {"empty", "randomselect", "random"}:
                return
            if Path(normalized).stem.casefold() in {"empty", "randomselect", "random"}:
                return
            if not normalized.casefold().startswith("chars/"):
                normalized = f"chars/{normalized}"
            if not normalized.casefold().endswith(".def"):
                normalized = self._resolve_character_token(normalized)
            if not normalized.casefold().endswith(".def"):
                return
            key = normalized.casefold()
            display = Path(normalized).stem.replace("_", " ")
            chars.setdefault(key, CharacterEntry(normalized, display, source))

        def add_stage(path: str, source: str) -> None:
            normalized = normalize_path_text(path)
            if not normalized or normalized.casefold() == "random":
                return
            if not normalized.casefold().startswith("stages/"):
                normalized = f"stages/{normalized}"
            if not normalized.casefold().endswith(".def"):
                return
            key = normalized.casefold()
            display = Path(normalized).stem.replace("_", " ")
            stages.setdefault(key, StageEntry(normalized, display, source))

        select_path = self.game_dir / self.profile.select_file if self.profile.select_file else None
        if select_path and select_path.exists():
            self.log(f"Lendo roster: {select_path.relative_to(self.game_dir)}")
            self._scan_select(select_path, add_char, add_stage)

        self._scan_directories(add_char, add_stage)

        log_path = self.game_dir / self.profile.log_file
        if log_path.exists():
            self._scan_log(log_path, add_char, add_stage)

        if binary_scan and self.profile.executable:
            exe = Path(self.profile.executable)
            if exe.exists():
                self._scan_binary(exe, add_char, add_stage)

        char_list = sorted(chars.values(), key=lambda item: item.display_name.casefold())
        stage_list = sorted(stages.values(), key=lambda item: item.display_name.casefold())
        self.log(f"Varredura concluída: {len(char_list)} personagens e {len(stage_list)} cenários.")
        return char_list, stage_list

    def _scan_select(
        self,
        path: Path,
        add_char: Callable[[str, str], None],
        add_stage: Callable[[str, str], None],
    ) -> None:
        section = ""
        for raw in read_text_safely(path).splitlines():
            line = strip_inline_comment(raw)
            if not line:
                continue
            if line.startswith("[") and line.endswith("]"):
                section = line[1:-1].strip().casefold()
                continue
            if section == "characters":
                token = line.split(",", 1)[0].strip()
                if token.casefold() not in {"empty", "randomselect", "random"}:
                    add_char(token, "select.def")
                parts = [part.strip() for part in line.split(",")]
                if len(parts) > 1 and parts[1].casefold().endswith(".def"):
                    add_stage(parts[1], "select.def")
            elif section in {"extrastages", "stages"}:
                token = line.split(",", 1)[0].strip()
                add_stage(token, "select.def")

    def _resolve_character_token(self, token: str) -> str:
        token = normalize_path_text(token)
        rel = token[6:] if token.casefold().startswith("chars/") else token
        char_root = self.game_dir / "chars"
        candidate_dir = find_case_insensitive(char_root, rel)
        if candidate_dir and candidate_dir.is_dir():
            same_name = find_case_insensitive(candidate_dir, f"{candidate_dir.name}.def")
            if same_name and same_name.is_file():
                return normalize_path_text(str(same_name.relative_to(self.game_dir)))
            defs = sorted(candidate_dir.glob("*.def"))
            if defs:
                return normalize_path_text(str(defs[0].relative_to(self.game_dir)))
        # Packed games may not expose chars/. Keep the conventional path.
        folder = Path(rel).name
        return f"chars/{rel}/{folder}.def"

    def _scan_directories(
        self,
        add_char: Callable[[str, str], None],
        add_stage: Callable[[str, str], None],
    ) -> None:
        char_root = self.game_dir / "chars"
        if char_root.exists():
            for path in char_root.rglob("*.def"):
                try:
                    add_char(str(path.relative_to(self.game_dir)), "pasta chars")
                except ValueError:
                    continue
        stage_root = self.game_dir / "stages"
        if stage_root.exists():
            for path in stage_root.rglob("*.def"):
                try:
                    add_stage(str(path.relative_to(self.game_dir)), "pasta stages")
                except ValueError:
                    continue

    def _scan_log(
        self,
        path: Path,
        add_char: Callable[[str, str], None],
        add_stage: Callable[[str, str], None],
    ) -> None:
        text = read_text_safely(path)
        for match in self.CHAR_LOG_RE.finditer(text):
            add_char(match.group(1), "mugen.log")
        for match in self.STAGE_LOG_RE.finditer(text):
            add_stage(match.group(1), "mugen.log")

    def _scan_binary(
        self,
        path: Path,
        add_char: Callable[[str, str], None],
        add_stage: Callable[[str, str], None],
    ) -> None:
        size = path.stat().st_size
        self.log(f"Procurando caminhos internos em {path.name} ({size / 1024 / 1024:.0f} MB)...")
        chunk_size = 16 * 1024 * 1024
        overlap = 512
        tail = b""
        last_report = 0
        with path.open("rb") as handle:
            processed = 0
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                data = tail + chunk
                for match in self.CHAR_BINARY_RE.finditer(data):
                    add_char(match.group(1).decode("latin-1", errors="ignore"), "executável empacotado")
                for match in self.STAGE_BINARY_RE.finditer(data):
                    add_stage(match.group(1).decode("latin-1", errors="ignore"), "executável empacotado")
                tail = data[-overlap:]
                processed += len(chunk)
                percent = int(processed * 100 / max(size, 1))
                if percent >= last_report + 10:
                    last_report = percent
                    self.log(f"Varredura do executável: {percent}%")


class LogTail:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.offset = 0
        self.identity: tuple[int, int] | None = None

    def reset_to_end(self) -> None:
        try:
            stat = self.path.stat()
            self.offset = stat.st_size
            self.identity = (stat.st_dev, stat.st_ino)
        except OSError:
            self.offset = 0
            self.identity = None

    def read_new(self) -> str:
        if not self.path.exists():
            return ""
        try:
            stat = self.path.stat()
            identity = (stat.st_dev, stat.st_ino)
            if self.identity != identity or stat.st_size < self.offset:
                self.offset = 0
                self.identity = identity
            with self.path.open("rb") as handle:
                handle.seek(self.offset)
                data = handle.read()
                self.offset = handle.tell()
            return data.decode("latin-1", errors="replace")
        except OSError:
            return ""

