from __future__ import annotations

import csv
import ctypes
import os
import random
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .core import GameProfile, parse_loose_ini
from .scanner import LogTail


MATCH_START_RE = re.compile(r"(?:Match loop init|Loading match assets|Gameflow 11)", re.IGNORECASE)
MATCH_END_RE = re.compile(r"(?:End of match loop|Entering victory screen|Match loop deinit)", re.IGNORECASE)
CHAR_SELECT_RE = re.compile(r"(?:Charsel init|Entering character select)", re.IGNORECASE)
FATAL_ERROR_RE = re.compile(
    r"(?:Error detected\.|Can't read file|Can't open stage|Error loading p[12]|Error loading character|Error parsing)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PlayerKeys:
    up: int
    down: int
    left: int
    right: int
    confirm: int


# SDL 1.2 keycodes used by classic MUGEN -> Windows virtual-key codes.
SDL_SPECIAL_TO_VK = {
    8: 0x08,    # backspace
    9: 0x09,    # tab
    13: 0x0D,   # return
    27: 0x1B,   # escape
    32: 0x20,   # space
    127: 0x2E,  # delete
    256: 0x60, 257: 0x61, 258: 0x62, 259: 0x63, 260: 0x64,
    261: 0x65, 262: 0x66, 263: 0x67, 264: 0x68, 265: 0x69,
    266: 0x6E, 267: 0x6F, 268: 0x6A, 269: 0x6D, 270: 0x6B,
    271: 0x0D, 272: 0xBB,
    273: 0x26,  # up
    274: 0x28,  # down
    275: 0x27,  # right
    276: 0x25,  # left
    277: 0x2D, 278: 0x24, 279: 0x23, 280: 0x21, 281: 0x22,
}


def sdl_key_to_vk(value: int | str, fallback: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        names = {
            "UP": 0x26,
            "DOWN": 0x28,
            "LEFT": 0x25,
            "RIGHT": 0x27,
            "ENTER": 0x0D,
            "RETURN": 0x0D,
            "SPACE": 0x20,
        }
        return names.get(str(value).strip().upper(), fallback)

    if number in SDL_SPECIAL_TO_VK:
        return SDL_SPECIAL_TO_VK[number]
    if 48 <= number <= 57:
        return number
    if 65 <= number <= 90:
        return number
    if 97 <= number <= 122:
        return ord(chr(number).upper())
    return fallback


def _find_section(parser, wanted: str) -> str | None:
    wanted_cf = wanted.casefold()
    return next((name for name in parser.sections() if name.casefold() == wanted_cf), None)


def load_player_keys(profile: GameProfile) -> tuple[PlayerKeys, PlayerKeys]:
    # Safe defaults; the mugen.cfg normally replaces all of them.
    p1 = PlayerKeys(0x26, 0x28, 0x25, 0x27, ord("A"))
    p2 = PlayerKeys(0x68, 0x62, 0x64, 0x66, ord("J"))
    if not profile.config_file:
        return p1, p2

    config_path = Path(profile.game_dir) / profile.config_file
    if not config_path.exists() or config_path.suffix.casefold() != ".cfg":
        return p1, p2

    try:
        parser = parse_loose_ini(config_path)
    except OSError:
        return p1, p2

    def read_player(section_name: str, defaults: PlayerKeys) -> PlayerKeys:
        section = _find_section(parser, section_name)
        if not section:
            return defaults

        def get(name: str, fallback: int) -> int:
            raw = parser.get(section, name, fallback=str(fallback))
            return sdl_key_to_vk(raw, fallback)

        confirm = defaults.confirm
        for attack in ("a", "b", "c", "x", "y", "z"):
            raw = parser.get(section, attack, fallback="").strip()
            if raw:
                confirm = sdl_key_to_vk(raw, confirm)
                break
        return PlayerKeys(
            up=get("jump", defaults.up),
            down=get("crouch", defaults.down),
            left=get("left", defaults.left),
            right=get("right", defaults.right),
            confirm=confirm,
        )

    return read_player("P1 Keys", p1), read_player("P2 Keys", p2)


class SelectionController:
    """Controls an already-open classic MUGEN through its configured keys."""

    def __init__(self, logger: Callable[[str], None], status: Callable[[str], None]) -> None:
        self.log = logger
        self.status = status
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.hwnd: int = 0
        self.matches = 0

    def is_running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, profile: GameProfile, continuous: bool = True) -> None:
        if self.is_running():
            raise RuntimeError("O modo seletor já está executando.")
        if os.name != "nt":
            raise RuntimeError("A automação da tela de seleção funciona no Windows.")
        if not profile.executable:
            raise ValueError("O executável do jogo ainda não foi detectado.")
        log_path = Path(profile.game_dir) / profile.log_file
        if not log_path.exists():
            raise ValueError("O mugen.log não foi encontrado. Abra o jogo uma vez e analise novamente.")

        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run,
            args=(profile, continuous),
            daemon=True,
            name="selector-loop",
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.status("Parando...")

    def _run(self, profile: GameProfile, continuous: bool) -> None:
        try:
            exe = Path(profile.executable)
            self.hwnd = self._find_game_window(exe.name)
            if not self.hwnd:
                self.log(
                    f"Jogo aberto não encontrado: {exe.name}. Abra o jogo manualmente, "
                    "entre em WATCH MODE e deixe na grade de personagens."
                )
                return

            log_path = Path(profile.game_dir) / profile.log_file
            state = self._current_log_state(log_path)
            if state != "character_select":
                self.log(
                    "O jogo foi encontrado, mas não está na grade de personagens. "
                    "Entre em WATCH MODE, avance até aparecerem os retratos e não mova os cursores."
                )
                return

            p1_keys, p2_keys = load_player_keys(profile)
            self.log("Tela de seleção detectada. O bot assumirá os controles P1 e P2.")

            while not self.stop_event.is_set():
                self.status("Selecionando P1")
                p1 = self._select_player(log_path, p1_keys, team=0)
                if not p1:
                    self.log("Não foi possível confirmar o personagem P1.")
                    break

                self.status("Selecionando P2")
                p2 = self._select_player(log_path, p2_keys, team=1)
                if not p2:
                    self.log("Não foi possível confirmar o personagem P2.")
                    break

                self.status(f"{p1} VS {p2}")
                if not self._start_selected_match(log_path, p1_keys):
                    self.log("A luta não iniciou. O jogo registrou um erro ou permaneceu no seletor.")
                    break

                self.matches += 1
                self.log(f"Luta iniciada pela tela de seleção: {p1} VS {p2}")
                if not continuous:
                    break

                self.status(f"Luta {self.matches} em andamento")
                if not self._wait_for_next_character_select(log_path, profile.match_timeout):
                    if not self.stop_event.is_set():
                        self.log("O jogo não retornou à seleção de personagens dentro do tempo esperado.")
                    break

                if self.stop_event.wait(max(0.0, profile.delay_seconds)):
                    break
        except Exception as exc:
            self.log(f"Erro no modo seletor: {exc}")
        finally:
            self.hwnd = 0
            self.status("Parado")

    def _select_player(self, log_path: Path, keys: PlayerKeys, team: int) -> str | None:
        tail = LogTail(log_path)
        tail.reset_to_end()
        selected_re = re.compile(
            rf"Selected char\s+\d+\s+on teamslot\s+{team}\.0.*?\r?\n"
            rf"Char\s+(.+?)\.def",
            re.IGNORECASE | re.DOTALL,
        )
        accumulated = ""

        for _ in range(24):
            if self.stop_event.is_set():
                return None
            if not self._focus_game():
                return None

            # Random walking avoids depending on screenpack rows/columns or character names.
            for _ in range(random.randint(3, 18)):
                self._tap(random.choice((keys.up, keys.down, keys.left, keys.right)), 0.035)
            self._tap(keys.confirm, 0.08)

            deadline = time.monotonic() + 2.2
            while time.monotonic() < deadline and not self.stop_event.is_set():
                chunk = tail.read_new()
                if chunk:
                    accumulated = (accumulated + chunk)[-12000:]
                    match = selected_re.search(accumulated)
                    if match:
                        return match.group(1).strip().replace("_", " ")
                    if FATAL_ERROR_RE.search(accumulated):
                        self.log("O MUGEN registrou um erro durante a escolha do personagem.")
                        return None
                time.sleep(0.08)
        return None

    def _start_selected_match(self, log_path: Path, p1_keys: PlayerKeys) -> bool:
        tail = LogTail(log_path)
        tail.reset_to_end()
        accumulated = ""
        next_press = 0.0
        deadline = time.monotonic() + 25.0

        while time.monotonic() < deadline and not self.stop_event.is_set():
            now = time.monotonic()
            if now >= next_press:
                self._focus_game()
                # Handles optional stage-select screens. On VS screens this only skips the wait.
                for _ in range(random.randint(0, 5)):
                    self._tap(random.choice((p1_keys.left, p1_keys.right)), 0.035)
                self._tap(p1_keys.confirm, 0.08)
                next_press = now + 1.4

            chunk = tail.read_new()
            if chunk:
                accumulated = (accumulated + chunk)[-20000:]
                if FATAL_ERROR_RE.search(accumulated):
                    return False
                if MATCH_START_RE.search(accumulated):
                    return True
            time.sleep(0.08)
        return False

    def _wait_for_next_character_select(self, log_path: Path, timeout: int) -> bool:
        tail = LogTail(log_path)
        tail.reset_to_end()
        accumulated = ""
        deadline = time.monotonic() + max(60, timeout)
        match_finished = False

        while time.monotonic() < deadline and not self.stop_event.is_set():
            chunk = tail.read_new()
            if chunk:
                accumulated = (accumulated + chunk)[-30000:]
                if FATAL_ERROR_RE.search(accumulated):
                    return False
                if MATCH_END_RE.search(accumulated):
                    match_finished = True
                if match_finished and CHAR_SELECT_RE.search(accumulated):
                    return True
            time.sleep(0.2)
        return False

    @staticmethod
    def _current_log_state(path: Path) -> str:
        try:
            with path.open("rb") as handle:
                handle.seek(0, 2)
                size = handle.tell()
                handle.seek(max(0, size - 2 * 1024 * 1024))
                text = handle.read().decode("latin-1", errors="replace")
        except OSError:
            return "unknown"

        markers = {
            "character_select": max(text.rfind("Charsel init"), text.rfind("Entering character select")),
            "match": max(text.rfind("Match loop init"), text.rfind("Loading match assets")),
            "match_end": max(text.rfind("End of match loop"), text.rfind("Entering victory screen")),
            "error": max(text.rfind("Error detected."), text.rfind("Can't read file")),
        }
        state, position = max(markers.items(), key=lambda item: item[1])
        return state if position >= 0 else "unknown"

    def _find_game_window(self, executable_name: str) -> int:
        try:
            output = subprocess.check_output(
                ["tasklist", "/FI", f"IMAGENAME eq {executable_name}", "/FO", "CSV", "/NH"],
                text=True,
                encoding="mbcs",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError):
            return 0

        pids: set[int] = set()
        for row in csv.reader(output.splitlines()):
            if len(row) >= 2 and row[0].casefold() == executable_name.casefold():
                try:
                    pids.add(int(row[1]))
                except ValueError:
                    pass
        if not pids:
            return 0

        user32 = ctypes.windll.user32
        found: list[int] = []
        callback_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        @callback_type
        def enum_callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = ctypes.c_ulong()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value in pids and user32.GetWindowTextLengthW(hwnd) > 0:
                found.append(int(hwnd))
                return False
            return True

        user32.EnumWindows(enum_callback, 0)
        return found[0] if found else 0

    def _focus_game(self) -> bool:
        if not self.hwnd or not ctypes.windll.user32.IsWindow(self.hwnd):
            return False
        user32 = ctypes.windll.user32
        user32.ShowWindow(self.hwnd, 9)  # SW_RESTORE
        # The temporary ALT key helps Windows permit foreground activation.
        user32.keybd_event(0x12, 0, 0, 0)
        user32.SetForegroundWindow(self.hwnd)
        user32.keybd_event(0x12, 0, 0x0002, 0)
        time.sleep(0.12)
        return True

    @staticmethod
    def _tap(vk: int, hold: float = 0.05) -> None:
        user32 = ctypes.windll.user32
        user32.keybd_event(vk, 0, 0, 0)
        time.sleep(max(0.01, hold))
        user32.keybd_event(vk, 0, 0x0002, 0)
        time.sleep(0.025)
