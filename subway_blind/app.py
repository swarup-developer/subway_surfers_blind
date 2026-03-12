from __future__ import annotations

import pygame

from subway_blind.audio import initialize_mixer_output
from subway_blind.config import load_settings
from subway_blind.game import SubwayBlindGame
from subway_blind.version import APP_VERSION, APP_WINDOW_TITLE


def main() -> None:
    settings = load_settings()
    pygame.init()
    settings["audio_output_device"] = initialize_mixer_output(settings.get("audio_output_device")) or ""

    pygame.display.set_caption(f"{APP_WINDOW_TITLE} {APP_VERSION}")
    screen = pygame.display.set_mode((900, 600), pygame.RESIZABLE)
    clock = pygame.time.Clock()

    game = SubwayBlindGame(screen, clock, settings)

    try:
        game.run()
    finally:
        pygame.quit()
