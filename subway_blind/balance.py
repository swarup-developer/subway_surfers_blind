from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeedProfile:
    base_speed: float
    max_speed: float
    cap_seconds: float
    spawn_gap_start: float
    spawn_gap_end: float

    def progress(self, elapsed_seconds: float) -> float:
        if self.cap_seconds <= 0:
            return 1.0
        return max(0.0, min(1.0, elapsed_seconds / self.cap_seconds))

    def speed_for_elapsed(self, elapsed_seconds: float) -> float:
        progress = self.progress(elapsed_seconds)
        return self.base_speed + (self.max_speed - self.base_speed) * progress

    def spawn_gap_for_elapsed(self, elapsed_seconds: float) -> float:
        progress = self.progress(elapsed_seconds)
        return self.spawn_gap_start + (self.spawn_gap_end - self.spawn_gap_start) * progress


SPEED_PROFILES: dict[str, SpeedProfile] = {
    "easy": SpeedProfile(
        base_speed=17.0,
        max_speed=30.5,
        cap_seconds=180.0,
        spawn_gap_start=1.45,
        spawn_gap_end=0.86,
    ),
    "normal": SpeedProfile(
        base_speed=18.4,
        max_speed=33.9,
        cap_seconds=180.0,
        spawn_gap_start=1.28,
        spawn_gap_end=0.74,
    ),
    "hard": SpeedProfile(
        base_speed=20.0,
        max_speed=36.0,
        cap_seconds=180.0,
        spawn_gap_start=1.12,
        spawn_gap_end=0.66,
    ),
}


def speed_profile_for_difficulty(difficulty: str) -> SpeedProfile:
    return SPEED_PROFILES.get(difficulty, SPEED_PROFILES["normal"])
