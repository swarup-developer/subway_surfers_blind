import os
import tempfile
import unittest
import copy
import json
import wave
from datetime import date
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from subway_blind import config as config_module
from subway_blind.audio import (
    Audio,
    Speaker,
    SAPI_PITCH_MAX,
    SAPI_PITCH_MIN,
    SAPI_RATE_MAX,
    SAPI_RATE_MIN,
    SAPI_SPEAK_IS_XML,
    SYSTEM_DEFAULT_OUTPUT_LABEL,
)
from subway_blind.balance import SPEED_PROFILES, speed_profile_for_difficulty
from subway_blind.controls import ConnectedController, PLAYSTATION_FAMILY, XBOX_FAMILY, family_label
from subway_blind.features import HEADSTART_SPEED_BONUS, HOVERBOARD_DURATION, headstart_duration_for_uses
from subway_blind.features import SHOP_PRICES
from subway_blind.game import (
    ACTIVE_GAMEPLAY_SOUND_KEYS,
    HEADSTART_SHAKE_CHANNEL,
    HEADSTART_SPRAY_CHANNEL,
    LEARN_SOUND_LOOP_PREVIEW_DURATION,
    LEARN_SOUND_PREVIEW_CHANNEL,
    MENU_REPEAT_INITIAL_DELAY,
    MENU_REPEAT_INTERVAL,
    SubwayBlindGame,
)
from subway_blind.hrtf_audio import OpenALHrtfEngine
from subway_blind.menu import Menu, MenuItem
from subway_blind.models import Obstacle, lane_name
from subway_blind.spatial_audio import SpatialThreatAudio
from subway_blind.spawn import PATTERNS, PatternEntry, RoutePattern, SpawnDirector
from subway_blind.updater import (
    GitHubReleaseUpdater,
    ReleaseAsset,
    ReleaseInfo,
    UpdateCheckResult,
    UpdateInstallProgress,
    UpdateInstallResult,
    normalize_version,
    version_key,
)
from subway_blind.version import APP_VERSION


class DummySpeaker:
    def __init__(self):
        self.enabled = True
        self.use_sapi = False
        self.sapi_voice_id = ""
        self.sapi_rate = 0
        self.sapi_pitch = 0
        self._sapi_voices = [
            ("voice-zira", "Microsoft Zira Desktop - English (United States)"),
            ("voice-david", "Microsoft David Desktop - English (United States)"),
            ("voice-yelda", "VE Turkish Yelda 22kHz"),
        ]
        self.messages: list[tuple[str, bool]] = []
        self.speed_factors: list[float] = []

    def speak(self, text: str, interrupt: bool = True) -> None:
        self.messages.append((text, interrupt))

    def set_speed_factor(self, speed_factor: float) -> None:
        self.speed_factors.append(speed_factor)

    def apply_settings(self, settings: dict) -> None:
        self.enabled = bool(settings.get("speech_enabled", True))
        self.use_sapi = bool(settings.get("sapi_speech_enabled", False))
        self.sapi_rate = max(SAPI_RATE_MIN, min(SAPI_RATE_MAX, int(settings.get("sapi_rate", 0))))
        self.sapi_pitch = max(SAPI_PITCH_MIN, min(SAPI_PITCH_MAX, int(settings.get("sapi_pitch", 0))))
        requested_voice_id = str(settings.get("sapi_voice_id", "") or "").strip()
        if any(voice_id == requested_voice_id for voice_id, _ in self._sapi_voices):
            self.sapi_voice_id = requested_voice_id
        elif self._sapi_voices:
            self.sapi_voice_id = self._sapi_voices[0][0]
        else:
            self.sapi_voice_id = ""

    def current_sapi_voice_display_name(self) -> str:
        for voice_id, name in self._sapi_voices:
            if voice_id == self.sapi_voice_id:
                return name
        if self._sapi_voices:
            return self._sapi_voices[0][1]
        return "Unavailable"

    def cycle_sapi_voice(self, direction: int) -> str:
        if not self._sapi_voices:
            return "Unavailable"
        current_ids = [voice_id for voice_id, _ in self._sapi_voices]
        try:
            current_index = current_ids.index(self.sapi_voice_id)
        except ValueError:
            current_index = 0
        next_index = (current_index + (-1 if direction < 0 else 1)) % len(self._sapi_voices)
        self.sapi_voice_id = self._sapi_voices[next_index][0]
        return self._sapi_voices[next_index][1]


class DummyAudio:
    def __init__(self, settings: dict):
        self.settings = settings
        self.sounds = {}
        self.played: list[tuple[str, str | None, bool]] = []
        self.play_calls: list[dict[str, object]] = []
        self.spatial_played: list[tuple[str, str, float, float, float, float, float, float | None]] = []
        self.spatial_updated: list[tuple[str, float, float, float, float, float, float | None]] = []
        self.stopped: list[str] = []
        self.refreshed = 0
        self.music_started = 0
        self.music_stopped = 0
        self.music_started_tracks: list[str] = []
        self.music_update_calls: list[float] = []
        self.music_idle = False
        self._output_device_name = settings.get("audio_output_device") or None

    def play(self, key: str, pan=None, loop: bool = False, channel: str | None = None, gain: float = 1.0) -> None:
        self.play_calls.append({"key": key, "channel": channel, "loop": loop, "pan": pan, "gain": gain})
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

    def music_start(self, track_key: str = "gameplay") -> None:
        self.music_started += 1
        self.music_started_tracks.append(track_key)
        self.music_idle = False

    def music_stop(self, immediate: bool = False) -> None:
        self.music_stopped += 1
        self.music_idle = True

    def update(self, delta_time: float) -> None:
        self.music_update_calls.append(delta_time)

    def music_is_idle(self) -> bool:
        return self.music_idle

    def _get_channel(self, name: str):
        return None

    def output_device_display_name(self) -> str:
        return self._output_device_name or SYSTEM_DEFAULT_OUTPUT_LABEL

    def current_output_device_name(self) -> str | None:
        return self._output_device_name

    def output_device_choices(self) -> list[str | None]:
        return [None, "External USB Headphones", "Studio Speakers"]

    def apply_output_device(self, device_name: str | None) -> str | None:
        self._output_device_name = device_name
        self.settings["audio_output_device"] = device_name or ""
        return self._output_device_name


class DummyUpdater:
    def __init__(self):
        self.check_results: list[UpdateCheckResult] = [
            UpdateCheckResult(
                status="no_releases",
                current_version=APP_VERSION,
                message="No published releases were found.",
            )
        ]
        self.check_calls = 0
        self.download_calls: list[ReleaseInfo] = []
        self.open_calls: list[ReleaseInfo | None] = []
        self.install_result = UpdateInstallResult(
            success=True,
            message="Update installed. Restart the game to finish applying it.",
            restart_required=True,
            restart_script_path=r"C:\Users\oguzhan\AppData\Local\Temp\apply_update.cmd",
        )
        self.open_success = True
        self.launch_restart_calls: list[str | None] = []

    def enqueue_result(self, result: UpdateCheckResult) -> None:
        self.check_results.append(result)

    def check_for_updates(self, current_version: str) -> UpdateCheckResult:
        self.check_calls += 1
        if self.check_results:
            return self.check_results.pop(0)
        return UpdateCheckResult(
            status="no_releases",
            current_version=current_version,
            message="No published releases were found.",
        )

    def has_installable_package(self, release: ReleaseInfo) -> bool:
        return any(asset.name.endswith(".zip") for asset in release.assets)

    def download_and_install(self, release: ReleaseInfo, progress_callback=None) -> UpdateInstallResult:
        self.download_calls.append(release)
        if progress_callback is not None:
            progress_callback(UpdateInstallProgress("download", 100.0, "Downloading update package. 100 percent."))
            progress_callback(UpdateInstallProgress("extract", 100.0, "Extracting update package. 100 percent."))
        return self.install_result

    def open_release_page(self, release: ReleaseInfo | None = None) -> bool:
        self.open_calls.append(release)
        return self.open_success

    def launch_restart_script(self, restart_script_path: str | None) -> bool:
        self.launch_restart_calls.append(restart_script_path)
        return restart_script_path is not None


class DummyControllerDevice:
    def __init__(self, name: str):
        self.name = name
        self.quit_calls = 0

    def quit(self) -> None:
        self.quit_calls += 1


def make_release_info(version: str = "0.2.0") -> ReleaseInfo:
    return ReleaseInfo(
        version=version,
        page_url="https://github.com/oguzhanproductions/subway_surfers_blind/releases/tag/v0.2.0",
        published_at="2026-03-08T10:00:00Z",
        title=f"v{version}",
        notes="Important fixes.",
        assets=(
            ReleaseAsset(
                name="SubwaySurfersBlind.zip",
                download_url="https://example.com/SubwaySurfersBlind.zip",
                content_type="application/zip",
                size=2048,
            ),
        ),
    )


class MenuTests(unittest.TestCase):
    def test_menu_navigation_and_selection(self):
        speaker = DummySpeaker()
        audio = DummyAudio({})
        menu = Menu(speaker, audio, "Main Menu", [MenuItem("Start", "start"), MenuItem("Quit", "quit")])

        menu.open()
        self.assertEqual(menu.index, 0)
        self.assertIn(("menuopen", "ui", False), audio.played)
        self.assertEqual(speaker.messages[0][0], "Main Menu. Start")

        self.assertIsNone(menu.handle_key(pygame.K_DOWN))
        self.assertEqual(menu.index, 1)
        self.assertEqual(speaker.messages[-1][0], "Quit")

        action = menu.handle_key(pygame.K_RETURN)
        self.assertEqual(action, "quit")
        self.assertIn(("confirm", "ui", False), audio.played)

    def test_menu_open_starts_from_left_and_moves_right_by_index(self):
        speaker = DummySpeaker()
        audio = DummyAudio({})
        menu = Menu(
            speaker,
            audio,
            "Main Menu",
            [MenuItem("Start", "start"), MenuItem("Shop", "shop"), MenuItem("Options", "options")],
        )

        menu.open(start_index=0)
        self.assertAlmostEqual(audio.play_calls[-1]["pan"], -0.8)

        menu.open(start_index=2)
        self.assertAlmostEqual(audio.play_calls[-1]["pan"], 0.8)

    def test_menu_home_and_end_jump_to_bounds(self):
        speaker = DummySpeaker()
        audio = DummyAudio({})
        menu = Menu(
            speaker,
            audio,
            "Main Menu",
            [MenuItem("Start", "start"), MenuItem("Shop", "shop"), MenuItem("Quit", "quit")],
        )

        menu.open(start_index=1)
        menu.handle_key(pygame.K_END)
        self.assertEqual(menu.index, 2)
        self.assertEqual(speaker.messages[-1][0], "Quit")

        menu.handle_key(pygame.K_HOME)
        self.assertEqual(menu.index, 0)
        self.assertEqual(speaker.messages[-1][0], "Start")

    def test_menu_navigation_and_confirm_use_current_item_pan(self):
        speaker = DummySpeaker()
        audio = DummyAudio({})
        menu = Menu(
            speaker,
            audio,
            "Main Menu",
            [MenuItem("Start", "start"), MenuItem("Shop", "shop"), MenuItem("Quit", "quit")],
        )

        menu.open(start_index=0)
        menu.handle_key(pygame.K_DOWN)
        self.assertAlmostEqual(audio.play_calls[-1]["pan"], 0.0)

        menu.handle_key(pygame.K_DOWN)
        self.assertAlmostEqual(audio.play_calls[-1]["pan"], 0.8)

        menu.handle_key(pygame.K_RETURN)
        self.assertAlmostEqual(audio.play_calls[-1]["pan"], 0.8)

    def test_menu_sound_hrtf_setting_disables_menu_pan(self):
        speaker = DummySpeaker()
        audio = DummyAudio({"menu_sound_hrtf": False})
        menu = Menu(
            speaker,
            audio,
            "Main Menu",
            [MenuItem("Start", "start"), MenuItem("Shop", "shop"), MenuItem("Quit", "quit")],
        )

        menu.open(start_index=2)
        self.assertIsNone(audio.play_calls[-1]["pan"])

        menu.handle_key(pygame.K_DOWN)
        self.assertIsNone(audio.play_calls[-1]["pan"])


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
        self.assertEqual(loaded["menu_sound_hrtf"], config_module.DEFAULT_SETTINGS["menu_sound_hrtf"])
        self.assertEqual(loaded["sapi_speech_enabled"], config_module.DEFAULT_SETTINGS["sapi_speech_enabled"])
        self.assertEqual(loaded["sapi_voice_id"], config_module.DEFAULT_SETTINGS["sapi_voice_id"])
        self.assertEqual(loaded["sapi_rate"], config_module.DEFAULT_SETTINGS["sapi_rate"])
        self.assertEqual(loaded["sapi_pitch"], config_module.DEFAULT_SETTINGS["sapi_pitch"])
        self.assertEqual(
            loaded["check_updates_on_startup"],
            config_module.DEFAULT_SETTINGS["check_updates_on_startup"],
        )
        self.assertEqual(loaded["difficulty"], "normal")

    def test_default_storage_base_dir_uses_roaming_appdata_vendor_and_game_name(self):
        with patch.dict(os.environ, {"APPDATA": r"C:\Users\Test\AppData\Roaming"}, clear=False):
            storage_path = config_module._default_storage_base_dir()

        self.assertEqual(
            storage_path,
            Path(r"C:\Users\Test\AppData\Roaming") / "Vireon Interactive" / "Subway Surfers Blind Edition",
        )

    def test_resource_path_prefers_external_resource_directory(self):
        original_resource_base_dir = config_module.RESOURCE_BASE_DIR
        original_bundled_resource_base_dir = config_module.BUNDLED_RESOURCE_BASE_DIR
        with tempfile.TemporaryDirectory() as temp_directory:
            temp_root = Path(temp_directory)
            external_assets = temp_root / "external" / "assets" / "sfx"
            bundled_assets = temp_root / "bundled" / "assets" / "sfx"
            external_assets.mkdir(parents=True, exist_ok=True)
            bundled_assets.mkdir(parents=True, exist_ok=True)
            (external_assets / "coin.wav").write_bytes(b"external")
            (bundled_assets / "coin.wav").write_bytes(b"bundled")
            config_module.RESOURCE_BASE_DIR = temp_root / "external"
            config_module.BUNDLED_RESOURCE_BASE_DIR = temp_root / "bundled"

            resolved_path = config_module.resource_path("assets", "sfx", "coin.wav")
        config_module.RESOURCE_BASE_DIR = original_resource_base_dir
        config_module.BUNDLED_RESOURCE_BASE_DIR = original_bundled_resource_base_dir

        self.assertEqual(resolved_path, str(external_assets / "coin.wav"))

    def test_load_settings_migrates_legacy_localappdata_data(self):
        original_base_dir = config_module.BASE_DIR
        original_resource_base_dir = config_module.RESOURCE_BASE_DIR
        with tempfile.TemporaryDirectory() as temp_directory:
            temp_root = Path(temp_directory)
            roaming_base_dir = temp_root / "Roaming" / "Vireon Interactive" / "Subway Surfers Blind Edition"
            legacy_local_root = temp_root / "Local" / "SubwaySurfersBlind"
            legacy_data_directory = legacy_local_root / "data"
            legacy_data_directory.mkdir(parents=True, exist_ok=True)
            legacy_settings_path = legacy_data_directory / "settings.json"
            legacy_settings_path.write_text(
                json.dumps({"sfx_volume": 0.2, "bank_coins": 321}),
                encoding="utf-8",
            )
            config_module.BASE_DIR = roaming_base_dir
            config_module.RESOURCE_BASE_DIR = temp_root / "bundle"
            with patch.dict(os.environ, {"LOCALAPPDATA": str(temp_root / "Local")}, clear=False):
                loaded = config_module.load_settings()
            migrated_settings_path = roaming_base_dir / "data" / "settings.json"
            self.assertTrue(migrated_settings_path.exists())
        config_module.BASE_DIR = original_base_dir
        config_module.RESOURCE_BASE_DIR = original_resource_base_dir

        self.assertEqual(loaded["sfx_volume"], 0.2)
        self.assertEqual(loaded["bank_coins"], 321)


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

    def test_audio_loads_standard_jetpack_loop_asset(self):
        loaded: list[tuple[str, str]] = []

        def capture_load(_self, key: str, path: str) -> None:
            loaded.append((key, path))

        with patch.object(Audio, "_load_sound", autospec=True, side_effect=capture_load), patch(
            "subway_blind.audio.OpenALHrtfEngine",
            return_value=type(
                "FakeHrtf",
                (),
                {
                    "available": False,
                    "register_sound": staticmethod(lambda *_args, **_kwargs: None),
                    "set_listener_gain": staticmethod(lambda *_args, **_kwargs: None),
                    "stop": staticmethod(lambda *_args, **_kwargs: None),
                    "play_sound": staticmethod(lambda *_args, **_kwargs: False),
                    "update_source": staticmethod(lambda *_args, **_kwargs: False),
                },
            )(),
        ):
            Audio(copy.deepcopy(config_module.DEFAULT_SETTINGS))

        jetpack_entry = next(path for key, path in loaded if key == "jetpack_loop")
        self.assertTrue(jetpack_entry.endswith("assets\\sfx\\jetpack_loop.wav"))

    def test_transient_player_channels_are_collapsed(self):
        self.assertEqual(Audio._normalize_channel_for_key("jump", "act"), "player_jump")
        self.assertEqual(Audio._normalize_channel_for_key("dodge", "move"), "player_dodge")
        self.assertEqual(Audio._normalize_channel_for_key("left_foot", "foot"), "player_footstep")
        self.assertEqual(Audio._normalize_channel_for_key("coin", "coin"), "player_pickup")
        self.assertEqual(Audio._normalize_channel_for_key("powerup", "act"), "player_power")

    def test_output_device_choices_keep_default_first_and_current_device_present(self):
        audio = Audio.__new__(Audio)
        audio.settings = {"audio_output_device": "Studio Monitor"}
        with patch("subway_blind.audio.list_output_devices", return_value=["USB DAC", "Studio Monitor"]):
            self.assertEqual(audio.output_device_choices(), [None, "USB DAC", "Studio Monitor"])

    def test_cycle_output_device_wraps_back_to_system_default(self):
        audio = Audio.__new__(Audio)
        audio.settings = {"audio_output_device": "USB DAC"}
        audio.apply_output_device = lambda device_name: device_name
        with patch.object(audio, "output_device_choices", return_value=[None, "USB DAC"]):
            requested, applied = audio.cycle_output_device()
        self.assertIsNone(requested)
        self.assertIsNone(applied)

    def test_discover_music_catalog_uses_first_matching_slot_file(self):
        audio = Audio.__new__(Audio)
        with patch("subway_blind.audio.resource_path", side_effect=lambda *parts: "/".join(parts)), patch(
            "subway_blind.audio.os.path.exists",
            side_effect=lambda path: path in {"assets/music/menu_intro.ogg", "assets/music/theme.ogg"},
        ):
            catalog = audio._discover_music_catalog()

        self.assertEqual(catalog["menu"], "assets/music/menu_intro.ogg")
        self.assertEqual(catalog["gameplay"], "assets/music/theme.ogg")

    def test_load_sound_keeps_running_when_hrtf_registration_fails(self):
        audio = Audio.__new__(Audio)
        audio.settings = {"sfx_volume": 1.0}
        audio.sounds = {}
        audio.sound_paths = {}
        audio._mixer_ready = False

        class RaisingHrtf:
            def register_sound(self, key: str, path: str) -> None:
                raise RuntimeError("boom")

        audio.hrtf = RaisingHrtf()

        with patch("subway_blind.audio.os.path.exists", return_value=True):
            audio._load_sound("coin", "coin.wav")

        self.assertEqual(audio.sound_paths["coin"], "coin.wav")

    def test_update_starts_pending_track_after_music_fades_out(self):
        audio = Audio.__new__(Audio)
        audio._mixer_ready = True
        audio._music_transition = "fade_out"
        audio._music_current_track = "menu"
        audio._music_pending_track = "gameplay"
        audio._music_fade_level = 0.05
        audio._apply_music_volume = lambda: None
        played_tracks: list[str] = []

        def stop_music_immediately() -> None:
            audio._music_current_track = None
            audio._music_pending_track = None
            audio._music_fade_level = 0.0
            audio._music_transition = None

        audio._stop_music_immediately = stop_music_immediately
        audio._play_music_track = lambda track_key: played_tracks.append(track_key) or True

        audio.update(1.0)

        self.assertEqual(played_tracks, ["gameplay"])


class UpdaterTests(unittest.TestCase):
    def test_normalize_version_handles_semver_and_v_prefix(self):
        self.assertEqual(normalize_version("v1.2.3"), "1.2.3")
        self.assertEqual(normalize_version("2.0"), "2.0.0")

    def test_version_key_orders_versions_correctly(self):
        self.assertGreater(version_key("1.4.0"), version_key("1.3.9"))
        self.assertEqual(version_key("v2.0"), (2, 0, 0))

    def test_check_for_updates_returns_update_available_when_release_is_newer(self):
        updater = GitHubReleaseUpdater(timeout_seconds=2.0)
        release_payload = {
            "tag_name": "v0.2.0",
            "name": "v0.2.0",
            "html_url": "https://github.com/oguzhanproductions/subway_surfers_blind/releases/tag/v0.2.0",
            "published_at": "2026-03-08T10:00:00Z",
            "body": "Notes",
            "assets": [
                {
                    "name": "SubwaySurfersBlind.zip",
                    "browser_download_url": "https://example.com/SubwaySurfersBlind.zip",
                    "content_type": "application/zip",
                    "size": 2048,
                }
            ],
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(release_payload).encode("utf-8")

        with patch("subway_blind.updater.urllib.request.urlopen", return_value=FakeResponse()):
            result = updater.check_for_updates(APP_VERSION)

        self.assertTrue(result.update_available)
        self.assertEqual(result.latest_version, "0.2.0")
        self.assertEqual(result.release.assets[0].name, "SubwaySurfersBlind.zip")


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

    def test_sapi_rate_combines_manual_setting_with_speed_factor(self):
        class FakeSapiVoice:
            def __init__(self):
                self.Rate = 0

        speaker = Speaker(enabled=False, sapi_rate=3)
        speaker.enabled = True
        speaker._sapi_voice = FakeSapiVoice()

        speaker.set_speed_factor(1.0)

        self.assertEqual(speaker._sapi_voice.Rate, 7)

    def test_sapi_speak_wraps_text_in_pitch_xml(self):
        class FakeSapiVoice:
            def __init__(self):
                self.calls: list[tuple[str, int]] = []

            def Speak(self, text: str, flags: int) -> None:
                self.calls.append((text, flags))

        speaker = Speaker(enabled=False, use_sapi=True, sapi_pitch=4)
        speaker.enabled = True
        speaker._sapi_voice = FakeSapiVoice()

        speaker.speak("Ready & go", interrupt=True)

        text, flags = speaker._sapi_voice.calls[-1]
        self.assertEqual(text, '<pitch middle="+4">Ready &amp; go</pitch>')
        self.assertTrue(flags & SAPI_SPEAK_IS_XML)



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
    def _write_wav(self, path: Path, channels: int) -> None:
        with wave.open(str(path), "wb") as writer:
            writer.setnchannels(channels)
            writer.setsampwidth(2)
            writer.setframerate(44100)
            writer.writeframes((b"\x00\x00" * channels) * 64)

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

    def test_prepare_openal_path_stages_unicode_wav_into_ascii_cache(self):
        engine = OpenALHrtfEngine.__new__(OpenALHrtfEngine)
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            source_directory = root / ("profile_" + chr(0x0130))
            source_directory.mkdir(parents=True, exist_ok=True)
            program_data = root / "ProgramData"
            source_path = source_directory / "coin.wav"
            self._write_wav(source_path, channels=2)

            with patch("subway_blind.hrtf_audio.BASE_DIR", source_directory), patch.dict(
                os.environ,
                {"PROGRAMDATA": str(program_data)},
                clear=False,
            ):
                prepared_path = Path(engine._prepare_openal_path(source_path))

            self.assertNotEqual(prepared_path, source_path)
            self.assertTrue(prepared_path.exists())
            self.assertTrue(str(prepared_path).isascii())
            with wave.open(str(prepared_path), "rb") as reader:
                self.assertEqual(reader.getnchannels(), 1)

            try:
                import pyopenalsoft as openal
            except Exception:
                openal = None

            if openal is not None and os.name == "nt":
                with self.assertRaises(RuntimeError):
                    openal.AudioData(str(source_path))
                self.assertEqual(openal.AudioData(str(prepared_path)).channels, 1)

    def test_register_sound_returns_without_raising_when_openal_load_fails(self):
        class FailingOpenALModule:
            def AudioData(self, path: str):
                raise RuntimeError("invalid audio")

            def Buffer(self, audio_data):
                raise AssertionError("Buffer should not be created when AudioData fails")

        with tempfile.TemporaryDirectory() as temp_root:
            source_path = Path(temp_root) / "coin.wav"
            self._write_wav(source_path, channels=1)
            engine = OpenALHrtfEngine.__new__(OpenALHrtfEngine)
            engine.available = True
            engine._al = FailingOpenALModule()
            engine._buffers = {}
            engine._buffer_paths = {}
            engine._sources = {}
            engine._channel_keys = {}
            engine._listener_gain = 1.0

            engine.register_sound("coin", str(source_path))

            self.assertNotIn("coin", engine._buffers)
            self.assertNotIn("coin", engine._buffer_paths)


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

        self.assertEqual(cue.prompt, "roll now")
        self.assertLess(cue.interval, 0.5)
        self.assertGreater(cue.gain, 0.7)

    def test_prompt_is_announced_earlier_for_current_lane_train(self):
        engine = SpatialThreatAudio()
        obstacle = Obstacle(kind="train", lane=0, z=14.5)

        cue = engine.build_threat_cues(0, 20.0, [obstacle])[0]

        self.assertEqual(cue.prompt, "turn left now")

    def test_prompt_shortens_at_high_speed(self):
        engine = SpatialThreatAudio()
        obstacle = Obstacle(kind="high", lane=0, z=15.5)

        cue = engine.build_threat_cues(0, 33.9, [obstacle])[0]

        self.assertEqual(cue.prompt, "roll")

    def test_center_lane_train_prefers_clearer_escape_side(self):
        engine = SpatialThreatAudio()
        obstacles = [
            Obstacle(kind="train", lane=0, z=12.0),
            Obstacle(kind="high", lane=-1, z=6.0),
            Obstacle(kind="low", lane=1, z=18.0),
        ]

        cue = next(cue for cue in engine.build_threat_cues(0, 20.0, obstacles) if cue.lane == 0)

        self.assertEqual(cue.prompt, "turn right now")

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
        self.assertLess(velocity_z, 0.0)

    def test_critical_prompt_interrupts_current_screen_reader_speech(self):
        engine = SpatialThreatAudio()
        audio = DummyAudio({})
        speaker = DummySpeaker()
        obstacle = Obstacle(kind="high", lane=0, z=7.0)

        engine.update(0.1, 0, 20.0, [obstacle], audio, speaker)

        self.assertIn(("roll now", True), speaker.messages)

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
        self.assertGreater(cue.velocity_z, 0.0)
        self.assertIsNone(cue.prompt)

    def test_obstacle_height_changes_vertical_spatial_position(self):
        engine = SpatialThreatAudio()

        high_cue = engine.build_threat_cues(0, 20.0, [Obstacle(kind="high", lane=0, z=6.0)])[0]
        low_cue = engine.build_threat_cues(0, 20.0, [Obstacle(kind="low", lane=0, z=6.0)])[0]

        self.assertGreater(high_cue.source_y, 0.0)
        self.assertLess(low_cue.source_y, 0.0)


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

        with patch("subway_blind.spawn.random.choice", return_value=-1), patch(
            "subway_blind.spawn.random.choices",
            return_value=[1],
        ):
            self.assertEqual(director.support_lane(0), 1)

    def test_support_lane_can_spawn_in_front_of_current_lane(self):
        director = SpawnDirector()
        director.last_safe_lane = 0

        with patch("subway_blind.spawn.random.choice", return_value=-1), patch(
            "subway_blind.spawn.random.choices",
            return_value=[1],
        ):
            self.assertEqual(director.support_lane(1), 1)

    def test_candidate_patterns_expand_single_lane_templates_across_all_lanes(self):
        director = SpawnDirector()

        candidates = director.candidate_patterns(0.0)
        single_train_variants = [pattern for pattern in candidates if pattern.name.startswith("single_train:")]

        self.assertEqual({pattern.entries[0].lane for pattern in single_train_variants}, {-1, 0, 1})

    def test_easy_difficulty_filters_out_harder_patterns_at_same_progress(self):
        director = SpawnDirector()

        easy_candidates = director.candidate_patterns(0.4, difficulty="easy")
        normal_candidates = director.candidate_patterns(0.4, difficulty="normal")

        easy_names = {pattern.name.split(":")[0] for pattern in easy_candidates}
        normal_names = {pattern.name.split(":")[0] for pattern in normal_candidates}

        self.assertNotIn("stagger_jump_route", easy_names)
        self.assertIn("stagger_jump_route", normal_names)

    def test_easy_difficulty_spaces_encounters_farther_than_hard(self):
        director = SpawnDirector()

        with patch("subway_blind.spawn.random.uniform", return_value=1.5):
            easy_gap = director.next_encounter_gap(0.5, difficulty="easy")
            hard_gap = director.next_encounter_gap(0.5, difficulty="hard")

        self.assertGreater(easy_gap, hard_gap)

    def test_transformed_pattern_updates_safe_lanes_with_lane_shift(self):
        shifted = SpawnDirector._transform_pattern(PATTERNS[0], 1, -1)

        self.assertIsNotNone(shifted)
        self.assertEqual(tuple(entry.lane for entry in shifted.entries), (-1,))
        self.assertEqual(shifted.safe_lanes, (0, 1))

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

    def make_game(self, updater: DummyUpdater | None = None, packaged_build: bool = False):
        settings = copy.deepcopy(config_module.DEFAULT_SETTINGS)
        settings["speech_enabled"] = False
        game = SubwayBlindGame(
            self.screen,
            pygame.time.Clock(),
            settings,
            updater=updater or DummyUpdater(),
            packaged_build=packaged_build,
        )
        speaker = DummySpeaker()
        speaker.apply_settings(settings)
        audio = DummyAudio(settings)
        game.speaker = speaker
        game.audio = audio
        for menu in (
            game.main_menu,
            game.shop_menu,
            game.options_menu,
            game.controls_menu,
            game.keyboard_bindings_menu,
            game.controller_bindings_menu,
            game.pause_menu,
            game.pause_confirm_menu,
            game.loadout_menu,
            game.revive_menu,
            game.learn_sounds_menu,
            game.update_menu,
            game.game_over_menu,
        ):
            menu.speaker = speaker
            menu.audio = audio
        game._sync_music_context()
        return game, speaker, audio

    def attach_controller(self, game: SubwayBlindGame, family: str = XBOX_FAMILY, name: str | None = None, instance_id: int = 41):
        controller_name = name or family_label(family)
        device = DummyControllerDevice(controller_name)
        game.controls.connected[instance_id] = ConnectedController(
            instance_id=instance_id,
            name=controller_name,
            family=family,
            controller=device,
        )
        game.controls.active_controller_instance_id = instance_id
        game._refresh_control_menus()
        return device

    def test_main_menu_is_english(self):
        game, _, _ = self.make_game()
        self.assertEqual(game.main_menu.title, f"Main Menu   Version: {APP_VERSION}")
        self.assertEqual(
            [item.label for item in game.main_menu.items],
            ["Start Game", "Shop", "Options", "How to Play", "Learn Game Sounds", "Check for Updates", "Exit"],
        )

    def test_options_menu_includes_output_device_entry(self):
        game, _, _ = self.make_game()
        labels = [item.label for item in game.options_menu.items]
        self.assertEqual(
            labels[:7] + labels[8:],
            [
                "SFX Volume: 90",
                "Music Volume: 60",
                "Check for Updates on Startup: On",
                "Output Device: System Default",
                "Menu Sound HRTF: On",
                "Speech: Off",
                "SAPI Speech: Off",
                "SAPI Rate: 0",
                "SAPI Pitch: 0",
                "Difficulty: Normal",
                "Controls",
                "Back",
            ],
        )
        self.assertTrue(labels[7].startswith("SAPI Voice: Microsoft "))

    def test_shop_menu_labels_include_coin_currency(self):
        game, _, _ = self.make_game()
        self.assertEqual(
            [item.label for item in game.shop_menu.items],
            [
                f"Buy Hoverboard   Cost: {SHOP_PRICES['hoverboard']} Coins   Owned: 3",
                f"Open Mystery Box   Cost: {SHOP_PRICES['mystery_box']} Coins",
                f"Buy Headstart   Cost: {SHOP_PRICES['headstart']} Coins   Owned: 2",
                f"Buy Score Booster   Cost: {SHOP_PRICES['score_booster']} Coins   Owned: 3",
                "Back",
            ],
        )

    def test_game_starts_with_menu_music_request(self):
        game, _, audio = self.make_game()

        self.assertIs(game.active_menu, game.main_menu)
        self.assertEqual(audio.music_started_tracks[-1], "menu")

    def test_startup_update_check_runs_when_setting_is_enabled(self):
        updater = DummyUpdater()

        game, _, _ = self.make_game(updater=updater, packaged_build=True)

        self.assertIs(game.active_menu, game.main_menu)
        self.assertEqual(updater.check_calls, 1)

    def test_startup_update_check_opens_mandatory_update_menu_for_newer_release(self):
        updater = DummyUpdater()
        updater.check_results = [
            UpdateCheckResult(
                status="update_available",
                current_version=APP_VERSION,
                latest_version="0.2.0",
                release=make_release_info("0.2.0"),
                message="Version 0.2.0 is available.",
            )
        ]

        game, _, _ = self.make_game(updater=updater, packaged_build=True)

        self.assertIs(game.active_menu, game.update_menu)
        self.assertTrue(game.update_menu.title.startswith("Update Required"))

    def test_source_build_skips_startup_update_check(self):
        updater = DummyUpdater()

        game, _, _ = self.make_game(updater=updater, packaged_build=False)

        self.assertIs(game.active_menu, game.main_menu)
        self.assertEqual(updater.check_calls, 0)

    def test_manual_check_for_updates_opens_update_menu_when_update_exists(self):
        updater = DummyUpdater()
        updater.check_results = [
            UpdateCheckResult(
                status="no_releases",
                current_version=APP_VERSION,
                message="No published releases were found.",
            ),
            UpdateCheckResult(
                status="update_available",
                current_version=APP_VERSION,
                latest_version="0.2.0",
                release=make_release_info("0.2.0"),
                message="Version 0.2.0 is available.",
            ),
        ]
        game, _, _ = self.make_game(updater=updater, packaged_build=True)

        game._handle_menu_action("check_updates")

        self.assertIs(game.active_menu, game.update_menu)
        self.assertEqual(game._update_release_notes, "Important fixes.")

    def test_manual_check_for_updates_does_not_play_a_second_confirm_after_response(self):
        updater = DummyUpdater()
        updater.check_results = [
            UpdateCheckResult(
                status="no_releases",
                current_version=APP_VERSION,
                message="No published releases were found.",
            ),
            UpdateCheckResult(
                status="up_to_date",
                current_version=APP_VERSION,
                latest_version=APP_VERSION,
                release=make_release_info(APP_VERSION),
                message="You already have the latest version.",
            ),
        ]
        game, _, audio = self.make_game(updater=updater, packaged_build=True)
        game.active_menu = game.main_menu
        game.main_menu.index = 5

        game._handle_active_menu_key(pygame.K_RETURN)

        confirm_plays = [call for call in audio.played if call[0] == "confirm"]
        self.assertEqual(len(confirm_plays), 1)

    def test_mandatory_update_download_action_launches_update_and_requests_exit(self):
        updater = DummyUpdater()
        updater.check_results = [
            UpdateCheckResult(
                status="update_available",
                current_version=APP_VERSION,
                latest_version="0.2.0",
                release=make_release_info("0.2.0"),
                message="Version 0.2.0 is available.",
            )
        ]
        game, _, _ = self.make_game(updater=updater, packaged_build=True)

        keep_running = game._handle_menu_action("download_update")
        if game._update_install_thread is not None:
            game._update_install_thread.join(timeout=1.0)
        game._update_update_install_state()

        self.assertTrue(keep_running)
        self.assertEqual(len(updater.download_calls), 1)
        self.assertEqual(game.update_menu.items[0].action, "restart_after_update")

    def test_restart_after_update_uses_restart_script_and_requests_exit(self):
        updater = DummyUpdater()
        updater.check_results = [
            UpdateCheckResult(
                status="update_available",
                current_version=APP_VERSION,
                latest_version="0.2.0",
                release=make_release_info("0.2.0"),
                message="Version 0.2.0 is available.",
            )
        ]
        game, _, _ = self.make_game(updater=updater, packaged_build=True)
        if game._update_install_thread is not None:
            game._update_install_thread.join(timeout=1.0)
        game._update_install_result = updater.install_result
        game._update_restart_script_path = updater.install_result.restart_script_path
        game.update_menu.items[0].action = "restart_after_update"

        keep_running = game._handle_menu_action("restart_after_update")

        self.assertFalse(keep_running)
        self.assertEqual(updater.launch_restart_calls[-1], updater.install_result.restart_script_path)

    def test_source_build_manual_update_check_opens_non_mandatory_update_menu(self):
        updater = DummyUpdater()
        updater.check_results = [
            UpdateCheckResult(
                status="update_available",
                current_version=APP_VERSION,
                latest_version="0.2.0",
                release=make_release_info("0.2.0"),
                message="Version 0.2.0 is available.",
            )
        ]

        game, _, _ = self.make_game(updater=updater, packaged_build=False)

        game._handle_menu_action("check_updates")

        self.assertIs(game.active_menu, game.update_menu)
        self.assertEqual(game.update_menu.title, "Update Available   0.1.0 -> 0.2.0")
        self.assertEqual(game.update_menu.items[0].action, "open_release_page")
        self.assertEqual(game.update_menu.items[2].action, "back")

    def test_start_run_uses_profile_base_speed(self):
        game, _, audio = self.make_game()
        game.settings["difficulty"] = "hard"

        game.start_run()

        self.assertEqual(game.state.speed, SPEED_PROFILES["hard"].base_speed)
        self.assertEqual(audio.music_started_tracks[-1], "gameplay")

    def test_start_run_includes_permanent_mission_multiplier_bonus(self):
        game, _, _ = self.make_game()
        game.settings["mission_multiplier_bonus"] = 4

        game.start_run()

        self.assertEqual(game.state.multiplier, 5)

    def test_start_run_with_headstart_plays_intro_headstart_sounds(self):
        game, _, audio = self.make_game()
        game.settings["headstarts"] = 2
        game.selected_headstarts = 1

        game.start_run()

        self.assertIn(("intro_shake", HEADSTART_SHAKE_CHANNEL, True), audio.played)
        self.assertIn(("intro_spray", HEADSTART_SPRAY_CHANNEL, True), audio.played)
        self.assertGreater(game.player.headstart, 0.0)

    def test_headstart_audio_stops_when_effect_expires(self):
        game, _, audio = self.make_game()
        game.settings["headstarts"] = 1
        game.selected_headstarts = 1

        game.start_run()
        game._update_game(headstart_duration_for_uses(1) + 0.1)

        self.assertIn(HEADSTART_SHAKE_CHANNEL, audio.stopped)
        self.assertIn(HEADSTART_SPRAY_CHANNEL, audio.stopped)
        self.assertEqual(game.player.headstart, 0.0)

    def test_start_action_opens_run_setup_menu(self):
        game, _, _ = self.make_game()

        game._handle_menu_action("start")

        self.assertIs(game.active_menu, game.loadout_menu)
        self.assertEqual(game.loadout_menu.title, "Run Setup")

    def test_learn_sounds_action_opens_sound_menu(self):
        game, _, _ = self.make_game()

        game._handle_menu_action("learn_sounds")

        self.assertIs(game.active_menu, game.learn_sounds_menu)
        self.assertEqual(game.learn_sounds_menu.title, "Learn Game Sounds")

    def test_enter_on_learn_sound_plays_preview_and_speaks_description(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.learn_sounds_menu
        game.learn_sounds_menu.index = 0
        game._refresh_learn_sound_description()

        result = game._handle_active_menu_key(pygame.K_RETURN)

        self.assertTrue(result)
        self.assertIn(("coin", LEARN_SOUND_PREVIEW_CHANNEL, False), audio.played)
        self.assertTrue(speaker.messages[-1][0].startswith("Coin Pickup."))
        self.assertEqual(game._learn_sound_description, "Plays when you collect a coin on the track.")

    def test_learn_sound_loop_preview_stops_after_timeout(self):
        game, _, audio = self.make_game()
        game.active_menu = game.learn_sounds_menu
        game.learn_sounds_menu.index = next(
            index for index, item in enumerate(game.learn_sounds_menu.items) if item.action == "learn_sound:guard_loop"
        )

        game._handle_active_menu_key(pygame.K_RETURN)
        game._update_learn_sound_preview(LEARN_SOUND_LOOP_PREVIEW_DURATION + 0.1)

        self.assertIn(( "guard_loop", LEARN_SOUND_PREVIEW_CHANNEL, True), audio.played)
        self.assertIn(LEARN_SOUND_PREVIEW_CHANNEL, audio.stopped)

    def test_learn_sounds_back_stops_preview_and_returns_to_main_menu(self):
        game, _, audio = self.make_game()
        game._set_active_menu(game.learn_sounds_menu)
        game.learn_sounds_menu.index = next(
            index for index, item in enumerate(game.learn_sounds_menu.items) if item.action == "learn_sound:magnet_loop"
        )

        game._handle_active_menu_key(pygame.K_RETURN)
        result = game._handle_active_menu_key(pygame.K_ESCAPE)

        self.assertTrue(result)
        self.assertIs(game.active_menu, game.main_menu)
        self.assertIn(LEARN_SOUND_PREVIEW_CHANNEL, audio.stopped)

    def test_learn_sounds_menu_contains_only_active_gameplay_sound_entries(self):
        game, _, _ = self.make_game()

        actions = [item.action for item in game.learn_sounds_menu.items]

        self.assertEqual(actions[:-1], [f"learn_sound:{key}" for key in ACTIVE_GAMEPLAY_SOUND_KEYS])
        self.assertEqual(actions[-1], "back")
        self.assertNotIn("learn_sound:menuopen", actions)

    def test_headstart_adds_speed_bonus_and_consumes_inventory(self):
        game, _, _ = self.make_game()
        game.selected_headstarts = 1
        starting_inventory = game.settings["headstarts"]

        game.start_run()
        game._update_game(0.5)

        self.assertEqual(game.settings["headstarts"], starting_inventory - 1)
        self.assertGreaterEqual(game.state.speed, SPEED_PROFILES["normal"].base_speed + HEADSTART_SPEED_BONUS)

    def test_end_run_banks_coins_and_plays_bank_sounds(self):
        game, _, audio = self.make_game()
        game.start_run()
        game.state.coins = 37
        game.settings["bank_coins"] = 12

        game.end_run(to_menu=True)

        self.assertEqual(game.settings["bank_coins"], 49)
        self.assertIn(("coin_gui", "ui", False), audio.played)
        self.assertIn(("gui_cash", "ui2", False), audio.played)
        self.assertEqual(audio.music_started_tracks[-1], "menu")

    def test_multiple_headstarts_extend_start_duration_and_consume_all_selected_charges(self):
        game, _, _ = self.make_game()
        game.settings["headstarts"] = 3
        game.selected_headstarts = 3

        game.start_run()

        self.assertEqual(game.settings["headstarts"], 0)
        self.assertEqual(game.player.headstart, headstart_duration_for_uses(3))

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
        ), patch.object(game.spawn_director, "choose_coin_lane", return_value=1), patch.object(
            game,
            "_choose_support_spawn_kind",
            return_value="key",
        ), patch.object(game.spawn_director, "support_lane", return_value=1):
            game._spawn_things(0.016)

        hazards = [obstacle for obstacle in game.obstacles if obstacle.kind in {"train", "low", "high"}]
        coins = [obstacle for obstacle in game.obstacles if obstacle.kind == "coin"]
        keys = [obstacle for obstacle in game.obstacles if obstacle.kind == "key"]

        self.assertEqual(len(hazards), 2)
        self.assertEqual({obstacle.lane for obstacle in hazards}, {-1, 1})
        self.assertEqual(len(coins), 6)
        self.assertTrue(all(obstacle.lane == 1 for obstacle in coins))
        self.assertEqual(len(keys), 1)
        self.assertEqual(keys[0].lane, 1)

    def test_update_game_clamps_invalid_player_lane_back_onto_track(self):
        game, _, _ = self.make_game()
        game.player.lane = 4

        game._update_game(0.016)

        self.assertEqual(game.player.lane, 1)

    def test_spawn_things_delays_when_existing_hazard_is_too_close(self):
        game, _, _ = self.make_game()
        game.start_run()
        game.state.next_spawn = 0.0
        game.obstacles.append(Obstacle(kind="train", lane=0, z=10.0))

        game._spawn_things(0.016)

        self.assertAlmostEqual(game.state.next_spawn, 0.3)

    def test_adjust_selected_option_changes_sfx_with_right_arrow(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 0
        game.settings["sfx_volume"] = 0.4

        game._adjust_selected_option(1)

        self.assertEqual(game.settings["sfx_volume"], 0.5)
        self.assertEqual(game.options_menu.items[0].label, "SFX Volume: 50")
        self.assertEqual(audio.refreshed, 1)
        self.assertEqual(speaker.messages[-1][0], "SFX Volume: 50")
        self.assertIsNotNone(audio.play_calls[-1]["pan"])

    def test_adjust_selected_option_changes_music_with_left_arrow(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 1
        game.settings["music_volume"] = 0.6

        game._adjust_selected_option(-1)

        self.assertEqual(game.settings["music_volume"], 0.5)
        self.assertEqual(game.options_menu.items[1].label, "Music Volume: 50")
        self.assertEqual(audio.refreshed, 1)
        self.assertEqual(speaker.messages[-1][0], "Music Volume: 50")

    def test_adjust_selected_option_toggles_startup_update_checks(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 2
        game.settings["check_updates_on_startup"] = True

        game._adjust_selected_option(-1)

        self.assertFalse(game.settings["check_updates_on_startup"])
        self.assertIn(("confirm", "ui", False), audio.played)
        self.assertEqual(speaker.messages[-1][0], "Check for Updates on Startup: Off")

    def test_adjust_selected_option_cycles_output_device_in_place(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 3

        game._adjust_selected_option(1)

        self.assertEqual(game.settings["audio_output_device"], "External USB Headphones")
        self.assertEqual(game.options_menu.items[3].label, "Output Device: External USB Headphones")
        self.assertEqual(speaker.messages[-1][0], "Output device set to External USB Headphones.")

    def test_enter_does_nothing_in_options_menu(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 3

        result = game._handle_active_menu_key(pygame.K_RETURN)

        self.assertTrue(result)
        self.assertIs(game.active_menu, game.options_menu)
        self.assertEqual(game.settings["audio_output_device"], "")
        self.assertEqual(audio.played, [])
        self.assertEqual(speaker.messages, [])

    def test_adjust_selected_option_on_back_only_plays_edge_feedback(self):
        game, _, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 12

        game._adjust_selected_option(1)

        self.assertEqual(audio.played, [])
        self.assertIs(game.active_menu, game.options_menu)

    def test_enter_on_back_returns_to_main_menu_from_options(self):
        game, _, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 12

        result = game._handle_active_menu_key(pygame.K_RETURN)

        self.assertTrue(result)
        self.assertIs(game.active_menu, game.main_menu)
        self.assertEqual(game.main_menu.index, 0)
        self.assertIn(("menuclose", "ui", False), audio.played)

    def test_controls_menu_defaults_to_keyboard_without_controller(self):
        game, _, _ = self.make_game()

        game._refresh_control_menus()

        self.assertEqual(
            [item.label for item in game.controls_menu.items],
            [
                "Active Input: Keyboard",
                "Binding Profile: Keyboard",
                "Customize Bindings",
                "Reset Keyboard",
                "Back",
            ],
        )

    def test_controls_menu_defaults_to_connected_controller_profile(self):
        game, _, _ = self.make_game()
        self.attach_controller(game, family=PLAYSTATION_FAMILY, name="Wireless Controller")
        game._selected_binding_device = "controller"

        game._refresh_control_menus()

        self.assertEqual(
            [item.label for item in game.controls_menu.items],
            [
                "Active Input: Keyboard",
                "Binding Profile: PlayStation Controller",
                "Customize Bindings",
                "Reset PlayStation Controller",
                "Back",
            ],
        )

    def test_options_controls_entry_opens_controls_menu(self):
        game, _, _ = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 11

        result = game._handle_active_menu_key(pygame.K_RETURN)

        self.assertTrue(result)
        self.assertIs(game.active_menu, game.controls_menu)
        self.assertEqual(game.controls_menu.items[1].label, "Binding Profile: Keyboard")

    def test_options_controls_entry_prefers_controller_profile_when_connected(self):
        game, _, _ = self.make_game()
        self.attach_controller(game, family=PLAYSTATION_FAMILY, name="Wireless Controller")
        game.active_menu = game.options_menu
        game.options_menu.index = 11

        result = game._handle_active_menu_key(pygame.K_RETURN)

        self.assertTrue(result)
        self.assertIs(game.active_menu, game.controls_menu)
        self.assertEqual(game.controls_menu.items[1].label, "Binding Profile: PlayStation Controller")

    def test_controls_menu_can_switch_binding_profile_like_options(self):
        game, speaker, _ = self.make_game()
        self.attach_controller(game, family=PLAYSTATION_FAMILY, name="Wireless Controller")
        game.active_menu = game.controls_menu
        game.controls_menu.index = 1
        game._selected_binding_device = "keyboard"
        game._build_controls_menu()

        game._handle_active_menu_key(pygame.K_RIGHT)

        self.assertEqual(game.controls_menu.items[1].label, "Binding Profile: PlayStation Controller")
        self.assertEqual(speaker.messages[-1][0], "Binding Profile: PlayStation Controller")

    def test_controls_menu_customize_uses_selected_controller_profile(self):
        game, _, _ = self.make_game()
        self.attach_controller(game, family=PLAYSTATION_FAMILY, name="Wireless Controller")
        game.active_menu = game.controls_menu
        game._selected_binding_device = "controller"
        game._build_controls_menu()
        game.controls_menu.index = 2

        result = game._handle_active_menu_key(pygame.K_RETURN)

        self.assertTrue(result)
        self.assertIs(game.active_menu, game.controller_bindings_menu)

    def test_keyboard_binding_capture_updates_menu_confirm(self):
        game, speaker, _ = self.make_game()
        game._build_keyboard_bindings_menu()
        game.active_menu = game.keyboard_bindings_menu

        game._begin_binding_capture("keyboard", "menu_confirm")
        game._handle_keyboard_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_f}))

        self.assertEqual(game.controls.keyboard_binding_for_action("menu_confirm"), pygame.K_f)
        self.assertIn(("Confirm set to F.", True), speaker.messages)

    def test_remapped_menu_up_uses_new_key_only(self):
        game, _, _ = self.make_game()
        game.controls.update_keyboard_binding("menu_up", pygame.K_j)
        game.active_menu = game.main_menu
        game.main_menu.index = 1

        game._handle_keyboard_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_UP}))
        self.assertEqual(game.main_menu.index, 1)

        game._handle_keyboard_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_j}))
        self.assertEqual(game.main_menu.index, 0)

    def test_remapped_menu_confirm_disables_enter(self):
        game, _, audio = self.make_game()
        game.controls.update_keyboard_binding("menu_confirm", pygame.K_f)
        game.active_menu = game.main_menu
        game.main_menu.index = 0

        game._handle_keyboard_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RETURN}))
        self.assertIs(game.active_menu, game.main_menu)
        self.assertNotIn(("confirm", "ui", False), audio.played)

        game._handle_keyboard_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_f}))
        self.assertIs(game.active_menu, game.loadout_menu)

    def test_remapped_option_adjustment_disables_old_arrow(self):
        game, _, _ = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 0
        game.settings["sfx_volume"] = 0.4
        game.controls.update_keyboard_binding("option_increase", pygame.K_l)

        game._handle_keyboard_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_RIGHT}))
        self.assertEqual(game.settings["sfx_volume"], 0.4)

        game._handle_keyboard_event(pygame.event.Event(pygame.KEYDOWN, {"key": pygame.K_l}))
        self.assertEqual(game.settings["sfx_volume"], 0.5)

    def test_controller_binding_capture_updates_playstation_jump_label(self):
        game, speaker, _ = self.make_game()
        self.attach_controller(game, family=PLAYSTATION_FAMILY, name="Wireless Controller")
        game._build_controller_bindings_menu()
        game.active_menu = game.controller_bindings_menu

        game._begin_binding_capture("controller", "game_jump")
        game._handle_controller_event(
            pygame.event.Event(
                pygame.CONTROLLERBUTTONDOWN,
                {"instance_id": 41, "button": pygame.CONTROLLER_BUTTON_X},
            )
        )

        self.assertEqual(game.controls.controller_binding_for_action("game_jump", PLAYSTATION_FAMILY), "button:x")
        self.assertIn(("Jump set to Square.", True), speaker.messages)

    def test_controller_a_button_triggers_jump_in_gameplay(self):
        game, _, _ = self.make_game()
        self.attach_controller(game, family=XBOX_FAMILY, name="Xbox Wireless Controller")
        game.start_run()

        game._handle_controller_event(
            pygame.event.Event(
                pygame.CONTROLLERBUTTONDOWN,
                {"instance_id": 41, "button": pygame.CONTROLLER_BUTTON_A},
            )
        )

        self.assertGreater(game.player.vy, 0.0)

    def test_controller_left_stick_moves_player_left(self):
        game, _, _ = self.make_game()
        self.attach_controller(game, family=XBOX_FAMILY, name="Xbox Wireless Controller")
        game.start_run()

        game._handle_controller_event(
            pygame.event.Event(
                pygame.CONTROLLERAXISMOTION,
                {"instance_id": 41, "axis": pygame.CONTROLLER_AXIS_LEFTX, "value": -0.95},
            )
        )

        self.assertEqual(game.player.lane, -1)

    def test_menu_hint_uses_playstation_labels_after_controller_input(self):
        game, _, _ = self.make_game()
        self.attach_controller(game, family=PLAYSTATION_FAMILY, name="Wireless Controller")
        game.controls.last_input_source = "controller"

        hint_text = game._menu_navigation_hint()

        self.assertEqual(hint_text, "Use D-Pad Up/D-Pad Down, Cross to select, Circle to go back.")

    def test_adjust_selected_option_toggles_menu_sound_hrtf(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 4
        game.settings["menu_sound_hrtf"] = True

        game._adjust_selected_option(-1)

        self.assertFalse(game.settings["menu_sound_hrtf"])
        self.assertIn(("confirm", "ui", False), audio.played)
        self.assertEqual(speaker.messages[-1][0], "Menu Sound HRTF: Off")

    def test_adjust_selected_option_sets_speech_state_from_direction(self):
        game, speaker, _ = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 5
        game.settings["speech_enabled"] = True
        game.speaker.enabled = True

        game._adjust_selected_option(-1)

        self.assertFalse(game.settings["speech_enabled"])
        self.assertFalse(game.speaker.enabled)
        self.assertEqual(speaker.messages[-1][0], "Speech: Off")

    def test_adjust_selected_option_toggles_sapi_speech(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 6
        game.settings["speech_enabled"] = True
        game.settings["sapi_speech_enabled"] = False

        game._adjust_selected_option(1)

        self.assertTrue(game.settings["sapi_speech_enabled"])
        self.assertTrue(speaker.use_sapi)
        self.assertIn(("confirm", "ui", False), audio.played)
        self.assertEqual(speaker.messages[-1][0], "SAPI Speech: On")

    def test_adjust_selected_option_cycles_sapi_voice(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 7

        game._adjust_selected_option(1)

        self.assertEqual(game.settings["sapi_voice_id"], "voice-david")
        self.assertEqual(game.options_menu.items[7].label, "SAPI Voice: Microsoft David Desktop - English (United States)")
        self.assertIn(("confirm", "ui", False), audio.played)
        self.assertEqual(speaker.messages[-1][0], "SAPI Voice: Microsoft David Desktop - English (United States)")

    def test_adjust_selected_option_changes_sapi_rate(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 8
        game.settings["sapi_rate"] = 0

        game._adjust_selected_option(1)

        self.assertEqual(game.settings["sapi_rate"], 1)
        self.assertEqual(speaker.sapi_rate, 1)
        self.assertEqual(game.options_menu.items[8].label, "SAPI Rate: 1")
        self.assertIn(("confirm", "ui", False), audio.played)
        self.assertEqual(speaker.messages[-1][0], "SAPI Rate: 1")

    def test_adjust_selected_option_changes_sapi_pitch(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 9
        game.settings["sapi_pitch"] = 0

        game._adjust_selected_option(-1)

        self.assertEqual(game.settings["sapi_pitch"], -1)
        self.assertEqual(speaker.sapi_pitch, -1)
        self.assertEqual(game.options_menu.items[9].label, "SAPI Pitch: -1")
        self.assertIn(("confirm", "ui", False), audio.played)
        self.assertEqual(speaker.messages[-1][0], "SAPI Pitch: -1")

    def test_adjust_selected_option_cycles_difficulty_backward(self):
        game, speaker, audio = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 10
        game.settings["difficulty"] = "normal"

        game._adjust_selected_option(-1)

        self.assertEqual(game.settings["difficulty"], "easy")
        self.assertIn(("confirm", "ui", False), audio.played)
        self.assertEqual(speaker.messages[-1][0], "Difficulty: Easy")

    def test_menu_repeat_moves_quickly_after_hold_delay(self):
        game, speaker, _ = self.make_game()
        game.active_menu = game.main_menu
        game.main_menu.index = 0

        game._prime_menu_repeat(pygame.K_DOWN)
        game._update_menu_repeat(MENU_REPEAT_INITIAL_DELAY + (MENU_REPEAT_INTERVAL * 2.1))

        self.assertEqual(game.main_menu.index, 3)
        self.assertEqual(speaker.messages[-1][0], "How to Play")

    def test_menu_repeat_adjusts_option_values_while_holding_horizontal_arrow(self):
        game, speaker, _ = self.make_game()
        game.active_menu = game.options_menu
        game.options_menu.index = 0
        game.settings["sfx_volume"] = 0.4

        game._prime_menu_repeat(pygame.K_RIGHT)
        game._update_menu_repeat(MENU_REPEAT_INITIAL_DELAY + MENU_REPEAT_INTERVAL)

        self.assertEqual(game.settings["sfx_volume"], 0.6)
        self.assertEqual(game.options_menu.items[0].label, "SFX Volume: 60")
        self.assertEqual(speaker.messages[-1][0], "SFX Volume: 60")

    def test_pause_menu_close_resumes_run(self):
        game, speaker, audio = self.make_game()
        game.state.paused = True
        game.active_menu = game.pause_menu

        game._handle_menu_action("close")

        self.assertFalse(game.state.paused)
        self.assertIsNone(game.active_menu)
        self.assertIn(("menuclose", "ui", False), audio.played)
        self.assertEqual(speaker.messages[-1][0], "Resume")

    def test_pause_menu_return_to_main_requests_confirmation(self):
        game, _, _ = self.make_game()
        game.state.paused = True
        game.active_menu = game.pause_menu

        game._handle_menu_action("to_main")

        self.assertIs(game.active_menu, game.pause_confirm_menu)
        self.assertEqual(game.pause_confirm_menu.title, "Return to Main Menu?")

    def test_pause_confirmation_no_returns_to_pause_menu(self):
        game, _, _ = self.make_game()
        game.state.paused = True
        game.active_menu = game.pause_confirm_menu

        game._handle_menu_action("cancel_to_main")

        self.assertIs(game.active_menu, game.pause_menu)
        self.assertEqual(game.pause_menu.index, 1)

    def test_pause_confirmation_yes_returns_to_main_menu(self):
        game, _, audio = self.make_game()
        game.state.running = True
        game.state.paused = True
        game.active_menu = game.pause_confirm_menu

        game._handle_menu_action("confirm_to_main")

        self.assertIs(game.active_menu, game.main_menu)
        self.assertEqual(audio.music_started_tracks[-1], "menu")

    def test_shop_purchase_spends_bank_coins_and_grants_hoverboard(self):
        game, speaker, _ = self.make_game()
        game.settings["bank_coins"] = SHOP_PRICES["hoverboard"]
        game.settings["hoverboards"] = 0
        game.active_menu = game.shop_menu

        game._purchase_shop_item("hoverboard")

        self.assertEqual(game.settings["bank_coins"], 0)
        self.assertEqual(game.settings["hoverboards"], 1)
        self.assertIn(("Hoverboard purchased.", True), speaker.messages)

    def test_shop_mystery_box_can_grant_multiple_hoverboards(self):
        game, speaker, _ = self.make_game()
        game.settings["hoverboards"] = 0

        with patch("subway_blind.game.shop_box_reward_amount", return_value=3):
            game._grant_shop_box_reward("hover")

        self.assertEqual(game.settings["hoverboards"], 3)
        self.assertIn(("Mystery box: 3 hoverboards.", False), speaker.messages)

    def test_hoverboard_absorbs_hit(self):
        game, speaker, _ = self.make_game()
        game.player.hover_active = 5.0

        game._on_hit()

        self.assertEqual(game.player.hover_active, 0.0)
        self.assertEqual(game.player.stumbles, 0)
        self.assertEqual(speaker.messages[-1][0], "Hoverboard destroyed.")

    def test_hoverboard_uses_original_duration_and_pauses_during_jetpack(self):
        game, _, _ = self.make_game()
        game.player.hoverboards = 1

        game._try_hoverboard()
        self.assertEqual(game.player.hover_active, HOVERBOARD_DURATION)

        game.player.jetpack = 4.0
        game._tick_powerups(1.0)

        self.assertEqual(game.player.hover_active, HOVERBOARD_DURATION)

    def test_bush_hit_uses_bush_stumble_sound(self):
        game, speaker, audio = self.make_game()

        game._on_hit("bush")

        self.assertIn(("stumble_bush", "act", False), audio.played)
        self.assertEqual(speaker.messages[-1][0], "You crashed. One chance left.")

    def test_first_stumble_starts_guard_loop_for_recovery_window(self):
        game, _, audio = self.make_game()
        game.state.running = True
        game._on_hit()

        game._tick_powerups(0.1)

        self.assertIn(("guard_loop", "loop_guard", True), audio.played)

    def test_guard_loop_stops_after_recovery_window_ends(self):
        game, _, audio = self.make_game()
        game.state.running = True
        game._on_hit()

        game._tick_powerups(1.5)

        self.assertIn("loop_guard", audio.stopped)

    def test_near_miss_triggers_swish_sound(self):
        game, _, audio = self.make_game()
        game.player.lane = 0
        game.player.y = 1.2
        game.obstacles = [Obstacle(kind="low", lane=0, z=1.0)]

        game._update_near_miss_audio()

        self.assertIn(("swish_mid", "near_0", False), audio.played)

    def test_second_hit_opens_game_over_dialog(self):
        game, speaker, audio = self.make_game()
        game.player.stumbles = 1
        game.state.score = 120
        game.state.coins = 8
        game.state.running = True
        game.settings["keys"] = 0
        game.active_menu = None

        game._on_hit()

        self.assertIs(game.active_menu, game.game_over_menu)
        self.assertEqual(audio.music_stopped, 0)
        self.assertEqual(audio.music_started_tracks[-1], "menu")
        self.assertEqual(game.settings["bank_coins"], 8)
        self.assertEqual(
            [item.label for item in game.game_over_menu.items],
            ["Score: 120", "Coins: 8", "Death reason: Hit train", "Run again", "Main menu"],
        )
        self.assertIn(("Run over. Score 120. Hit train.", True), speaker.messages)
        self.assertEqual(game.game_over_menu.index, 0)
        self.assertEqual(speaker.messages[-1], ("Game Over.", True))

    def test_second_hit_opens_revive_menu_when_keys_exist(self):
        game, speaker, _ = self.make_game()
        game.player.stumbles = 1
        game.settings["keys"] = 2
        game.active_menu = None

        game._on_hit()

        self.assertIs(game.active_menu, game.revive_menu)
        self.assertIn(("You can revive for 1 key.", True), speaker.messages)

    def test_bush_death_reason_is_recorded_in_game_over_dialog(self):
        game, _, _ = self.make_game()
        game.player.stumbles = 1
        game.state.score = 40
        game.state.coins = 3
        game.state.running = True
        game.settings["keys"] = 0

        game._on_hit("bush")

        self.assertIs(game.active_menu, game.game_over_menu)
        self.assertEqual(game.game_over_menu.items[2].label, "Death reason: Hit bush")

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

    def test_collect_multiplier_pickup_uses_existing_powerup_audio(self):
        game, speaker, audio = self.make_game()

        game._collect_multiplier_pickup()

        self.assertGreater(game.player.mult2x, 0.0)
        self.assertIn(("powerup", "act", False), audio.played)
        self.assertIn(("2x multiplier.", False), speaker.messages)

    def test_collect_power_starts_magnet_loop_when_reward_is_magnet(self):
        game, speaker, audio = self.make_game()

        game._apply_power_reward("magnet", from_headstart=False)

        self.assertGreater(game.player.magnet, 0.0)
        self.assertIn(("magnet_loop", "loop_magnet", True), audio.played)
        self.assertIn(("Magnet.", False), speaker.messages)

    def test_collect_power_starts_jetpack_loop_when_reward_is_jetpack(self):
        game, speaker, audio = self.make_game()

        game._apply_power_reward("jetpack", from_headstart=False)

        self.assertGreater(game.player.jetpack, 0.0)
        self.assertEqual(game.player.y, 2.0)
        self.assertIn(("jetpack_loop", "loop_jetpack", True), audio.played)
        self.assertIn(("Jetpack.", False), speaker.messages)

    def test_collect_super_mysterizer_uses_existing_mystery_audio(self):
        game, speaker, audio = self.make_game()
        original_keys = game.settings["keys"]

        with patch("subway_blind.game.pick_super_mystery_box_reward", return_value="keys"), patch(
            "subway_blind.game.random.randint",
            return_value=2,
        ):
            game._collect_super_mysterizer()

        self.assertEqual(game.settings["keys"], original_keys + 2)
        self.assertIn(("mystery_box_open", "ui", False), audio.played)
        self.assertIn(("mystery_combo", "ui2", False), audio.played)
        self.assertTrue(any("Super Mysterizer" in message for message, _ in speaker.messages))

    def test_collect_pogo_stick_launches_player_with_existing_sounds(self):
        game, speaker, audio = self.make_game()

        game._collect_pogo_stick()

        self.assertGreater(game.player.pogo_active, 0.0)
        self.assertGreater(game.player.vy, 0.0)
        self.assertIn(("powerup", "act", False), audio.played)
        self.assertIn(("sneakers_jump", "act", False), audio.played)
        self.assertIn(("Pogo stick.", False), speaker.messages)

    def test_pogo_bounce_avoids_high_obstacle_collision(self):
        game, _, _ = self.make_game()
        game.player.pogo_active = 2.0
        game.player.y = 1.2
        game.obstacles = [Obstacle(kind="high", lane=0, z=1.0)]

        game._handle_obstacles()

        self.assertEqual(game.player.stumbles, 0)

    def test_mystery_box_announces_opening_before_reward(self):
        game, speaker, _ = self.make_game()

        with patch("subway_blind.game.pick_mystery_box_reward", return_value="key"):
            game._collect_box()

        self.assertEqual(speaker.messages[0], ("Opening Mystery Box.", True))
        self.assertEqual(speaker.messages[1], ("Mystery box: key.", False))

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

    def test_collect_word_letter_ignores_unexpected_letter(self):
        game, speaker, audio = self.make_game()
        word = game._current_word()
        game.settings["word_hunt_day"] = date.today().isoformat()
        game.settings["word_hunt_letters"] = word[:1]

        game._collect_word_letter(Obstacle(kind="word", lane=0, z=1.0, label="Z"))

        self.assertEqual(game.settings["word_hunt_letters"], word[:1])
        self.assertEqual(speaker.messages, [])
        self.assertEqual(audio.played, [])

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

    def test_super_mystery_box_can_grant_jetpack_reward(self):
        game, speaker, _ = self.make_game()
        game.state.running = True

        with patch("subway_blind.game.pick_super_mystery_box_reward", return_value="jetpack"):
            game._open_super_mystery_box("Mission Set")

        self.assertGreater(game.player.jetpack, 0.0)
        self.assertIn(("Mission Set: Super Mystery Box. Jetpack.", True), speaker.messages)

    def test_tick_powerups_starts_loop_for_active_jetpack(self):
        game, _, audio = self.make_game()
        game.player.jetpack = 6.5

        game._tick_powerups(0.016)

        self.assertIn(("jetpack_loop", "loop_jetpack", True), audio.played)

    def test_lane_names_are_english(self):
        self.assertEqual(lane_name(-1), "Left lane")
        self.assertEqual(lane_name(0), "Center lane")
        self.assertEqual(lane_name(1), "Right lane")

    def test_coin_announcement_hotkey_works_during_headstart(self):
        game, speaker, _ = self.make_game()
        game.state.coins = 17
        game.player.headstart = 3.0

        game._handle_game_key(pygame.K_r)

        self.assertIn(("Coins collected: 17.", False), speaker.messages)

    def test_coin_announcement_hotkey_works_through_keyboard_translation(self):
        game, speaker, _ = self.make_game()
        game.state.coins = 23
        game.active_menu = None

        event = pygame.event.Event(pygame.KEYDOWN, key=pygame.K_r)
        game._handle_keyboard_event(event)

        self.assertIn(("Coins collected: 23.", False), speaker.messages)

    def test_jetpack_auto_collects_coins_while_airborne(self):
        game, _, _ = self.make_game()
        game.player.jetpack = 4.0
        game.obstacles = [Obstacle(kind="coin", lane=1, z=1.0, value=1)]

        game._handle_obstacles()

        self.assertEqual(game.state.coins, 1)
        self.assertLess(game.obstacles[0].z, -100)

    def test_jetpack_disables_lane_change_actions(self):
        game, _, audio = self.make_game()
        game.player.jetpack = 4.0
        game.player.lane = 0

        game._handle_game_key(pygame.K_LEFT)

        self.assertEqual(game.player.lane, 0)
        self.assertNotIn(("dodge", "move", False), audio.played)

    def test_second_hit_summary_remains_last_spoken_before_game_over_dialog(self):
        game, speaker, audio = self.make_game()
        game.player.stumbles = 1
        game.state.score = 120
        game.state.coins = 8
        game.state.running = True
        game.settings["keys"] = 0
        game.active_menu = None

        game._on_hit()

        self.assertIs(game.active_menu, game.game_over_menu)
        self.assertEqual(audio.music_stopped, 0)
        self.assertEqual(audio.music_started_tracks[-1], "menu")
        self.assertEqual(speaker.messages[-1], ("Game Over.", True))

    def test_game_over_dialog_defers_score_announcement_until_delay_expires(self):
        game, speaker, _ = self.make_game()
        game.state.score = 120
        game.state.coins = 8

        game._open_game_over_dialog("Hit train")

        self.assertEqual(speaker.messages[-1], ("Game Over.", True))
        self.assertEqual(game.game_over_menu.index, 0)
        self.assertIsNotNone(game._pending_menu_announcement)

    def test_game_over_menu_run_again_starts_new_run(self):
        game, _, audio = self.make_game()
        game.state.score = 80
        game.state.coins = 6
        game._game_over_summary = {"score": 80, "coins": 6, "death_reason": "Hit train"}
        game._refresh_game_over_menu()
        game.active_menu = game.game_over_menu

        game._handle_menu_action("game_over_retry")

        self.assertIsNone(game.active_menu)
        self.assertTrue(game.state.running)
        self.assertGreaterEqual(audio.music_started, 1)

    def test_game_over_menu_main_menu_returns_to_main_menu(self):
        game, _, _ = self.make_game()
        game._game_over_summary = {"score": 80, "coins": 6, "death_reason": "Hit train"}
        game._refresh_game_over_menu()
        game.active_menu = game.game_over_menu

        game._handle_menu_action("game_over_main_menu")

        self.assertIs(game.active_menu, game.main_menu)

    def test_game_over_detail_rows_are_read_only(self):
        game, speaker, _ = self.make_game()
        game._game_over_summary = {"score": 80, "coins": 6, "death_reason": "Hit train"}
        game._refresh_game_over_menu()
        game.active_menu = game.game_over_menu
        game.game_over_menu.index = 0

        game._handle_menu_action("game_over_info_score")

        self.assertIs(game.active_menu, game.game_over_menu)
        self.assertEqual(speaker.messages[-1], ("Score: 80", True))

    def test_revive_end_run_opens_game_over_dialog_with_generic_crash_reason(self):
        game, speaker, _ = self.make_game()
        game.state.score = 55
        game.state.coins = 4
        game.state.running = True
        game.active_menu = game.revive_menu

        game._handle_menu_action("end_run")

        self.assertIs(game.active_menu, game.game_over_menu)
        self.assertEqual(game.game_over_menu.items[2].label, "Death reason: Run ended after crash")
        self.assertIn(("Run over. Score 55. Run ended after crash.", True), speaker.messages)

    def test_draw_menu_keeps_hint_on_small_screens(self):
        game, _, _ = self.make_game()
        game.screen = pygame.display.set_mode((320, 240))

        game._draw_menu(game.main_menu)

        self.assertEqual(game.screen.get_size(), (320, 240))

    def test_handle_window_resize_enforces_minimum_window_size(self):
        game, _, _ = self.make_game()

        game._handle_window_event(pygame.event.Event(pygame.VIDEORESIZE, w=320, h=200))

        self.assertEqual(game.screen.get_size(), (640, 360))

    def test_window_size_changed_refreshes_screen_reference(self):
        game, _, _ = self.make_game()
        resized = pygame.display.set_mode((700, 420), pygame.RESIZABLE)

        game._handle_window_event(pygame.event.Event(pygame.WINDOWSIZECHANGED, x=700, y=420))

        self.assertIs(game.screen, resized)


if __name__ == "__main__":
    unittest.main()
