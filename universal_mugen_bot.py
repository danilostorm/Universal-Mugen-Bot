from umbot import CharacterEntry, GameProfile, RosterScanner, StageEntry
from umbot.detector import EngineDetector
import umbot.ui as ui

ui.EngineDetector = EngineDetector
ui.APP_VERSION = "0.2.1"
main = ui.main

__all__ = [
    "CharacterEntry",
    "EngineDetector",
    "GameProfile",
    "RosterScanner",
    "StageEntry",
    "main",
]

if __name__ == "__main__":
    main()
