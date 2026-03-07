from __future__ import annotations

import pygame

from subway_blind.config import load_settings
from subway_blind.game import SubwayBlindGame


def main() -> None:
    pygame.init()
    try:
        pygame.mixer.init()
    except pygame.error:
        pass

    pygame.display.set_caption("Subway Surfers - Blind Edition")
    screen = pygame.display.set_mode((900, 600))
    clock = pygame.time.Clock()

    settings = load_settings()
    game = SubwayBlindGame(screen, clock, settings)

    try:
        game.run()
    finally:
        pygame.quit()
