import tempfile
import unittest
from pathlib import Path

from universal_mugen_bot import EngineDetector, GameProfile, RosterScanner
from umbot.controller import failure_kind, stage_argument
from umbot.selector import (
    SelectionController,
    load_player_keys,
    load_selection_grid,
    normalize_team_settings,
    sdl_key_to_vk,
    validate_character_dependencies,
)


CHAR_DEF = """[Info]\nname = Test\ndisplayname = Test\n[Files]\ncmd = test.cmd\ncns = test.cns\nsprite = test.sff\nanim = test.air\n"""
STAGE_DEF = """[Info]\nname = Arena\n[Camera]\nstartx = 0\n[StageInfo]\nzoffset = 200\n[BGDef]\nspr = arena.sff\n"""


class CoreTests(unittest.TestCase):
    def test_prefers_real_game_executable_in_nested_game_folder(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "MKDOTE.exe").write_bytes(b"MZ" + b"0" * 500_000)
            game = root / "Game"
            game.mkdir()
            (game / "MKDOTE.exe").write_bytes(b"MZ" + b"0" * 6_000_000)
            (game / "MugenhookSettings.ini").write_text("[Settings]\n", encoding="utf-8")
            (game / "Elecbyte.MUGEN.libs").mkdir()
            (game / "data").mkdir()
            (game / "plugins").mkdir()
            (game / "mugen.log").write_text(
                "M.U.G.E.N ver 1.1.0 Beta 1 P1 (2013.08.11) status log\n",
                encoding="latin-1",
            )
            profile = EngineDetector(root, lambda _: None).detect()
            self.assertEqual(Path(profile.game_dir), game.resolve())
            self.assertEqual(Path(profile.executable), (game / "MKDOTE.exe").resolve())
            self.assertEqual(profile.engine, "MUGEN 1.1 + MugenHook")

    def test_log_accepts_only_successfully_loaded_characters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mugen.log").write_text(
                "Loading character chars/rain/rain.def...\n"
                "Character rain.def loaded OK\n"
                "Loading character chars/broken/broken.def...\n"
                "Error loading character broken.def\n",
                encoding="latin-1",
            )
            profile = GameProfile(game_dir=str(root), log_file="mugen.log")
            chars, _ = RosterScanner(profile, lambda _: None).scan(binary_scan=False)
            self.assertEqual([c.command_path for c in chars], ["chars/rain/rain.def"])

    def test_filters_storyboards_and_discovers_real_assets(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            char_dir = root / "chars" / "jarek"
            char_dir.mkdir(parents=True)
            (char_dir / "jarek.def").write_text(CHAR_DEF, encoding="utf-8")
            (char_dir / "intro.def").write_text("[SceneDef]\nspr = intro.sff\n", encoding="utf-8")
            (char_dir / "ending.def").write_text("[SceneDef]\nspr = end.sff\n", encoding="utf-8")
            stage_dir = root / "stages" / "MK5"
            stage_dir.mkdir(parents=True)
            (stage_dir / "arena.def").write_text(STAGE_DEF, encoding="utf-8")
            profile = GameProfile(game_dir=str(root))
            chars, stages = RosterScanner(profile, lambda _: None).scan(binary_scan=False)
            self.assertEqual([c.command_path for c in chars], ["chars/jarek/jarek.def"])
            self.assertEqual([s.command_path for s in stages], ["stages/MK5/arena.def"])

    def test_select_stage_path_is_not_duplicated(self):
        self.assertEqual(stage_argument("stages/MK5/linkueiarena.def"), "MK5/linkueiarena.def")
        self.assertEqual(stage_argument("MK5/linkueiarena.def"), "MK5/linkueiarena.def")

    def test_mugen_error_dialog_log_is_detected(self):
        self.assertEqual(
            failure_kind("Error detected.\nCan't open stage: stages/stage0-720.def"),
            "stage",
        )

    def test_detector_follows_motif_and_select(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mugen.exe").write_bytes(b"MZ" + b"0" * 6_000_000)
            data = root / "data"
            motif = data / "Custom"
            motif.mkdir(parents=True)
            (data / "mugen.cfg").write_text(
                "[Options]\nmotif = data/Custom/system.def\n", encoding="utf-8"
            )
            (motif / "system.def").write_text(
                "[Files]\nselect = select.def\nfight = fight.def\n", encoding="utf-8"
            )
            (motif / "select.def").write_text("[Characters]\ntest\n", encoding="utf-8")
            profile = EngineDetector(root, lambda _: None).detect()
            self.assertEqual(profile.system_file.replace("\\", "/"), "data/Custom/system.def")
            self.assertEqual(profile.select_file.replace("\\", "/"), "data/Custom/select.def")

    def test_sdl_keycodes_are_converted_to_windows_keys(self):
        self.assertEqual(sdl_key_to_vk(273, 0), 0x26)
        self.assertEqual(sdl_key_to_vk(276, 0), 0x25)
        self.assertEqual(sdl_key_to_vk(102, 0), ord("F"))
        self.assertEqual(sdl_key_to_vk(257, 0), 0x61)

    def test_selector_reads_keys_from_mugen_cfg(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data"
            data.mkdir()
            (data / "mugen.cfg").write_text(
                "[P1 Keys]\nJump=273\nCrouch=274\nLeft=276\nRight=275\nA=102\n"
                "[P2 Keys]\nJump=264\nCrouch=258\nLeft=260\nRight=262\nA=107\n",
                encoding="utf-8",
            )
            profile = GameProfile(game_dir=str(root), config_file="data/mugen.cfg")
            p1, p2 = load_player_keys(profile)
            self.assertEqual((p1.up, p1.down, p1.left, p1.right, p1.confirm), (0x26, 0x28, 0x25, 0x27, ord("F")))
            self.assertEqual((p2.up, p2.down, p2.left, p2.right, p2.confirm), (0x68, 0x62, 0x64, 0x66, ord("K")))

    def test_selector_detects_character_select_as_latest_state(self):
        with tempfile.TemporaryDirectory() as temp:
            log = Path(temp) / "mugen.log"
            log.write_text(
                "Match loop init\nEnd of match loop\nEntering character select.\nCharsel init\n",
                encoding="latin-1",
            )
            self.assertEqual(SelectionController._current_log_state(log), "character_select")

    def test_selector_rejects_character_with_missing_cmd(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            char_dir = root / "chars" / "Eyedol"
            char_dir.mkdir(parents=True)
            char_def = char_dir / "MUGEN_Size.def"
            char_def.write_text(
                "[Info]\nname=Broken\n[Files]\ncmd=CODE/SHAOKAHN.cmd\n"
                "cns=ok.cns\nsprite=ok.sff\nanim=ok.air\n",
                encoding="utf-8",
            )
            for name in ("ok.cns", "ok.sff", "ok.air"):
                (char_dir / name).write_bytes(b"ok")
            profile = GameProfile(game_dir=str(root))
            valid, reason = validate_character_dependencies(profile, char_def)
            self.assertFalse(valid)
            self.assertIn("SHAOKAHN.cmd", reason)

    def test_selection_grid_keeps_only_usable_characters(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data"
            chars = root / "chars"
            data.mkdir()
            (data / "system.def").write_text(
                "[Select Info]\ncolumns=4\np1.cursor.startcell=0\np2.cursor.startcell=1\n",
                encoding="utf-8",
            )
            (data / "select.def").write_text(
                "[Characters]\ngood/good.def\nEyedol/MUGEN_Size.def\nempty\nrandomselect\n",
                encoding="utf-8",
            )
            good = chars / "good"
            broken = chars / "Eyedol"
            good.mkdir(parents=True)
            broken.mkdir(parents=True)
            (good / "good.def").write_text(CHAR_DEF, encoding="utf-8")
            for name in ("test.cmd", "test.cns", "test.sff", "test.air"):
                (good / name).write_bytes(b"ok")
            (broken / "MUGEN_Size.def").write_text(
                "[Info]\nname=Broken\n[Files]\ncmd=CODE/SHAOKAHN.cmd\n"
                "cns=ok.cns\nsprite=ok.sff\nanim=ok.air\n",
                encoding="utf-8",
            )
            for name in ("ok.cns", "ok.sff", "ok.air"):
                (broken / name).write_bytes(b"ok")
            profile = GameProfile(
                game_dir=str(root),
                system_file="data/system.def",
                select_file="data/select.def",
                characters=["chars/good/good.def", "chars/Eyedol/MUGEN_Size.def"],
            )
            grid = load_selection_grid(profile, lambda _: None)
            self.assertIsNotNone(grid)
            self.assertEqual(grid.columns, 4)
            self.assertEqual([(slot.index, slot.command_path) for slot in grid.slots], [(0, "chars/good/good.def")])

    def test_team_mode_normalization(self):
        self.assertEqual(normalize_team_settings("Single", 4), ("single", 1))
        self.assertEqual(normalize_team_settings("Simul", 2), ("simul", 2))
        self.assertEqual(normalize_team_settings("Simul", 9), ("simul", 4))
        self.assertEqual(normalize_team_settings("Turns", 4), ("turns", 4))
        self.assertEqual(normalize_team_settings("Turns", 1), ("turns", 2))
        self.assertEqual(normalize_team_settings("desconhecido", 4), ("single", 1))


if __name__ == "__main__":
    unittest.main()
