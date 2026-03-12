from __future__ import annotations

import sys
import textwrap
from dataclasses import dataclass
import random
import threading
from typing import Optional

import pygame

from subway_blind.audio import (
    Audio,
    Speaker,
    SAPI_RATE_MAX,
    SAPI_RATE_MIN,
    SAPI_PITCH_MAX,
    SAPI_PITCH_MIN,
    SAPI_VOICE_UNAVAILABLE_LABEL,
    SYSTEM_DEFAULT_OUTPUT_LABEL,
)
from subway_blind.balance import SpeedProfile, speed_profile_for_difficulty
from subway_blind.config import save_settings
from subway_blind.controls import (
    ACTION_DEFINITIONS_BY_KEY,
    CONTROLLER_ACTION_ORDER,
    GAME_CONTEXT,
    KEYBOARD_ACTION_ORDER,
    MENU_CONTEXT,
    ControllerSupport,
    action_label,
    controller_binding_label,
    family_label,
    keyboard_key_label,
)
from subway_blind.features import (
    clamp_headstart_uses,
    HEADSTART_SPEED_BONUS,
    headstart_duration_for_uses,
    HOVERBOARD_DURATION,
    pick_headstart_end_reward,
    pick_mystery_box_reward,
    pick_shop_mystery_box_reward,
    revive_cost,
    SHOP_PRICES,
    shop_box_reward_amount,
    score_booster_bonus,
)
from subway_blind.menu import Menu, MenuItem
from subway_blind.models import LANES, Obstacle, Player, RunState, lane_name, lane_to_pan, normalize_lane
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
from subway_blind.updater import GitHubReleaseUpdater, UpdateCheckResult, UpdateInstallProgress, UpdateInstallResult
from subway_blind.version import APP_VERSION

DIFFICULTY_LABELS = {
    "easy": "Easy",
    "normal": "Normal",
    "hard": "Hard",
}

GUARD_LOOP_DURATION = 1.35
MULTIPLIER_PICKUP_DURATION = 12.0
POGO_STICK_DURATION = 5.5
MENU_REPEAT_INITIAL_DELAY = 0.34
MENU_REPEAT_INTERVAL = 0.075
LEARN_SOUND_PREVIEW_CHANNEL = "learn_sound_preview"
LEARN_SOUND_LOOP_PREVIEW_DURATION = 2.6
HEADSTART_SHAKE_CHANNEL = "intro_headstart_shake"
HEADSTART_SPRAY_CHANNEL = "intro_headstart_spray"
MIN_WINDOW_WIDTH = 640
MIN_WINDOW_HEIGHT = 360


@dataclass(frozen=True)
class BindingCaptureRequest:
    device: str
    action_key: str


@dataclass(frozen=True)
class LearnSoundEntry:
    key: str
    label: str
    description: str
    loop: bool = False
    gain: float = 1.0


LEARN_SOUND_DETAILS: dict[str, LearnSoundEntry] = {
    "coin": LearnSoundEntry("coin", "Coin Pickup", "Plays when you collect a coin on the track."),
    "coin_gui": LearnSoundEntry("coin_gui", "Coin Bank", "Plays when coins are added to your saved total."),
    "jump": LearnSoundEntry("jump", "Jump", "Plays when you perform a normal jump."),
    "roll": LearnSoundEntry("roll", "Roll", "Plays when you duck under a high obstacle."),
    "dodge": LearnSoundEntry("dodge", "Lane Change", "Plays when you move left or right between lanes."),
    "landing": LearnSoundEntry("landing", "Landing", "Plays when you land after a normal jump."),
    "stumble": LearnSoundEntry("stumble", "Stumble", "Plays after a standard hit that still leaves one chance."),
    "stumble_side": LearnSoundEntry("stumble_side", "Side Stumble", "Plays after a side impact warning stumble."),
    "stumble_bush": LearnSoundEntry("stumble_bush", "Bush Stumble", "Plays when you hit a bush and survive the impact."),
    "crash": LearnSoundEntry("crash", "Crash", "Plays when a hoverboard absorbs a crash."),
    "death": LearnSoundEntry("death", "Death", "Main run over sound after the final hit."),
    "death_bodyfall": LearnSoundEntry("death_bodyfall", "Body Fall", "Body impact layer used during a full run loss."),
    "death_hitcam": LearnSoundEntry("death_hitcam", "Hit Camera", "Heavy hit layer used during the run over sequence."),
    "guard_catch": LearnSoundEntry("guard_catch", "Guard Catch", "Plays when the guard reaches you after a serious collision."),
    "guard_loop": LearnSoundEntry("guard_loop", "Guard Loop", "Short guard pressure loop after the first stumble.", loop=True, gain=0.72),
    "powerup": LearnSoundEntry("powerup", "Power Up", "Plays when you collect or activate a positive power item."),
    "powerdown": LearnSoundEntry("powerdown", "Power Down", "Plays when a temporary power effect expires."),
    "magnet_loop": LearnSoundEntry("magnet_loop", "Magnet Loop", "Looping sound while the coin magnet is active.", loop=True, gain=0.88),
    "jetpack_loop": LearnSoundEntry("jetpack_loop", "Jetpack Loop", "Looping sound while the jetpack is active.", loop=True, gain=0.88),
    "mystery_box": LearnSoundEntry("mystery_box", "Mystery Box", "Plays when a mystery box is collected or opened."),
    "mission_reward": LearnSoundEntry("mission_reward", "Mission Reward", "Reward chime for milestones, missions, and progress."),
    "train_pass": LearnSoundEntry("train_pass", "Train Pass", "Warning fly-by for a train moving through the scene."),
    "intro_start": LearnSoundEntry("intro_start", "Run Start", "Opening sound when a new run begins."),
    "intro_shake": LearnSoundEntry("intro_shake", "Headstart Shake", "Headstart launch shake effect."),
    "intro_spray": LearnSoundEntry("intro_spray", "Headstart Spray", "Headstart spray layer during the run intro."),
    "gui_cash": LearnSoundEntry("gui_cash", "Cash Reward", "Reward sound for large coin payouts."),
    "gui_close": LearnSoundEntry("gui_close", "Close Burst", "Sharp UI burst used before the revive choice."),
    "gui_tap": LearnSoundEntry("gui_tap", "Shop Tap", "Plays when a shop purchase is accepted."),
    "unlock": LearnSoundEntry("unlock", "Unlock", "Reward unlock sound for items and keys."),
    "left_foot": LearnSoundEntry("left_foot", "Left Footstep", "Regular left foot running step."),
    "right_foot": LearnSoundEntry("right_foot", "Right Footstep", "Regular right foot running step."),
    "sneakers_jump": LearnSoundEntry("sneakers_jump", "Super Sneakers Jump", "High jump launch used by super sneakers and pogo."),
    "sneakers_left": LearnSoundEntry("sneakers_left", "Super Sneakers Left Step", "Enhanced left footstep while super sneakers are active."),
    "sneakers_right": LearnSoundEntry("sneakers_right", "Super Sneakers Right Step", "Enhanced right footstep while super sneakers are active."),
    "slide_letters": LearnSoundEntry("slide_letters", "Letter Slide", "Plays when word hunt letters or intro tiles slide in."),
    "mystery_combo": LearnSoundEntry("mystery_combo", "Mystery Combo", "Bonus layer used for special mystery rewards."),
    "kick": LearnSoundEntry("kick", "Kick", "Impact layer used in the run over sequence."),
    "land_h": LearnSoundEntry("land_h", "Heavy Landing", "Heavy landing used after strong jumps or headstart endings."),
    "swish_short": LearnSoundEntry("swish_short", "Short Near Miss", "Short near-miss pass sound for a very quick close call."),
    "swish_mid": LearnSoundEntry("swish_mid", "Medium Near Miss", "Medium near-miss pass sound for a close call."),
    "swish_long": LearnSoundEntry("swish_long", "Long Near Miss", "Long near-miss pass sound for a sweeping close call."),
    "warning": LearnSoundEntry("warning", "Warning Pulse", "Warning pulse for hazards and support items ahead."),
}
ACTIVE_GAMEPLAY_SOUND_KEYS: tuple[str, ...] = (
    "coin",
    "coin_gui",
    "jump",
    "roll",
    "dodge",
    "landing",
    "stumble",
    "stumble_side",
    "stumble_bush",
    "crash",
    "death",
    "death_bodyfall",
    "death_hitcam",
    "guard_catch",
    "guard_loop",
    "powerup",
    "powerdown",
    "magnet_loop",
    "jetpack_loop",
    "mystery_box",
    "mission_reward",
    "train_pass",
    "intro_start",
    "intro_shake",
    "intro_spray",
    "gui_cash",
    "gui_close",
    "gui_tap",
    "unlock",
    "left_foot",
    "right_foot",
    "sneakers_jump",
    "sneakers_left",
    "sneakers_right",
    "slide_letters",
    "mystery_combo",
    "kick",
    "land_h",
    "swish_short",
    "swish_mid",
    "swish_long",
    "warning",
)
LEARN_SOUND_LIBRARY: tuple[LearnSoundEntry, ...] = tuple(
    LEARN_SOUND_DETAILS[key] for key in ACTIVE_GAMEPLAY_SOUND_KEYS
)


def step_volume(value: float, direction: int) -> float:
    stepped = round(float(value) + (0.1 * direction), 1)
    return max(0.0, min(1.0, stepped))


def step_int(value: int, direction: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, int(value) + direction))


class SubwayBlindGame:
    def __init__(
        self,
        screen: pygame.Surface,
        clock: pygame.time.Clock,
        settings: dict,
        updater: GitHubReleaseUpdater | None = None,
        packaged_build: bool | None = None,
    ):
        self.screen = screen
        self.clock = clock
        self.settings = settings
        self.speaker = Speaker.from_settings(settings)
        self.audio = Audio(settings)
        self.updater = updater or GitHubReleaseUpdater()
        self.packaged_build = bool(getattr(sys, "frozen", False)) if packaged_build is None else bool(packaged_build)
        self.font = pygame.font.SysFont("segoeui", 22)
        self.big = pygame.font.SysFont("segoeui", 38, bold=True)
        ensure_progression_state(self.settings)

        self.state = RunState()
        self.player = Player()
        self.obstacles: list[Obstacle] = []
        self.speed_profile: SpeedProfile = speed_profile_for_difficulty(str(self.settings["difficulty"]))
        self.spatial_audio = SpatialThreatAudio()
        self.spawn_director = SpawnDirector()
        self.selected_headstarts = 0
        self.selected_score_boosters = 0
        self._footstep_timer = 0.0
        self._left_foot_next = True
        self._run_rewards_committed = False
        self._near_miss_signatures: set[tuple[str, int]] = set()
        self._guard_loop_timer = 0.0
        self._menu_repeat_key: int | None = None
        self._menu_repeat_delay_remaining = 0.0
        self._learn_sound_entries_by_action = {
            f"learn_sound:{entry.key}": entry for entry in LEARN_SOUND_LIBRARY
        }
        self._learn_sound_description = "Press Enter to play the selected game sound."
        self._learn_sound_preview_timer = 0.0
        self._exit_requested = False
        self._latest_update_result: UpdateCheckResult | None = None
        self._update_status_message = "Check GitHub Releases for a newer version."
        self._update_release_notes = "No release notes were provided."
        self._update_progress_percent = 0.0
        self._update_progress_message = ""
        self._update_progress_stage = "idle"
        self._update_progress_announced_bucket = -1
        self._update_install_thread: threading.Thread | None = None
        self._update_install_result: UpdateInstallResult | None = None
        self._update_restart_script_path: str | None = None
        self._update_install_error = ""
        self._update_ready_announced = False
        self.controls = ControllerSupport(settings)
        self._binding_capture: BindingCaptureRequest | None = None
        self._selected_binding_device = "controller" if self.controls.active_controller() is not None else "keyboard"
        self._game_over_summary = {"score": 0, "coins": 0, "death_reason": "Run ended."}
        self._last_death_reason = "Run ended."
        self._pending_menu_announcement: Optional[tuple[Menu, float]] = None
        self._magnet_loop_active = False
        self._jetpack_loop_active = False

        self.pause_menu = Menu(
            self.speaker,
            self.audio,
            "Paused",
            [
                MenuItem("Resume", "resume"),
                MenuItem("Return to Main Menu", "to_main"),
            ],
        )
        self.pause_confirm_menu = Menu(
            self.speaker,
            self.audio,
            "Return to Main Menu?",
            [
                MenuItem("Yes", "confirm_to_main"),
                MenuItem("No", "cancel_to_main"),
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
        self.game_over_menu = Menu(
            self.speaker,
            self.audio,
            "Game Over",
            [
                MenuItem("Score: 0", "game_over_info_score"),
                MenuItem("Coins: 0", "game_over_info_coins"),
                MenuItem("Death reason: Run ended.", "game_over_info_reason"),
                MenuItem("Run again", "game_over_retry"),
                MenuItem("Main menu", "game_over_main_menu"),
            ],
        )
        self.main_menu = Menu(
            self.speaker,
            self.audio,
            self._main_menu_title(),
            [
                MenuItem("Start Game", "start"),
                MenuItem("Shop", "shop"),
                MenuItem("Options", "options"),
                MenuItem("How to Play", "howto"),
                MenuItem("Learn Game Sounds", "learn_sounds"),
                MenuItem("Check for Updates", "check_updates"),
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
                MenuItem(self._updates_option_label(), "opt_updates"),
                MenuItem(self._audio_output_option_label(), "opt_output"),
                MenuItem(self._menu_sound_hrtf_option_label(), "opt_menu_hrtf"),
                MenuItem(self._speech_option_label(), "opt_speech"),
                MenuItem(self._sapi_speech_option_label(), "opt_sapi"),
                MenuItem(self._sapi_voice_option_label(), "opt_sapi_voice"),
                MenuItem(self._sapi_rate_option_label(), "opt_sapi_rate"),
                MenuItem(self._sapi_pitch_option_label(), "opt_sapi_pitch"),
                MenuItem(self._difficulty_option_label(), "opt_diff"),
                MenuItem("Controls", "opt_controls"),
                MenuItem("Back", "back"),
            ],
        )
        self.controls_menu = Menu(
            self.speaker,
            self.audio,
            "Controls",
            [],
        )
        self.keyboard_bindings_menu = Menu(
            self.speaker,
            self.audio,
            "Keyboard Bindings",
            [],
        )
        self.controller_bindings_menu = Menu(
            self.speaker,
            self.audio,
            "Controller Bindings",
            [],
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
        self.learn_sounds_menu = Menu(
            self.speaker,
            self.audio,
            "Learn Game Sounds",
            [MenuItem(entry.label, f"learn_sound:{entry.key}") for entry in LEARN_SOUND_LIBRARY] + [MenuItem("Back", "back")],
        )
        self.update_menu = Menu(
            self.speaker,
            self.audio,
            "Update Required",
            [
                MenuItem("Download and Install Update", "download_update"),
                MenuItem("Open Release Page", "open_release_page"),
                MenuItem("Quit Game", "quit"),
            ],
        )
        self._refresh_control_menus()

        self.active_menu: Optional[Menu] = self.main_menu
        if self.packaged_build and bool(self.settings.get("check_updates_on_startup", True)):
            self._check_for_updates(announce_result=False, automatic=True)
        if self.active_menu == self.main_menu and not self.main_menu.opened:
            self.active_menu.open()
            self._sync_music_context()

    def _sfx_option_label(self) -> str:
        return f"SFX Volume: {int(float(self.settings['sfx_volume']) * 100)}"

    def _main_menu_title(self) -> str:
        return f"Main Menu   Version: {APP_VERSION}"

    def _shop_title(self) -> str:
        return "Shop"

    def _shop_coins_label(self) -> str:
        return f"Coins: {int(self.settings.get('bank_coins', 0))}"

    def _music_option_label(self) -> str:
        return f"Music Volume: {int(float(self.settings['music_volume']) * 100)}"

    def _updates_option_label(self) -> str:
        return (
            f"Check for Updates on Startup: "
            f"{'On' if self.settings['check_updates_on_startup'] else 'Off'}"
        )

    def _speech_option_label(self) -> str:
        return f"Speech: {'On' if self.settings['speech_enabled'] else 'Off'}"

    def _sapi_speech_option_label(self) -> str:
        return f"SAPI Speech: {'On' if self.settings['sapi_speech_enabled'] else 'Off'}"

    def _audio_output_option_label(self) -> str:
        return f"Output Device: {self.audio.output_device_display_name()}"

    def _menu_sound_hrtf_option_label(self) -> str:
        return f"Menu Sound HRTF: {'On' if self.settings['menu_sound_hrtf'] else 'Off'}"

    def _sapi_voice_option_label(self) -> str:
        voice_name = self.speaker.current_sapi_voice_display_name()
        return f"SAPI Voice: {voice_name}"

    def _sapi_rate_option_label(self) -> str:
        return f"SAPI Rate: {int(self.settings.get('sapi_rate', 0))}"

    def _sapi_pitch_option_label(self) -> str:
        return f"SAPI Pitch: {int(self.settings.get('sapi_pitch', 0))}"

    def _difficulty_option_label(self) -> str:
        difficulty = DIFFICULTY_LABELS.get(str(self.settings["difficulty"]), "Normal")
        return f"Difficulty: {difficulty}"

    def _headstart_option_label(self) -> str:
        owned = int(self.settings.get("headstarts", 0))
        return f"Headstart: {self.selected_headstarts}   Owned: {owned}"

    def _score_booster_option_label(self) -> str:
        owned = int(self.settings.get("score_boosters", 0))
        return f"Score Booster: {self.selected_score_boosters}   Owned: {owned}"

    def _revive_option_label(self) -> str:
        cost = revive_cost(self.state.revives_used)
        owned = int(self.settings.get("keys", 0))
        return f"Use {cost} key{'s' if cost != 1 else ''} to revive   Owned: {owned}"

    def _shop_hoverboard_label(self) -> str:
        return (
            f"Buy Hoverboard   Cost: {SHOP_PRICES['hoverboard']} Coins   "
            f"Owned: {int(self.settings.get('hoverboards', 0))}"
        )

    def _shop_box_label(self) -> str:
        return f"Open Mystery Box   Cost: {SHOP_PRICES['mystery_box']} Coins"

    def _shop_headstart_label(self) -> str:
        return (
            f"Buy Headstart   Cost: {SHOP_PRICES['headstart']} Coins   "
            f"Owned: {int(self.settings.get('headstarts', 0))}"
        )

    def _shop_score_booster_label(self) -> str:
        return (
            f"Buy Score Booster   Cost: {SHOP_PRICES['score_booster']} Coins   "
            f"Owned: {int(self.settings.get('score_boosters', 0))}"
        )

    def _refresh_options_menu_labels(self) -> None:
        self.options_menu.items[0].label = self._sfx_option_label()
        self.options_menu.items[1].label = self._music_option_label()
        self.options_menu.items[2].label = self._updates_option_label()
        self.options_menu.items[3].label = self._audio_output_option_label()
        self.options_menu.items[4].label = self._menu_sound_hrtf_option_label()
        self.options_menu.items[5].label = self._speech_option_label()
        self.options_menu.items[6].label = self._sapi_speech_option_label()
        self.options_menu.items[7].label = self._sapi_voice_option_label()
        self.options_menu.items[8].label = self._sapi_rate_option_label()
        self.options_menu.items[9].label = self._sapi_pitch_option_label()
        self.options_menu.items[10].label = self._difficulty_option_label()
        self.options_menu.items[11].label = "Controls"

    def _refresh_loadout_menu_labels(self) -> None:
        self.loadout_menu.items[0].label = self._headstart_option_label()
        self.loadout_menu.items[1].label = self._score_booster_option_label()

    def _refresh_revive_menu_label(self) -> None:
        self.revive_menu.items[0].label = self._revive_option_label()

    def _refresh_game_over_menu(self) -> None:
        summary = self._game_over_summary
        self.game_over_menu.items[0].label = f"Score: {int(summary['score'])}"
        self.game_over_menu.items[1].label = f"Coins: {int(summary['coins'])}"
        self.game_over_menu.items[2].label = f"Death reason: {summary['death_reason']}"
        self.game_over_menu.items[3].label = "Run again"
        self.game_over_menu.items[4].label = "Main menu"

    def _refresh_shop_menu_labels(self) -> None:
        self.shop_menu.title = self._shop_title()
        self.shop_menu.items[0].label = self._shop_hoverboard_label()
        self.shop_menu.items[1].label = self._shop_box_label()
        self.shop_menu.items[2].label = self._shop_headstart_label()
        self.shop_menu.items[3].label = self._shop_score_booster_label()

    def _build_controls_menu(self) -> None:
        self._sync_selected_binding_device()
        items = [
            MenuItem(f"Active Input: {self.controls.current_input_label()}", "announce_active_input"),
            MenuItem(f"Binding Profile: {self._selected_binding_profile_label()}", "select_binding_profile"),
            MenuItem("Customize Bindings", "open_selected_bindings"),
            MenuItem(f"Reset {self._selected_binding_profile_label()}", "reset_selected_bindings"),
        ]
        items.append(MenuItem("Back", "back"))
        self.controls_menu.items = items
        self.controls_menu.title = "Controls"

    def _sync_selected_binding_device(self) -> None:
        if self.controls.active_controller() is None:
            self._selected_binding_device = "keyboard"
            return
        if self._selected_binding_device not in {"keyboard", "controller"}:
            self._selected_binding_device = "controller"
            return
        if self.controls.last_input_source == "controller":
            self._selected_binding_device = "controller"

    def _selected_binding_profile_label(self) -> str:
        if self._selected_binding_device == "controller" and self.controls.active_controller() is not None:
            return family_label(self.controls.current_controller_family())
        return "Keyboard"

    def _cycle_selected_binding_device(self, direction: int) -> None:
        if direction not in (-1, 1):
            return
        available_devices = ["keyboard"]
        if self.controls.active_controller() is not None:
            available_devices.append("controller")
        if len(available_devices) == 1:
            self._play_menu_feedback("menuedge")
            return
        try:
            current_index = available_devices.index(self._selected_binding_device)
        except ValueError:
            current_index = 0
        self._selected_binding_device = available_devices[(current_index + direction) % len(available_devices)]
        self._play_menu_feedback("confirm")
        self._build_controls_menu()
        self.speaker.speak(self.controls_menu.items[1].label, interrupt=True)

    def _build_keyboard_bindings_menu(self) -> None:
        items = []
        for action_key in KEYBOARD_ACTION_ORDER:
            label = action_label(action_key)
            binding = keyboard_key_label(self.controls.keyboard_binding_for_action(action_key))
            items.append(MenuItem(f"{label}: {binding}", f"bind_keyboard:{action_key}"))
        items.append(MenuItem("Reset to Defaults", "reset_keyboard_bindings"))
        items.append(MenuItem("Back", "back"))
        self.keyboard_bindings_menu.items = items
        self.keyboard_bindings_menu.title = "Keyboard Bindings"

    def _build_controller_bindings_menu(self) -> None:
        family = self.controls.current_controller_family()
        items = []
        for action_key in CONTROLLER_ACTION_ORDER:
            label = action_label(action_key)
            binding = controller_binding_label(self.controls.controller_binding_for_action(action_key, family), family)
            items.append(MenuItem(f"{label}: {binding}", f"bind_controller:{action_key}"))
        items.append(MenuItem("Reset to Recommended", "reset_controller_bindings"))
        items.append(MenuItem("Back", "back"))
        self.controller_bindings_menu.items = items
        self.controller_bindings_menu.title = f"{family_label(family)} Bindings"

    def _refresh_control_menus(self) -> None:
        self._build_controls_menu()
        self._build_keyboard_bindings_menu()
        self._build_controller_bindings_menu()

    def _current_learn_sound_entry(self) -> LearnSoundEntry | None:
        if self.active_menu != self.learn_sounds_menu:
            return None
        if self.learn_sounds_menu.index >= len(LEARN_SOUND_LIBRARY):
            return None
        return LEARN_SOUND_LIBRARY[self.learn_sounds_menu.index]

    def _refresh_learn_sound_description(self) -> None:
        entry = self._current_learn_sound_entry()
        if entry is None:
            self._learn_sound_description = "Return to the main menu."
            return
        self._learn_sound_description = entry.description

    def _stop_learn_sound_preview(self) -> None:
        self._learn_sound_preview_timer = 0.0
        self.audio.stop(LEARN_SOUND_PREVIEW_CHANNEL)

    def _start_headstart_audio(self) -> None:
        if self.player.headstart <= 0:
            return
        self.audio.play("intro_shake", loop=True, channel=HEADSTART_SHAKE_CHANNEL, gain=0.84)
        self.audio.play("intro_spray", loop=True, channel=HEADSTART_SPRAY_CHANNEL, gain=0.92)

    def _stop_headstart_audio(self) -> None:
        self.audio.stop(HEADSTART_SHAKE_CHANNEL)
        self.audio.stop(HEADSTART_SPRAY_CHANNEL)

    def _play_learn_sound_preview(self, entry: LearnSoundEntry) -> None:
        self._stop_learn_sound_preview()
        self._learn_sound_description = entry.description
        self.audio.play(
            entry.key,
            loop=entry.loop,
            channel=LEARN_SOUND_PREVIEW_CHANNEL,
            gain=entry.gain,
        )
        if entry.loop:
            self._learn_sound_preview_timer = LEARN_SOUND_LOOP_PREVIEW_DURATION
        self.speaker.speak(f"{entry.label}. {entry.description}", interrupt=True)

    def _update_learn_sound_preview(self, delta_time: float) -> None:
        if self._learn_sound_preview_timer <= 0:
            return
        self._learn_sound_preview_timer = max(0.0, self._learn_sound_preview_timer - delta_time)
        if self._learn_sound_preview_timer <= 0:
            self.audio.stop(LEARN_SOUND_PREVIEW_CHANNEL)

    def _play_menu_feedback(self, key: str) -> None:
        if self.active_menu is not None:
            self.active_menu.play_feedback(key)
            return
        self.audio.play(key, channel="ui")

    def _update_option_index(self, action: str) -> int:
        for index, item in enumerate(self.options_menu.items):
            if item.action == action:
                return index
        return 0

    def _refresh_update_menu(self, result: UpdateCheckResult) -> None:
        latest_version = result.latest_version or "Unknown"
        if self.packaged_build:
            self.update_menu.title = f"Update Required   {APP_VERSION} -> {latest_version}"
            self._update_status_message = (
                f"A newer version is available. Current version {APP_VERSION}. Latest version {latest_version}."
            )
        else:
            self.update_menu.title = f"Update Available   {APP_VERSION} -> {latest_version}"
            self._update_status_message = (
                f"A newer release is available. This source checkout reports version {APP_VERSION}. "
                f"Latest release {latest_version}."
            )
        self._update_release_notes = (
            result.release.notes.strip() if result.release is not None and result.release.notes.strip() else "No release notes were provided."
        )
        self._update_progress_percent = 0.0
        self._update_progress_message = ""
        self._update_progress_stage = "idle"
        self._update_progress_announced_bucket = -1
        self._update_install_thread = None
        self._update_install_result = None
        self._update_restart_script_path = None
        self._update_install_error = ""
        self._update_ready_announced = False
        has_zip_package = self.packaged_build and bool(result.release and self.updater.has_installable_package(result.release))
        self.update_menu.items[0].label = "Download and Install Update" if has_zip_package else "Open Release Page"
        self.update_menu.items[0].action = "download_update" if has_zip_package else "open_release_page"
        self.update_menu.items[1].label = "Open Release Page"
        self.update_menu.items[1].action = "open_release_page"
        self.update_menu.items[2].label = "Back" if not self.packaged_build else "Quit Game"
        self.update_menu.items[2].action = "back" if not self.packaged_build else "quit"

    def _menu_navigation_hint(self) -> str:
        up = keyboard_key_label(self.controls.keyboard_binding_for_action("menu_up"))
        down = keyboard_key_label(self.controls.keyboard_binding_for_action("menu_down"))
        confirm = keyboard_key_label(self.controls.keyboard_binding_for_action("menu_confirm"))
        back = keyboard_key_label(self.controls.keyboard_binding_for_action("menu_back"))
        if self.controls.last_input_source == "controller" and self.controls.active_controller() is not None:
            family = self.controls.current_controller_family()
            up = controller_binding_label(self.controls.controller_binding_for_action("menu_up", family), family)
            down = controller_binding_label(self.controls.controller_binding_for_action("menu_down", family), family)
            confirm = controller_binding_label(self.controls.controller_binding_for_action("menu_confirm", family), family)
            back = controller_binding_label(self.controls.controller_binding_for_action("menu_back", family), family)
        return f"Use {up}/{down}, {confirm} to select, {back} to go back."

    def _option_adjustment_hint(self) -> str:
        decrease = keyboard_key_label(self.controls.keyboard_binding_for_action("option_decrease"))
        increase = keyboard_key_label(self.controls.keyboard_binding_for_action("option_increase"))
        if self.controls.last_input_source == "controller" and self.controls.active_controller() is not None:
            family = self.controls.current_controller_family()
            decrease = controller_binding_label(self.controls.controller_binding_for_action("option_decrease", family), family)
            increase = controller_binding_label(self.controls.controller_binding_for_action("option_increase", family), family)
        return f"Adjust values with {decrease}/{increase}."

    def _gameplay_controls_summary(self) -> str:
        move_left = keyboard_key_label(self.controls.keyboard_binding_for_action("game_move_left"))
        move_right = keyboard_key_label(self.controls.keyboard_binding_for_action("game_move_right"))
        jump = keyboard_key_label(self.controls.keyboard_binding_for_action("game_jump"))
        roll = keyboard_key_label(self.controls.keyboard_binding_for_action("game_roll"))
        hoverboard = keyboard_key_label(self.controls.keyboard_binding_for_action("game_hoverboard"))
        pause = keyboard_key_label(self.controls.keyboard_binding_for_action("game_pause"))
        speech = keyboard_key_label(self.controls.keyboard_binding_for_action("game_toggle_speech"))
        if self.controls.last_input_source == "controller" and self.controls.active_controller() is not None:
            family = self.controls.current_controller_family()
            move_left = controller_binding_label(self.controls.controller_binding_for_action("game_move_left", family), family)
            move_right = controller_binding_label(self.controls.controller_binding_for_action("game_move_right", family), family)
            jump = controller_binding_label(self.controls.controller_binding_for_action("game_jump", family), family)
            roll = controller_binding_label(self.controls.controller_binding_for_action("game_roll", family), family)
            hoverboard = controller_binding_label(self.controls.controller_binding_for_action("game_hoverboard", family), family)
            pause = controller_binding_label(self.controls.controller_binding_for_action("game_pause", family), family)
            speech = controller_binding_label(self.controls.controller_binding_for_action("game_toggle_speech", family), family)
        return (
            f"Use {move_left} and {move_right} to change lanes. "
            f"Press {jump} to jump, {roll} to roll, {hoverboard} to activate a hoverboard, "
            f"{pause} to pause, and {speech} to toggle speech."
        )

    def _open_mandatory_update_menu(self, result: UpdateCheckResult) -> None:
        self._latest_update_result = result
        self._refresh_update_menu(result)
        self._set_active_menu(self.update_menu)
        self.speaker.speak(self._update_status_message, interrupt=True)

    def _begin_update_install(self) -> None:
        if not self.packaged_build:
            release = self._latest_update_result.release if self._latest_update_result is not None else None
            opened = self.updater.open_release_page(release)
            if opened:
                self.speaker.speak("Source builds cannot install updates automatically. Opening the release page.", interrupt=True)
            else:
                self._play_menu_feedback("menuedge")
                self.speaker.speak("Source builds cannot install updates automatically.", interrupt=True)
            return
        release = self._latest_update_result.release if self._latest_update_result is not None else None
        if release is None:
            self._play_menu_feedback("menuedge")
            self.speaker.speak("No release information is available.", interrupt=True)
            return
        if self._update_install_thread is not None and self._update_install_thread.is_alive():
            return

        self._update_progress_stage = "download"
        self._update_progress_percent = 0.0
        self._update_progress_message = "Starting update download."
        self._update_progress_announced_bucket = -1
        self._update_install_result = None
        self._update_restart_script_path = None
        self._update_install_error = ""
        self._update_ready_announced = False
        self.update_menu.items[0].label = "Installing Update..."
        self.update_menu.items[0].action = "install_busy"

        def progress_callback(progress: UpdateInstallProgress) -> None:
            self._update_progress_stage = progress.stage
            self._update_progress_percent = max(0.0, min(100.0, float(progress.percent)))
            self._update_progress_message = progress.message

        def worker() -> None:
            result = self.updater.download_and_install(release, progress_callback=progress_callback)
            self._update_install_result = result
            self._update_restart_script_path = result.restart_script_path
            if not result.success:
                self._update_install_error = result.message

        self._update_install_thread = threading.Thread(target=worker, name="update-install", daemon=True)
        self._update_install_thread.start()

    def _update_update_install_state(self) -> None:
        if self.active_menu != self.update_menu:
            return
        if self._update_progress_stage == "download":
            bucket = int(self._update_progress_percent // 10)
            if bucket > self._update_progress_announced_bucket and bucket < 10:
                self._update_progress_announced_bucket = bucket
                if bucket > 0:
                    self.speaker.speak(f"Download {bucket * 10} percent.", interrupt=False)
        if self._update_install_thread is None or self._update_install_thread.is_alive():
            return
        self._update_install_thread = None
        result = self._update_install_result
        if result is None:
            return
        self._update_status_message = result.message
        if not result.success:
            self.update_menu.items[0].label = "Download and Install Update"
            self.update_menu.items[0].action = "download_update"
            self._update_progress_stage = "error"
            self._play_menu_feedback("menuedge")
            self.speaker.speak(result.message, interrupt=True)
            self._update_install_result = None
            return
        self.update_menu.items[0].label = "Restart Game"
        self.update_menu.items[0].action = "restart_after_update"
        self.update_menu.items[1].label = "Open Release Page"
        self.update_menu.items[1].action = "open_release_page"
        self.update_menu.items[2].label = "Quit Game"
        self.update_menu.items[2].action = "quit"
        self._update_progress_stage = "ready"
        if not self._update_ready_announced:
            self._update_ready_announced = True
            self.speaker.speak(result.message, interrupt=True)

    def _check_for_updates(self, announce_result: bool, automatic: bool = False) -> None:
        result = self.updater.check_for_updates(APP_VERSION)
        self._latest_update_result = result
        if result.update_available:
            self._refresh_update_menu(result)
            if self.packaged_build or not automatic:
                self._set_active_menu(self.update_menu)
            if self.packaged_build:
                self.speaker.speak(self._update_status_message, interrupt=True)
                return
            if announce_result:
                self.speaker.speak(
                    f"{self._update_status_message} Open the release page to download the new build.",
                    interrupt=True,
                )
            return
        if result.release is not None:
            self._update_status_message = (
                f"Current version {APP_VERSION}. Latest release {result.release.version}. {result.message}"
            )
        else:
            self._update_status_message = result.message
        if announce_result:
            self.speaker.speak(self._update_status_message, interrupt=True)
            return
        if automatic and result.status == "error":
            return

    def _menu_uses_gameplay_music(self, menu: Menu | None) -> bool:
        return menu in {self.pause_menu, self.pause_confirm_menu, self.revive_menu}

    def _sync_music_context(self) -> None:
        if self._exit_requested:
            return
        if self.active_menu is None:
            if self.state.running:
                self.audio.music_start("gameplay")
            else:
                self.audio.music_stop()
            return
        if self.state.running and self._menu_uses_gameplay_music(self.active_menu):
            self.audio.music_start("gameplay")
            return
        self.audio.music_start("menu")

    def _difficulty_key(self) -> str:
        return str(self.settings.get("difficulty", "normal")).strip().lower()

    def _request_exit(self) -> None:
        if self._exit_requested:
            return
        self._exit_requested = True
        self.audio.music_stop()

    @staticmethod
    def _death_reason_for_variant(variant: str) -> str:
        return {
            "train": "Hit train",
            "low": "Hit low obstacle",
            "high": "Hit high obstacle",
            "bush": "Hit bush",
        }.get(variant, "Run ended after crash")

    def _open_game_over_dialog(self, death_reason: Optional[str] = None) -> None:
        summary_reason = death_reason or self._last_death_reason or "Run ended after crash"
        self._game_over_summary = {
            "score": int(self.state.score),
            "coins": int(self.state.coins),
            "death_reason": summary_reason,
        }
        self._refresh_game_over_menu()
        self.active_menu = self.game_over_menu
        self.game_over_menu.opened = True
        self.game_over_menu.index = 0
        self._pending_menu_announcement = (self.game_over_menu, 0.45)
        self._sync_music_context()
        self.speaker.speak("Game Over.", interrupt=True)

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
        active_multiplier = self.player.mult2x > 0 or any(
            obstacle.kind == "multiplier" and obstacle.z > 0 for obstacle in self.obstacles
        )
        active_super_box = any(obstacle.kind == "super_box" and obstacle.z > 0 for obstacle in self.obstacles)
        active_pogo = self.player.pogo_active > 0 or any(obstacle.kind == "pogo" and obstacle.z > 0 for obstacle in self.obstacles)
        if not active_multiplier:
            kinds.append("multiplier")
            weights.append(0.09)
        if not active_super_box:
            kinds.append("super_box")
            weights.append(0.06)
        if not active_pogo:
            kinds.append("pogo")
            weights.append(0.09)
        if self._remaining_word_letters() and not active_word:
            kinds.append("word")
            weights.append(0.08)
        if next_season_reward_threshold(self.settings) is not None and not active_token:
            kinds.append("season_token")
            weights.append(0.05)
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
        self.audio.play("mystery_box_open", channel="ui")
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
        if reward == "jetpack":
            self.audio.play("unlock", channel="ui3")
            self._apply_power_reward("jetpack", from_headstart=False)
            self.speaker.speak(f"{source}: Super Mystery Box. Jetpack.", interrupt=True)
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
        self.audio.play("gui_cash", channel="ui")
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
        self.speaker.speak(self._shop_coins_label(), interrupt=False)

    def _grant_shop_box_reward(self, reward: str) -> None:
        self.speaker.speak("Opening Mystery Box.", interrupt=True)
        self.audio.play("mystery_box_open", channel="player_box")
        if reward == "coins":
            gain = shop_box_reward_amount("coins")
            self.settings["bank_coins"] = int(self.settings.get("bank_coins", 0)) + gain
            self.audio.play("gui_cash", channel="ui3")
            self.speaker.speak(f"Mystery box: {gain} coins.", interrupt=False)
            return
        if reward == "hover":
            gain = shop_box_reward_amount("hover")
            self.settings["hoverboards"] = int(self.settings.get("hoverboards", 0)) + gain
            self.audio.play("unlock", channel="ui3")
            self.speaker.speak(f"Mystery box: {gain} hoverboard{'s' if gain != 1 else ''}.", interrupt=False)
            return
        if reward == "key":
            gain = shop_box_reward_amount("key")
            self.settings["keys"] = int(self.settings.get("keys", 0)) + gain
            self.audio.play("unlock", channel="ui3")
            self.speaker.speak(f"Mystery box: {gain} key{'s' if gain != 1 else ''}.", interrupt=False)
            return
        if reward == "headstart":
            gain = shop_box_reward_amount("headstart")
            self.settings["headstarts"] = int(self.settings.get("headstarts", 0)) + gain
            self.audio.play("mystery_combo", channel="ui3")
            self.speaker.speak(f"Mystery box: {gain} headstart{'s' if gain != 1 else ''}.", interrupt=False)
            return
        if reward == "score_booster":
            gain = shop_box_reward_amount("score_booster")
            self.settings["score_boosters"] = int(self.settings.get("score_boosters", 0)) + gain
            self.audio.play("mystery_combo", channel="ui3")
            self.speaker.speak(f"Mystery box: {gain} score booster{'s' if gain != 1 else ''}.", interrupt=False)
            return
        if reward == "jackpot":
            gain = shop_box_reward_amount("jackpot")
            self.settings["bank_coins"] = int(self.settings.get("bank_coins", 0)) + gain
            self.audio.play("gui_cash", channel="ui3")
            self.audio.play("unlock", channel="ui4")
            self.speaker.speak(f"Mystery box jackpot: {gain} coins.", interrupt=False)
            return
        self.speaker.speak("Mystery box: empty.", interrupt=False)

    def _commit_run_rewards(self) -> None:
        if self._run_rewards_committed or not self.state.running:
            return
        self._run_rewards_committed = True
        saved_coins = int(self.state.coins)
        self.settings["bank_coins"] = int(self.settings.get("bank_coins", 0)) + saved_coins
        if saved_coins > 0:
            self.audio.play("coin_gui", channel="ui")
            self.audio.play("gui_cash", channel="ui2")

    def _clear_menu_repeat(self) -> None:
        self._menu_repeat_key = None
        self._menu_repeat_delay_remaining = 0.0

    def _set_active_menu(self, menu: Optional[Menu], start_index: int = 0) -> None:
        self._clear_menu_repeat()
        self._stop_learn_sound_preview()
        self.active_menu = menu
        if menu is not None:
            menu.open(start_index=start_index)
            if menu == self.learn_sounds_menu:
                self._refresh_learn_sound_description()
        self._sync_music_context()

    def _menu_key_supports_repeat(self, key: int) -> bool:
        if self.active_menu is None:
            return False
        if key in (pygame.K_UP, pygame.K_DOWN, pygame.K_w, pygame.K_s):
            return True
        if self.active_menu == self.options_menu and key in (pygame.K_LEFT, pygame.K_RIGHT):
            return True
        if self.active_menu == self.controls_menu and key in (pygame.K_LEFT, pygame.K_RIGHT):
            selected_action = self.controls_menu.items[self.controls_menu.index].action if self.controls_menu.items else ""
            return selected_action == "select_binding_profile"
        return False

    def _prime_menu_repeat(self, key: int) -> None:
        if self._menu_key_supports_repeat(key):
            self._menu_repeat_key = key
            self._menu_repeat_delay_remaining = MENU_REPEAT_INITIAL_DELAY
            return
        if self._menu_repeat_key == key:
            self._clear_menu_repeat()

    def _release_menu_repeat(self, key: int) -> None:
        if self._menu_repeat_key == key:
            self._clear_menu_repeat()

    def _update_menu_repeat(self, delta_time: float) -> None:
        if self._menu_repeat_key is None or self.active_menu is None:
            return
        if not self._menu_key_supports_repeat(self._menu_repeat_key):
            self._clear_menu_repeat()
            return
        self._menu_repeat_delay_remaining -= delta_time
        while self._menu_repeat_delay_remaining <= 0:
            self._handle_active_menu_key(self._menu_repeat_key)
            if self._menu_repeat_key is None or self.active_menu is None:
                return
            self._menu_repeat_delay_remaining += MENU_REPEAT_INTERVAL

    def _input_context(self) -> str:
        return MENU_CONTEXT if self.active_menu is not None else GAME_CONTEXT

    def _process_translated_keydown(self, key: int) -> bool:
        if self._exit_requested:
            return True
        if self.active_menu is not None:
            if self._pending_menu_announcement is not None and self.active_menu == self.game_over_menu:
                return True
            keep_running = self._handle_active_menu_key(key)
            if keep_running:
                self._prime_menu_repeat(key)
                return True
            self._request_exit()
            return False
        self._handle_game_key(key)
        return True

    def _process_translated_keyup(self, key: int) -> None:
        self._release_menu_repeat(key)

    def _announce_controller_connected(self, name: str, family: str) -> None:
        self._selected_binding_device = "controller"
        self._refresh_control_menus()
        self.speaker.speak(
            f"{family_label(family)} connected. Open Controls in Options to review bindings.",
            interrupt=True,
        )

    def _announce_controller_disconnected(self, name: str, family: str) -> None:
        self._selected_binding_device = "keyboard"
        self._refresh_control_menus()
        self.speaker.speak(f"{family_label(family)} disconnected. Keyboard controls remain available.", interrupt=True)

    def _cancel_binding_capture(self, announce: bool = True) -> None:
        if self._binding_capture is None:
            return
        self._binding_capture = None
        if announce:
            self.speaker.speak("Control reassignment cancelled.", interrupt=True)

    def _begin_binding_capture(self, device: str, action_key: str) -> None:
        self._binding_capture = BindingCaptureRequest(device=device, action_key=action_key)
        prompt = action_label(action_key)
        if device == "keyboard":
            self.speaker.speak(f"Press a key for {prompt}. Press Escape to cancel.", interrupt=True)
            return
        controller_name = family_label(self.controls.current_controller_family())
        self.speaker.speak(
            f"Press a button or stick direction on the {controller_name} for {prompt}. Press Escape to cancel.",
            interrupt=True,
        )

    def _complete_keyboard_binding_capture(self, key: int) -> None:
        if self._binding_capture is None:
            return
        action_key = self._binding_capture.action_key
        self.controls.update_keyboard_binding(action_key, key)
        self._binding_capture = None
        self._build_keyboard_bindings_menu()
        binding_label = keyboard_key_label(self.controls.keyboard_binding_for_action(action_key))
        self.speaker.speak(f"{action_label(action_key)} set to {binding_label}.", interrupt=True)

    def _complete_controller_binding_capture(self, binding: str) -> None:
        if self._binding_capture is None:
            return
        action_key = self._binding_capture.action_key
        family = self.controls.current_controller_family()
        self.controls.update_controller_binding(family, action_key, binding)
        self._binding_capture = None
        self._build_controller_bindings_menu()
        binding_label = controller_binding_label(self.controls.controller_binding_for_action(action_key, family), family)
        self.speaker.speak(f"{action_label(action_key)} set to {binding_label}.", interrupt=True)

    def _handle_keyboard_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.KEYDOWN:
            if self._binding_capture is not None and self._binding_capture.device == "keyboard":
                if event.key == pygame.K_ESCAPE:
                    self._cancel_binding_capture()
                    return
                self._complete_keyboard_binding_capture(event.key)
                return
            translated_key = self.controls.translate_keyboard_key(event.key, self._input_context())
            if translated_key is None:
                return
            self._process_translated_keydown(translated_key)
            return
        if event.type == pygame.KEYUP:
            translated_key = self.controls.translate_keyboard_key(event.key, self._input_context())
            if translated_key is None:
                return
            self._process_translated_keyup(translated_key)

    def _handle_window_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.VIDEORESIZE:
            width = max(MIN_WINDOW_WIDTH, int(getattr(event, "w", MIN_WINDOW_WIDTH)))
            height = max(MIN_WINDOW_HEIGHT, int(getattr(event, "h", MIN_WINDOW_HEIGHT)))
            self.screen = pygame.display.set_mode((width, height), pygame.RESIZABLE)
            return
        if event.type == pygame.WINDOWSIZECHANGED:
            surface = pygame.display.get_surface()
            if surface is not None:
                self.screen = surface

    def _handle_controller_event(self, event: pygame.event.Event) -> None:
        if event.type == pygame.CONTROLLERDEVICEADDED:
            connected = self.controls.register_added_controller(getattr(event, "device_index", None))
            if connected is not None:
                self._announce_controller_connected(connected.name, connected.family)
            return
        if event.type == pygame.CONTROLLERDEVICEREMOVED:
            disconnected = self.controls.handle_device_removed(getattr(event, "instance_id", None))
            if disconnected is not None:
                self._announce_controller_disconnected(disconnected.name, disconnected.family)
            return
        if event.type == pygame.CONTROLLERDEVICEREMAPPED:
            self.controls.refresh_connected_controllers()
            self._refresh_control_menus()
            return
        if self._binding_capture is not None and self._binding_capture.device == "controller":
            binding = self.controls.capture_controller_binding(event)
            if binding is not None:
                self._complete_controller_binding_capture(binding)
            return
        for translated_key, pressed in self.controls.translate_controller_event(event, self._input_context()):
            if pressed:
                self._process_translated_keydown(translated_key)
            else:
                self._process_translated_keyup(translated_key)

    def _add_run_coins(self, amount: int) -> None:
        if amount <= 0:
            return
        self.state.coins += amount
        # Fatal collisions can commit rewards mid-frame; bank late coin pickups immediately.
        if self._run_rewards_committed:
            self.settings["bank_coins"] = int(self.settings.get("bank_coins", 0)) + amount

    def run(self) -> None:
        running = True
        while running:
            delta_time = self.clock.tick(60) / 1000.0
            if self._pending_menu_announcement is not None:
                menu, remaining = self._pending_menu_announcement
                remaining = max(0.0, remaining - delta_time)
                if remaining <= 0:
                    self._pending_menu_announcement = None
                    if self.active_menu is menu:
                        menu._announce_current()
                else:
                    self._pending_menu_announcement = (menu, remaining)
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self._request_exit()
                elif event.type in (pygame.VIDEORESIZE, pygame.WINDOWSIZECHANGED):
                    self._handle_window_event(event)
                elif event.type in (pygame.KEYDOWN, pygame.KEYUP):
                    self._handle_keyboard_event(event)
                elif event.type in (
                    pygame.CONTROLLERDEVICEADDED,
                    pygame.CONTROLLERDEVICEREMOVED,
                    pygame.CONTROLLERDEVICEREMAPPED,
                    pygame.CONTROLLERBUTTONDOWN,
                    pygame.CONTROLLERBUTTONUP,
                    pygame.CONTROLLERAXISMOTION,
                ):
                    self._handle_controller_event(event)

            if not self._exit_requested and self.active_menu is not None:
                self._update_menu_repeat(delta_time)
                self._update_learn_sound_preview(delta_time)
                self._update_update_install_state()

            if not self._exit_requested and self.active_menu is None:
                if not self.state.paused:
                    self._update_game(delta_time)
            self.audio.update(delta_time)

            if self.active_menu is None:
                self._draw_game()
            else:
                self._draw_menu(self.active_menu)

            pygame.display.flip()
            if self._exit_requested and self.audio.music_is_idle():
                running = False

        save_settings(self.settings)

    def _handle_active_menu_key(self, key: int) -> bool:
        if self.active_menu is None:
            return True
        if self._binding_capture is not None:
            if key == pygame.K_ESCAPE:
                self._cancel_binding_capture()
            else:
                self._play_menu_feedback("menuedge")
            return True
        if self.active_menu == self.options_menu:
            if key in (pygame.K_LEFT, pygame.K_RIGHT):
                self._adjust_selected_option(-1 if key == pygame.K_LEFT else 1)
                return True
            if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                selected_action = self.options_menu.items[self.options_menu.index].action
                if selected_action in {"back", "opt_controls"}:
                    return self._handle_menu_action(selected_action)
                return True
        if self.active_menu == self.controls_menu:
            if key in (pygame.K_LEFT, pygame.K_RIGHT):
                selected_action = self.controls_menu.items[self.controls_menu.index].action
                if selected_action == "select_binding_profile":
                    self._cycle_selected_binding_device(-1 if key == pygame.K_LEFT else 1)
                else:
                    self._play_menu_feedback("menuedge")
                return True
        if self.active_menu == self.learn_sounds_menu:
            if key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                selected_action = self.learn_sounds_menu.items[self.learn_sounds_menu.index].action
                if selected_action == "back":
                    return self._handle_menu_action("back")
                entry = self._learn_sound_entries_by_action.get(selected_action)
                if entry is not None:
                    self._play_learn_sound_preview(entry)
                return True
            previous_index = self.learn_sounds_menu.index
            action = self.learn_sounds_menu.handle_key(key)
            if self.learn_sounds_menu.index != previous_index:
                self._refresh_learn_sound_description()
            if action:
                return self._handle_menu_action(action)
            return True
        action = self.active_menu.handle_key(key)
        if action:
            return self._handle_menu_action(action)
        return True

    def _handle_menu_action(self, action: str) -> bool:
        if action == "close":
            if self.active_menu == self.revive_menu:
                self._finish_run_loss("Run ended after crash")
                return True
            if self.active_menu == self.game_over_menu:
                self.active_menu.index = 0
                self.speaker.speak(self.active_menu.items[0].label, interrupt=True)
                return True
            if self.active_menu == self.update_menu:
                return False
            if self.active_menu == self.main_menu:
                return False
            if self.active_menu == self.controls_menu:
                self._refresh_options_menu_labels()
                self._set_active_menu(self.options_menu, start_index=self._update_option_index("opt_controls"))
                return True
            if self.active_menu in {self.keyboard_bindings_menu, self.controller_bindings_menu}:
                self._build_controls_menu()
                self._set_active_menu(self.controls_menu)
                return True
            if self.active_menu == self.pause_menu:
                self.state.paused = False
                self._set_active_menu(None)
                self.audio.play("menuclose", channel="ui")
                self.speaker.speak("Resume", interrupt=True)
                return True
            if self.active_menu == self.pause_confirm_menu:
                self._set_active_menu(self.pause_menu, start_index=1)
                return True
            self._set_active_menu(self.main_menu)
            return True

        if self.active_menu == self.main_menu:
            if action == "start":
                self.selected_headstarts = 0
                self.selected_score_boosters = 0
                self._refresh_loadout_menu_labels()
                self._set_active_menu(self.loadout_menu)
                return True
            if action == "shop":
                self._refresh_shop_menu_labels()
                self._set_active_menu(self.shop_menu)
                self.speaker.speak(self._shop_coins_label(), interrupt=False)
                return True
            if action == "options":
                self._refresh_options_menu_labels()
                self._set_active_menu(self.options_menu)
                return True
            if action == "howto":
                self._say_how_to_play()
                return True
            if action == "learn_sounds":
                self._set_active_menu(self.learn_sounds_menu)
                return True
            if action == "check_updates":
                self._check_for_updates(announce_result=True)
                return True
            if action == "quit":
                return False

        if self.active_menu == self.loadout_menu:
            if action == "back":
                self._set_active_menu(self.main_menu)
                return True
            if action == "toggle_headstart":
                owned = int(self.settings.get("headstarts", 0))
                if owned <= 0:
                    self.audio.play("menuedge", channel="ui")
                    self.speaker.speak("No headstarts available.", interrupt=True)
                    return True
                self.selected_headstarts = (self.selected_headstarts + 1) % (clamp_headstart_uses(owned) + 1)
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
            if action == "opt_controls":
                self._selected_binding_device = "controller" if self.controls.active_controller() is not None else "keyboard"
                self._refresh_control_menus()
                self._set_active_menu(self.controls_menu)
                return True
            if action == "back":
                self.audio.play("menuclose", channel="ui")
                self._set_active_menu(self.main_menu)
                return True
            return True

        if self.active_menu == self.controls_menu:
            if action == "announce_active_input":
                self.speaker.speak(
                    f"Current input is {self.controls.current_input_label()}. {self.controls.current_controller_label()}.",
                    interrupt=True,
                )
                return True
            if action == "select_binding_profile":
                self.speaker.speak(self.controls_menu.items[self.controls_menu.index].label, interrupt=True)
                return True
            if action == "open_selected_bindings":
                if self._selected_binding_device == "controller":
                    if self.controls.active_controller() is None:
                        self._play_menu_feedback("menuedge")
                        self.speaker.speak("No controller connected.", interrupt=True)
                        return True
                    self._build_controller_bindings_menu()
                    self._set_active_menu(self.controller_bindings_menu)
                    return True
                self._build_keyboard_bindings_menu()
                self._set_active_menu(self.keyboard_bindings_menu)
                return True
            if action == "reset_selected_bindings":
                if self._selected_binding_device == "controller":
                    if self.controls.active_controller() is None:
                        self._play_menu_feedback("menuedge")
                        self.speaker.speak("No controller connected.", interrupt=True)
                        return True
                    family = self.controls.current_controller_family()
                    self.controls.reset_controller_bindings(family)
                    self._build_controls_menu()
                    self._play_menu_feedback("confirm")
                    self.speaker.speak(f"{family_label(family)} bindings reset to recommended defaults.", interrupt=True)
                    return True
                self.controls.reset_keyboard_bindings()
                self._build_controls_menu()
                self._play_menu_feedback("confirm")
                self.speaker.speak("Keyboard bindings reset to defaults.", interrupt=True)
                return True
            if action == "back":
                self._refresh_options_menu_labels()
                self._set_active_menu(self.options_menu, start_index=self._update_option_index("opt_controls"))
                return True

        if self.active_menu == self.keyboard_bindings_menu:
            if action == "reset_keyboard_bindings":
                self.controls.reset_keyboard_bindings()
                self._build_keyboard_bindings_menu()
                self._play_menu_feedback("confirm")
                self.speaker.speak("Keyboard bindings reset to defaults.", interrupt=True)
                return True
            if action.startswith("bind_keyboard:"):
                self._begin_binding_capture("keyboard", action.split(":", 1)[1])
                return True
            if action == "back":
                self._build_controls_menu()
                self._set_active_menu(self.controls_menu, start_index=2)
                return True

        if self.active_menu == self.controller_bindings_menu:
            if action == "reset_controller_bindings":
                family = self.controls.current_controller_family()
                self.controls.reset_controller_bindings(family)
                self._build_controller_bindings_menu()
                self._play_menu_feedback("confirm")
                self.speaker.speak(f"{family_label(family)} bindings reset to recommended defaults.", interrupt=True)
                return True
            if action.startswith("bind_controller:"):
                if self.controls.active_controller() is None:
                    self._play_menu_feedback("menuedge")
                    self.speaker.speak("No controller connected.", interrupt=True)
                    return True
                self._begin_binding_capture("controller", action.split(":", 1)[1])
                return True
            if action == "back":
                self._build_controls_menu()
                self._set_active_menu(self.controls_menu, start_index=2)
                return True

        if self.active_menu == self.update_menu:
            if action == "back":
                self._set_active_menu(self.main_menu)
                return True
            if action == "download_update":
                self._begin_update_install()
                return True
            if action == "install_busy":
                return True
            if action == "restart_after_update":
                if self._update_restart_script_path and self.updater.launch_restart_script(self._update_restart_script_path):
                    self.speaker.speak("Restarting to apply the update.", interrupt=True)
                    return False
                self.speaker.speak("Update files are ready. Restart the game to finish applying them.", interrupt=True)
                return False
            if action == "open_release_page":
                release = self._latest_update_result.release if self._latest_update_result is not None else None
                opened = self.updater.open_release_page(release)
                if opened:
                    self.speaker.speak("Opening the release page.", interrupt=True)
                    return True
                self._play_menu_feedback("menuedge")
                self.speaker.speak("Unable to open the release page.", interrupt=True)
                return True
            if action == "quit":
                return False

        if self.active_menu == self.shop_menu:
            if action == "back":
                self._set_active_menu(self.main_menu)
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

        if self.active_menu == self.learn_sounds_menu:
            if action == "back":
                self._set_active_menu(self.main_menu)
                return True

        if self.active_menu == self.pause_menu:
            if action == "resume":
                self.state.paused = False
                self._set_active_menu(None)
                self.speaker.speak("Resume", interrupt=True)
                return True
            if action == "to_main":
                self._set_active_menu(self.pause_confirm_menu)
                return True

        if self.active_menu == self.pause_confirm_menu:
            if action == "confirm_to_main":
                self.end_run(to_menu=True)
                return True
            if action == "cancel_to_main":
                self._set_active_menu(self.pause_menu, start_index=1)
                return True

        if self.active_menu == self.revive_menu:
            if action == "revive":
                self._revive_run()
                return True
            if action in ("end_run", "close"):
                self._finish_run_loss("Run ended after crash")
                return True

        if self.active_menu == self.game_over_menu:
            if action == "game_over_retry":
                self.start_run()
                return True
            if action == "game_over_main_menu":
                self.active_menu = self.main_menu
                self.active_menu.open()
                return True
            if action.startswith("game_over_info_"):
                current_item = self.active_menu.items[self.active_menu.index]
                self.speaker.speak(current_item.label, interrupt=True)
                return True

        return True

    def _cycle_output_device_in_options(self, direction: int) -> None:
        devices = self.audio.output_device_choices()
        current_device = self.audio.current_output_device_name()
        try:
            current_index = devices.index(current_device)
        except ValueError:
            current_index = 0
        requested_device = devices[(current_index + direction) % len(devices)]
        applied_device = self.audio.apply_output_device(requested_device)
        self._refresh_options_menu_labels()
        selected_label = applied_device or SYSTEM_DEFAULT_OUTPUT_LABEL
        if requested_device == applied_device:
            self.speaker.speak(
                f"Output device set to {selected_label}.",
                interrupt=True,
            )
            return
        self.speaker.speak(
            f"Requested output device unavailable. Using {selected_label}.",
            interrupt=True,
        )

    def _apply_speaker_settings(self) -> None:
        self.speaker.apply_settings(self.settings)

    def _adjust_selected_option(self, direction: int) -> None:
        if self.active_menu != self.options_menu or direction not in (-1, 1):
            return
        selected_action = self.options_menu.items[self.options_menu.index].action
        if selected_action == "back":
            return
        if selected_action == "opt_sfx":
            current = float(self.settings["sfx_volume"])
            updated = step_volume(current, direction)
            if updated == current:
                self._play_menu_feedback("menuedge")
                return
            self.settings["sfx_volume"] = updated
            self.audio.refresh_volumes()
            self._play_menu_feedback("confirm")
            self._refresh_options_menu_labels()
            self.speaker.speak(self.options_menu.items[self._update_option_index("opt_sfx")].label, interrupt=True)
            return
        if selected_action == "opt_music":
            current = float(self.settings["music_volume"])
            updated = step_volume(current, direction)
            if updated == current:
                self._play_menu_feedback("menuedge")
                return
            self.settings["music_volume"] = updated
            self.audio.refresh_volumes()
            self._play_menu_feedback("confirm")
            self._refresh_options_menu_labels()
            self.speaker.speak(self.options_menu.items[self._update_option_index("opt_music")].label, interrupt=True)
            return
        if selected_action == "opt_updates":
            self.settings["check_updates_on_startup"] = direction > 0
            self._play_menu_feedback("confirm")
            self._refresh_options_menu_labels()
            self.speaker.speak(self.options_menu.items[self._update_option_index("opt_updates")].label, interrupt=True)
            return
        if selected_action == "opt_output":
            self._play_menu_feedback("confirm")
            self._cycle_output_device_in_options(direction)
            return
        if selected_action == "opt_menu_hrtf":
            self.settings["menu_sound_hrtf"] = direction > 0
            self._play_menu_feedback("confirm")
            self._refresh_options_menu_labels()
            self.speaker.speak(self.options_menu.items[self._update_option_index("opt_menu_hrtf")].label, interrupt=True)
            return
        if selected_action == "opt_speech":
            self._play_menu_feedback("confirm")
            self.settings["speech_enabled"] = direction > 0
            self._refresh_options_menu_labels()
            label = self.options_menu.items[self._update_option_index("opt_speech")].label
            if self.settings["speech_enabled"]:
                self._apply_speaker_settings()
                self.speaker.speak(label, interrupt=True)
            else:
                self.speaker.speak(label, interrupt=True)
                self._apply_speaker_settings()
            return
        if selected_action == "opt_sapi":
            self.settings["sapi_speech_enabled"] = direction > 0
            self._apply_speaker_settings()
            self._play_menu_feedback("confirm")
            self._refresh_options_menu_labels()
            self.speaker.speak(self.options_menu.items[self._update_option_index("opt_sapi")].label, interrupt=True)
            return
        if selected_action == "opt_sapi_voice":
            selected_voice = self.speaker.cycle_sapi_voice(direction)
            if selected_voice == SAPI_VOICE_UNAVAILABLE_LABEL:
                self._play_menu_feedback("menuedge")
                self.speaker.speak("No SAPI voices available.", interrupt=True)
                return
            self.settings["sapi_voice_id"] = self.speaker.sapi_voice_id or ""
            self._apply_speaker_settings()
            self._play_menu_feedback("confirm")
            self._refresh_options_menu_labels()
            self.speaker.speak(self.options_menu.items[self._update_option_index("opt_sapi_voice")].label, interrupt=True)
            return
        if selected_action == "opt_sapi_rate":
            current = int(self.settings.get("sapi_rate", 0))
            updated = step_int(current, direction, SAPI_RATE_MIN, SAPI_RATE_MAX)
            if updated == current:
                self._play_menu_feedback("menuedge")
                return
            self.settings["sapi_rate"] = updated
            self._apply_speaker_settings()
            self._play_menu_feedback("confirm")
            self._refresh_options_menu_labels()
            self.speaker.speak(self.options_menu.items[self._update_option_index("opt_sapi_rate")].label, interrupt=True)
            return
        if selected_action == "opt_sapi_pitch":
            current = int(self.settings.get("sapi_pitch", 0))
            updated = step_int(current, direction, SAPI_PITCH_MIN, SAPI_PITCH_MAX)
            if updated == current:
                self._play_menu_feedback("menuedge")
                return
            self.settings["sapi_pitch"] = updated
            self._apply_speaker_settings()
            self._play_menu_feedback("confirm")
            self._refresh_options_menu_labels()
            self.speaker.speak(self.options_menu.items[self._update_option_index("opt_sapi_pitch")].label, interrupt=True)
            return
        if selected_action == "opt_diff":
            order = ["easy", "normal", "hard"]
            current = str(self.settings["difficulty"])
            try:
                current_index = order.index(current)
            except ValueError:
                current_index = order.index("normal")
            self.settings["difficulty"] = order[(current_index + direction) % len(order)]
            self._play_menu_feedback("confirm")
            self._refresh_options_menu_labels()
            self.speaker.speak(self.options_menu.items[self._update_option_index("opt_diff")].label, interrupt=True)

    def _say_how_to_play(self) -> None:
        self.speaker.speak(
            f"Controls: {self._gameplay_controls_summary()} "
            "Danger speech now only calls the action for your current lane. "
            "Bushes must be jumped. Before each run you can stack up to three Headstarts and three Score Boosters. "
            "Press R anytime during a run to hear your current collected coins. "
            "This is useful when many announcements are playing and you want a quick coin check. "
            "Keys can revive you after a crash. Missions raise your permanent multiplier. "
            "Word Hunt letters and Season Hunt tokens appear during runs. "
            "The shop lets you spend saved coins on items and mystery boxes.",
            interrupt=True,
        )

    def start_run(self) -> None:
        ensure_progression_state(self.settings)
        self.state = RunState(running=True)
        self._set_active_menu(None)
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
        self._guard_loop_timer = 0.0
        self._last_death_reason = "Run ended."
        self._game_over_summary = {"score": 0, "coins": 0, "death_reason": "Run ended."}
        self._magnet_loop_active = False
        self._jetpack_loop_active = False

        if self.selected_headstarts > 0:
            self.settings["headstarts"] = max(0, int(self.settings.get("headstarts", 0)) - self.selected_headstarts)
            self.player.headstart = headstart_duration_for_uses(self.selected_headstarts)
            self.player.y = 2.8
            self.player.vy = 0.0
            self._start_headstart_audio()
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
        self.audio.music_start("gameplay")
        if self.selected_headstarts > 0:
            self.speaker.speak(
                f"Run started. Headstart active for {self.selected_headstarts} charge{'s' if self.selected_headstarts != 1 else ''}.",
                interrupt=True,
            )
        else:
            self.speaker.speak("Run started. Center lane.", interrupt=True)

        self.selected_headstarts = 0
        self.selected_score_boosters = 0
        self._refresh_loadout_menu_labels()

    def end_run(self, to_menu: bool = True) -> None:
        self._commit_run_rewards()
        self.state.running = False
        self._stop_headstart_audio()
        self.audio.stop("loop_guard")
        self.audio.stop("loop_magnet")
        self.audio.stop("loop_jetpack")
        self._stop_spatial_audio()
        self.spatial_audio.reset()
        self._set_active_menu(self.main_menu if to_menu else None)

    def _handle_game_key(self, key: int) -> None:
        if key == pygame.K_ESCAPE:
            self.state.paused = True
            self._set_active_menu(self.pause_menu)
            self.audio.play("menuclose", channel="ui")
            return
        if key == pygame.K_r:
            self.speaker.speak(f"Coins collected: {self.state.coins}.", interrupt=False)
            return

        if self.state.paused or self.player.jetpack > 0 or self.player.headstart > 0:
            return

        self.player.lane = normalize_lane(self.player.lane)
        if key == pygame.K_LEFT:
            if self.player.lane > LANES[0]:
                self.player.lane = normalize_lane(self.player.lane - 1)
                self._record_mission_event("dodges")
                self.audio.play("dodge", pan=lane_to_pan(self.player.lane), channel="move")
                if self.settings.get("announce_lane", True):
                    self.speaker.speak(lane_name(self.player.lane), interrupt=False)
            else:
                self.audio.play("menuedge", channel="ui")
        elif key == pygame.K_RIGHT:
            if self.player.lane < LANES[-1]:
                self.player.lane = normalize_lane(self.player.lane + 1)
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
            if self.settings["speech_enabled"]:
                self._apply_speaker_settings()
                self.speaker.speak("Speech enabled", interrupt=True)
            else:
                self.speaker.speak("Speech disabled", interrupt=True)
                self._apply_speaker_settings()

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
        self.player.hover_active = HOVERBOARD_DURATION
        self.audio.play("powerup", channel="act")
        self.speaker.speak("Hoverboard active.", interrupt=False)

    def _update_game(self, delta_time: float) -> None:
        self.player.lane = normalize_lane(self.player.lane)
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
                sound_key = "land_h" if self.player.super_sneakers > 0 or self.player.pogo_active > 0 else "landing"
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
            self._stop_headstart_audio()
            self.player.y = 0.0
            self.player.vy = 0.0
            self.audio.play("land_h", channel="headstart_end")
            self.audio.play("powerup", channel="headstart_reward")
            self._apply_power_reward(pick_headstart_end_reward(), from_headstart=True)
        elif previous_headstart <= 0 and self.player.headstart > 0:
            self._start_headstart_audio()

        if self.player.headstart <= 0 and self.player.jetpack <= 0:
            decay("hover_active")
        if self.player.jetpack <= 0 and self.player.headstart <= 0:
            decay("super_sneakers")

        previous_magnet = self.player.magnet
        if self.player.jetpack <= 0 and self.player.headstart <= 0:
            decay("magnet")
        if previous_magnet > 0 and self.player.magnet <= 0:
            self.audio.stop("loop_magnet")
            self._magnet_loop_active = False
            self.audio.play("powerdown", channel="act")
            self.speaker.speak("Magnet expired.", interrupt=False)
        elif self.player.magnet > 0 and not self._magnet_loop_active:
            self.audio.play("magnet_loop", loop=True, channel="loop_magnet")
            self._magnet_loop_active = True

        previous_jetpack = self.player.jetpack
        decay("jetpack")
        if previous_jetpack > 0 and self.player.jetpack <= 0:
            self.audio.stop("loop_jetpack")
            self._jetpack_loop_active = False
            self.audio.play("powerdown", channel="act")
            self.speaker.speak("Jetpack expired.", interrupt=False)
        elif self.player.jetpack > 0 and not self._jetpack_loop_active:
            self.audio.play("jetpack_loop", loop=True, channel="loop_jetpack")
            self._jetpack_loop_active = True

        previous_multiplier = self.player.mult2x
        if self.player.jetpack <= 0 and self.player.headstart <= 0:
            decay("mult2x")
        if previous_multiplier > 0 and self.player.mult2x <= 0:
            self.audio.play("powerdown", channel="act")
            self.speaker.speak("Score boost expired.", interrupt=False)

        previous_pogo = self.player.pogo_active
        decay("pogo_active")
        if previous_pogo > 0 and self.player.pogo_active <= 0:
            self.audio.play("powerdown", channel="act")
            self.speaker.speak("Pogo stick expired.", interrupt=False)
        elif self.player.pogo_active > 0:
            self._launch_pogo_bounce()

        self._guard_loop_timer = max(0.0, self._guard_loop_timer - delta_time)
        if self.state.running and not self.state.paused and self._guard_loop_timer > 0:
            self.audio.play("guard_loop", loop=True, channel="loop_guard", gain=0.72)
        else:
            self.audio.stop("loop_guard")

    def _spawn_things(self, delta_time: float) -> None:
        self.state.next_spawn -= delta_time
        self.state.next_coinline -= delta_time
        self.state.next_support -= delta_time
        progress = self.speed_profile.progress(self.state.time)
        difficulty = self._difficulty_key()

        if self.state.next_spawn <= 0:
            if self.spawn_director.should_delay_spawn(self.obstacles):
                self.state.next_spawn = 0.3
            else:
                pattern = self._choose_playable_pattern(progress, difficulty)
                if pattern is None:
                    self.state.next_spawn = 0.35
                else:
                    chosen_pattern, distance = pattern
                    self._spawn_pattern(chosen_pattern, distance)
                    minimum_gap = 1.05 if difficulty == "easy" else 0.85
                    self.state.next_spawn = max(
                        minimum_gap,
                        self.spawn_director.next_encounter_gap(progress, difficulty=difficulty),
                    )

        if self.state.next_coinline <= 0:
            lane = self.spawn_director.choose_coin_lane(self.player.lane)
            self._spawn_coin_line(
                lane,
                start_distance=self.spawn_director.base_spawn_distance(
                    progress,
                    self.state.speed,
                    difficulty=difficulty,
                )
                - 7.5,
            )
            self.state.next_coinline = max(1.55, self.spawn_director.next_coin_gap(progress, difficulty=difficulty))

        if self.state.next_support <= 0:
            kind = self._choose_support_spawn_kind()
            lane = self.spawn_director.support_lane(self.player.lane)
            distance = self.spawn_director.base_spawn_distance(
                progress,
                self.state.speed,
                difficulty=difficulty,
            ) + 1.5
            self._spawn_support_collectible(kind, lane, distance)
            self.state.next_support = max(5.5, self.spawn_director.next_support_gap(progress, difficulty=difficulty))

    def _spawn_pattern(self, pattern: RoutePattern, base_distance: float) -> None:
        for entry in pattern.entries:
            self.obstacles.append(Obstacle(kind=entry.kind, lane=entry.lane, z=base_distance + entry.z_offset))

    def _choose_playable_pattern(self, progress: float, difficulty: str | None = None) -> Optional[tuple[RoutePattern, float]]:
        selected_difficulty = difficulty or self._difficulty_key()
        for pattern in self.spawn_director.candidate_patterns(progress, difficulty=selected_difficulty):
            distance = self.spawn_director.base_spawn_distance(progress, self.state.speed, difficulty=selected_difficulty)
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
        if kind == "multiplier":
            self.obstacles.append(Obstacle(kind="multiplier", lane=lane, z=distance, label="2X"))
            return
        if kind == "super_box":
            self.obstacles.append(Obstacle(kind="super_box", lane=lane, z=distance, label="?"))
            return
        if kind == "pogo":
            self.obstacles.append(Obstacle(kind="pogo", lane=lane, z=distance, label="P"))
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
            if not obstacle.warned and 0 < obstacle.z < warning_distance and obstacle.kind in (
                "power",
                "box",
                "multiplier",
                "super_box",
                "pogo",
            ):
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

            if obstacle.kind in ("power", "box", "key", "word", "season_token", "multiplier", "super_box", "pogo") and -0.8 < obstacle.z < 2.4:
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
                    elif obstacle.kind == "multiplier":
                        self._collect_multiplier_pickup()
                    elif obstacle.kind == "super_box":
                        self._collect_super_mysterizer()
                    elif obstacle.kind == "pogo":
                        self._collect_pogo_stick()
                    else:
                        self._collect_box()
                    obstacle.z = -999

            if obstacle.kind in ("train", "low", "high", "bush") and -0.8 < obstacle.z < hit_distance:
                if self.player.jetpack > 0 or self.player.headstart > 0 or obstacle.lane != self.player.lane:
                    continue
                if self.player.pogo_active > 0 and self.player.y > 1.0:
                    continue
                if obstacle.kind in ("low", "bush") and self.player.y > 0.6:
                    continue
                if obstacle.kind == "high" and self.player.rolling > 0:
                    continue
                self._on_hit(obstacle.kind)
                obstacle.z = -999

    def _collect_coin(self, obstacle: Obstacle) -> None:
        self._add_run_coins(1)
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

    def _collect_multiplier_pickup(self) -> None:
        self._record_mission_event("powerups")
        self.audio.play("powerup", channel="act")
        self.player.mult2x = max(self.player.mult2x, MULTIPLIER_PICKUP_DURATION)
        self.speaker.speak("2x multiplier.", interrupt=False)

    def _collect_super_mysterizer(self) -> None:
        self._record_mission_event("boxes")
        self._open_super_mystery_box("Super Mysterizer")

    def _launch_pogo_bounce(self) -> None:
        if self.player.pogo_active <= 0 or self.player.jetpack > 0 or self.player.headstart > 0:
            return
        if self.player.y > 0.01 or self.player.vy > 0.01:
            return
        self.player.rolling = 0.0
        self.player.vy = 14.6
        self.audio.play("sneakers_jump", channel="act")

    def _collect_pogo_stick(self) -> None:
        self._record_mission_event("powerups")
        self.audio.play("powerup", channel="act")
        self.player.pogo_active = max(self.player.pogo_active, POGO_STICK_DURATION)
        self._launch_pogo_bounce()
        self.speaker.speak("Pogo stick.", interrupt=False)

    def _collect_box(self) -> None:
        self._record_mission_event("boxes")
        reward = pick_mystery_box_reward()
        self.speaker.speak("Opening Mystery Box.", interrupt=True)
        self.audio.play("mystery_box_open", channel="act")
        if reward == "coins":
            gain = random.randint(10, 40)
            self._add_run_coins(gain)
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
        expected_letter = self._next_word_letter()
        if not expected_letter or obstacle.label != expected_letter:
            return
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

    def _activate_magnet(self, duration: float) -> None:
        was_inactive = self.player.magnet <= 0
        self.player.magnet = max(self.player.magnet, float(duration))
        if was_inactive and self.player.jetpack <= 0 and self.player.headstart <= 0:
            self.audio.play("magnet_loop", loop=True, channel="loop_magnet")

    def _activate_jetpack(self, duration: float) -> None:
        was_inactive = self.player.jetpack <= 0
        self.player.jetpack = max(self.player.jetpack, float(duration))
        self.player.y = 2.0
        self.player.vy = 0.0
        if was_inactive:
            self.audio.play("jetpack_loop", loop=True, channel="loop_jetpack")

    def _apply_power_reward(self, reward: str, from_headstart: bool) -> None:
        if reward == "magnet":
            self._activate_magnet(9.0)
            message = "Headstart reward: magnet." if from_headstart else "Magnet."
            self.speaker.speak(message, interrupt=False)
            return
        if reward == "jetpack":
            self._activate_jetpack(6.5)
            self.speaker.speak("Jetpack.", interrupt=False)
            return
        if reward == "mult2x":
            self.player.mult2x = max(self.player.mult2x, 10.0)
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
        self._set_active_menu(self.revive_menu)
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
        self._guard_loop_timer = 0.0
        self._set_active_menu(None)
        self.audio.play("unlock", channel="ui")
        self.audio.play("powerup", channel="act")
        self.speaker.speak("Revived. Temporary shield active.", interrupt=True)

    def _finish_run_loss(self, death_reason: Optional[str] = None) -> None:
        self.state.paused = False
        self._stop_spatial_audio()
        self.audio.play("kick", channel="player_kick")
        self.audio.play("death_hitcam", channel="player_death_cam")
        self.audio.play("death_bodyfall", channel="player_death_fall")
        self.audio.play("death", channel="act")
        self.audio.play("guard_catch", channel="act2")
        summary_reason = death_reason or self._last_death_reason or "Run ended after crash"
        self.speaker.speak(f"Run over. Score {int(self.state.score)}. {summary_reason}.", interrupt=True)
        self._commit_run_rewards()
        self.audio.stop("loop_guard")
        self.audio.stop("loop_magnet")
        self.audio.stop("loop_jetpack")
        self._stop_spatial_audio()
        self.spatial_audio.reset()
        self._open_game_over_dialog(summary_reason)

    def _stop_spatial_audio(self) -> None:
        for lane in LANES:
            self.audio.stop(f"spatial_{lane}")

    def _on_hit(self, variant: str = "train") -> None:
        if self.player.hover_active > 0:
            self.player.hover_active = 0.0
            self.audio.play("crash", channel="act")
            self.audio.play("powerdown", channel="act2")
            self.speaker.speak("Hoverboard destroyed.", interrupt=True)
            return

        self._last_death_reason = self._death_reason_for_variant(variant)
        self.player.stumbles += 1
        if self.player.stumbles >= 2:
            self._guard_loop_timer = 0.0
            self._queue_revive_or_finish()
            return

        if variant == "bush":
            stumble_sound = "stumble_bush"
        else:
            stumble_sound = "stumble_side" if self.player.lane != 0 else "stumble"
        self._guard_loop_timer = GUARD_LOOP_DURATION
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
        width, height = self.screen.get_size()
        self.screen.fill((10, 10, 15))
        title_surface = self.big.render(menu.title, True, (240, 240, 240))
        self.screen.blit(title_surface, (40, 32))

        list_top = 110
        row_height = 38
        visible_rows = 9 if menu == self.learn_sounds_menu else 10
        max_start_index = max(0, len(menu.items) - visible_rows)
        start_index = max(0, min(menu.index - (visible_rows // 2), max_start_index))
        visible_items = menu.items[start_index : start_index + visible_rows]
        y_position = list_top
        if menu == self.shop_menu:
            coins_surface = self.font.render(self._shop_coins_label(), True, (220, 220, 220))
            self.screen.blit(coins_surface, (70, y_position))
            y_position += 40
        for relative_index, item in enumerate(visible_items):
            actual_index = start_index + relative_index
            color = (255, 255, 0) if actual_index == menu.index else (220, 220, 220)
            label_surface = self.font.render(item.label, True, color)
            self.screen.blit(label_surface, (70, y_position))
            y_position += row_height

        if start_index > 0:
            top_more = self.font.render("...", True, (160, 160, 160))
            self.screen.blit(top_more, (40, list_top - 28))
        if start_index + len(visible_items) < len(menu.items):
            bottom_more = self.font.render("...", True, (160, 160, 160))
            self.screen.blit(bottom_more, (40, y_position - 8))

        hint_text = self._menu_navigation_hint()
        if menu == self.learn_sounds_menu:
            description_lines = textwrap.wrap(self._learn_sound_description, width=62)[:3]
            description_top = min(height - 132, y_position + 18)
            prompt_surface = self.font.render("Select a sound to hear its gameplay cue.", True, (205, 205, 205))
            self.screen.blit(prompt_surface, (40, description_top))
            for line_index, line in enumerate(description_lines):
                line_surface = self.font.render(line, True, (180, 180, 180))
                self.screen.blit(line_surface, (40, description_top + 32 + (line_index * 26)))
            hint_text = self._menu_navigation_hint()
        elif menu == self.update_menu:
            description_lines = textwrap.wrap(self._update_status_message, width=62)[:2]
            release_note_lines = textwrap.wrap(self._update_release_notes, width=62)[:5]
            description_top = min(height - 176, y_position + 14)
            prompt_surface = self.font.render("Update required before you can continue.", True, (205, 205, 205))
            self.screen.blit(prompt_surface, (40, description_top))
            for line_index, line in enumerate(description_lines):
                line_surface = self.font.render(line, True, (180, 180, 180))
                self.screen.blit(line_surface, (40, description_top + 32 + (line_index * 26)))
            if self._update_progress_stage in {"download", "extract", "ready", "error"}:
                progress_surface = self.font.render(
                    f"Status: {self._update_progress_message or self._update_status_message}",
                    True,
                    (190, 210, 190) if self._update_progress_stage == "ready" else (180, 180, 180),
                )
                self.screen.blit(progress_surface, (40, description_top + 88))
                percent_surface = self.font.render(
                    f"Progress: {int(self._update_progress_percent)}%",
                    True,
                    (220, 220, 120),
                )
                self.screen.blit(percent_surface, (40, description_top + 116))
                notes_top = description_top + 150
            else:
                notes_top = description_top + 88
            notes_label_surface = self.font.render("Release Notes:", True, (205, 205, 205))
            self.screen.blit(notes_label_surface, (40, notes_top))
            for line_index, line in enumerate(release_note_lines):
                line_surface = self.font.render(line, True, (180, 180, 180))
                self.screen.blit(line_surface, (40, notes_top + 28 + (line_index * 24)))
            hint_text = self._menu_navigation_hint()
        elif menu == self.options_menu:
            hint_text = f"{self._menu_navigation_hint()} {self._option_adjustment_hint()}"
        elif menu in {self.keyboard_bindings_menu, self.controller_bindings_menu} and self._binding_capture is not None:
            capture_prompt = (
                f"Press a key for {action_label(self._binding_capture.action_key)}. Escape cancels."
                if self._binding_capture.device == "keyboard"
                else f"Press a controller input for {action_label(self._binding_capture.action_key)}. Escape cancels."
            )
            prompt_surface = self.font.render(capture_prompt, True, (255, 220, 120))
            self.screen.blit(prompt_surface, (40, max(height - 80, y_position + 18)))

        hint_surface = self.font.render(hint_text, True, (180, 180, 180))
        hint_rect = hint_surface.get_rect(left=40, bottom=max(40, height - 20))
        self.screen.blit(hint_surface, hint_rect)

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
            elif obstacle.kind == "multiplier":
                color = (255, 210, 70)
                size = max(14, size // 2)
            elif obstacle.kind == "super_box":
                color = (245, 120, 255)
                size = max(14, size // 2)
            elif obstacle.kind == "pogo":
                color = (110, 235, 210)
                size = max(14, size // 2)
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
