from __future__ import annotations

import os
import random
import subprocess
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from .core import CRASH_PATTERNS, MATCH_END_PATTERNS, MATCH_START_PATTERNS, GameProfile
from .scanner import LogTail

class BattleController:
    def __init__(self, logger: Callable[[str], None], status: Callable[[str], None]) -> None:
        self.log = logger
        self.status = status
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.process: Optional[subprocess.Popen] = None
        self.stats = {"matches": 0, "failures": 0}
        self.failure_counts: dict[str, int] = {}

    def is_running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, profile: GameProfile, characters: list[str], stages: list[str], continuous: bool = True) -> None:
        if self.is_running():
            raise RuntimeError("O bot já está executando.")
        if len(characters) < 2:
            raise ValueError("São necessários pelo menos dois personagens detectados.")
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run_loop,
            args=(profile, characters, stages, continuous),
            daemon=True,
            name="battle-loop",
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self._terminate_process()
        self.status("Parando...")

    def _run_loop(self, profile: GameProfile, characters: list[str], stages: list[str], continuous: bool) -> None:
        try:
            disabled = {item.casefold() for item in profile.disabled_characters}
            available = [item for item in characters if item.casefold() not in disabled]
            if len(available) < 2:
                raise ValueError("Menos de dois personagens estão habilitados.")

            while not self.stop_event.is_set():
                p1, p2 = random.sample(available, 2)
                stage = random.choice(stages) if stages else ""
                self.status(f"{Path(p1).stem} VS {Path(p2).stem}")
                self.log(f"Nova luta: {p1}  VS  {p2}" + (f"  |  {stage}" if stage else ""))
                ok = self._run_match(profile, p1, p2, stage)
                if ok:
                    self.stats["matches"] += 1
                    self.log(f"Luta concluída. Total: {self.stats['matches']}")
                else:
                    self.stats["failures"] += 1
                    self.failure_counts[p1] = self.failure_counts.get(p1, 0) + 1
                    self.failure_counts[p2] = self.failure_counts.get(p2, 0) + 1
                    self.log("A luta falhou ou o jogo fechou. O bot continuará com outra combinação.")
                    for char in (p1, p2):
                        if self.failure_counts.get(char, 0) >= 3 and char in available and len(available) > 2:
                            available.remove(char)
                            self.log(f"Personagem temporariamente desativado após 3 falhas: {char}")
                if not continuous:
                    break
                if self.stop_event.wait(max(0.0, profile.delay_seconds)):
                    break
        except Exception as exc:
            self.log(f"Erro no controlador: {exc}")
            self.log(traceback.format_exc())
        finally:
            self._terminate_process()
            self.status("Parado")

    def _run_match(self, profile: GameProfile, p1: str, p2: str, stage: str) -> bool:
        styles = [profile.launch_style]
        if profile.launch_style == "auto":
            styles = ["flags", "positional", "legacy"]

        for index, style in enumerate(styles):
            if self.stop_event.is_set():
                return False
            if index:
                self.log(f"Tentando método de inicialização alternativo: {style}")
            result = self._attempt(profile, p1, p2, stage, style)
            if result is not None:
                return result
        return False

    def _attempt(self, profile: GameProfile, p1: str, p2: str, stage: str, style: str) -> Optional[bool]:
        exe = Path(profile.executable)
        game_dir = Path(profile.game_dir)
        log_path = game_dir / profile.log_file
        cmd = self._build_command(exe, p1, p2, stage, profile, style)
        self.log("Comando: " + subprocess.list2cmdline(cmd))

        tail = LogTail(log_path)
        tail.reset_to_end()
        creationflags = 0
        if os.name == "nt":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
        try:
            self.process = subprocess.Popen(
                cmd,
                cwd=str(game_dir),
                creationflags=creationflags,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            self.log(f"Não foi possível abrir o jogo: {exc}")
            return False

        started = False
        ended = False
        crash_detected = False
        process_exit_code: Optional[int] = None
        start_time = time.monotonic()
        last_log = ""

        while not self.stop_event.is_set():
            now = time.monotonic()
            chunk = tail.read_new()
            if chunk:
                last_log = (last_log + chunk)[-12000:]
                if any(pattern.casefold() in last_log.casefold() for pattern in MATCH_START_PATTERNS):
                    if not started:
                        started = True
                        self.log("Luta detectada pelo log.")
                if started and any(pattern.casefold() in last_log.casefold() for pattern in MATCH_END_PATTERNS):
                    ended = True
                    break
                if any(pattern.casefold() in last_log.casefold() for pattern in CRASH_PATTERNS):
                    crash_detected = True

            code = self.process.poll()
            if code is not None:
                process_exit_code = code
                if started and code == 0 and not crash_detected:
                    ended = True
                break

            elapsed = now - start_time
            if not started and elapsed > profile.startup_timeout:
                self.log(f"Nenhuma luta foi detectada em {profile.startup_timeout}s usando '{style}'.")
                self._terminate_process()
                return None
            if started and elapsed > profile.match_timeout:
                self.log(f"Tempo máximo da luta atingido ({profile.match_timeout}s).")
                break
            time.sleep(0.25)

        self._terminate_process()
        if self.stop_event.is_set():
            return False
        if ended:
            return True
        if crash_detected:
            return False
        # Some builds exit automatically after a direct match.
        return bool(started and process_exit_code == 0 and not crash_detected)

    @staticmethod
    def _build_command(
        exe: Path,
        p1: str,
        p2: str,
        stage: str,
        profile: GameProfile,
        style: str,
    ) -> list[str]:
        # Classic MUGEN uses this parameter primarily as an AI on/off switch. IKEMEN accepts levels.
        ai_value = profile.ai_level if "IKEMEN" in profile.engine.upper() else 1
        common_tail = ["-p1.ai", str(ai_value), "-p2.ai", str(ai_value), "-rounds", str(profile.rounds)]
        if style == "flags":
            cmd = [str(exe), "-p1", p1, "-p2", p2]
            if stage:
                cmd.extend(["-s", stage])
            cmd.extend(common_tail)
            return cmd
        if style == "positional":
            cmd = [str(exe), "-rounds", str(profile.rounds), "-p1.ai", str(ai_value), p1, "-p2.ai", str(ai_value), p2]
            if stage:
                cmd.extend(["-s", stage])
            return cmd
        # Old builds and some wrappers accept the two DEF paths as positional arguments.
        cmd = [str(exe), p1, p2]
        if stage:
            cmd.extend(["-s", stage])
        cmd.extend(common_tail)
        return cmd

    def _terminate_process(self) -> None:
        process = self.process
        self.process = None
        if not process or process.poll() is not None:
            return
        try:
            process.terminate()
            process.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass

