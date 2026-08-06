import tempfile
import unittest
from pathlib import Path

from universal_mugen_bot import EngineDetector, GameProfile, RosterScanner
from umbot.controller import failure_kind, stage_argument


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


if __name__ == "__main__":
    unittest.main()
