from __future__ import annotations

import random

HEADSTART_DURATION = 9.0
HEADSTART_SPEED_BONUS = 12.0
HEADSTART_END_REWARDS = ("magnet", "mult2x", "sneakers")

SCORE_BOOSTER_MULTIPLIER_BONUS = {
    0: 0,
    1: 5,
    2: 6,
    3: 7,
}

MYSTERY_BOX_REWARD_WEIGHTS = {
    "coins": 44,
    "hover": 22,
    "mult": 15,
    "key": 8,
    "headstart": 6,
    "score_booster": 3,
    "nothing": 2,
}

SHOP_PRICES = {
    "hoverboard": 300,
    "mystery_box": 500,
    "headstart": 2000,
    "score_booster": 3000,
}

SHOP_BOX_REWARD_WEIGHTS = {
    "coins": 52,
    "hover": 18,
    "key": 12,
    "headstart": 10,
    "score_booster": 6,
    "nothing": 2,
}


def revive_cost(revives_used: int) -> int:
    return 2 ** max(0, revives_used)


def score_booster_bonus(uses: int) -> int:
    clamped_uses = max(0, min(3, uses))
    return SCORE_BOOSTER_MULTIPLIER_BONUS[clamped_uses]


def pick_mystery_box_reward() -> str:
    rewards = list(MYSTERY_BOX_REWARD_WEIGHTS.keys())
    weights = list(MYSTERY_BOX_REWARD_WEIGHTS.values())
    return random.choices(rewards, weights=weights, k=1)[0]


def pick_headstart_end_reward() -> str:
    return random.choice(HEADSTART_END_REWARDS)


def pick_shop_mystery_box_reward() -> str:
    rewards = list(SHOP_BOX_REWARD_WEIGHTS.keys())
    weights = list(SHOP_BOX_REWARD_WEIGHTS.values())
    return random.choices(rewards, weights=weights, k=1)[0]
