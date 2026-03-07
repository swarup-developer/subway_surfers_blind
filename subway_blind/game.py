from __future__ import annotations

import random
from typing import Optional

import pygame

from subway_blind.audio import Audio, Speaker
from subway_blind.balance import SpeedProfile, speed_profile_for_difficulty
from subway_blind.config import save_settings
from subway_blind.features import (
    HEADSTART_DURATION,
    HEADSTART_SPEED_BONUS,
    pick_headstart_end_reward,
    pick_mystery_box_reward,
    pick_shop_mystery_box_reward,
    revive_cost,
    SHOP_PRICES,
    score_booster_bonus,
)
from subway_blind.menu import Menu, MenuItem
from subway_blind.models import LANES, Obstacle, Player, RunState, lane_name, lane_to_pan
from subway_blind.progression import (
    can_claim_season_reward,
    claim_season_reward,
    completed_mission_metrics,
    daily_word_for,
    ensure_progression_state,
    mission_goals_for_set,
    next_season_reward_threshold,
    pick_super_mystery_box_reward,
    register_season_token,
    register_word_letter,
    remaining_word_letters,
    update_word_hunt_streak,
    word_hunt_reward_for_streak,
)
from subway_blind.spawn import RoutePattern, SpawnDirector
from subway_blind.spatial_audio import SpatialThreatAudio

DIFFICULTY_LABELS = {
    "easy": "Easy",
    "normal": "Normal",
    "hard": "Hard",
}


def cycle_volume(value: float) -> float:
    rounded = round(float(value), 1)
    if rounded >= 1.0:
        return 0.0
    return round(rounded + 0.1, 1)


class SubwayBlindGame:
    def __init__(self, screen: pygame.Surface, clock: pygame.time.Clock, settings: dict):
        self.screen = screen
        self.clock = clock
        self.settings = settings
        self.speaker = Speaker(enabled=bool(settings["speech_enabled"]))
        self.audio = Audio(settings)
        self.font = pygame.font.SysFont("segoeui", 22)
        self.big = pygame.font.SysFont("segoeui", 38, bold=True)
        ensure_progression_state(self.settings)

        self.state = RunState()
        self.player = Player()
        self.obstacles: list[Obstacle] = []
        self.speed_profile: SpeedProfile = speed_profile_for_difficulty(str(self.settings["difficulty"]))
        self.spatial_audio = SpatialThreatAudio()
        self.spawn_director = SpawnDirector()
        self.selected_headstart = False
        self.selected_score_boosters = 0
        self._footstep_timer = 0.0
        self._left_foot_next = True
        self._run_rewards_committed = False
        self._near_miss_signatures: set[tuple[str, int]] = set()

        self.pause_menu = Menu(
            self.speaker,
            self.audio,
            "Paused",
            [
                MenuItem("Resume", "resume"),
                MenuItem("Return to Main Menu", "to_main"),
            ],
        )
        self.revive_menu = Menu(
            self.speaker,
            self.audio,
            "Revive",
            [
                MenuItem(self._revive_option_label(), "revive"),
                MenuItem("End Run", "end_run"),
            ],
        )
        self.main_menu = Menu(
            self.speaker,
            self.audio,
            "Main Menu",
            [
                MenuItem("Start Game", "start"),
                MenuItem("Shop", "shop"),
                MenuItem("Options", "options"),
                MenuItem("How to Play", "howto"),
                MenuItem("Exit", "quit"),
            ],
        )
        self.loadout_menu = Menu(
            self.speaker,
            self.audio,
            "Run Setup",
            [
                MenuItem(self._headstart_option_label(), "toggle_headstart"),
                MenuItem(self._score_booster_option_label(), "toggle_score_booster"),
                MenuItem("Begin Run", "begin_run"),
                MenuItem("Back", "back"),
            ],
        )
        self.options_menu = Menu(
            self.speaker,
            self.audio,
            "Options",
            [
                MenuItem(self._sfx_option_label(), "opt_sfx"),
                MenuItem(self._music_option_label(), "opt_music"),
                MenuItem(self._speech_option_label(), "opt_speech"),
                MenuItem(self._difficulty_option_label(), "opt_diff"),
                MenuItem("Back", "back"),
            ],
        )
        self.shop_menu = Menu(
            self.speaker,
            self.audio,
            self._shop_title(),
            [
                MenuItem(self._shop_hoverboard_label(), "buy_hoverboard"),
                MenuItem(self._shop_box_label(), "buy_box"),
                MenuItem(self._shop_headstart_label(), "buy_headstart"),
                MenuItem(self._shop_score_booster_label(), "buy_score_booster"),
                MenuItem("Back", "back"),
            ],
        )

        self.active_menu: Optional[Menu] = self.main_menu
        self.active_menu.open()

    def _sfx_option_label(self) -> str:
        return f"SFX Volume: {int(float(self.settings['sfx_volume']) * 100)}"

    def _shop_title(self) -> str:
        return f"Shop   Coins: {int(self.settings.get('bank_coins', 0))}"

    def _music_option_label(self) -> str:
        return f"Music Volume: {int(float(self.settings['music_volume']) * 100)}"

    def _speech_option_label(self) -> str:
        return f"Speech: {'On' if self.settings['speech_enabled'] else 'Off'}"

    def _difficulty_option_label(self) -> str:
        difficulty = DIFFICULTY_LABELS.get(str(self.settings["difficulty"]), "Normal")
        return f"Difficulty: {difficulty}"

    def _headstart_option_label(self) -> str:
        status = "On" if self.selected_headstart else "Off"
        owned = int(self.settings.get("headstarts", 0))
        return f"Headstart: {status}   Owned: {owned}"

    def _score_booster_option_label(self) -> str:
        owned = int(self.settings.get("score_boosters", 0))
        return f"Score Booster: {self.selected_score_boosters}   Owned: {owned}"

    def _revive_option_label(self) -> str:
        cost = revive_cost(self.state.revives_used)
        owned = int(self.settings.get("keys", 0))
        return f"Use {cost} key{'s' if cost != 1 else ''} to revive   Owned: {owned}"

    def _shop_hoverboard_label(self) -> str:
        return (
            f"Buy Hoverboard   Cost: {SHOP_PRICES['hoverboard']}   "
            f"Owned: {int(self.settings.get('hoverboards', 0))}"
        )

    def _shop_box_label(self) -> str:
        return f"Open Mystery Box   Cost: {SHOP_PRICES['mystery_box']}"

    def _shop_headstart_label(self) -> str:
        return (
            f"Buy Headstart   Cost: {SHOP_PRICES['headstart']}   "
            f"Owned: {int(self.settings.get('headstarts', 0))}"
        )

    def _shop_score_booster_label(self) -> str:
        return (
            f"Buy Score Booster   Cost: {SHOP_PRICES['score_booster']}   "
            f"Owned: {int(self.settings.get('score_boosters', 0))}"
        )

    def _refresh_options_menu_labels(self) -> None:
        self.options_menu.items[0].label = self._sfx_option_label()
        self.options_menu.items[1].label = self._music_option_label()
        self.options_menu.items[2].label = self._speech_option_label()
        self.options_menu.items[3].label = self._difficulty_option_label()

    def _refresh_loadout_menu_labels(self) -> None:
        self.loadout_menu.items[0].label = self._headstart_option_label()
        self.loadout_menu.items[1].label = self._score_booster_option_label()

    def _refresh_revive_menu_label(self) -> None:
        self.revive_menu.items[0].label = self._revive_option_label()

    def _refresh_shop_menu_labels(self) -> None:
        self.shop_menu.title = self._shop_title()
        self.shop_menu.items[0].label = self._shop_hoverboard_label()
        self.shop_menu.items[1].label = self._shop_box_label()
        self.shop_menu.items[2].label = self._shop_headstart_label()
        self.shop_menu.items[3].label = self._shop_score_booster_label()

    def _mission_goals(self):
        return mission_goals_for_set(int(self.settings.get("mission_set", 1)))

    def _mission_status_text(self) -> str:
        completed = len(completed_mission_metrics(self.settings))
        return f"Missions {completed}/3"

    def _current_word(self) -> str:
        return daily_word_for()

    def _remaining_word_letters(self) -> str:
        return remaining_word_letters(self.settings)

    def _next_word_letter(self) -> str:
        remaining_letters = self._remaining_word_letters()
        return remaining_letters[:1]

    def _choose_support_spawn_kind(self) -> str:
        kinds = ["power", "box", "key"]
        weights = [0.58, 0.18, 0.08]
        active_word = any(obstacle.kind == "word" and obstacle.z > 0 for obstacle in self.obstacles)
        active_token = any(obstacle.kind == "season_token" and obstacle.z > 0 for obstacle in self.obstacles)
        if self._remaining_word_letters() and not active_word:
            kinds.append("word")
            weights.append(0.10)
        if next_season_reward_threshold(self.settings) is not None and not active_token:
            kinds.append("season_token")
            weights.append(0.06)
        return random.choices(kinds, weights=weights, k=1)[0]

    def _complete_mission_set(self) -> None:
        self.settings["mission_set"] = int(self.settings.get("mission_set", 1)) + 1
        self.settings["mission_metrics"] = {
            "coins": 0,
            "jumps": 0,
            "rolls": 0,
            "dodges": 0,
            "powerups": 0,
            "boxes": 0,
        }
        if int(self.settings.get("mission_multiplier_bonus", 0)) < 29:
            self.settings["mission_multiplier_bonus"] = int(self.settings.get("mission_multiplier_bonus", 0)) + 1
            if self.state.running:
                self.state.multiplier += 1
            self.audio.play("mission_reward", channel="ui")
            self.audio.play("unlock", channel="ui2")
            self.speaker.speak(
                f"Mission set complete. Permanent multiplier is now x{1 + int(self.settings['mission_multiplier_bonus'])}.",
                interrupt=True,
            )
            return
        self.audio.play("mission_reward", channel="ui")
        self.speaker.speak("Mission set complete. Super Mystery Box.", interrupt=True)
        self._open_super_mystery_box("Mission Set")

    def _record_mission_event(self, metric: str, amount: int = 1) -> None:
        ensure_progression_state(self.settings)
        metrics = self.settings.get("mission_metrics", {})
        if metric not in metrics or amount <= 0:
            return
        goals = self._mission_goals()
        completed_before = completed_mission_metrics(self.settings)
        metrics[metric] = int(metrics.get(metric, 0)) + amount
        completed_after = completed_mission_metrics(self.settings)
        newly_completed = completed_after - completed_before
        for goal in goals:
            if goal.metric in newly_completed:
                self.audio.play("mission_reward", channel="ui")
                self.speaker.speak(f"Mission complete: {goal.label}.", interrupt=False)
        if len(completed_after) == len(goals) and len(completed_before) != len(goals):
            self._complete_mission_set()

    def _open_super_mystery_box(self, source: str) -> None:
        reward = pick_super_mystery_box_reward()
        self.audio.play("mystery_box", channel="ui")
        self.audio.play("mystery_combo", channel="ui2")
        if reward == "coins":
            gain = random.randint(450, 1100)
            self.settings["bank_coins"] = int(self.settings.get("bank_coins", 0)) + gain
            self.audio.play("gui_cash", channel="ui3")
            self.speaker.speak(f"{source}: Super Mystery Box. {gain} coins saved.", interrupt=True)
            return
        if reward == "hoverboards":
            gain = random.randint(1, 2)
            self.settings["hoverboards"] = int(self.settings.get("hoverboards", 0)) + gain
            self.audio.play("unlock", channel="ui3")
            self.speaker.speak(f"{source}: Super Mystery Box. {gain} hoverboard{'s' if gain != 1 else ''}.", interrupt=True)
            return
        if reward == "keys":
            gain = random.randint(1, 2)
            self.settings["keys"] = int(self.settings.get("keys", 0)) + gain
            self.audio.play("unlock", channel="ui3")
            self.speaker.speak(f"{source}: Super Mystery Box. {gain} key{'s' if gain != 1 else ''}.", interrupt=True)
            return
        if reward == "headstarts":
            self.settings["headstarts"] = int(self.settings.get("headstarts", 0)) + 1
            self.audio.play("unlock", channel="ui3")
            self.speaker.speak(f"{source}: Super Mystery Box. Headstart.", interrupt=True)
            return
        if reward == "score_boosters":
            self.settings["score_boosters"] = int(self.settings.get("score_boosters", 0)) + 1
            self.audio.play("unlock", channel="ui3")
            self.speaker.speak(f"{source}: Super Mystery Box. Score Booster.", interrupt=True)
            return
        if reward == "jackpot":
            gain = random.randint(1500, 2600)
            self.settings["bank_coins"] = int(self.settings.get("bank_coins", 0)) + gain
            self.audio.play("gui_cash", channel="ui3")
            self.audio.play("unlock", channel="ui4")
            self.speaker.speak(f"{source}: Super Mystery Box jackpot. {gain} coins saved.", interrupt=True)
            return
        if int(self.settings.get("mission_multiplier_bonus", 0)) < 29:
            self.settings["mission_multiplier_bonus"] = int(self.settings.get("mission_multiplier_bonus", 0)) + 1
            if self.state.running:
                self.state.multiplier += 1
            self.audio.play("unlock", channel="ui3")
            self.speaker.speak(
                f"{source}: Super Mystery Box. Permanent multiplier x{1 + int(self.settings['mission_multiplier_bonus'])}.",
                interrupt=True,
            )
            return
        gain = random.randint(900, 1500)
        self.settings["bank_coins"] = int(self.settings.get("bank_coins", 0)) + gain
        self.audio.play("gui_cash", channel="ui3")
        self.speaker.speak(f"{source}: Super Mystery Box. {gain} coins saved.", interrupt=True)

    def _complete_word_hunt(self) -> None:
        streak = update_word_hunt_streak(self.settings)
        reward_kind, amount = word_hunt_reward_for_streak(streak)
        self.audio.play("mission_reward", channel="ui")
        if reward_kind == "coins":
            self.settings["bank_coins"] = int(self.settings.get("bank_coins", 0)) + amount
            self.audio.play("gui_cash", channel="ui2")
            self.speaker.speak(
                f"Word Hunt complete. Streak {streak}. {amount} coins saved.",
                interrupt=True,
            )
            return
        self.speaker.speak(f"Word Hunt complete. Streak {streak}. Super Mystery Box.", interrupt=True)
        self._open_super_mystery_box("Word Hunt")

    def _claim_season_reward(self) -> None:
        reward = claim_season_reward(self.settings)
        if reward is None:
            return
        self.audio.play("mission_reward", channel="ui")
        self.audio.play("unlock", channel="ui2")
        if reward == "coins":
            gain = 500
            self.settings["bank_coins"] = int(self.settings.get("bank_coins", 0)) + gain
            self.audio.play("gui_cash", channel="ui3")
            self.speaker.speak(f"Season Hunt reward. {gain} coins saved.", interrupt=True)
            return
        if reward == "key":
            self.settings["keys"] = int(self.settings.get("keys", 0)) + 1
            self.speaker.speak("Season Hunt reward. Key.", interrupt=True)
            return
        if reward == "headstart":
            self.settings["headstarts"] = int(self.settings.get("headstarts", 0)) + 1
            self.speaker.speak("Season Hunt reward. Headstart.", interrupt=True)
            return
        self.speaker.speak("Season Hunt reward. Super Mystery Box.", interrupt=True)
        self._open_super_mystery_box("Season Hunt")

    def _spend_bank_coins(self, cost: int) -> bool:
        current = int(self.settings.get("bank_coins", 0))
        if current < cost:
            self.audio.play("menuedge", channel="ui")
            self.speaker.speak("Not enough coins.", interrupt=True)
            return False
        self.settings["bank_coins"] = current - cost
        self.audio.play("gui_tap", channel="ui")
        self.audio.play("coin_gui", channel="ui2")
        return True

    def _purchase_shop_item(self, item: str) -> None:
        if item == "hoverboard":
            if not self._spend_bank_coins(SHOP_PRICES["hoverboard"]):
                return
            self.settings["hoverboards"] = int(self.settings.get("hoverboards", 0)) + 1
            self.audio.play("unlock", channel="ui3")
            self.speaker.speak("Hoverboard purchased.", interrupt=True)
        elif item == "mystery_box":
            if not self._spend_bank_coins(SHOP_PRICES["mystery_box"]):
                return
            self.audio.play("mystery_box", channel="player_box")
            self._grant_shop_box_reward(pick_shop_mystery_box_reward())
        elif item == "headstart":
            if not self._spend_bank_coins(SHOP_PRICES["headstart"]):
                return
            self.settings["headstarts"] = int(self.settings.get("headstarts", 0)) + 1
            self.audio.play("unlock", channel="ui3")
            self.speaker.speak("Headstart purchased.", interrupt=True)
        elif item == "score_booster":
            if not self._spend_bank_coins(SHOP_PRICES["score_booster"]):
                return
            self.settings["score_boosters"] = int(self.settings.get("score_boosters", 0)) + 1
            self.audio.play("unlock", channel="ui3")
            self.speaker.speak("Score booster purchased.", interrupt=True)
        self._refresh_shop_menu_labels()
        self.speaker.speak(self.shop_menu.title, interrupt=False)

    def _grant_shop_box_reward(self, reward: str) -> None:
        if reward == "coins":
            gain = random.randint(120, 420)
            self.settings["bank_coins"] = int(self.settings.get("bank_coins", 0)) + gain
            self.audio.play("gui_cash", channel="ui3")
            self.speaker.speak(f"Mystery box: {gain} coins.", interrupt=True)
            return
        if reward == "hover":
            self.settings["hoverboards"] = int(self.settings.get("hoverboards", 0)) + 1
            self.audio.play("unlock", channel="ui3")
            self.speaker.speak("Mystery box: hoverboard.", interrupt=True)
            return
        if reward == "key":
            self.settings["keys"] = int(self.settings.get("keys", 0)) + 1
            self.audio.play("unlock", channel="ui3")
            self.speaker.speak("Mystery box: key.", interrupt=True)
            return
        if reward == "headstart":
            self.settings["headstarts"] = int(self.settings.get("headstarts", 0)) + 1
            self.audio.play("mystery_combo", channel="ui3")
            self.speaker.speak("Mystery box: headstart.", interrupt=True)
            return
        if reward == "score_booster":
            self.settings["score_boosters"] = int(self.settings.get("score_boosters", 0)) + 1
            self.audio.play("mystery_combo", channel="ui3")
            self.speaker.speak("Mystery box: score booster.", interrupt=True)
            return
        self.speaker.speak("Mystery box: empty.", interrupt=True)

    def _commit_run_rewards(self) -> None:
        if self._run_rewards_committed or not self.state.running:
            return
        self._run_rewards_committed = True
        self.settings["bank_coins"] = int(self.settings.get("bank_coins", 0)) + self.state.coins

    def run(self) -> None:
        running = True
        while running:
            delta_time = self.clock.tick(60) / 1000.0
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    running = False
                elif event.type == pygame.KEYDOWN:
                    if self.active_menu is not None:
                        action = self.active_menu.handle_key(event.key)
                        if action:
                            running = self._handle_menu_action(action)
                    else:
                        self._handle_game_key(event.key)

            if self.active_menu is None:
                if not self.state.paused:
                    self._update_game(delta_time)
                self._draw_game()
            else:
                self._draw_menu(self.active_menu)

            pygame.display.flip()

        save_settings(self.settings)

    def _handle_menu_action(self, action: str) -> bool:
        if action == "close":
            if self.active_menu == self.revive_menu:
                self._finish_run_loss()
                return True
            if self.active_menu == self.main_menu:
                return False
            self.active_menu = self.main_menu
            self.active_menu.open()
            return True

        if self.active_menu == self.main_menu:
            if action == "start":
                self.selected_headstart = False
                self.selected_score_boosters = 0
                self._refresh_loadout_menu_labels()
                self.active_menu = self.loadout_menu
                self.active_menu.open()
                return True
            if action == "shop":
                self._refresh_shop_menu_labels()
                self.active_menu = self.shop_menu
                self.active_menu.open()
                return True
            if action == "options":
                self._refresh_options_menu_labels()
                self.active_menu = self.options_menu
                self.active_menu.open()
                return True
            if action == "howto":
                self._say_how_to_play()
                return True
            if action == "quit":
                return False

        if self.active_menu == self.loadout_menu:
            if action == "back":
                self.active_menu = self.main_menu
                self.active_menu.open()
                return True
            if action == "toggle_headstart":
                if int(self.settings.get("headstarts", 0)) <= 0:
                    self.audio.play("menuedge", channel="ui")
                    self.speaker.speak("No headstarts available.", interrupt=True)
                    return True
                self.selected_headstart = not self.selected_headstart
                self.audio.play("confirm", channel="ui")
                self._refresh_loadout_menu_labels()
                self.speaker.speak(self.loadout_menu.items[0].label, interrupt=True)
                return True
            if action == "toggle_score_booster":
                owned = int(self.settings.get("score_boosters", 0))
                if owned <= 0:
                    self.audio.play("menuedge", channel="ui")
                    self.speaker.speak("No score boosters available.", interrupt=True)
                    return True
                self.selected_score_boosters = (self.selected_score_boosters + 1) % (min(3, owned) + 1)
                self.audio.play("confirm", channel="ui")
                self._refresh_loadout_menu_labels()
                self.speaker.speak(self.loadout_menu.items[1].label, interrupt=True)
                return True
            if action == "begin_run":
                self.start_run()
                return True

        if self.active_menu == self.options_menu:
            if action == "back":
                self.active_menu = self.main_menu
                self.active_menu.open()
                return True
            if action == "opt_sfx":
                self.settings["sfx_volume"] = cycle_volume(float(self.settings["sfx_volume"]))
                self.audio.refresh_volumes()
                self.audio.play("confirm", channel="ui")
                self._refresh_options_menu_labels()
                self.speaker.speak(self.options_menu.items[0].label, interrupt=True)
                return True
            if action == "opt_music":
                self.settings["music_volume"] = cycle_volume(float(self.settings["music_volume"]))
                self.audio.refresh_volumes()
                self.audio.play("confirm", channel="ui")
                self._refresh_options_menu_labels()
                self.speaker.speak(self.options_menu.items[1].label, interrupt=True)
                return True
            if action == "opt_speech":
                self.settings["speech_enabled"] = not self.settings["speech_enabled"]
                self.speaker.enabled = bool(self.settings["speech_enabled"])
                self.audio.play("confirm", channel="ui")
                self._refresh_options_menu_labels()
                self.speaker.speak(self.options_menu.items[2].label, interrupt=True)
                return True
            if action == "opt_diff":
                order = ["easy", "normal", "hard"]
                current = str(self.settings["difficulty"])
                next_difficulty = order[(order.index(current) + 1) % len(order)] if current in order else "normal"
                self.settings["difficulty"] = next_difficulty
                self.audio.play("confirm", channel="ui")
                self._refresh_options_menu_labels()
                self.speaker.speak(self.options_menu.items[3].label, interrupt=True)
                return True

        if self.active_menu == self.shop_menu:
            if action == "back":
                self.active_menu = self.main_menu
                self.active_menu.open()
                return True
            if action == "buy_hoverboard":
                self._purchase_shop_item("hoverboard")
                return True
            if action == "buy_box":
                self._purchase_shop_item("mystery_box")
                return True
            if action == "buy_headstart":
                self._purchase_shop_item("headstart")
                return True
            if action == "buy_score_booster":
                self._purchase_shop_item("score_booster")
                return True

        if self.active_menu == self.pause_menu:
            if action == "resume":
                self.state.paused = False
                self.active_menu = None
                self.speaker.speak("Resume", interrupt=True)
                return True
            if action == "to_main":
                self.end_run(to_menu=True)
                return True

        if self.active_menu == self.revive_menu:
            if action == "revive":
                self._revive_run()
                return True
            if action in ("end_run", "close"):
                self._finish_run_loss()
                return True

        return True

    def _say_how_to_play(self) -> None:
        self.speaker.speak(
            "Controls: use the left and right arrow keys to change lanes. "
            "Press the up arrow to jump, the down arrow to roll, and space to activate a hoverboard. "
            "Press escape to pause. Danger speech now only calls the action for your current lane. "
            "Bushes must be jumped. Before each run you can enable Headstart and Score Booster. "
            "Keys can revive you after a crash. Missions raise your permanent multiplier. "
            "Word Hunt letters and Season Hunt tokens appear during runs. "
            "The shop lets you spend saved coins on items and mystery boxes.",
            interrupt=True,
        )

    def start_run(self) -> None:
        ensure_progression_state(self.settings)
        self.active_menu = None
        self.state = RunState(running=True)
        self.player = Player()
        self.player.hoverboards = int(self.settings.get("hoverboards", 0))
        self.obstacles = []
        self.speed_profile = speed_profile_for_difficulty(str(self.settings["difficulty"]))
        self.spatial_audio.reset()
        self.spawn_director.reset()
        self.state.multiplier = 1 + int(self.settings.get("mission_multiplier_bonus", 0)) + score_booster_bonus(
            self.selected_score_boosters
        )
        self.state.speed = self.speed_profile.base_speed
        self._footstep_timer = 0.0
        self._left_foot_next = True
        self._run_rewards_committed = False
        self._near_miss_signatures.clear()

        if self.selected_headstart:
            self.settings["headstarts"] = max(0, int(self.settings.get("headstarts", 0)) - 1)
            self.player.headstart = HEADSTART_DURATION
            self.player.y = 2.8
            self.player.vy = 0.0
            self.audio.play("intro_shake", channel="intro")
            self.audio.play("intro_spray", channel="intro_fx")
        if self.selected_score_boosters > 0:
            self.settings["score_boosters"] = max(
                0,
                int(self.settings.get("score_boosters", 0)) - self.selected_score_boosters,
            )
            self.audio.play("mission_reward", channel="boost")
            self.speaker.speak(
                f"Score booster active. Multiplier starts at x{self.state.multiplier}.",
                interrupt=False,
            )

        self.audio.play("slide_letters", channel="intro_ui")
        self.audio.play("intro_start", channel="ui")
        self.audio.music_start()
        if self.selected_headstart:
            self.speaker.speak("Run started. Headstart active.", interrupt=True)
        else:
            self.speaker.speak("Run started. Center lane.", interrupt=True)

        self.selected_headstart = False
        self.selected_score_boosters = 0
        self._refresh_loadout_menu_labels()

    def end_run(self, to_menu: bool = True) -> None:
        self._commit_run_rewards()
        self.audio.music_stop()
        self.audio.stop("loop_guard")
        self.audio.stop("loop_magnet")
        self.audio.stop("loop_jetpack")
        self._stop_spatial_audio()
        self.spatial_audio.reset()
        self.active_menu = self.main_menu if to_menu else None
        if self.active_menu is not None:
            self.active_menu.open()

    def _handle_game_key(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            self.state.paused = True
            self.active_menu = self.pause_menu
            self.pause_menu.open()
            self.audio.play("menuclose", channel="ui")
            return

        if self.state.paused or self.player.jetpack > 0 or self.player.headstart > 0:
            return

        if key == pygame.K_LEFT:
            if self.player.lane > -1:
                self.player.lane -= 1
                self._record_mission_event("dodges")
                self.audio.play("dodge", pan=lane_to_pan(self.player.lane), channel="move")
                if self.settings.get("announce_lane", True):
                    self.speaker.speak(lane_name(self.player.lane), interrupt=False)
            else:
                self.audio.play("menuedge", channel="ui")
        elif key == pygame.K_RIGHT:
            if self.player.lane < 1:
                self.player.lane += 1
                self._record_mission_event("dodges")
                self.audio.play("dodge", pan=lane_to_pan(self.player.lane), channel="move")
                if self.settings.get("announce_lane", True):
                    self.speaker.speak(lane_name(self.player.lane), interrupt=False)
            else:
                self.audio.play("menuedge", channel="ui")
        elif key == pygame.K_UP:
            self._try_jump()
        elif key == pygame.K_DOWN:
            self._try_roll()
        elif key == pygame.K_SPACE:
            self._try_hoverboard()
        elif key == pygame.K_m:
            self.settings["speech_enabled"] = not self.settings["speech_enabled"]
            self.speaker.enabled = bool(self.settings["speech_enabled"])
            message = "Speech enabled" if self.speaker.enabled else "Speech disabled"
            self.speaker.speak(message, interrupt=True)

    def _try_jump(self) -> None:
        if self.player.y > 0.01 or self.player.rolling > 0:
            return
        self.player.vy = 13.0 if self.player.super_sneakers > 0 else 10.5
        self._record_mission_event("jumps")
        sound_key = "sneakers_jump" if self.player.super_sneakers > 0 else "jump"
        self.audio.play(sound_key, pan=lane_to_pan(self.player.lane), channel="act")

    def _try_roll(self) -> None:
        if self.player.y > 0.01:
            return
        self.player.rolling = 0.7
        self._record_mission_event("rolls")
        self.audio.play("roll", pan=lane_to_pan(self.player.lane), channel="act")

    def _try_hoverboard(self) -> None:
        if self.player.hover_active > 0:
            return
        if self.player.hoverboards <= 0:
            self.speaker.speak("No hoverboards available.", interrupt=False)
            self.audio.play("menuedge", channel="ui")
            return
        self.player.hoverboards -= 1
        self.settings["hoverboards"] = max(0, int(self.settings.get("hoverboards", 0)) - 1)
        self.player.hover_active = 10.0
        self.audio.play("powerup", channel="act")
        self.speaker.speak("Hoverboard active.", interrupt=False)

    def _update_game(self, delta_time: float) -> None:
        self.state.time += delta_time
        base_speed = self.speed_profile.speed_for_elapsed(self.state.time)
        self.state.speed = base_speed + HEADSTART_SPEED_BONUS if self.player.headstart > 0 else base_speed
        speed_factor = self.speed_profile.progress(self.state.time)
        self.speaker.set_speed_factor(speed_factor)
        self.state.distance += self.state.speed * delta_time
        self.state.score += (self.state.speed * delta_time) * self._score_multiplier()

        if self.player.jetpack <= 0 and self.player.y <= 0.01 and self.player.rolling <= 0:
            self._footstep_timer -= delta_time
            if self._footstep_timer <= 0:
                self._footstep_timer = 0.33
                self._left_foot_next = not self._left_foot_next
                if self.player.super_sneakers > 0:
                    sound_key = "sneakers_left" if self._left_foot_next else "sneakers_right"
                else:
                    sound_key = "left_foot" if self._left_foot_next else "right_foot"
                if sound_key in self.audio.sounds:
                    self.audio.play(sound_key, pan=lane_to_pan(self.player.lane), channel="foot")
        else:
            self._footstep_timer = 0.0

        if self.player.jetpack <= 0 and self.player.headstart <= 0 and (self.player.y > 0 or self.player.vy != 0):
            self.player.vy -= 25.0 * delta_time
            self.player.y = max(0.0, self.player.y + self.player.vy * delta_time)
            if self.player.y <= 0.0 and self.player.vy < 0:
                self.player.y = 0.0
                self.player.vy = 0.0
                sound_key = "land_h" if self.player.super_sneakers > 0 else "landing"
                self.audio.play(sound_key, pan=lane_to_pan(self.player.lane), channel="act")

        if self.player.rolling > 0:
            self.player.rolling = max(0.0, self.player.rolling - delta_time)

        self._tick_powerups(delta_time)
        self._spawn_things(delta_time)

        for obstacle in self.obstacles:
            obstacle.z -= self.state.speed * delta_time

        self._update_near_miss_audio()

        if self.player.jetpack > 0 or self.player.headstart > 0:
            self._stop_spatial_audio()
            self.spatial_audio.reset()
        else:
            self.spatial_audio.update(delta_time, self.player.lane, self.state.speed, self.obstacles, self.audio, self.speaker)

        self._handle_obstacles()
        self.obstacles = [obstacle for obstacle in self.obstacles if obstacle.z > -5]

        milestone = int(self.state.distance // 250)
        if milestone > self.state.milestone:
            self.state.milestone = milestone
            self.audio.play("mission_reward", channel="ui")
            self.speaker.speak(f"{milestone * 250:.0f} meters", interrupt=False)

    def _score_multiplier(self) -> int:
        multiplier = self.state.multiplier
        if self.player.mult2x > 0:
            multiplier *= 2
        return multiplier

    def _tick_powerups(self, delta_time: float) -> None:
        def decay(attribute: str) -> None:
            current_value = getattr(self.player, attribute)
            if current_value > 0:
                setattr(self.player, attribute, max(0.0, current_value - delta_time))

        previous_headstart = self.player.headstart
        decay("headstart")
        if previous_headstart > 0 and self.player.headstart <= 0:
            self.player.y = 0.0
            self.player.vy = 0.0
            self.audio.play("land_h", channel="headstart_end")
            self.audio.play("powerup", channel="headstart_reward")
            self._apply_power_reward(pick_headstart_end_reward(), from_headstart=True)

        if self.player.headstart <= 0:
            decay("hover_active")
        if self.player.jetpack <= 0 and self.player.headstart <= 0:
            decay("super_sneakers")

        previous_magnet = self.player.magnet
        if self.player.jetpack <= 0 and self.player.headstart <= 0:
            decay("magnet")
        if previous_magnet > 0 and self.player.magnet <= 0:
            self.audio.stop("loop_magnet")
            self.audio.play("powerdown", channel="act")
            self.speaker.speak("Magnet expired.", interrupt=False)
        elif previous_magnet <= 0 and self.player.magnet > 0:
            self.audio.play("magnet_loop", loop=True, channel="loop_magnet")

        previous_jetpack = self.player.jetpack
        decay("jetpack")
        if previous_jetpack > 0 and self.player.jetpack <= 0:
            self.audio.stop("loop_jetpack")
            self.audio.play("powerdown", channel="act")
            self.speaker.speak("Jetpack expired.", interrupt=False)
        elif previous_jetpack <= 0 and self.player.jetpack > 0:
            self.audio.play("jetpack_loop", loop=True, channel="loop_jetpack")

        previous_multiplier = self.player.mult2x
        if self.player.jetpack <= 0 and self.player.headstart <= 0:
            decay("mult2x")
        if previous_multiplier > 0 and self.player.mult2x <= 0:
            self.audio.play("powerdown", channel="act")
            self.speaker.speak("Score boost expired.", interrupt=False)

        if self.player.stumbles >= 1 and self.player.hover_active <= 0:
            guard_channel = self.audio._get_channel("loop_guard")
            if guard_channel is not None and not guard_channel.get_busy():
                self.audio.play("guard_loop", loop=True, channel="loop_guard")
        else:
            self.audio.stop("loop_guard")

    def _spawn_things(self, delta_time: float) -> None:
        self.state.next_spawn -= delta_time
        self.state.next_coinline -= delta_time
        self.state.next_support -= delta_time
        progress = self.speed_profile.progress(self.state.time)

        if self.state.next_spawn <= 0:
            if self.spawn_director.should_delay_spawn(self.obstacles):
                self.state.next_spawn = 0.3
            else:
                pattern = self._choose_playable_pattern(progress)
                if pattern is None:
                    self.state.next_spawn = 0.35
                else:
                    chosen_pattern, distance = pattern
                    self._spawn_pattern(chosen_pattern, distance)
                    self.state.next_spawn = max(0.85, self.spawn_director.next_encounter_gap(progress))

        if self.state.next_coinline <= 0:
            lane = self.spawn_director.choose_coin_lane(self.player.lane)
            self._spawn_coin_line(lane, start_distance=self.spawn_director.base_spawn_distance(progress, self.state.speed) - 7.5)
            self.state.next_coinline = max(1.55, self.spawn_director.next_coin_gap(progress))

        if self.state.next_support <= 0:
            kind = self._choose_support_spawn_kind()
            lane = self.spawn_director.support_lane()
            distance = self.spawn_director.base_spawn_distance(progress, self.state.speed) + 1.5
            self._spawn_support_collectible(kind, lane, distance)
            self.state.next_support = max(5.5, self.spawn_director.next_support_gap(progress))

    def _spawn_pattern(self, pattern: RoutePattern, base_distance: float) -> None:
        for entry in pattern.entries:
            self.obstacles.append(Obstacle(kind=entry.kind, lane=entry.lane, z=base_distance + entry.z_offset))

    def _choose_playable_pattern(self, progress: float) -> Optional[tuple[RoutePattern, float]]:
        for pattern in self.spawn_director.candidate_patterns(progress):
            distance = self.spawn_director.base_spawn_distance(progress, self.state.speed)
            if not self.spawn_director.pattern_is_playable(
                pattern,
                distance,
                self.obstacles,
                current_lane=self.player.lane,
            ):
                continue
            self.spawn_director.accept_pattern(pattern)
            return pattern, distance
        return None

    def _spawn_coin_line(self, lane: int, start_distance: float) -> None:
        start_distance = max(18.0, start_distance)
        for index in range(6):
            self.obstacles.append(Obstacle(kind="coin", lane=lane, z=start_distance + index * 2.2, value=1))

    def _spawn_support_collectible(self, kind: str, lane: int, distance: float) -> None:
        if kind == "word":
            next_letter = self._next_word_letter()
            if next_letter:
                self.obstacles.append(Obstacle(kind="word", lane=lane, z=distance, label=next_letter))
                return
            kind = "power"
        if kind == "season_token":
            self.obstacles.append(Obstacle(kind="season_token", lane=lane, z=distance, label="S"))
            return
        if kind == "power":
            obstacle_kind = "box" if random.random() < 0.22 else "power"
        else:
            obstacle_kind = kind
        self.obstacles.append(Obstacle(kind=obstacle_kind, lane=lane, z=distance))

    def _handle_obstacles(self) -> None:
        warning_distance = 14.0
        hit_distance = 2.1
        pickup_distance = 2.2

        for obstacle in self.obstacles:
            if not obstacle.warned and 0 < obstacle.z < warning_distance and obstacle.kind in ("power", "box"):
                obstacle.warned = True
                self.audio.play("warning", pan=lane_to_pan(obstacle.lane), channel=f"warn_{id(obstacle)}", gain=0.5)

            if obstacle.kind == "coin" and -0.5 < obstacle.z < pickup_distance:
                if self.player.jetpack > 0:
                    self._collect_coin(obstacle)
                    obstacle.z = -999
                elif self.player.headstart > 0:
                    obstacle.z = -999
                elif obstacle.lane == self.player.lane:
                    self._collect_coin(obstacle)
                    obstacle.z = -999
                elif self.player.magnet > 0 and abs(obstacle.lane - self.player.lane) <= 1:
                    self._collect_coin(obstacle)
                    obstacle.z = -999

            if obstacle.kind in ("power", "box", "key", "word", "season_token") and -0.8 < obstacle.z < 2.4:
                if self.player.jetpack > 0:
                    continue
                if self.player.headstart > 0:
                    obstacle.z = -999
                    continue
                if obstacle.lane == self.player.lane:
                    if obstacle.kind == "power":
                        self._collect_power()
                    elif obstacle.kind == "key":
                        self._collect_key()
                    elif obstacle.kind == "word":
                        self._collect_word_letter(obstacle)
                    elif obstacle.kind == "season_token":
                        self._collect_season_token()
                    else:
                        self._collect_box()
                    obstacle.z = -999

            if obstacle.kind in ("train", "low", "high", "bush") and -0.8 < obstacle.z < hit_distance:
                if self.player.jetpack > 0 or self.player.headstart > 0 or obstacle.lane != self.player.lane:
                    continue
                if obstacle.kind in ("low", "bush") and self.player.y > 0.6:
                    continue
                if obstacle.kind == "high" and self.player.rolling > 0:
                    continue
                self._on_hit("bush" if obstacle.kind == "bush" else "default")
                obstacle.z = -999

    def _collect_coin(self, obstacle: Obstacle) -> None:
        self.state.coins += 1
        self._record_mission_event("coins")
        self.audio.play("coin", pan=lane_to_pan(obstacle.lane), channel="coin")
        announce_every = int(self.settings.get("announce_coins_every", 10) or 0)
        if announce_every and self.state.coins % announce_every == 0:
            self.speaker.speak(f"{self.state.coins} coins", interrupt=False)

    def _collect_power(self) -> None:
        self._record_mission_event("powerups")
        self.audio.play("powerup", channel="act")
        reward = random.choices(
            ["magnet", "jetpack", "mult2x", "sneakers"],
            weights=[0.35, 0.20, 0.30, 0.15],
            k=1,
        )[0]
        self._apply_power_reward(reward, from_headstart=False)

    def _collect_box(self) -> None:
        self._record_mission_event("boxes")
        self.audio.play("mystery_box", channel="act")
        reward = pick_mystery_box_reward()
        if reward == "coins":
            gain = random.randint(10, 40)
            self.state.coins += gain
            self.speaker.speak(f"Mystery box: {gain} coins.", interrupt=False)
            self.audio.play("gui_cash", channel="ui")
        elif reward == "hover":
            self.settings["hoverboards"] = int(self.settings.get("hoverboards", 0)) + 1
            self.player.hoverboards += 1
            self.speaker.speak("Mystery box: hoverboard.", interrupt=False)
            self.audio.play("unlock", channel="ui")
        elif reward == "mult":
            self.state.multiplier = min(10, self.state.multiplier + 1)
            self.speaker.speak(f"Mystery box: multiplier {self.state.multiplier}.", interrupt=False)
            self.audio.play("mission_reward", channel="ui")
        elif reward == "key":
            self.settings["keys"] = int(self.settings.get("keys", 0)) + 1
            self.speaker.speak("Mystery box: key.", interrupt=False)
            self.audio.play("unlock", channel="ui")
        elif reward == "headstart":
            self.settings["headstarts"] = int(self.settings.get("headstarts", 0)) + 1
            self.speaker.speak("Mystery box: headstart.", interrupt=False)
            self.audio.play("mystery_combo", channel="ui")
        elif reward == "score_booster":
            self.settings["score_boosters"] = int(self.settings.get("score_boosters", 0)) + 1
            self.speaker.speak("Mystery box: score booster.", interrupt=False)
            self.audio.play("mystery_combo", channel="ui")
        else:
            self.speaker.speak("Mystery box: empty.", interrupt=False)

    def _collect_key(self) -> None:
        self.settings["keys"] = int(self.settings.get("keys", 0)) + 1
        self.audio.play("unlock", channel="ui")
        self.speaker.speak(f"Key collected. Total keys: {self.settings['keys']}.", interrupt=False)

    def _collect_word_letter(self, obstacle: Obstacle) -> None:
        letter, completed = register_word_letter(self.settings)
        if not letter:
            return
        self.audio.play("slide_letters", channel="ui")
        if completed:
            self.speaker.speak(f"Letter {letter}.", interrupt=False)
            self._complete_word_hunt()
            return
        remaining_letters = len(self._remaining_word_letters())
        self.speaker.speak(f"Letter {letter}. {remaining_letters} letters left.", interrupt=False)

    def _collect_season_token(self) -> None:
        tokens, next_threshold = register_season_token(self.settings)
        self.audio.play("coin_gui", channel="ui")
        if can_claim_season_reward(self.settings):
            self.speaker.speak("Season token. Reward unlocked.", interrupt=False)
            self._claim_season_reward()
            return
        if next_threshold is None:
            self.speaker.speak(f"Season token. Total {tokens}.", interrupt=False)
            return
        self.speaker.speak(f"Season token. {tokens} of {next_threshold}.", interrupt=False)

    def _apply_power_reward(self, reward: str, from_headstart: bool) -> None:
        if reward == "magnet":
            self.player.magnet = 9.0
            message = "Headstart reward: magnet." if from_headstart else "Magnet."
            self.speaker.speak(message, interrupt=False)
            return
        if reward == "jetpack":
            self.player.jetpack = 6.5
            self.player.y = 2.0
            self.player.vy = 0.0
            self.speaker.speak("Jetpack.", interrupt=False)
            return
        if reward == "mult2x":
            self.player.mult2x = 10.0
            message = "Headstart reward: double score." if from_headstart else "Double score."
            self.speaker.speak(message, interrupt=False)
            return
        if reward == "sneakers":
            self.player.super_sneakers = 10.0
            message = "Headstart reward: super sneakers." if from_headstart else "Super sneakers."
            self.speaker.speak(message, interrupt=False)

    def _queue_revive_or_finish(self) -> None:
        cost = revive_cost(self.state.revives_used)
        if int(self.settings.get("keys", 0)) < cost:
            self._finish_run_loss()
            return
        self.state.paused = True
        self.audio.play("guard_catch", channel="act2")
        self.audio.play("gui_close", channel="ui")
        self._refresh_revive_menu_label()
        self.active_menu = self.revive_menu
        self.active_menu.open()
        self.speaker.speak(
            f"You can revive for {cost} key{'s' if cost != 1 else ''}.",
            interrupt=True,
        )

    def _revive_run(self) -> None:
        cost = revive_cost(self.state.revives_used)
        owned = int(self.settings.get("keys", 0))
        if owned < cost:
            self.audio.play("menuedge", channel="ui")
            self.speaker.speak("Not enough keys.", interrupt=True)
            return
        self.settings["keys"] = owned - cost
        self.state.revives_used += 1
        self.state.paused = False
        self.player.stumbles = 0
        self.player.rolling = 0.0
        self.player.y = 0.0
        self.player.vy = 0.0
        self.player.hover_active = max(self.player.hover_active, 3.5)
        self.active_menu = None
        self.audio.play("unlock", channel="ui")
        self.audio.play("powerup", channel="act")
        self.speaker.speak("Revived. Temporary shield active.", interrupt=True)

    def _finish_run_loss(self) -> None:
        self.state.paused = False
        self._stop_spatial_audio()
        self.audio.play("kick", channel="player_kick")
        self.audio.play("death_hitcam", channel="player_death_cam")
        self.audio.play("death_bodyfall", channel="player_death_fall")
        self.audio.play("death", channel="act")
        self.audio.play("guard_catch", channel="act2")
        self.speaker.speak(
            f"Run over. Score {int(self.state.score)}. Coins {self.state.coins}.",
            interrupt=True,
        )
        self.end_run(to_menu=True)

    def _stop_spatial_audio(self) -> None:
        for lane in LANES:
            self.audio.stop(f"spatial_{lane}")

    def _on_hit(self, variant: str = "default") -> None:
        if self.player.hover_active > 0:
            self.player.hover_active = 0.0
            self.audio.play("crash", channel="act")
            self.audio.play("powerdown", channel="act2")
            self.speaker.speak("Hoverboard destroyed.", interrupt=True)
            return

        self.player.stumbles += 1
        if self.player.stumbles >= 2:
            self._queue_revive_or_finish()
            return

        if variant == "bush":
            stumble_sound = "stumble_bush"
        else:
            stumble_sound = "stumble_side" if self.player.lane != 0 else "stumble"
        self.audio.play(stumble_sound, channel="act")
        self.audio.play("crash", channel="act2")
        self.speaker.speak("You crashed. One chance left.", interrupt=True)

    def _update_near_miss_audio(self) -> None:
        active_signatures: set[tuple[str, int]] = set()
        for obstacle in self.obstacles:
            if obstacle.kind not in {"train", "low", "high", "bush"}:
                continue
            if not (-0.2 <= obstacle.z <= 2.1):
                continue
            lane_delta = abs(obstacle.lane - self.player.lane)
            if lane_delta > 1:
                continue
            if lane_delta == 0:
                if obstacle.kind in {"low", "bush"} and self.player.y > 0.6:
                    pass
                elif obstacle.kind == "high" and self.player.rolling > 0:
                    pass
                else:
                    continue
            signature = (obstacle.kind, id(obstacle))
            active_signatures.add(signature)
            if signature in self._near_miss_signatures:
                continue
            if obstacle.kind == "train":
                sound_key = "swish_long"
            elif lane_delta == 0:
                sound_key = "swish_mid"
            else:
                sound_key = "swish_short"
            self.audio.play(sound_key, channel=f"near_{obstacle.lane}")
        self._near_miss_signatures = active_signatures

    def _draw_menu(self, menu: Menu) -> None:
        self.screen.fill((10, 10, 15))
        title_surface = self.big.render(menu.title, True, (240, 240, 240))
        self.screen.blit(title_surface, (40, 40))
        y_position = 120
        for index, item in enumerate(menu.items):
            color = (255, 255, 0) if index == menu.index else (220, 220, 220)
            label_surface = self.font.render(item.label, True, color)
            self.screen.blit(label_surface, (70, y_position))
            y_position += 40

        hint_surface = self.font.render("Use up/down, Enter to select, Esc to go back.", True, (180, 180, 180))
        self.screen.blit(hint_surface, (40, 520))

    def _draw_game(self) -> None:
        width, height = self.screen.get_size()
        self.screen.fill((5, 5, 10))

        lane_width = width // 3
        for index in range(3):
            x = index * lane_width
            pygame.draw.rect(self.screen, (18, 18, 28), (x + 2, 0, lane_width - 4, height))
            pygame.draw.line(self.screen, (40, 40, 60), (x, 0), (x, height), 2)

        for obstacle in self.obstacles:
            if obstacle.z > 60 or obstacle.z < -1:
                continue
            size = max(10, int(1400 / (obstacle.z + 15)))
            lane_index = obstacle.lane + 1
            center_x = lane_index * lane_width + lane_width // 2
            center_y = int(height - 80 - (60 - obstacle.z) * 6)
            color = (200, 80, 80)
            if obstacle.kind == "coin":
                color = (240, 200, 40)
                size = max(8, size // 2)
            elif obstacle.kind == "power":
                color = (60, 200, 220)
            elif obstacle.kind == "box":
                color = (160, 100, 220)
            elif obstacle.kind == "key":
                color = (80, 220, 255)
                size = max(10, size // 2)
            elif obstacle.kind == "word":
                color = (250, 235, 90)
                size = max(12, size // 2)
            elif obstacle.kind == "season_token":
                color = (255, 145, 60)
                size = max(12, size // 2)
            elif obstacle.kind == "high":
                color = (220, 120, 60)
            elif obstacle.kind == "low":
                color = (60, 220, 120)
            elif obstacle.kind == "bush":
                color = (40, 160, 60)
            elif obstacle.kind == "train":
                color = (180, 180, 180)
            pygame.draw.rect(self.screen, color, (center_x - size // 2, center_y - size // 2, size, size))
            if obstacle.label:
                glyph_surface = self.font.render(obstacle.label, True, (20, 20, 20))
                glyph_rect = glyph_surface.get_rect(center=(center_x, center_y))
                self.screen.blit(glyph_surface, glyph_rect)

        player_x = (self.player.lane + 1) * lane_width + lane_width // 2
        player_y = height - 120 - int(self.player.y * 40)
        player_height = 50 if self.player.rolling <= 0 else 28
        pygame.draw.rect(self.screen, (80, 160, 255), (player_x - 18, player_y - player_height, 36, player_height))

        hud = (
            f"Score: {int(self.state.score)}   Coins: {self.state.coins}   "
            f"Multiplier: x{self._score_multiplier()}   Speed: {self.state.speed:.1f}   "
            f"Boards: {self.player.hoverboards}   Keys: {int(self.settings.get('keys', 0))}"
        )
        if self.player.hover_active > 0:
            hud += "   [Hoverboard]"
        if self.player.headstart > 0:
            hud += "   [Headstart]"
        if self.player.magnet > 0:
            hud += "   [Magnet]"
        if self.player.jetpack > 0:
            hud += "   [Jetpack]"
        if self.player.mult2x > 0:
            hud += "   [2x]"
        if self.player.super_sneakers > 0:
            hud += "   [Super Sneakers]"
        hud_surface = self.font.render(hud, True, (230, 230, 230))
        self.screen.blit(hud_surface, (15, 10))

        next_threshold = next_season_reward_threshold(self.settings)
        word = self._current_word()
        found_letters = str(self.settings.get("word_hunt_letters", ""))
        season_progress = (
            f"{int(self.settings.get('season_tokens', 0))}/{next_threshold}"
            if next_threshold is not None
            else f"{int(self.settings.get('season_tokens', 0))}/done"
        )
        meta_hud = (
            f"{self._mission_status_text()}   "
            f"Word Hunt: {found_letters or '-'} / {word}   "
            f"Season Hunt: {season_progress}"
        )
        meta_surface = self.font.render(meta_hud, True, (205, 205, 205))
        self.screen.blit(meta_surface, (15, 36))

        if self.state.paused:
            overlay = pygame.Surface((width, height), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 160))
            self.screen.blit(overlay, (0, 0))
