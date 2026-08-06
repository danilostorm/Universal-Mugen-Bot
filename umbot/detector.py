from __future__ import annotations

import re
from pathlib import Path
from typing import Callable, Optional

from .core import GameProfile, find_case_insensitive, parse_loose_ini, read_text_safely


class EngineDetector:
    """Detecta o executável real, mesmo quando o usuário seleciona a pasta-pai."""

    EXE_EXCLUDES = {
        "unins000.exe",
        "uninstall.exe",
        "mugenwatcher.exe",
        "universalmugenbot.exe",
        "universalmugenbot-debug.exe",
    }

    def __init__(self, game_dir: Path, logger: Callable[[str], None]) -> None:
        self.selected_dir = game_dir.resolve()
        self.game_dir = self.selected_dir
        self.log = logger

    def detect(self) -> GameProfile:
        exe = self._find_executable()
        if exe:
            self.game_dir = exe.parent.resolve()
            if self.game_dir != self.selected_dir:
                self.log(f"Pasta real do jogo detectada automaticamente: {self.game_dir}")

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

    def _candidate_executables(self) -> list[Path]:
        candidates: list[Path] = []
        patterns = ("*.exe", "*/*.exe", "*/*/*.exe")
        seen: set[str] = set()
        for pattern in patterns:
            for path in self.selected_dir.glob(pattern):
                if not path.is_file() or path.name.casefold() in self.EXE_EXCLUDES:
                    continue
                key = str(path.resolve()).casefold()
                if key not in seen:
                    seen.add(key)
                    candidates.append(path)
        return candidates

    def _score_executable(self, path: Path) -> tuple[int, int]:
        name = path.name.casefold()
        folder = path.parent
        score = 0

        if name == "ikemen_go.exe":
            score += 150
        elif name in {"mugen.exe", "winmugen.exe"}:
            score += 130
        elif "ikemen" in name:
            score += 120
        elif "mugen" in name:
            score += 100
        else:
            score += 35

        # Sinais encontrados ao lado do executável real do jogo.
        if any((folder / n).exists() for n in ("MugenhookSettings.ini", "MugenHookSettings.ini")):
            score += 100
        if (folder / "Elecbyte.MUGEN.libs").exists():
            score += 80
        if (folder / "data").is_dir():
            score += 45
        if (folder / "plugins").is_dir():
            score += 20
        if (folder / "mugen.log").exists() or (folder / "ikemen.log").exists():
            score += 45
        if folder.name.casefold() in {"game", "mugen", "ikemen", "bin"}:
            score += 25

        try:
            size = path.stat().st_size
        except OSError:
            size = 0

        # Em packs empacotados, o executável verdadeiro costuma ser muito maior
        # que o launcher localizado na pasta-pai.
        if size >= 500_000_000:
            score += 180
        elif size >= 100_000_000:
            score += 100
        elif size >= 20_000_000:
            score += 55
        elif size >= 5_000_000:
            score += 20
        elif size < 4_000_000:
            score -= 15

        depth = len(path.relative_to(self.selected_dir).parts) - 1
        score -= max(0, depth - 2) * 10
        return score, size

    def _find_executable(self) -> Optional[Path]:
        candidates = self._candidate_executables()
        if not candidates:
            return None

        ranked = sorted(
            ((self._score_executable(path), path) for path in candidates),
            key=lambda item: item[0],
            reverse=True,
        )
        (score, size), selected = ranked[0]
        self.log(
            f"Executável detectado: {selected.name} "
            f"({size / 1024 / 1024:.0f} MB, pontuação {score})"
        )
        return selected

    def _find_log_file(self) -> Optional[Path]:
        for path in (self.game_dir / "mugen.log", self.game_dir / "ikemen.log"):
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
            head = read_text_safely(log_file)[:2500]
            if "ikemen" in head.casefold():
                return "IKEMEN GO"
            match = re.search(r"M\.U\.G\.E\.N ver ([^\r\n]+)", head, re.IGNORECASE)
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
