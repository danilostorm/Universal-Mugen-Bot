from __future__ import annotations

import os
import random
import subprocess
import threading
import time
import traceback
from pathlib import Path
from typing import Callable, Optional

from .core import MATCH_END_PATTERNS, MATCH_START_PATTERNS, GameProfile
from .scanner import LogTail


CRASH_PATTERNS = (
    "error detected",
    "m.u.g.e.n error",
    "can't open stage",
    "can't open",
    "error loading",
    "can't load",
    "could not open",
    "invalid state controller",
    "error parsing",
)


def stage_argument(stage: str, engine: str = "") -> str:
    """Converte o caminho do select.def para o formato esperado por -s.

    MUGEN acrescenta automaticamente `stages/`. Enviar `stages/foo.def`
    causava `stages/stages/foo.def`.
    """
    normalized = str(stage).strip().strip('"').replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    if normalized.casefold().startswith("stages/"):
        normalized = normalized[7:]
    return normalized


def failure_kind(log_text: str) -> str:
    lowered = log_text.casefold()
    if "can't open stage" in lowered:
        return "stage"
    if "error reading character" in lowered or "error loading character" in lowered:
        return "character"
    if any(pattern in lowered for pattern in CRASH_PATTERNS):
        return "engine"
    return ""


class BattleController:
    def __init__(self, logger: Callable[[str], None], status: Callable[[str], None]) -> None:
        self.log = logger
        self.status = status
        self.stop_event = threading.Event()
        self.thread: Optional[threading.Thread] = None
        self.process: Optional[subprocess.Popen] = None
        self.stats = {"matches": 0, "failures": 0}
        self.failure_counts: dict[str, int] = {}
        self.last_failure_kind = ""

    def is_running(self) -> bool:
        return bool(self.thread and self.thread.is_alive())

    def start(self, profile: GameProfile, characters: list[str], stages: list[str], continuous: bool = True) -> None:
        if self.is_running():
            raise RuntimeError("O bot já está executando.")
        if len(characters) < 2:
            raise ValueError("São necessários pelo menos dois personagens válidos.")
        if not stages and "IKEMEN" not in profile.engine.upper():
            raise ValueError(
                "Nenhum cenário válido foi detectado. O MUGEN clássico exige um stage "
                "na linha de comando. Clique em Analisar depois de fazer uma luta manual "
                "ou use uma compilação com select.def/stages acessíveis."
            )
        self.stop_event.clear()
        self.thread = threading.Thread(
            target=self._run_loop,
            args=(profile, list(characters), list(stages), continuous),
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
            available_chars = [item for item in characters if item.casefold() not in disabled]
            available_stages = list(dict.fromkeys(stages))
            if len(available_chars) < 2:
                raise ValueError("Menos de dois personagens estão habilitados.")

            while not self.stop_event.is_set():
                if not available_stages and "IKEMEN" not in profile.engine.upper():
                    self.log("Todos os cenários detectados falharam; sequência interrompida.")
                    break
                p1, p2 = random.sample(available_chars, 2)
                stage = random.choice(available_stages) if available_stages else ""
                self.status(f"{Path(p1).stem} VS {Path(p2).stem}")
                self.log(f"Nova luta: {p1}  VS  {p2}" + (f"  |  {stage}" if stage else ""))
                ok = self._run_match(profile, p1, p2, stage)
                if ok:
                    self.stats["matches"] += 1
                    self.log(f"Luta concluída. Total: {self.stats['matches']}")
                else:
                    self.stats["failures"] += 1
                    if self.last_failure_kind == "stage" and stage in available_stages:
                        available_stages.remove(stage)
                        self.log(f"Cenário removido desta sessão após falha: {stage}")
                    else:
                        for char in (p1, p2):
                            self.failure_counts[char] = self.failure_counts.get(char, 0) + 1
                            if (
                                self.failure_counts[char] >= 3
                                and char in available_chars
                                and len(available_chars) > 2
                            ):
                                available_chars.remove(char)
                                self.log(f"Personagem desativado após 3 falhas: {char}")
                    self.log("A luta falhou. O bot tentará outra combinação válida.")
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
        self.last_failure_kind = ""
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
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
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
        process_exit_code: Optional[int] = None
        start_time = time.monotonic()
        recent_log = ""

        while not self.stop_event.is_set():
            now = time.monotonic()
            chunk = tail.read_new()
            if chunk:
                recent_log = (recent_log + chunk)[-24000:]
                kind = failure_kind(recent_log)
                if kind:
                    self.last_failure_kind = kind
                    excerpt = self._failure_excerpt(recent_log)
                    self.log(f"Erro detectado pelo log ({kind}): {excerpt}")
                    break
                if any(pattern.casefold() in recent_log.casefold() for pattern in MATCH_START_PATTERNS):
                    if not started:
                        started = True
                        self.log("Luta detectada pelo log.")
                if started and any(pattern.casefold() in recent_log.casefold() for pattern in MATCH_END_PATTERNS):
                    ended = True
                    break

            code = self.process.poll()
            if code is not None:
                process_exit_code = code
                if started and code == 0 and not self.last_failure_kind:
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
        if self.stop_event.is_set() or self.last_failure_kind:
            return False
        if ended:
            return True
        return bool(started and process_exit_code == 0)

    @staticmethod
    def _failure_excerpt(text: str) -> str:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        for line in reversed(lines):
            lowered = line.casefold()
            if any(pattern in lowered for pattern in CRASH_PATTERNS):
                return line[:300]
        return lines[-1][:300] if lines else "erro desconhecido"

    @staticmethod
    def _build_command(
        exe: Path,
        p1: str,
        p2: str,
        stage: str,
        profile: GameProfile,
        style: str,
    ) -> list[str]:
        ai_value = profile.ai_level if "IKEMEN" in profile.engine.upper() else 1
        stage_arg = stage_argument(stage, profile.engine) if stage else ""
        common_tail = ["-p1.ai", str(ai_value), "-p2.ai", str(ai_value), "-rounds", str(profile.rounds)]
        if style == "flags":
            cmd = [str(exe), "-p1", p1, "-p2", p2]
            if stage_arg:
                cmd.extend(["-s", stage_arg])
            cmd.extend(common_tail)
            return cmd
        if style == "positional":
            cmd = [str(exe), "-rounds", str(profile.rounds), "-p1.ai", str(ai_value), p1, "-p2.ai", str(ai_value), p2]
            if stage_arg:
                cmd.extend(["-s", stage_arg])
            return cmd
        cmd = [str(exe), p1, p2]
        if stage_arg:
            cmd.extend(["-s", stage_arg])
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
