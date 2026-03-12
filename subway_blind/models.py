from __future__ import annotations

from dataclasses import dataclass

LANES = (-1, 0, 1)


def normalize_lane(lane: int) -> int:
    return max(LANES[0], min(LANES[-1], int(lane)))


def lane_to_pan(lane: int) -> float:
    return float(normalize_lane(lane)) * 0.9


def lane_name(lane: int) -> str:
    return {-1: "Left lane", 0: "Center lane", 1: "Right lane"}.get(normalize_lane(lane), "Lane")


@dataclass
class Obstacle:
    kind: str
    lane: int
    z: float
    warned: bool = False
    value: int = 0
    label: str = ""


@dataclass
class Player:
    lane: int = 0
    y: float = 0.0
    vy: float = 0.0
    rolling: float = 0.0
    stumbles: int = 0
    hoverboards: int = 1
    hover_active: float = 0.0
    super_sneakers: float = 0.0
    magnet: float = 0.0
    jetpack: float = 0.0
    headstart: float = 0.0
    mult2x: float = 0.0
    pogo_active: float = 0.0


@dataclass
class RunState:
    running: bool = False
    paused: bool = False
    score: float = 0.0
    coins: int = 0
    speed: float = 18.0
    distance: float = 0.0
    multiplier: int = 1
    time: float = 0.0
    next_spawn: float = 0.0
    next_coinline: float = 0.0
    next_support: float = 0.0
    milestone: int = 0
    revives_used: int = 0
