from __future__ import annotations

import json
import os
import queue
import threading
import time
import traceback
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

from .controller import BattleController
from .core import (
    APP_NAME,
    APP_VERSION,
    CharacterEntry,
    EngineDetector,
    GameProfile,
    ProfileStore,
    StageEntry,
)
from .scanner import RosterScanner

class UniversalMugenBotApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_NAME} v{APP_VERSION}")
        self.root.geometry("900x680")
        self.root.minsize(760, 580)

        self.store = ProfileStore()
        self.profile = GameProfile()
        self.characters: list[CharacterEntry] = []
        self.stages: list[StageEntry] = []
        self.events: queue.Queue[tuple[str, str]] = queue.Queue()
        self.controller = BattleController(self._thread_log, self._thread_status)
        self.scan_thread: Optional[threading.Thread] = None

        self.folder_var = tk.StringVar()
        self.engine_var = tk.StringVar(value="Nenhum jogo selecionado")
        self.exe_var = tk.StringVar(value="-")
        self.roster_var = tk.StringVar(value="0 personagens | 0 cenários")
        self.status_var = tk.StringVar(value="Parado")
        self.rounds_var = tk.IntVar(value=1)
        self.ai_var = tk.IntVar(value=8)
        self.delay_var = tk.DoubleVar(value=2.0)
        self.binary_var = tk.BooleanVar(value=True)
        self.style_var = tk.StringVar(value="auto")

        self._build_ui()
        self.root.after(100, self._drain_events)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill="both", expand=True)

        source = ttk.LabelFrame(outer, text="Jogo MUGEN / IKEMEN", padding=10)
        source.pack(fill="x")
        ttk.Entry(source, textvariable=self.folder_var).pack(side="left", fill="x", expand=True)
        ttk.Button(source, text="Escolher pasta", command=self._choose_folder).pack(side="left", padx=(8, 0))
        ttk.Button(source, text="Analisar", command=self._scan_clicked).pack(side="left", padx=(8, 0))

        info = ttk.Frame(outer, padding=(0, 10))
        info.pack(fill="x")
        ttk.Label(info, text="Motor:", width=13).grid(row=0, column=0, sticky="w")
        ttk.Label(info, textvariable=self.engine_var).grid(row=0, column=1, sticky="w")
        ttk.Label(info, text="Executável:", width=13).grid(row=1, column=0, sticky="w")
        ttk.Label(info, textvariable=self.exe_var).grid(row=1, column=1, sticky="w")
        ttk.Label(info, text="Detectado:", width=13).grid(row=2, column=0, sticky="w")
        ttk.Label(info, textvariable=self.roster_var).grid(row=2, column=1, sticky="w")

        settings = ttk.LabelFrame(outer, text="Configurações", padding=10)
        settings.pack(fill="x")
        ttk.Label(settings, text="Rounds:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(settings, from_=1, to=10, textvariable=self.rounds_var, width=6).grid(row=0, column=1, padx=(5, 18))
        ttk.Label(settings, text="IA:").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(settings, from_=1, to=8, textvariable=self.ai_var, width=6).grid(row=0, column=3, padx=(5, 18))
        ttk.Label(settings, text="Intervalo:").grid(row=0, column=4, sticky="w")
        ttk.Spinbox(settings, from_=0, to=60, increment=0.5, textvariable=self.delay_var, width=7).grid(row=0, column=5, padx=(5, 18))
        ttk.Label(settings, text="Método:").grid(row=0, column=6, sticky="w")
        ttk.Combobox(settings, textvariable=self.style_var, values=("auto", "flags", "positional", "legacy"), state="readonly", width=12).grid(row=0, column=7, padx=(5, 0))
        ttk.Checkbutton(
            settings,
            text="Procurar personagens dentro do EXE (necessário em jogos empacotados)",
            variable=self.binary_var,
        ).grid(row=1, column=0, columnspan=8, sticky="w", pady=(9, 0))

        controls = ttk.Frame(outer, padding=(0, 10))
        controls.pack(fill="x")
        self.start_button = ttk.Button(controls, text="Iniciar lutas automáticas", command=self._start_continuous)
        self.start_button.pack(side="left")
        ttk.Button(controls, text="Testar uma luta", command=self._start_one).pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(controls, text="Parar", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        ttk.Label(controls, textvariable=self.status_var).pack(side="right")

        log_frame = ttk.LabelFrame(outer, text="Log", padding=8)
        log_frame.pack(fill="both", expand=True)
        self.log_box = ScrolledText(log_frame, wrap="word", height=18, state="disabled")
        self.log_box.pack(fill="both", expand=True)

        footer = ttk.Label(
            outer,
            text="O programa não altera chars, stages, system.def ou select.def. Ele apenas inicia e acompanha as lutas.",
        )
        footer.pack(fill="x", pady=(8, 0))

    def _choose_folder(self) -> None:
        selected = filedialog.askdirectory(title="Escolha a pasta onde fica o executável do jogo")
        if selected:
            self.folder_var.set(selected)
            self._scan_clicked()

    def _scan_clicked(self) -> None:
        if self.scan_thread and self.scan_thread.is_alive():
            return
        raw = self.folder_var.get().strip()
        if not raw:
            messagebox.showwarning(APP_NAME, "Escolha a pasta do jogo primeiro.")
            return
        game_dir = Path(raw)
        if not game_dir.is_dir():
            messagebox.showerror(APP_NAME, "A pasta selecionada não existe.")
            return
        self._set_buttons(scanning=True)
        self._clear_log()
        self._append_log("Analisando a pasta...")
        self.scan_thread = threading.Thread(target=self._scan_worker, args=(game_dir,), daemon=True)
        self.scan_thread.start()

    def _scan_worker(self, game_dir: Path) -> None:
        try:
            detector = EngineDetector(game_dir, self._thread_log)
            detected = detector.detect()
            saved = self.store.load(game_dir)
            if saved:
                # Preserve user settings but refresh all automatically detected paths.
                detected.rounds = saved.rounds
                detected.ai_level = saved.ai_level
                detected.delay_seconds = saved.delay_seconds
                detected.startup_timeout = saved.startup_timeout
                detected.match_timeout = saved.match_timeout
                detected.launch_style = saved.launch_style
                detected.scan_binary = saved.scan_binary
                detected.disabled_characters = saved.disabled_characters
            scanner = RosterScanner(detected, self._thread_log)
            chars, stages = scanner.scan(binary_scan=self.binary_var.get())
            detected.characters = [item.command_path for item in chars]
            detected.stages = [item.command_path for item in stages]
            self.events.put(("scan_result", json.dumps({"profile": asdict(detected), "chars": [asdict(c) for c in chars], "stages": [asdict(s) for s in stages]}, ensure_ascii=False)))
        except Exception:
            self.events.put(("log", traceback.format_exc()))
            self.events.put(("scan_error", "Não foi possível analisar esse jogo."))

    def _start_continuous(self) -> None:
        self._start(continuous=True)

    def _start_one(self) -> None:
        self._start(continuous=False)

    def _start(self, continuous: bool) -> None:
        if not self.profile.executable:
            messagebox.showwarning(APP_NAME, "Analise a pasta do jogo primeiro.")
            return
        self._sync_profile_settings()
        try:
            self.controller.start(
                self.profile,
                [c.command_path for c in self.characters],
                [s.command_path for s in self.stages],
                continuous=continuous,
            )
        except (ValueError, RuntimeError) as exc:
            messagebox.showerror(APP_NAME, str(exc))
            return
        self.store.save(self.profile)
        self.start_button.configure(state="disabled")
        self.stop_button.configure(state="normal")

    def _stop(self) -> None:
        self.controller.stop()

    def _sync_profile_settings(self) -> None:
        self.profile.rounds = max(1, int(self.rounds_var.get()))
        self.profile.ai_level = max(1, min(8, int(self.ai_var.get())))
        self.profile.delay_seconds = max(0.0, float(self.delay_var.get()))
        self.profile.scan_binary = bool(self.binary_var.get())
        self.profile.launch_style = self.style_var.get()

    def _set_buttons(self, scanning: bool = False) -> None:
        state = "disabled" if scanning else "normal"
        self.start_button.configure(state=state)

    def _thread_log(self, message: str) -> None:
        self.events.put(("log", message))

    def _thread_status(self, message: str) -> None:
        self.events.put(("status", message))

    def _drain_events(self) -> None:
        try:
            while True:
                kind, value = self.events.get_nowait()
                if kind == "log":
                    self._append_log(value)
                elif kind == "status":
                    self.status_var.set(value)
                    if value == "Parado":
                        self.start_button.configure(state="normal")
                        self.stop_button.configure(state="disabled")
                elif kind == "scan_result":
                    payload = json.loads(value)
                    self.profile = GameProfile(**payload["profile"])
                    self.characters = [CharacterEntry(**item) for item in payload["chars"]]
                    self.stages = [StageEntry(**item) for item in payload["stages"]]
                    self.engine_var.set(self.profile.engine)
                    self.exe_var.set(Path(self.profile.executable).name if self.profile.executable else "Não encontrado")
                    self.roster_var.set(f"{len(self.characters)} personagens | {len(self.stages)} cenários")
                    self.rounds_var.set(self.profile.rounds)
                    self.ai_var.set(self.profile.ai_level)
                    self.delay_var.set(self.profile.delay_seconds)
                    self.binary_var.set(self.profile.scan_binary)
                    self.style_var.set(self.profile.launch_style)
                    self.store.save(self.profile)
                    self._set_buttons(scanning=False)
                    if len(self.characters) < 2:
                        self._append_log("AVISO: poucos personagens foram encontrados. Faça uma luta manual para alimentar o mugen.log ou ative a varredura interna do EXE.")
                elif kind == "scan_error":
                    self._set_buttons(scanning=False)
                    messagebox.showerror(APP_NAME, value)
        except queue.Empty:
            pass
        self.root.after(100, self._drain_events)

    def _append_log(self, message: str) -> None:
        timestamp = time.strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        for line in str(message).rstrip().splitlines() or [""]:
            self.log_box.insert("end", f"[{timestamp}] {line}\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    def _on_close(self) -> None:
        self.controller.stop()
        self.root.destroy()


def main() -> None:
    if os.name != "nt":
        # The UI can still open elsewhere for development/testing, but launching Windows EXEs is Windows-only.
        pass
    root = tk.Tk()
    try:
        style = ttk.Style(root)
        if "vista" in style.theme_names():
            style.theme_use("vista")
    except tk.TclError:
        pass
    UniversalMugenBotApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
