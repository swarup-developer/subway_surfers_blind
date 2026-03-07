from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from subway_blind.models import Obstacle, lane_to_pan

HAZARD_KINDS = {"train", "low", "high", "bush"}
TRAIN_FRONT_TRACKING_DISTANCE = 40.0
TRAIN_REAR_TRACKING_DISTANCE = 10.0
OBSTACLE_FRONT_TRACKING_DISTANCE = 26.0
LANE_ORDER = (-1, 0, 1)
PRIORITY = {"train": 0, "high": 1, "low": 2, "bush": 3}


@dataclass(frozen=True)
class ThreatCue:
    lane: int
    kind: str
    distance: float
    interval: float
    gain: float
    pan: float
    source_x: float
    source_y: float
    source_z: float
    velocity_x: float
    velocity_y: float
    velocity_z: float
    pitch: float
    prompt: Optional[str]


class SpatialThreatAudio:
    def __init__(self) -> None:
        self._pulse_cooldowns = {lane: 0.0 for lane in LANE_ORDER}
        self._spoken_signatures: dict[int, tuple[str, str]] = {}

    def reset(self) -> None:
        self._pulse_cooldowns = {lane: 0.0 for lane in LANE_ORDER}
        self._spoken_signatures.clear()

    def update(self, delta_time: float, player_lane: int, speed: float, obstacles: list[Obstacle], audio, speaker) -> None:
        for lane in LANE_ORDER:
            self._pulse_cooldowns[lane] = max(0.0, self._pulse_cooldowns[lane] - delta_time)

        cues = self.build_threat_cues(player_lane, speed, obstacles)
        active_lanes = {cue.lane for cue in cues}
        for lane in LANE_ORDER:
            if lane not in active_lanes:
                self._spoken_signatures.pop(lane, None)
                audio.stop(f"spatial_{lane}")

        for cue in cues:
            audio.update_spatial(
                channel=f"spatial_{cue.lane}",
                x=cue.source_x,
                y=cue.source_y,
                z=cue.source_z,
                gain=cue.gain,
                pitch=cue.pitch,
                fallback_pan=cue.pan,
                velocity_x=cue.velocity_x,
                velocity_y=cue.velocity_y,
                velocity_z=cue.velocity_z,
            )
            if self._pulse_cooldowns[cue.lane] <= 0:
                sound_key = "train_pass" if cue.kind == "train" else "warning"
                audio.play_spatial(
                    sound_key,
                    channel=f"spatial_{cue.lane}",
                    x=cue.source_x,
                    y=cue.source_y,
                    z=cue.source_z,
                    gain=cue.gain,
                    pitch=cue.pitch,
                    fallback_pan=cue.pan,
                    velocity_x=cue.velocity_x,
                    velocity_y=cue.velocity_y,
                    velocity_z=cue.velocity_z,
                )
                self._pulse_cooldowns[cue.lane] = cue.interval
            if cue.prompt is not None:
                signature = (cue.kind, cue.prompt)
                if self._spoken_signatures.get(cue.lane) != signature:
                    speaker.speak(cue.prompt, interrupt=False)
                    self._spoken_signatures[cue.lane] = signature

    def build_threat_cues(self, player_lane: int, speed: float, obstacles: list[Obstacle]) -> list[ThreatCue]:
        lane_threats = self._nearest_hazard_per_lane(obstacles)
        cues: list[ThreatCue] = []
        for lane in LANE_ORDER:
            threat = lane_threats.get(lane)
            if threat is None:
                continue
            cues.append(self._build_cue(player_lane, speed, threat))
        return cues

    def _nearest_hazard_per_lane(self, obstacles: list[Obstacle]) -> dict[int, Obstacle]:
        lane_threats: dict[int, Obstacle] = {}
        for obstacle in obstacles:
            if obstacle.kind not in HAZARD_KINDS:
                continue
            if not self._is_within_tracking_window(obstacle):
                continue
            current = lane_threats.get(obstacle.lane)
            if current is None:
                lane_threats[obstacle.lane] = obstacle
                continue
            obstacle_metric = self._threat_metric(obstacle)
            current_metric = self._threat_metric(current)
            if obstacle_metric < current_metric:
                lane_threats[obstacle.lane] = obstacle
        return lane_threats

    def _is_within_tracking_window(self, obstacle: Obstacle) -> bool:
        if obstacle.kind == "train":
            return -TRAIN_REAR_TRACKING_DISTANCE <= obstacle.z <= TRAIN_FRONT_TRACKING_DISTANCE
        return 0.0 < obstacle.z <= OBSTACLE_FRONT_TRACKING_DISTANCE

    def _threat_metric(self, obstacle: Obstacle) -> tuple[int, float, int]:
        return (0 if obstacle.z > 0 else 1, abs(obstacle.z), PRIORITY.get(obstacle.kind, 99))

    def _build_cue(self, player_lane: int, speed: float, obstacle: Obstacle) -> ThreatCue:
        range_limit = TRAIN_FRONT_TRACKING_DISTANCE if obstacle.kind == "train" else OBSTACLE_FRONT_TRACKING_DISTANCE
        signed_distance = obstacle.z
        distance = min(range_limit, abs(signed_distance))
        closeness = 1.0 - (distance / range_limit)
        speed_factor = self._speed_factor(speed)
        interval = 1.05 - closeness * 0.88
        gain = 0.22 + closeness * 0.86
        interval *= 1.0 - speed_factor * 0.14
        gain = min(1.0, gain + speed_factor * 0.06)
        if signed_distance < 0:
            gain *= 0.82
            interval *= 1.08
        lateral_bias = 0.18 * closeness if obstacle.lane != player_lane else 0.0
        if obstacle.lane < player_lane:
            pan = lane_to_pan(obstacle.lane) - lateral_bias
        elif obstacle.lane > player_lane:
            pan = lane_to_pan(obstacle.lane) + lateral_bias
        else:
            pan = lane_to_pan(obstacle.lane)
        relative_lane = obstacle.lane - player_lane
        source_x = relative_lane * 1.7
        source_y = -0.1
        source_z = max(-TRAIN_FRONT_TRACKING_DISTANCE, min(TRAIN_REAR_TRACKING_DISTANCE, -signed_distance * 0.95))
        velocity_x = 0.0
        velocity_y = 0.0
        velocity_z = max(4.0, speed * 0.95)
        pitch = 0.92 + closeness * 0.24
        if signed_distance < 0:
            pitch = max(0.82, pitch - 0.08)
        prompt = self._prompt_for_obstacle(player_lane, obstacle, signed_distance, speed_factor)
        return ThreatCue(
            lane=obstacle.lane,
            kind=obstacle.kind,
            distance=distance,
            interval=max(0.12, interval),
            gain=max(0.12, min(1.0, gain)),
            pan=max(-1.0, min(1.0, pan)),
            source_x=source_x,
            source_y=source_y,
            source_z=source_z,
            velocity_x=velocity_x,
            velocity_y=velocity_y,
            velocity_z=velocity_z,
            pitch=max(0.75, min(1.3, pitch)),
            prompt=prompt,
        )

    def _prompt_for_obstacle(
        self,
        player_lane: int,
        obstacle: Obstacle,
        signed_distance: float,
        speed_factor: float,
    ) -> Optional[str]:
        base_prompt_distance = {"train": 15.5, "low": 13.5, "high": 13.5, "bush": 13.5}[obstacle.kind]
        prompt_distance = base_prompt_distance + speed_factor * (4.5 if obstacle.kind == "train" else 3.5)
        if signed_distance <= 0 or signed_distance > prompt_distance:
            return None
        if obstacle.lane != player_lane:
            return None
        if speed_factor >= 0.72:
            maneuver = {"train": "switch", "low": "jump", "high": "roll", "bush": "jump"}[obstacle.kind]
        else:
            maneuver = {"train": "switch now", "low": "jump soon", "high": "roll soon", "bush": "jump soon"}[obstacle.kind]
        return maneuver

    @staticmethod
    def _speed_factor(speed: float) -> float:
        return max(0.0, min(1.0, (float(speed) - 18.0) / 16.0))
