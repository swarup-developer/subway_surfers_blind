from __future__ import annotations

import random
from dataclasses import dataclass

from subway_blind.models import LANES, Obstacle, normalize_lane

TRAIN_FRONT_BUFFER = 18.0
SLICE_LENGTH = 2.4
TRACKED_HAZARDS = {"train", "low", "high", "bush"}
DIFFICULTY_PROGRESS_SCALE = {
    "easy": 0.55,
    "normal": 1.0,
    "hard": 1.18,
}
DIFFICULTY_ENCOUNTER_GAP_OFFSET = {
    "easy": 0.22,
    "normal": 0.0,
    "hard": -0.08,
}
DIFFICULTY_COIN_GAP_OFFSET = {
    "easy": 0.12,
    "normal": 0.0,
    "hard": -0.06,
}
DIFFICULTY_SUPPORT_GAP_OFFSET = {
    "easy": 0.9,
    "normal": 0.0,
    "hard": -0.45,
}
DIFFICULTY_SPAWN_DISTANCE_OFFSET = {
    "easy": 3.8,
    "normal": 0.0,
    "hard": -1.2,
}


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

    def _difficulty_progress(self, progress: float, difficulty: str) -> float:
        scale = DIFFICULTY_PROGRESS_SCALE.get(str(difficulty), 1.0)
        return max(0.0, min(1.0, float(progress) * scale))

    @staticmethod
    def _difficulty_offset(mapping: dict[str, float], difficulty: str) -> float:
        return float(mapping.get(str(difficulty), 0.0))

    def next_encounter_gap(self, progress: float, difficulty: str = "normal") -> float:
        adjusted_progress = self._difficulty_progress(progress, difficulty)
        return random.uniform(1.45, 1.7) - adjusted_progress * 0.32 + self._difficulty_offset(
            DIFFICULTY_ENCOUNTER_GAP_OFFSET,
            difficulty,
        )

    def next_coin_gap(self, progress: float, difficulty: str = "normal") -> float:
        adjusted_progress = self._difficulty_progress(progress, difficulty)
        return random.uniform(2.3, 3.0) - adjusted_progress * 0.3 + self._difficulty_offset(
            DIFFICULTY_COIN_GAP_OFFSET,
            difficulty,
        )

    def next_support_gap(self, progress: float, difficulty: str = "normal") -> float:
        adjusted_progress = self._difficulty_progress(progress, difficulty)
        return random.uniform(9.0, 12.5) - adjusted_progress * 1.2 + self._difficulty_offset(
            DIFFICULTY_SUPPORT_GAP_OFFSET,
            difficulty,
        )

    def base_spawn_distance(self, progress: float, speed: float, difficulty: str = "normal") -> float:
        adjusted_progress = self._difficulty_progress(progress, difficulty)
        near = 31.0 + adjusted_progress * 2.0
        far = 37.0 + adjusted_progress * 3.0
        far += min(3.0, (speed - 18.0) * 0.18)
        difficulty_offset = self._difficulty_offset(DIFFICULTY_SPAWN_DISTANCE_OFFSET, difficulty)
        near += difficulty_offset
        far += difficulty_offset
        return random.uniform(near, far)

    def candidate_patterns(self, progress: float, difficulty: str = "normal") -> list[RoutePattern]:
        adjusted_progress = self._difficulty_progress(progress, difficulty)
        pool: list[RoutePattern] = []
        seen: set[tuple[tuple[tuple[str, int, float], ...], tuple[int, ...]]] = set()
        for pattern in PATTERNS:
            if pattern.min_progress > adjusted_progress:
                continue
            for candidate in self._pattern_variants(pattern):
                signature = self._pattern_signature(candidate)
                if signature in seen:
                    continue
                seen.add(signature)
                pool.append(candidate)
        return sorted(pool, key=lambda pattern: random.random() / max(0.001, pattern.weight))

    def choose_pattern(self, progress: float, difficulty: str = "normal") -> RoutePattern:
        pattern = self.candidate_patterns(progress, difficulty=difficulty)[0]
        self.last_safe_lane = random.choice(pattern.safe_lanes)
        return pattern

    def accept_pattern(self, pattern: RoutePattern) -> None:
        self.last_safe_lane = random.choice(pattern.safe_lanes)

    def choose_coin_lane(self, current_lane: int) -> int:
        current_lane = normalize_lane(current_lane)
        candidates = [current_lane, self.last_safe_lane, random.choice(LANES)]
        weights = [0.55, 0.3, 0.15]
        return normalize_lane(random.choices(candidates, weights=weights, k=1)[0])

    def choose_support_kind(self) -> str:
        return random.choices(
            ["power", "box", "key"],
            weights=[0.74, 0.18, 0.08],
            k=1,
        )[0]

    def support_lane(self, current_lane: int) -> int:
        current_lane = normalize_lane(current_lane)
        candidates = [current_lane, self.last_safe_lane, random.choice(LANES)]
        weights = [0.55, 0.35, 0.1]
        return normalize_lane(random.choices(candidates, weights=weights, k=1)[0])

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

    @staticmethod
    def _pattern_variants(pattern: RoutePattern) -> tuple[RoutePattern, ...]:
        variants: list[RoutePattern] = []
        for direction in (1, -1):
            for offset in LANES:
                transformed = SpawnDirector._transform_pattern(pattern, direction, offset)
                if transformed is not None:
                    variants.append(transformed)
        return tuple(variants)

    @staticmethod
    def _transform_pattern(pattern: RoutePattern, direction: int, offset: int) -> RoutePattern | None:
        entries: list[PatternEntry] = []
        for entry in pattern.entries:
            lane = SpawnDirector._transform_lane(entry.lane, direction, offset)
            if lane not in LANES:
                return None
            entries.append(PatternEntry(entry.kind, lane, entry.z_offset))

        normalized_safe_lanes = SpawnDirector._derive_safe_lanes(tuple(entries))
        if not normalized_safe_lanes:
            return None

        return RoutePattern(
            name=f"{pattern.name}:{direction}:{offset}",
            entries=tuple(entries),
            safe_lanes=normalized_safe_lanes,
            min_progress=pattern.min_progress,
            weight=pattern.weight,
        )

    @staticmethod
    def _transform_lane(lane: int, direction: int, offset: int) -> int:
        return lane * direction + offset

    @staticmethod
    def _pattern_signature(pattern: RoutePattern) -> tuple[tuple[tuple[str, int, float], ...], tuple[int, ...]]:
        entries = tuple(sorted((entry.kind, entry.lane, entry.z_offset) for entry in pattern.entries))
        return entries, tuple(sorted(pattern.safe_lanes))

    @staticmethod
    def _derive_safe_lanes(entries: tuple[PatternEntry, ...]) -> tuple[int, ...]:
        severity = {"train": 3, "low": 1, "high": 1, "bush": 1}
        lane_scores = {lane: 0 for lane in LANES}
        for entry in entries:
            lane_scores[entry.lane] += severity.get(entry.kind, 0)
        best_score = min(lane_scores.values())
        return tuple(lane for lane in LANES if lane_scores[lane] == best_score)
