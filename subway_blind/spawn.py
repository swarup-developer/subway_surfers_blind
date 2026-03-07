from __future__ import annotations

import random
from dataclasses import dataclass

from subway_blind.models import LANES, Obstacle

TRAIN_FRONT_BUFFER = 18.0
SLICE_LENGTH = 2.4
TRACKED_HAZARDS = {"train", "low", "high", "bush"}


@dataclass(frozen=True)
class PatternEntry:
    kind: str
    lane: int
    z_offset: float = 0.0


@dataclass(frozen=True)
class RoutePattern:
    name: str
    entries: tuple[PatternEntry, ...]
    safe_lanes: tuple[int, ...]
    min_progress: float
    weight: float


PATTERNS: tuple[RoutePattern, ...] = (
    RoutePattern("single_train", (PatternEntry("train", 0),), (-1, 1), 0.0, 1.0),
    RoutePattern("single_low", (PatternEntry("low", 0),), (-1, 1), 0.0, 1.0),
    RoutePattern("single_high", (PatternEntry("high", 0),), (-1, 1), 0.0, 1.0),
    RoutePattern("single_bush", (PatternEntry("bush", 0),), (-1, 1), 0.1, 0.7),
    RoutePattern("double_side_trains", (PatternEntry("train", -1), PatternEntry("train", 1)), (0,), 0.08, 0.85),
    RoutePattern("train_left_low_mid", (PatternEntry("train", -1), PatternEntry("low", 0)), (1,), 0.18, 0.75),
    RoutePattern("train_right_high_mid", (PatternEntry("train", 1), PatternEntry("high", 0)), (-1,), 0.22, 0.72),
    RoutePattern("center_train_side_barrier", (PatternEntry("train", 0), PatternEntry("low", -1)), (1,), 0.28, 0.68),
    RoutePattern("center_train_side_barrier_alt", (PatternEntry("train", 0), PatternEntry("high", 1)), (-1,), 0.28, 0.68),
    RoutePattern(
        "stagger_jump_route",
        (PatternEntry("low", -1, 0.0), PatternEntry("train", 0, 2.8)),
        (1,),
        0.35,
        0.62,
    ),
    RoutePattern(
        "stagger_roll_route",
        (PatternEntry("high", 1, 0.0), PatternEntry("train", 0, 2.8)),
        (-1,),
        0.35,
        0.62,
    ),
    RoutePattern(
        "triple_readable_split",
        (PatternEntry("train", -1), PatternEntry("train", 1), PatternEntry("high", 0, 2.8)),
        (0,),
        0.5,
        0.42,
    ),
)


class SpawnDirector:
    def __init__(self) -> None:
        self.last_safe_lane = 0

    def reset(self) -> None:
        self.last_safe_lane = 0

    def next_encounter_gap(self, progress: float) -> float:
        return random.uniform(1.45, 1.7) - progress * 0.32

    def next_coin_gap(self, progress: float) -> float:
        return random.uniform(2.3, 3.0) - progress * 0.3

    def next_support_gap(self, progress: float) -> float:
        return random.uniform(9.0, 12.5) - progress * 1.2

    def base_spawn_distance(self, progress: float, speed: float) -> float:
        near = 31.0 + progress * 2.0
        far = 37.0 + progress * 3.0
        far += min(3.0, (speed - 18.0) * 0.18)
        return random.uniform(near, far)

    def candidate_patterns(self, progress: float) -> list[RoutePattern]:
        pool = [pattern for pattern in PATTERNS if pattern.min_progress <= progress]
        return sorted(pool, key=lambda pattern: random.random() / max(0.001, pattern.weight))

    def choose_pattern(self, progress: float) -> RoutePattern:
        pattern = self.candidate_patterns(progress)[0]
        self.last_safe_lane = random.choice(pattern.safe_lanes)
        return pattern

    def accept_pattern(self, pattern: RoutePattern) -> None:
        self.last_safe_lane = random.choice(pattern.safe_lanes)

    def choose_coin_lane(self, current_lane: int) -> int:
        candidates = [self.last_safe_lane, current_lane, random.choice(LANES)]
        weights = [0.55, 0.3, 0.15]
        return random.choices(candidates, weights=weights, k=1)[0]

    def choose_support_kind(self) -> str:
        return random.choices(
            ["power", "box", "key"],
            weights=[0.74, 0.18, 0.08],
            k=1,
        )[0]

    def support_lane(self) -> int:
        return self.last_safe_lane

    def should_delay_spawn(self, obstacles: list[Obstacle]) -> bool:
        nearest_hazard = min(
            (obstacle.z for obstacle in obstacles if obstacle.kind in TRACKED_HAZARDS and obstacle.z > 0),
            default=99.0,
        )
        return nearest_hazard < TRAIN_FRONT_BUFFER

    def pattern_is_playable(
        self,
        pattern: RoutePattern,
        base_distance: float,
        obstacles: list[Obstacle],
        current_lane: int,
    ) -> bool:
        requirements: dict[int, dict[int, str]] = {}

        def merge_requirement(slice_index: int, lane: int, requirement: str) -> None:
            lane_requirements = requirements.setdefault(slice_index, {})
            current = lane_requirements.get(lane)
            if current is None:
                lane_requirements[lane] = requirement
                return
            if current == "blocked" or requirement == "blocked":
                lane_requirements[lane] = "blocked"
                return
            if current != requirement:
                lane_requirements[lane] = "blocked"

        for obstacle in obstacles:
            if obstacle.kind not in TRACKED_HAZARDS or obstacle.z <= 0:
                continue
            merge_requirement(self._slice_index(obstacle.z), obstacle.lane, self._requirement_for_kind(obstacle.kind))

        for entry in pattern.entries:
            merge_requirement(self._slice_index(base_distance + entry.z_offset), entry.lane, self._requirement_for_kind(entry.kind))

        if not requirements:
            return True

        reachable_lanes = {current_lane}
        previous_slice = 0
        for slice_index in sorted(requirements):
            step_count = max(0, slice_index - previous_slice)
            next_reachable = set(reachable_lanes)
            for _ in range(step_count):
                expanded: set[int] = set()
                for lane in next_reachable:
                    expanded.add(lane)
                    if lane > LANES[0]:
                        expanded.add(lane - 1)
                    if lane < LANES[-1]:
                        expanded.add(lane + 1)
                next_reachable = expanded
            blocked_lanes = requirements.get(slice_index, {})
            reachable_lanes = {
                lane
                for lane in next_reachable
                if blocked_lanes.get(lane) != "blocked"
            }
            if not reachable_lanes:
                return False
            previous_slice = slice_index

        return True

    @staticmethod
    def _requirement_for_kind(kind: str) -> str:
        if kind == "train":
            return "blocked"
        if kind == "low":
            return "jump"
        if kind == "high":
            return "roll"
        if kind == "bush":
            return "jump"
        return "free"

    @staticmethod
    def _slice_index(z: float) -> int:
        return max(0, int(z / SLICE_LENGTH))
