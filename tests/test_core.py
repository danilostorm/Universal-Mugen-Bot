import tempfile
import unittest
from pathlib import Path

from universal_mugen_bot import EngineDetector, GameProfile, RosterScanner


class CoreTests(unittest.TestCase):
    def test_detects_renamed_mugenhook_executable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "MKDOTE.exe").write_bytes(b"MZ" + b"0" * 1024)
            (root / "MugenhookSettings.ini").write_text("[Settings]\n", encoding="utf-8")
            (root / "Elecbyte.MUGEN.libs").mkdir()
            profile = EngineDetector(root, lambda _: None).detect()
            self.assertEqual(Path(profile.executable).name, "MKDOTE.exe")
            self.assertEqual(profile.engine, "MUGEN 1.1 + MugenHook")

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

    def test_reads_paths_from_log(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "mugen.log").write_text(
                "Loading character chars/rain/rain.def...\n"
                "Loading character chars/shao kahn/shao kahn.def...\n",
                encoding="latin-1",
            )
            profile = GameProfile(game_dir=str(root), log_file="mugen.log")
            chars, stages = RosterScanner(profile, lambda _: None).scan(binary_scan=False)
            self.assertEqual(len(chars), 2)
            self.assertIn("chars/rain/rain.def", [c.command_path for c in chars])
            self.assertEqual(stages, [])

    def test_reads_select_def_with_nonmatching_def_name(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            data = root / "data"
            data.mkdir()
            select = data / "select.def"
            select.write_text(
                "[Characters]\nReptile/reptile_by_eddie.def, stages/Pit.def\nempty\nrandomselect\n"
                "[ExtraStages]\nstages/Temple.def\n",
                encoding="utf-8",
            )
            profile = GameProfile(game_dir=str(root), select_file="data/select.def")
            chars, stages = RosterScanner(profile, lambda _: None).scan(binary_scan=False)
            self.assertEqual([c.command_path for c in chars], ["chars/Reptile/reptile_by_eddie.def"])
            self.assertEqual({s.command_path for s in stages}, {"stages/Pit.def", "stages/Temple.def"})


if __name__ == "__main__":
    unittest.main()
