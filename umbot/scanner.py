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


SPECIAL_DEF_STEMS = {
    "empty",
    "random",
    "randomselect",
    "intro",
    "introduction",
    "ending",
    "ending1",
    "ending2",
    "ending3",
    "end",
    "credits",
    "credit",
    "gameover",
    "logo",
    "storyboard",
    "streamer",
    "scene",
    "select",
    "system",
    "fight",
    "common1",
}


class RosterScanner:
    """Descobre somente personagens e cenários utilizáveis.

    A ordem de confiança é: select.def -> pastas reais -> itens carregados com
    sucesso no log -> cache -> strings de pacotes/EXE. Isso reduz falsos
    positivos como intro.def, ending.def e caminhos de cenários inexistentes.
    """

    CHAR_LOAD_RE = re.compile(
        r"Loading character\s+(chars[\\/].+?\.def)\.\.\.", re.IGNORECASE
    )
    CHAR_OK_RE = re.compile(r"Character\s+.+?\.def\s+loaded OK", re.IGNORECASE)
    STAGE_REF_RE = re.compile(
        r"(?i)(stages[\\/][^\x00\r\n\"<>|*?]{1,220}?\.def)"
    )
    CHAR_REF_RE = re.compile(
        r"(?i)(chars[\\/][^\x00\r\n\"<>|*?]{1,220}?\.def)"
    )
    BAD_STAGE_RE = re.compile(r"Can't open stage:\s*(.+?\.def)", re.IGNORECASE)

    def __init__(self, profile: GameProfile, logger: Callable[[str], None]) -> None:
        self.profile = profile
        self.game_dir = Path(profile.game_dir).resolve()
        self.log = logger
        self._bad_stages: set[str] = set()

    def scan(self, binary_scan: bool = True) -> tuple[list[CharacterEntry], list[StageEntry]]:
        chars: dict[str, CharacterEntry] = {}
        stages: dict[str, StageEntry] = {}

        log_path = self.game_dir / self.profile.log_file
        if log_path.exists():
            log_text = read_text_safely(log_path)
            self._bad_stages = {
                normalize_path_text(match.group(1)).casefold()
                for match in self.BAD_STAGE_RE.finditer(log_text)
            }
        else:
            log_text = ""

        def add_char(path: str, source: str) -> None:
            normalized = self._normalize_character(path)
            if not normalized or self._is_special_def(normalized):
                return

            real_file = self._resolve_real_file(normalized)
            if real_file is not None and not self._is_character_def(real_file):
                return

            key = normalized.casefold()
            display = Path(normalized).stem.replace("_", " ")
            chars.setdefault(key, CharacterEntry(normalized, display, source))

        def add_stage(path: str, source: str) -> None:
            normalized = self._normalize_stage(path)
            if not normalized or self._is_special_def(normalized):
                return
            if normalized.casefold() in self._bad_stages:
                return

            real_file = self._resolve_real_file(normalized)
            if real_file is not None and not self._is_stage_def(real_file):
                return

            key = normalized.casefold()
            display = Path(normalized).stem.replace("_", " ")
            stages.setdefault(key, StageEntry(normalized, display, source))

        for path in self.profile.characters:
            add_char(path, "cache do perfil")
        for path in self.profile.stages:
            add_stage(path, "cache do perfil")

        select_path = self.game_dir / self.profile.select_file if self.profile.select_file else None
        if select_path and select_path.exists():
            self.log(f"Lendo roster ativo: {select_path.relative_to(self.game_dir)}")
            self._scan_select(select_path, add_char, add_stage)

        self._scan_asset_directories(add_char, add_stage)
        self._scan_small_text_files(add_char, add_stage)

        if log_text:
            self._scan_successful_log(log_text, add_char)

        if binary_scan and (len(chars) < 2 or len(stages) < 1):
            self._scan_packaged_files(
                add_char if len(chars) < 2 else None,
                add_stage if len(stages) < 1 else None,
            )

        char_list = sorted(chars.values(), key=lambda item: item.display_name.casefold())
        stage_list = sorted(stages.values(), key=lambda item: item.display_name.casefold())
        self.log(
            f"Varredura concluída: {len(char_list)} personagens válidos e "
            f"{len(stage_list)} cenários válidos."
        )
        if not stage_list:
            self.log(
                "AVISO: nenhum cenário utilizável foi encontrado. O bot não iniciará "
                "uma luta direta até localizar um stage válido."
            )
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
                parts = [part.strip() for part in line.split(",")]
                token = parts[0] if parts else ""
                add_char(token, "select.def")
                if len(parts) > 1 and parts[1].casefold().endswith(".def"):
                    add_stage(parts[1], "select.def")
            elif section in {"extrastages", "stages"}:
                add_stage(line.split(",", 1)[0].strip(), "select.def")

    def _scan_asset_directories(
        self,
        add_char: Callable[[str, str], None],
        add_stage: Callable[[str, str], None],
    ) -> None:
        for root in self._asset_roots("chars"):
            for path in root.rglob("*.def"):
                if self._is_character_def(path):
                    add_char(self._command_path_from_asset(path, root, "chars"), "pasta chars")

        for root in self._asset_roots("stages"):
            for path in root.rglob("*.def"):
                if self._is_stage_def(path):
                    add_stage(self._command_path_from_asset(path, root, "stages"), "pasta stages")

    def _asset_roots(self, name: str) -> list[Path]:
        found: dict[str, Path] = {}
        direct = self.game_dir / name
        if direct.is_dir():
            found[str(direct.resolve()).casefold()] = direct
        try:
            for path in self.game_dir.rglob("*"):
                if path.is_dir() and path.name.casefold() == name.casefold():
                    try:
                        depth = len(path.relative_to(self.game_dir).parts)
                    except ValueError:
                        continue
                    if depth <= 5:
                        found[str(path.resolve()).casefold()] = path
        except OSError:
            pass
        return list(found.values())

    def _command_path_from_asset(self, path: Path, asset_root: Path, prefix: str) -> str:
        relative_inside = normalize_path_text(str(path.relative_to(asset_root)))
        return f"{prefix}/{relative_inside}"

    def _scan_small_text_files(
        self,
        add_char: Callable[[str, str], None],
        add_stage: Callable[[str, str], None],
    ) -> None:
        extensions = {".cfg", ".ini", ".def", ".txt"}
        for path in self.game_dir.rglob("*"):
            try:
                if not path.is_file() or path.suffix.casefold() not in extensions:
                    continue
                if path.stat().st_size > 20 * 1024 * 1024:
                    continue
                text = read_text_safely(path)
            except OSError:
                continue
            for match in self.CHAR_REF_RE.finditer(text):
                add_char(match.group(1), f"referência em {path.name}")
            for match in self.STAGE_REF_RE.finditer(text):
                add_stage(match.group(1), f"referência em {path.name}")

    def _scan_successful_log(self, text: str, add_char: Callable[[str, str], None]) -> None:
        pending: str | None = None
        for line in text.splitlines():
            match = self.CHAR_LOAD_RE.search(line)
            if match:
                pending = match.group(1)
                continue
            if pending and self.CHAR_OK_RE.search(line):
                add_char(pending, "mugen.log: carregado com sucesso")
                pending = None
            elif pending and (
                "error loading" in line.casefold()
                or "error reading" in line.casefold()
                or "can't open" in line.casefold()
            ):
                pending = None

    def _scan_packaged_files(
        self,
        add_char: Callable[[str, str], None] | None,
        add_stage: Callable[[str, str], None] | None,
    ) -> None:
        candidates: dict[str, Path] = {}
        if self.profile.executable:
            exe = Path(self.profile.executable)
            if exe.exists():
                candidates[str(exe.resolve()).casefold()] = exe

        package_exts = {".exe", ".dat", ".bin", ".pak", ".vfs", ".lib", ".dll"}
        for path in self.game_dir.rglob("*"):
            try:
                if not path.is_file() or path.stat().st_size < 512 * 1024:
                    continue
                if path.suffix.casefold() in package_exts or "mugen.libs" in str(path.parent).casefold():
                    candidates[str(path.resolve()).casefold()] = path
            except OSError:
                continue

        for path in sorted(candidates.values(), key=lambda item: item.stat().st_size, reverse=True):
            self._scan_one_binary(path, add_char, add_stage)

    def _scan_one_binary(
        self,
        path: Path,
        add_char: Callable[[str, str], None] | None,
        add_stage: Callable[[str, str], None] | None,
    ) -> None:
        try:
            size = path.stat().st_size
        except OSError:
            return
        self.log(f"Procurando assets empacotados em {path.name} ({size / 1024 / 1024:.0f} MB)...")
        chunk_size = 16 * 1024 * 1024
        overlap = 1024
        tail = b""
        processed = 0
        last_report = 0
        try:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(chunk_size)
                    if not chunk:
                        break
                    data = tail + chunk
                    ascii_text = data.decode("latin-1", errors="ignore")
                    utf16_text = data[: len(data) - (len(data) % 2)].decode(
                        "utf-16le", errors="ignore"
                    )
                    for text in (ascii_text, utf16_text):
                        if add_char is not None:
                            for match in self.CHAR_REF_RE.finditer(text):
                                add_char(match.group(1), f"pacote {path.name}")
                        if add_stage is not None:
                            for match in self.STAGE_REF_RE.finditer(text):
                                add_stage(match.group(1), f"pacote {path.name}")
                    tail = data[-overlap:]
                    processed += len(chunk)
                    percent = int(processed * 100 / max(size, 1))
                    if percent >= last_report + 25:
                        last_report = percent
                        self.log(f"Varredura de {path.name}: {percent}%")
        except OSError as exc:
            self.log(f"Não foi possível ler {path.name}: {exc}")

    def _normalize_character(self, value: str) -> str:
        token = normalize_path_text(value).strip().strip(",")
        if not token or token.casefold() in {"empty", "random", "randomselect"}:
            return ""
        if not token.casefold().endswith(".def"):
            token = self._resolve_character_token(token)
        if not token.casefold().startswith("chars/"):
            token = f"chars/{token}"
        return normalize_path_text(token)

    def _normalize_stage(self, value: str) -> str:
        token = normalize_path_text(value).strip().strip(",")
        if not token or token.casefold() in {"random", "randomselect"}:
            return ""
        if not token.casefold().endswith(".def"):
            return ""
        lowered = token.casefold()
        if "/stages/" in lowered:
            index = lowered.rfind("/stages/")
            token = token[index + 1 :]
        elif not lowered.startswith("stages/"):
            token = f"stages/{token}"
        return normalize_path_text(token)

    def _resolve_character_token(self, token: str) -> str:
        token = normalize_path_text(token)
        rel = token[6:] if token.casefold().startswith("chars/") else token
        if rel.casefold().endswith(".def"):
            return f"chars/{rel}"
        for char_root in self._asset_roots("chars"):
            candidate_dir = find_case_insensitive(char_root, rel)
            if candidate_dir and candidate_dir.is_dir():
                same_name = find_case_insensitive(candidate_dir, f"{candidate_dir.name}.def")
                if same_name and same_name.is_file() and self._is_character_def(same_name):
                    return f"chars/{normalize_path_text(str(same_name.relative_to(char_root)))}"
                defs = [path for path in candidate_dir.glob("*.def") if self._is_character_def(path)]
                if defs:
                    return f"chars/{normalize_path_text(str(defs[0].relative_to(char_root)))}"
        folder = Path(rel).name
        return f"chars/{rel}/{folder}.def"

    def _resolve_real_file(self, command_path: str) -> Path | None:
        direct = find_case_insensitive(self.game_dir, command_path)
        if direct and direct.is_file():
            return direct

        normalized = normalize_path_text(command_path)
        if normalized.casefold().startswith("chars/"):
            relative = normalized[6:]
            roots = self._asset_roots("chars")
        elif normalized.casefold().startswith("stages/"):
            relative = normalized[7:]
            roots = self._asset_roots("stages")
        else:
            return None
        for root in roots:
            found = find_case_insensitive(root, relative)
            if found and found.is_file():
                return found
        return None

    @staticmethod
    def _is_special_def(path: str) -> bool:
        stem = Path(normalize_path_text(path)).stem.casefold().replace("-", "_")
        return stem in SPECIAL_DEF_STEMS or stem.startswith("ending") or stem.startswith("intro_")

    @staticmethod
    def _is_character_def(path: Path) -> bool:
        try:
            text = read_text_safely(path).casefold()
        except OSError:
            return False
        if "[scenedef]" in text or "[scene " in text:
            return False
        has_info = "[info]" in text
        has_files = "[files]" in text
        has_name = re.search(r"(?im)^\s*(?:displayname|name)\s*=", text) is not None
        has_asset = re.search(
            r"(?im)^\s*(?:cmd|cns|st|stcommon|sprite|anim|sound)\s*=", text
        ) is not None
        return bool(has_info and has_files and has_name and has_asset)

    @staticmethod
    def _is_stage_def(path: Path) -> bool:
        try:
            text = read_text_safely(path).casefold()
        except OSError:
            return False
        return "[camera]" in text and "[bgdef]" in text and (
            "[info]" in text or "[stageinfo]" in text
        )


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
