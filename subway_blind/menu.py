from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pygame


@dataclass
class MenuItem:
    label: str
    action: str


class Menu:
    def __init__(self, speaker, audio, title: str, items: list[MenuItem]):
        self.speaker = speaker
        self.audio = audio
        self.title = title
        self.items = items
        self.index = 0
        self.opened = False

    def open(self) -> None:
        self.opened = True
        self.index = 0
        self.speaker.speak(self.title, interrupt=True)
        self._announce_current()

    def _announce_current(self) -> None:
        if not self.items:
            return
        self.speaker.speak(self.items[self.index].label, interrupt=True)

    def handle_key(self, key: int) -> Optional[str]:
        if key == pygame.K_ESCAPE:
            self.audio.play("menuclose", channel="ui")
            return "close"
        if key in (pygame.K_UP, pygame.K_w):
            if self.index > 0:
                self.index -= 1
                self.audio.play("menumove", channel="ui")
                self._announce_current()
            else:
                self.audio.play("menuedge", channel="ui")
            return None
        if key in (pygame.K_DOWN, pygame.K_s):
            if self.index < len(self.items) - 1:
                self.index += 1
                self.audio.play("menumove", channel="ui")
                self._announce_current()
            else:
                self.audio.play("menuedge", channel="ui")
            return None
        if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
            self.audio.play("menuedge", channel="ui")
            return self.items[self.index].action
        return None
