from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True)
class MissionGoal:
    metric: str
    label: str
    target: int


@dataclass(frozen=True)
class MissionTemplate:
    metric: str
    text: str
    base_target: int
    target_step: int
    cap: int


MISSION_TEMPLATES: tuple[MissionTemplate, ...] = (
    MissionTemplate("coins", "Collect {target} coins", 55, 12, 220),
    MissionTemplate("jumps", "Jump {target} times", 12, 3, 45),
    MissionTemplate("rolls", "Roll {target} times", 10, 3, 42),
    MissionTemplate("dodges", "Change lanes {target} times", 18, 4, 70),
    MissionTemplate("powerups", "Collect {target} power-ups", 4, 1, 14),
    MissionTemplate("boxes", "Open {target} mystery boxes", 2, 1, 8),
)

MISSION_METRIC_DEFAULTS = {
    "coins": 0,
    "jumps": 0,
    "rolls": 0,
    "dodges": 0,
    "powerups": 0,
    "boxes": 0,
}

WORD_HUNT_WORDS: tuple[str, ...] = (
    "SURF",
    "TRAIN",
    "COINS",
    "BOARD",
    "TRACK",
    "BOOST",
    "MAGNET",
    "HOVER",
    "METRO",
    "SPRAY",
    "DODGE",
    "SNEAK",
)

WORD_HUNT_COIN_REWARDS = {
    1: 300,
    2: 450,
    3: 650,
    4: 900,
}

SEASON_REWARD_THRESHOLDS: tuple[int, ...] = (5, 14, 28, 45)
SEASON_REWARD_SEQUENCE: tuple[str, ...] = ("coins", "key", "headstart", "super_box")

SUPER_MYSTERY_BOX_REWARD_WEIGHTS = {
    "coins": 38,
    "hoverboards": 16,
    "keys": 18,
    "headstarts": 10,
    "score_boosters": 8,
    "jackpot": 6,
    "mission_bonus": 4,
}


def ensure_progression_state(settings: dict, today: date | None = None) -> None:
    current_day = today or date.today()
    settings["mission_set"] = max(1, int(settings.get("mission_set", 1)))
    settings["mission_multiplier_bonus"] = max(0, min(29, int(settings.get("mission_multiplier_bonus", 0))))

    metrics = settings.get("mission_metrics")
    if not isinstance(metrics, dict):
        metrics = {}
    normalized_metrics: dict[str, int] = {}
    for metric, default_value in MISSION_METRIC_DEFAULTS.items():
        normalized_metrics[metric] = max(0, int(metrics.get(metric, default_value)))
    settings["mission_metrics"] = normalized_metrics

    today_iso = current_day.isoformat()
    settings.setdefault("word_hunt_streak", 0)
    settings.setdefault("word_hunt_completed_on", "")
    if settings.get("word_hunt_day") != today_iso:
        settings["word_hunt_day"] = today_iso
        settings["word_hunt_letters"] = ""
    current_letters = settings.get("word_hunt_letters", "")
    if not isinstance(current_letters, str):
        current_letters = ""
    active_word = daily_word_for(current_day)
    if not active_word.startswith(current_letters):
        current_letters = ""
    settings["word_hunt_letters"] = current_letters

    season_id = season_identifier(current_day)
    if settings.get("season_hunt_id") != season_id:
        settings["season_hunt_id"] = season_id
        settings["season_tokens"] = 0
        settings["season_reward_stage"] = 0
    settings["season_tokens"] = max(0, int(settings.get("season_tokens", 0)))
    settings["season_reward_stage"] = max(0, int(settings.get("season_reward_stage", 0)))


def mission_goals_for_set(set_number: int) -> tuple[MissionGoal, ...]:
    normalized_set = max(1, int(set_number))
    rng = random.Random(normalized_set * 7919)
    templates = rng.sample(MISSION_TEMPLATES, 3)
    goals: list[MissionGoal] = []
    for template in templates:
        target = min(template.cap, template.base_target + (normalized_set - 1) * template.target_step)
        goals.append(
            MissionGoal(
                metric=template.metric,
                label=template.text.format(target=target),
                target=target,
            )
        )
    return tuple(goals)


def completed_mission_metrics(settings: dict) -> set[str]:
    metrics = settings.get("mission_metrics", {})
    completed: set[str] = set()
    for goal in mission_goals_for_set(int(settings.get("mission_set", 1))):
        if int(metrics.get(goal.metric, 0)) >= goal.target:
            completed.add(goal.metric)
    return completed


def daily_word_for(today: date | None = None) -> str:
    current_day = today or date.today()
    return WORD_HUNT_WORDS[current_day.toordinal() % len(WORD_HUNT_WORDS)]


def remaining_word_letters(settings: dict, today: date | None = None) -> str:
    current_day = today or date.today()
    if settings.get("word_hunt_day") != current_day.isoformat():
        return daily_word_for(current_day)
    collected = str(settings.get("word_hunt_letters", ""))
    word = daily_word_for(current_day)
    if not word.startswith(collected):
        return word
    return word[len(collected) :]


def register_word_letter(settings: dict, today: date | None = None) -> tuple[str, bool]:
    current_day = today or date.today()
    ensure_progression_state(settings, current_day)
    word = daily_word_for(current_day)
    collected = str(settings.get("word_hunt_letters", ""))
    next_letter = word[len(collected) : len(collected) + 1]
    if not next_letter:
        return "", True
    settings["word_hunt_letters"] = collected + next_letter
    return next_letter, settings["word_hunt_letters"] == word


def update_word_hunt_streak(settings: dict, today: date | None = None) -> int:
    current_day = today or date.today()
    ensure_progression_state(settings, current_day)
    previous_completion = str(settings.get("word_hunt_completed_on", ""))
    yesterday_iso = (current_day - timedelta(days=1)).isoformat()
    if previous_completion == yesterday_iso:
        streak = int(settings.get("word_hunt_streak", 0)) + 1
    elif previous_completion == current_day.isoformat():
        streak = int(settings.get("word_hunt_streak", 1))
    else:
        streak = 1
    settings["word_hunt_streak"] = streak
    settings["word_hunt_completed_on"] = current_day.isoformat()
    return streak


def word_hunt_reward_for_streak(streak: int) -> tuple[str, int]:
    normalized_streak = max(1, int(streak))
    if normalized_streak >= 5:
        return "super_box", 1
    return "coins", WORD_HUNT_COIN_REWARDS.get(normalized_streak, WORD_HUNT_COIN_REWARDS[4])


def season_identifier(today: date | None = None) -> str:
    current_day = today or date.today()
    return f"{current_day.year:04d}-{current_day.month:02d}"


def next_season_reward_threshold(settings: dict) -> int | None:
    stage = int(settings.get("season_reward_stage", 0))
    if stage >= len(SEASON_REWARD_THRESHOLDS):
        return None
    return SEASON_REWARD_THRESHOLDS[stage]


def register_season_token(settings: dict) -> tuple[int, int | None]:
    settings["season_tokens"] = max(0, int(settings.get("season_tokens", 0))) + 1
    return int(settings["season_tokens"]), next_season_reward_threshold(settings)


def can_claim_season_reward(settings: dict) -> bool:
    threshold = next_season_reward_threshold(settings)
    if threshold is None:
        return False
    return int(settings.get("season_tokens", 0)) >= threshold


def claim_season_reward(settings: dict) -> str | None:
    if not can_claim_season_reward(settings):
        return None
    stage = int(settings.get("season_reward_stage", 0))
    reward = SEASON_REWARD_SEQUENCE[min(stage, len(SEASON_REWARD_SEQUENCE) - 1)]
    settings["season_reward_stage"] = stage + 1
    return reward


def pick_super_mystery_box_reward() -> str:
    rewards = list(SUPER_MYSTERY_BOX_REWARD_WEIGHTS.keys())
    weights = list(SUPER_MYSTERY_BOX_REWARD_WEIGHTS.values())
    return random.choices(rewards, weights=weights, k=1)[0]
