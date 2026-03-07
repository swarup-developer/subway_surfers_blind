import os
import tempfile
import unittest
import copy
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from subway_blind import config as config_module
from subway_blind.audio import Audio, Speaker
from subway_blind.balance import SPEED_PROFILES, speed_profile_for_difficulty
from subway_blind.features import HEADSTART_SPEED_BONUS
from subway_blind.features import SHOP_PRICES
from subway_blind.game import SubwayBlindGame, cycle_volume
from subway_blind.hrtf_audio import OpenALHrtfEngine
from subway_blind.menu import Menu, MenuItem
from subway_blind.models import Obstacle, lane_name
from subway_blind.spatial_audio import SpatialThreatAudio
from subway_blind.spawn import PATTERNS, PatternEntry, RoutePattern, SpawnDirector


class DummySpeaker:
    def __init__(self):
        self.enabled = True
        self.messages: list[tuple[str, bool]] = []
        self.speed_factors: list[float] = []

    def speak(self, text: str, interrupt: bool = True) -> None:
        self.messages.append((text, interrupt))

    def set_speed_factor(self, speed_factor: float) -> None:
        self.speed_factors.append(speed_factor)


class DummyAudio:
    def __init__(self, settings: dict):
        self.settings = settings
        self.sounds = {}
        self.played: list[tuple[str, str | None, bool]] = []
        self.spatial_played: list[tuple[str, str, float, float, float, float, float, float | None]] = []
        self.spatial_updated: list[tuple[str, float, float, float, float, float, float | None]] = []
        self.stopped: list[str] = []
        self.refreshed = 0
        self.music_started = 0
        self.music_stopped = 0

    def play(self, key: str, pan=None, loop: bool = False, channel: str | None = None, gain: float = 1.0) -> None:
        self.played.append((key, channel, loop))

    def stop(self, channel: str) -> None:
        self.stopped.append(channel)

    def play_spatial(
        self,
        key: str,
        channel: str,
        x: float,
        y: float,
        z: float,
        gain: float,
        pitch: float = 1.0,
        fallback_pan: float | None = None,
        velocity_x: float = 0.0,
        velocity_y: float = 0.0,
        velocity_z: float = 0.0,
    ) -> None:
        self.spatial_played.append(
            (key, channel, x, y, z, gain, pitch, fallback_pan, velocity_x, velocity_y, velocity_z)
        )

    def update_spatial(
        self,
        channel: str,
        x: float,
        y: float,
        z: float,
        gain: float,
        pitch: float = 1.0,
        fallback_pan: float | None = None,
        velocity_x: float = 0.0,
        velocity_y: float = 0.0,
        velocity_z: float = 0.0,
    ) -> None:
        self.spatial_updated.append((channel, x, y, z, gain, pitch, fallback_pan, velocity_x, velocity_y, velocity_z))

    def refresh_volumes(self) -> None:
        self.refreshed += 1

    def music_start(self) -> None:
        self.music_started += 1

    def music_stop(self) -> None:
        self.music_stopped += 1

    def _get_channel(self, name: str):
        return None


class MenuTests(unittest.TestCase):
    def test_menu_navigation_and_selection(self):
        speaker = DummySpeaker()
        audio = DummyAudio({})
        menu = Menu(speaker, audio, "Main Menu", [MenuItem("Start", "start"), MenuItem("Quit", "quit")])

        menu.open()
        self.assertEqual(menu.index, 0)
        self.assertEqual(speaker.messages[0][0], "Main Menu")
        self.assertEqual(speaker.messages[1][0], "Start")

        self.assertIsNone(menu.handle_key(pygame.K_DOWN))
        self.assertEqual(menu.index, 1)
        self.assertEqual(speaker.messages[-1][0], "Quit")

        action = menu.handle_key(pygame.K_RETURN)
        self.assertEqual(action, "quit")


class ConfigTests(unittest.TestCase):
    def test_settings_round_trip_preserves_defaults(self):
        original_base_dir = config_module.BASE_DIR
        with tempfile.TemporaryDirectory() as temp_directory:
            config_module.BASE_DIR = Path(temp_directory)
            config_module.save_settings({"sfx_volume": 0.4})
            loaded = config_module.load_settings()
        config_module.BASE_DIR = original_base_dir

        self.assertEqual(loaded["sfx_volume"], 0.4)
        self.assertEqual(loaded["music_volume"], config_module.DEFAULT_SETTINGS["music_volume"])
        self.assertEqual(loaded["difficulty"], "normal")


class BalanceTests(unittest.TestCase):
    def test_normal_profile_reaches_cap_at_three_minutes(self):
        profile = SPEED_PROFILES["normal"]
        self.assertAlmostEqual(profile.speed_for_elapsed(0.0), 18.4)
        self.assertAlmostEqual(profile.speed_for_elapsed(180.0), 33.9)
        self.assertAlmostEqual(profile.speed_for_elapsed(240.0), 33.9)

    def test_unknown_difficulty_falls_back_to_normal(self):
        self.assertIs(speed_profile_for_difficulty("unknown"), SPEED_PROFILES["normal"])


class AudioTests(unittest.TestCase):
    def test_footstep_pan_does_not_follow_lane(self):
        self.assertEqual(Audio._normalize_pan_for_key("left_foot", 0.9), -0.18)
        self.assertEqual(Audio._normalize_pan_for_key("right_foot", -0.9), 0.18)
        self.assertEqual(Audio._normalize_pan_for_key("jump", 0.9), 0.0)
        self.assertEqual(Audio._normalize_pan_for_key("dodge", -0.9), 0.0)

    def test_transient_player_channels_are_collapsed(self):
        self.assertEqual(Audio._normalize_channel_for_key("jump", "act"), "player_jump")
        self.assertEqual(Audio._normalize_channel_for_key("dodge", "move"), "player_dodge")
        self.assertEqual(Audio._normalize_channel_for_key("left_foot", "foot"), "player_footstep")
        self.assertEqual(Audio._normalize_channel_for_key("coin", "coin"), "player_pickup")
        self.assertEqual(Audio._normalize_channel_for_key("powerup", "act"), "player_power")


class SpeakerTests(unittest.TestCase):
    def test_set_speed_factor_applies_rate_to_supported_outputs(self):
        class RateOutput:
            def __init__(self):
                self.rate = None

            def has_rate(self):
                return True

            def min_rate(self):
                return -10

            def max_rate(self):
                return 10

            def set_rate(self, value):
                self.rate = value
        speaker = Speaker(enabled=False)
        speaker._driver = type("Driver", (), {"outputs": [RateOutput()]})()

        speaker.set_speed_factor(1.0)

        rate_output = speaker._driver.outputs[0]
        self.assertIsNotNone(rate_output.rate)
        self.assertGreater(rate_output.rate, 0.0)



class FakeOpenALSource:
    def __init__(self):
        self.reference_distance = 0.0
        self.rolloff_factor = 0.0
        self.max_distance = 0.0
        self.relative = False
        self.looping = False
        self.gain = 0.0
        self.pitch = 1.0
        self.playing = False
        self.calls: list[tuple[str, object | None]] = []

    def set_buffer(self, buffer) -> None:
        self.calls.append(("set_buffer", buffer))

    def set_position(self, x: float, y: float, z: float) -> None:
        self.calls.append(("set_position", (x, y, z)))

    def set_velocity(self, x: float, y: float, z: float) -> None:
        self.calls.append(("set_velocity", (x, y, z)))

    def stop(self) -> None:
        self.calls.append(("stop", None))
        self.playing = False

    def play(self) -> None:
        self.calls.append(("play", None))
        self.playing = True


class FakeOpenALModule:
    def __init__(self, source: FakeOpenALSource):
        self._source = source

    def Source(self) -> FakeOpenALSource:
        return self._source


class HrtfEngineTests(unittest.TestCase):
    def test_changing_buffer_stops_source_before_rebinding(self):
        source = FakeOpenALSource()
        engine = OpenALHrtfEngine.__new__(OpenALHrtfEngine)
        engine.available = True
        engine._al = FakeOpenALModule(source)
        engine._buffers = {"box": object(), "jump": object()}
        engine._buffer_paths = {}
        engine._sources = {}
        engine._channel_keys = {}
        engine._listener_gain = 1.0
        engine.register_sound = lambda key, path: None

        engine.play_sound("box", "box.wav", "player_action", 0.0, 0.0, -1.0, 1.0)
        source.calls.clear()
        source.playing = True

        engine.play_sound("jump", "jump.wav", "player_action", 0.0, 0.0, -1.0, 1.0)

        self.assertEqual(source.calls[0][0], "stop")
        self.assertEqual(source.calls[1][0], "set_buffer")
        self.assertEqual(engine._channel_keys["player_action"], "jump")

    def test_stop_clears_channel_key(self):
        source = FakeOpenALSource()
        engine = OpenALHrtfEngine.__new__(OpenALHrtfEngine)
        engine.available = True
        engine._al = FakeOpenALModule(source)
        engine._buffers = {}
        engine._buffer_paths = {}
        engine._sources = {"player_action": source}
        engine._channel_keys = {"player_action": "box"}
        engine._listener_gain = 1.0

        engine.stop("player_action")

        self.assertNotIn("player_action", engine._channel_keys)
        self.assertEqual(source.calls[-1][0], "stop")

    def test_update_source_ignores_missing_or_stopped_sources(self):
        source = FakeOpenALSource()
        engine = OpenALHrtfEngine.__new__(OpenALHrtfEngine)
        engine.available = True
        engine._sources = {"player_action": source}
        engine._listener_gain = 1.0

        self.assertFalse(engine.update_source("missing", 0.0, 0.0, -1.0, 1.0))
        self.assertFalse(engine.update_source("player_action", 0.0, 0.0, -1.0, 1.0))

    def test_update_source_repositions_playing_source(self):
        source = FakeOpenALSource()
        source.playing = True
        engine = OpenALHrtfEngine.__new__(OpenALHrtfEngine)
        engine.available = True
        engine._sources = {"spatial_0": source}
        engine._listener_gain = 1.0

        updated = engine.update_source("spatial_0", 1.2, -0.1, -4.0, 0.8, 1.1, True)

        self.assertTrue(updated)
        self.assertEqual(source.calls[-2], ("set_position", (1.2, -0.1, -4.0)))
        self.assertEqual(source.calls[-1], ("set_velocity", (0.0, 0.0, 0.0)))
        self.assertTrue(source.relative)


class SpatialAudioTests(unittest.TestCase):
    def test_build_threat_cues_prefers_nearest_hazard_per_lane(self):
        engine = SpatialThreatAudio()
        obstacles = [
            Obstacle(kind="low", lane=-1, z=16.0),
            Obstacle(kind="train", lane=-1, z=9.0),
            Obstacle(kind="high", lane=1, z=7.5),
            Obstacle(kind="coin", lane=0, z=4.0),
        ]

        cues = engine.build_threat_cues(0, 20.0, obstacles)

        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0].kind, "train")
        self.assertEqual(cues[0].lane, -1)
        self.assertEqual(cues[1].kind, "high")
        self.assertEqual(cues[1].lane, 1)

    def test_close_current_lane_threat_generates_action_prompt(self):
        engine = SpatialThreatAudio()
        obstacle = Obstacle(kind="high", lane=0, z=5.0)

        cue = engine.build_threat_cues(0, 20.0, [obstacle])[0]

        self.assertEqual(cue.prompt, "roll soon")
        self.assertLess(cue.interval, 0.5)
        self.assertGreater(cue.gain, 0.7)

    def test_prompt_is_announced_earlier_for_current_lane_train(self):
        engine = SpatialThreatAudio()
        obstacle = Obstacle(kind="train", lane=0, z=14.5)

        cue = engine.build_threat_cues(0, 20.0, [obstacle])[0]

        self.assertEqual(cue.prompt, "switch now")

    def test_prompt_shortens_at_high_speed(self):
        engine = SpatialThreatAudio()
        obstacle = Obstacle(kind="high", lane=0, z=15.5)

        cue = engine.build_threat_cues(0, 33.9, [obstacle])[0]

        self.assertEqual(cue.prompt, "roll")

    def test_off_lane_threat_does_not_speak(self):
        engine = SpatialThreatAudio()
        obstacle = Obstacle(kind="low", lane=1, z=5.0)

        cue = engine.build_threat_cues(0, 20.0, [obstacle])[0]

        self.assertIsNone(cue.prompt)

    def test_update_emits_spatial_audio_coordinates(self):
        engine = SpatialThreatAudio()
        audio = DummyAudio({})
        speaker = DummySpeaker()
        obstacle = Obstacle(kind="train", lane=1, z=8.0)

        engine.update(0.1, 0, 20.0, [obstacle], audio, speaker)

        self.assertEqual(len(audio.spatial_played), 1)
        key, channel, x, _, z, gain, pitch, fallback_pan, _, _, velocity_z = audio.spatial_played[0]
        self.assertEqual(key, "train_pass")
        self.assertEqual(channel, "spatial_1")
        self.assertGreater(x, 0.0)
        self.assertLess(z, 0.0)
        self.assertGreater(gain, 0.0)
        self.assertGreater(pitch, 0.9)
        self.assertIsNotNone(fallback_pan)
        self.assertGreater(velocity_z, 0.0)

    def test_update_repositions_active_spatial_sources_and_stops_inactive_ones(self):
        engine = SpatialThreatAudio()
        audio = DummyAudio({})
        speaker = DummySpeaker()
        obstacle = Obstacle(kind="train", lane=1, z=8.0)

        engine.update(0.1, 0, 20.0, [obstacle], audio, speaker)

        self.assertTrue(any(update[0] == "spatial_1" for update in audio.spatial_updated))
        self.assertIn("spatial_-1", audio.stopped)
        self.assertIn("spatial_0", audio.stopped)

    def test_train_cue_continues_behind_listener(self):
        engine = SpatialThreatAudio()
        obstacle = Obstacle(kind="train", lane=0, z=-3.0)

        cue = engine.build_threat_cues(0, 20.0, [obstacle])[0]

        self.assertGreater(cue.source_z, 0.0)
        self.assertIsNone(cue.prompt)


class SpawnDirectorTests(unittest.TestCase):
    def test_patterns_always_leave_a_safe_lane(self):
        for pattern in PATTERNS:
            self.assertTrue(pattern.safe_lanes)
            self.assertTrue(set(pattern.safe_lanes).issubset({-1, 0, 1}))
            blocked_by_step: dict[float, set[int]] = {}
            for entry in pattern.entries:
                if entry.kind not in {"train", "low", "high"}:
                    continue
                blocked_by_step.setdefault(entry.z_offset, set()).add(entry.lane)
            self.assertTrue(all(len(blocked_lanes) < 3 for blocked_lanes in blocked_by_step.values()))

    def test_support_lane_uses_last_safe_lane(self):
        director = SpawnDirector()

        with patch("subway_blind.spawn.random.choices", return_value=[PATTERNS[0]]), patch(
            "subway_blind.spawn.random.choice",
            side_effect=[1],
        ):
            director.choose_pattern(0.0)

        self.assertEqual(director.support_lane(), 1)

    def test_support_reward_pool_contains_only_expected_types(self):
        director = SpawnDirector()

        for _ in range(100):
            self.assertIn(director.choose_support_kind(), {"power", "box", "key"})

    def test_pattern_is_rejected_when_it_closes_all_lanes_with_active_hazards(self):
        director = SpawnDirector()
        existing = [
            Obstacle(kind="train", lane=-1, z=32.2),
            Obstacle(kind="train", lane=1, z=32.4),
        ]

        playable = director.pattern_is_playable(PATTERNS[0], 32.0, existing, current_lane=0)

        self.assertFalse(playable)

    def test_pattern_is_rejected_when_open_lane_is_not_reachable_from_current_lane(self):
        director = SpawnDirector()
        pattern = RoutePattern(
            "right_wall",
            (PatternEntry("train", 1),),
            (-1,),
            0.0,
            1.0,
        )
        existing = [Obstacle(kind="train", lane=0, z=2.4)]

        playable = director.pattern_is_playable(pattern, 2.4, existing, current_lane=1)

        self.assertFalse(playable)

    def test_spawn_is_delayed_when_near_hazard_is_still_active(self):
        director = SpawnDirector()

        self.assertTrue(director.should_delay_spawn([Obstacle(kind="train", lane=0, z=11.0)]))
        self.assertFalse(director.should_delay_spawn([Obstacle(kind="train", lane=0, z=26.0)]))


class GameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pygame.init()
        try:
            pygame.mixer.init()
        except pygame.error:
            pass
        cls.screen = pygame.display.set_mode((320, 240))

    @classmethod
    def tearDownClass(cls):
        pygame.quit()

    def make_game(self):
        settings = copy.deepcopy(config_module.DEFAULT_SETTINGS)
        settings["speech_enabled"] = False
        game = SubwayBlindGame(self.screen, pygame.time.Clock(), settings)
        speaker = DummySpeaker()
        audio = DummyAudio(settings)
        game.speaker = speaker
        game.audio = audio
        for menu in (game.main_menu, game.shop_menu, game.options_menu, game.pause_menu, game.loadout_menu, game.revive_menu):
            menu.speaker = speaker
            menu.audio = audio
        return game, speaker, audio

    def test_main_menu_is_english(self):
        game, _, _ = self.make_game()
        self.assertEqual(game.main_menu.title, "Main Menu")
        self.assertEqual([item.label for item in game.main_menu.items], ["Start Game", "Shop", "Options", "How to Play", "Exit"])

    def test_start_run_uses_profile_base_speed(self):
        game, _, audio = self.make_game()
        game.settings["difficulty"] = "hard"

        game.start_run()

        self.assertEqual(game.state.speed, SPEED_PROFILES["hard"].base_speed)
        self.assertEqual(audio.music_started, 1)

    def test_start_run_includes_permanent_mission_multiplier_bonus(self):
        game, _, _ = self.make_game()
        game.settings["mission_multiplier_bonus"] = 4

        game.start_run()

        self.assertEqual(game.state.multiplier, 5)

    def test_cycle_volume_wraps_after_maximum(self):
        self.assertEqual(cycle_volume(1.0), 0.0)
        self.assertEqual(cycle_volume(0.9), 1.0)

    def test_start_action_opens_run_setup_menu(self):
        game, _, _ = self.make_game()

        game._handle_menu_action("start")

        self.assertIs(game.active_menu, game.loadout_menu)
        self.assertEqual(game.loadout_menu.title, "Run Setup")

    def test_headstart_adds_speed_bonus_and_consumes_inventory(self):
        game, _, _ = self.make_game()
        game.selected_headstart = True
        starting_inventory = game.settings["headstarts"]

        game.start_run()
        game._update_game(0.5)

        self.assertEqual(game.settings["headstarts"], starting_inventory - 1)
        self.assertGreaterEqual(game.state.speed, SPEED_PROFILES["normal"].base_speed + HEADSTART_SPEED_BONUS)

    def test_update_game_caps_speed_after_profile_limit(self):
        game, _, _ = self.make_game()
        game.start_run()
        game.state.time = 179.95

        game._update_game(1.0)

        self.assertAlmostEqual(game.state.speed, SPEED_PROFILES["normal"].max_speed)

    def test_update_game_updates_speaker_speed_factor(self):
        game, speaker, _ = self.make_game()
        game.start_run()
        game.state.time = 179.0

        game._update_game(1.0)

        self.assertTrue(speaker.speed_factors)
        self.assertGreater(speaker.speed_factors[-1], 0.95)

    def test_spawn_things_creates_pattern_coinline_and_support_in_safe_route(self):
        game, _, _ = self.make_game()
        game.start_run()
        game.state.next_spawn = 0.0
        game.state.next_coinline = 0.0
        game.state.next_support = 0.0

        with patch.object(game, "_choose_playable_pattern", return_value=(PATTERNS[4], 32.0)), patch.object(
            game.spawn_director,
            "base_spawn_distance",
            side_effect=[29.0, 34.0],
        ), patch.object(game.spawn_director, "choose_coin_lane", return_value=0), patch.object(
            game,
            "_choose_support_spawn_kind",
            return_value="key",
        ), patch.object(game.spawn_director, "support_lane", return_value=0):
            game._spawn_things(0.016)

        hazards = [obstacle for obstacle in game.obstacles if obstacle.kind in {"train", "low", "high"}]
        coins = [obstacle for obstacle in game.obstacles if obstacle.kind == "coin"]
        keys = [obstacle for obstacle in game.obstacles if obstacle.kind == "key"]

        self.assertEqual(len(hazards), 2)
        self.assertEqual({obstacle.lane for obstacle in hazards}, {-1, 1})
        self.assertEqual(len(coins), 6)
        self.assertTrue(all(obstacle.lane == 0 for obstacle in coins))
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0].lane, 0)

    def test_spawn_things_delays_when_existing_hazard_is_too_close(self):
        game, _, _ = self.make_game()
        game.start_run()
        game.state.next_spawn = 0.0
        game.obstacles.append(Obstacle(kind="train", lane=0, z=10.0))

        game._spawn_things(0.016)

        self.assertAlmostEqual(game.state.next_spawn, 0.3)

    def test_option_action_updates_sfx_label(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.options_menu
        game.settings["sfx_volume"] = 1.0

        game._handle_menu_action("opt_sfx")

        self.assertEqual(game.settings["sfx_volume"], 0.0)
        self.assertEqual(game.options_menu.items[0].label, "SFX Volume: 0")
        self.assertEqual(audio.refreshed, 1)
        self.assertEqual(speaker.messages[-1][0], "SFX Volume: 0")

    def test_shop_purchase_spends_bank_coins_and_grants_hoverboard(self):
        game, speaker, _ = self.make_game()
        game.settings["bank_coins"] = SHOP_PRICES["hoverboard"]
        game.settings["hoverboards"] = 0
        game.active_menu = game.shop_menu

        game._purchase_shop_item("hoverboard")

        self.assertEqual(game.settings["bank_coins"], 0)
        self.assertEqual(game.settings["hoverboards"], 1)
        self.assertIn(("Hoverboard purchased.", True), speaker.messages)

    def test_hoverboard_absorbs_hit(self):
        game, speaker, _ = self.make_game()
        game.player.hover_active = 5.0

        game._on_hit()

        self.assertEqual(game.player.hover_active, 0.0)
        self.assertEqual(game.player.stumbles, 0)
        self.assertEqual(speaker.messages[-1][0], "Hoverboard destroyed.")

    def test_bush_hit_uses_bush_stumble_sound(self):
        game, speaker, audio = self.make_game()

        game._on_hit("bush")

        self.assertIn(("stumble_bush", "act", False), audio.played)
        self.assertEqual(speaker.messages[-1][0], "You crashed. One chance left.")

    def test_near_miss_triggers_swish_sound(self):
        game, _, audio = self.make_game()
        game.player.lane = 0
        game.player.y = 1.2
        game.obstacles = [Obstacle(kind="low", lane=0, z=1.0)]

        game._update_near_miss_audio()

        self.assertIn(("swish_mid", "near_0", False), audio.played)

    def test_second_hit_returns_to_main_menu(self):
        game, speaker, audio = self.make_game()
        game.player.stumbles = 1
        game.state.score = 120
        game.state.coins = 8
        game.state.running = True
        game.settings["keys"] = 0
        game.active_menu = None

        game._on_hit()

        self.assertIs(game.active_menu, game.main_menu)
        self.assertEqual(audio.music_stopped, 1)
        self.assertEqual(game.settings["bank_coins"], 8)
        self.assertIn(("Run over. Score 120. Coins 8.", True), speaker.messages)

    def test_second_hit_opens_revive_menu_when_keys_exist(self):
        game, speaker, _ = self.make_game()
        game.player.stumbles = 1
        game.settings["keys"] = 2
        game.active_menu = None

        game._on_hit()

        self.assertIs(game.active_menu, game.revive_menu)
        self.assertIn(("You can revive for 1 key.", True), speaker.messages)

    def test_revive_consumes_key_and_restores_run(self):
        game, _, _ = self.make_game()
        game.settings["keys"] = 2
        game.state.revives_used = 0
        game.active_menu = game.revive_menu
        game.state.paused = True
        game.player.stumbles = 2

        game._revive_run()

        self.assertEqual(game.settings["keys"], 1)
        self.assertEqual(game.state.revives_used, 1)
        self.assertFalse(game.state.paused)
        self.assertIsNone(game.active_menu)
        self.assertEqual(game.player.stumbles, 0)
        self.assertGreater(game.player.hover_active, 0)

    def test_mystery_box_can_grant_new_inventory_rewards(self):
        game, _, _ = self.make_game()
        original_keys = game.settings["keys"]

        with patch("subway_blind.game.pick_mystery_box_reward", return_value="key"):
            game._collect_box()

        self.assertEqual(game.settings["keys"], original_keys + 1)

    def test_collect_word_letter_completes_word_hunt_and_awards_bank_coins(self):
        game, speaker, _ = self.make_game()
        word = game._current_word()
        game.settings["word_hunt_day"] = date.today().isoformat()
        game.settings["word_hunt_letters"] = word[:-1]
        game.settings["word_hunt_completed_on"] = ""
        game.settings["word_hunt_streak"] = 0

        game._collect_word_letter(Obstacle(kind="word", lane=0, z=1.0, label=word[-1]))

        self.assertEqual(game.settings["bank_coins"], 300)
        self.assertTrue(any("Word Hunt complete." in message for message, _ in speaker.messages))

    def test_collect_season_token_claims_reward(self):
        game, speaker, _ = self.make_game()
        game.settings["season_tokens"] = 4
        game.settings["season_reward_stage"] = 0

        game._collect_season_token()

        self.assertEqual(game.settings["season_reward_stage"], 1)
        self.assertEqual(game.settings["bank_coins"], 500)
        self.assertTrue(any("Season Hunt reward." in message for message, _ in speaker.messages))

    def test_record_mission_event_completes_set_and_increases_multiplier(self):
        game, speaker, _ = self.make_game()
        goals = game._mission_goals()
        for goal in goals[:-1]:
            game.settings["mission_metrics"][goal.metric] = goal.target
        final_goal = goals[-1]
        game.settings["mission_metrics"][final_goal.metric] = final_goal.target - 1
        game.state.running = True
        game.state.multiplier = 1

        game._record_mission_event(final_goal.metric)

        self.assertEqual(game.settings["mission_set"], 2)
        self.assertEqual(game.settings["mission_multiplier_bonus"], 1)
        self.assertEqual(game.state.multiplier, 2)
        self.assertTrue(any("Mission set complete." in message for message, _ in speaker.messages))

    def test_lane_names_are_english(self):
        self.assertEqual(lane_name(-1), "Left lane")
        self.assertEqual(lane_name(0), "Center lane")
        self.assertEqual(lane_name(1), "Right lane")


if __name__ == "__main__":
    unittest.main()
