from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent

DEFAULT_SETTINGS: dict[str, Any] = {
    "sfx_volume": 0.9,
    "music_volume": 0.6,
    "speech_enabled": True,
    "difficulty": "normal",
    "announce_lane": True,
    "announce_coins_every": 10,
    "bank_coins": 0,
    "keys": 3,
    "hoverboards": 3,
    "headstarts": 2,
    "score_boosters": 3,
    "mission_set": 1,
    "mission_multiplier_bonus": 0,
    "mission_metrics": {
        "coins": 0,
        "jumps": 0,
        "rolls": 0,
        "dodges": 0,
        "powerups": 0,
        "boxes": 0,
    },
    "word_hunt_day": "",
    "word_hunt_letters": "",
    "word_hunt_streak": 0,
    "word_hunt_completed_on": "",
    "season_hunt_id": "",
    "season_tokens": 0,
    "season_reward_stage": 0,
}


def resource_path(*parts: str) -> str:
    return str(BASE_DIR.joinpath(*parts))


def load_settings() -> dict[str, Any]:
    settings_path = BASE_DIR / "data" / "settings.json"
    if not settings_path.exists():
        return copy.deepcopy(DEFAULT_SETTINGS)
    try:
        with settings_path.open("r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except Exception:
        return copy.deepcopy(DEFAULT_SETTINGS)
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    for key, default_value in DEFAULT_SETTINGS.items():
        merged[key] = copy.deepcopy(loaded.get(key, default_value))
    return merged


def save_settings(settings: dict[str, Any]) -> None:
    data_directory = BASE_DIR / "data"
    data_directory.mkdir(parents=True, exist_ok=True)
    settings_path = data_directory / "settings.json"
    try:
        with settings_path.open("w", encoding="utf-8") as handle:
            json.dump(settings, handle, ensure_ascii=False, indent=2)
    except Exception:
        return
