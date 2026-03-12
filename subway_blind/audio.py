from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Optional
from xml.sax.saxutils import escape

import pygame

from subway_blind.config import resource_path
from subway_blind.hrtf_audio import OpenALHrtfEngine

FIXED_FOOTSTEP_PAN = {
    "left_foot": -0.18,
    "sneakers_left": -0.18,
    "right_foot": 0.18,
    "sneakers_right": 0.18,
}

CENTERED_PLAYER_KEYS = {
    "jump",
    "sneakers_jump",
    "roll",
    "dodge",
    "landing",
    "land_h",
    "coin",
    "powerup",
    "powerdown",
    "mystery_box",
    "stumble",
    "stumble_side",
    "stumble_bush",
    "crash",
    "death",
    "kick",
    "guard_catch",
}

KEY_CHANNEL_OVERRIDES = {
    "jump": "player_jump",
    "sneakers_jump": "player_jump",
    "roll": "player_roll",
    "dodge": "player_dodge",
    "landing": "player_land",
    "land_h": "player_land",
    "coin": "player_pickup",
    "powerup": "player_power",
    "powerdown": "player_powerdown",
    "mystery_box": "player_box",
    "stumble": "player_impact",
    "stumble_side": "player_impact",
    "stumble_bush": "player_impact",
    "crash": "player_crash",
    "death": "player_death",
    "guard_catch": "player_guard",
    "kick": "player_kick",
}

CHANNEL_FALLBACK_OVERRIDES = {
    "move": "player_move",
    "act": "player_action",
    "act2": "player_impact",
    "coin": "player_pickup",
    "headstart_end": "player_reward",
    "headstart_reward": "player_reward",
}

SYSTEM_DEFAULT_OUTPUT_LABEL = "System Default"
SAPI_VOICE_UNAVAILABLE_LABEL = "Unavailable"
SAPI_VOICE_DEFAULT_LABEL = "Default Voice"
SAPI_SPEAK_ASYNC = 1
SAPI_SPEAK_PURGE_BEFORE_SPEAK = 2
SAPI_SPEAK_IS_XML = 8
SAPI_RATE_MIN = -10
SAPI_RATE_MAX = 10
SAPI_PITCH_MIN = -10
SAPI_PITCH_MAX = 10
MUSIC_FILE_EXTENSIONS = (".ogg", ".wav", ".mp3")
MUSIC_TRACK_CANDIDATES = {
    "menu": ("menu_intro", "game_intro", "menu_theme", "menu"),
    "gameplay": ("gameplay_main", "subway_surfers_theme", "main_theme", "theme", "run_theme"),
}
MUSIC_FADE_IN_SECONDS = 1.05
MUSIC_FADE_OUT_SECONDS = 0.75


@dataclass(frozen=True)
class SapiVoiceChoice:
    voice_id: str
    name: str


def normalize_output_device_name(device_name: object) -> str | None:
    normalized = str(device_name or "").strip()
    return normalized or None


def list_output_devices() -> list[str]:
    try:
        import pygame._sdl2.audio as sdl2_audio
    except Exception:
        return []
    try:
        names = sdl2_audio.get_audio_device_names(False)
    except Exception:
        return []
    devices: list[str] = []
    seen: set[str] = set()
    for name in names:
        normalized = normalize_output_device_name(name)
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        devices.append(normalized)
    return devices


def initialize_mixer_output(device_name: object) -> str | None:
    selected_device = normalize_output_device_name(device_name)
    if pygame.mixer.get_init() is not None:
        try:
            pygame.mixer.quit()
        except Exception:
            pass
    if selected_device is not None:
        try:
            pygame.mixer.init(devicename=selected_device)
            return selected_device
        except pygame.error:
            pass
    try:
        pygame.mixer.init()
    except pygame.error:
        return None
    return None


class Speaker:
    def __init__(
        self,
        enabled: bool = True,
        use_sapi: bool = False,
        sapi_voice_id: str | None = None,
        sapi_rate: int = 0,
        sapi_pitch: int = 0,
    ):
        self.enabled = bool(enabled)
        self.use_sapi = bool(use_sapi)
        self.sapi_voice_id = self._normalize_voice_id(sapi_voice_id)
        self.sapi_rate = self._normalize_sapi_rate(sapi_rate)
        self.sapi_pitch = self._normalize_sapi_pitch(sapi_pitch)
        self._driver = None
        self._sapi_voice = None
        self._sapi_voice_name = SAPI_VOICE_DEFAULT_LABEL
        self._speed_factor = 0.0
        self._sapi_voice_choices_cache: list[SapiVoiceChoice] | None = None
        self._initialize_backend()

    @classmethod
    def from_settings(cls, settings: dict) -> "Speaker":
        return cls(
            enabled=bool(settings.get("speech_enabled", True)),
            use_sapi=bool(settings.get("sapi_speech_enabled", False)),
            sapi_voice_id=settings.get("sapi_voice_id"),
            sapi_rate=settings.get("sapi_rate", 0),
            sapi_pitch=settings.get("sapi_pitch", 0),
        )

    @staticmethod
    def _normalize_voice_id(voice_id: object) -> str | None:
        normalized = str(voice_id or "").strip()
        return normalized or None

    @staticmethod
    def _normalize_sapi_rate(value: object) -> int:
        try:
            normalized = int(round(float(value)))
        except (TypeError, ValueError):
            normalized = 0
        return max(SAPI_RATE_MIN, min(SAPI_RATE_MAX, normalized))

    @staticmethod
    def _normalize_sapi_pitch(value: object) -> int:
        try:
            normalized = int(round(float(value)))
        except (TypeError, ValueError):
            normalized = 0
        return max(SAPI_PITCH_MIN, min(SAPI_PITCH_MAX, normalized))

    def apply_settings(self, settings: dict) -> None:
        enabled = bool(settings.get("speech_enabled", True))
        use_sapi = bool(settings.get("sapi_speech_enabled", False))
        sapi_voice_id = self._normalize_voice_id(settings.get("sapi_voice_id"))
        sapi_rate = self._normalize_sapi_rate(settings.get("sapi_rate", 0))
        sapi_pitch = self._normalize_sapi_pitch(settings.get("sapi_pitch", 0))
        if (
            enabled == self.enabled
            and use_sapi == self.use_sapi
            and sapi_voice_id == self.sapi_voice_id
            and sapi_rate == self.sapi_rate
            and sapi_pitch == self.sapi_pitch
        ):
            return
        should_reinitialize = (
            enabled != self.enabled
            or use_sapi != self.use_sapi
            or (enabled and use_sapi and sapi_voice_id != self.sapi_voice_id)
        )
        self.enabled = enabled
        self.use_sapi = use_sapi
        self.sapi_voice_id = sapi_voice_id
        self.sapi_rate = sapi_rate
        self.sapi_pitch = sapi_pitch
        if should_reinitialize:
            self._initialize_backend()
            return
        self._apply_sapi_rate()

    def _initialize_backend(self) -> None:
        self._driver = None
        self._sapi_voice = None
        self._sapi_voice_name = self.current_sapi_voice_display_name()
        if not self.enabled:
            return
        if self.use_sapi and self._initialize_sapi():
            self._apply_sapi_rate()
            return
        self._initialize_accessible_output()

    def _initialize_accessible_output(self) -> None:
        try:
            from accessible_output2.outputs.auto import Auto

            self._driver = Auto()
            self._apply_rate_to_supported_outputs()
        except Exception:
            self._driver = None

    def _initialize_sapi(self) -> bool:
        if os.name != "nt":
            return False
        try:
            from win32com.client import Dispatch
        except Exception:
            return False
        try:
            sapi_voice = Dispatch("SAPI.SpVoice")
            for voice_choice in self.sapi_voice_choices():
                if voice_choice.voice_id != self.sapi_voice_id:
                    continue
                for index in range(sapi_voice.GetVoices().Count):
                    token = sapi_voice.GetVoices().Item(index)
                    token_id = self._normalize_voice_id(getattr(token, "Id", None))
                    if token_id != voice_choice.voice_id:
                        continue
                    sapi_voice.Voice = token
                    break
                break
            current_token = getattr(sapi_voice, "Voice", None)
            current_voice_id = self._normalize_voice_id(getattr(current_token, "Id", None))
            if current_voice_id is not None:
                self.sapi_voice_id = current_voice_id
            try:
                self._sapi_voice_name = current_token.GetDescription()
            except Exception:
                self._sapi_voice_name = self.current_sapi_voice_display_name()
            self._sapi_voice = sapi_voice
            return True
        except Exception:
            self._sapi_voice = None
            return False

    def speak(self, text: str, interrupt: bool = True) -> None:
        if not self.enabled:
            return
        if self._sapi_voice is not None:
            flags = SAPI_SPEAK_ASYNC
            if interrupt:
                flags |= SAPI_SPEAK_PURGE_BEFORE_SPEAK
            message = str(text)
            if self.sapi_pitch != 0:
                flags |= SAPI_SPEAK_IS_XML
                message = f'<pitch middle="{self.sapi_pitch:+d}">{escape(message)}</pitch>'
            try:
                self._sapi_voice.Speak(message, flags)
            except Exception:
                return
            return
        if self._driver is None:
            try:
                print(text)
            except Exception:
                return
            return
        try:
            self._driver.speak(text, interrupt=interrupt)
        except TypeError:
            try:
                self._driver.speak(text, interrupt)
            except Exception:
                return
        except Exception:
            return

    def set_speed_factor(self, speed_factor: float) -> None:
        normalized = max(0.0, min(1.0, float(speed_factor)))
        if abs(normalized - self._speed_factor) < 0.04:
            return
        self._speed_factor = normalized
        self._apply_sapi_rate()
        self._apply_rate_to_supported_outputs()

    def sapi_available(self) -> bool:
        return len(self.sapi_voice_choices()) > 0

    def sapi_voice_choices(self) -> list[SapiVoiceChoice]:
        if self._sapi_voice_choices_cache is not None:
            return list(self._sapi_voice_choices_cache)
        choices: list[SapiVoiceChoice] = []
        if os.name == "nt":
            try:
                from win32com.client import Dispatch

                token_collection = Dispatch("SAPI.SpVoice").GetVoices()
                for index in range(token_collection.Count):
                    token = token_collection.Item(index)
                    voice_id = self._normalize_voice_id(getattr(token, "Id", None))
                    if voice_id is None:
                        continue
                    try:
                        name = str(token.GetDescription()).strip() or voice_id
                    except Exception:
                        name = voice_id
                    choices.append(SapiVoiceChoice(voice_id=voice_id, name=name))
            except Exception:
                choices = []
        self._sapi_voice_choices_cache = choices
        return list(self._sapi_voice_choices_cache)

    def current_sapi_voice_display_name(self) -> str:
        if self._sapi_voice is not None:
            return self._sapi_voice_name
        choices = self.sapi_voice_choices()
        if not choices:
            return SAPI_VOICE_UNAVAILABLE_LABEL
        if self.sapi_voice_id is None:
            return choices[0].name
        for choice in choices:
            if choice.voice_id == self.sapi_voice_id:
                return choice.name
        return choices[0].name

    def cycle_sapi_voice(self, direction: int) -> str:
        choices = self.sapi_voice_choices()
        if not choices:
            return SAPI_VOICE_UNAVAILABLE_LABEL
        normalized_direction = -1 if direction < 0 else 1
        current_voice_id = self.sapi_voice_id
        try:
            current_index = next(index for index, choice in enumerate(choices) if choice.voice_id == current_voice_id)
        except StopIteration:
            current_index = 0
        selected = choices[(current_index + normalized_direction) % len(choices)]
        self.sapi_voice_id = selected.voice_id
        self._sapi_voice_name = selected.name
        if self._sapi_voice is not None:
            self._initialize_backend()
        return selected.name

    def _apply_sapi_rate(self) -> None:
        if self._sapi_voice is None:
            return
        dynamic_rate_offset = int(round(-1 + (self._speed_factor * 5.0)))
        target_rate = self.sapi_rate + dynamic_rate_offset
        try:
            self._sapi_voice.Rate = max(SAPI_RATE_MIN, min(SAPI_RATE_MAX, target_rate))
        except Exception:
            return

    def stop(self) -> None:
        if self._sapi_voice is not None:
            try:
                self._sapi_voice.Speak("", SAPI_SPEAK_ASYNC | SAPI_SPEAK_PURGE_BEFORE_SPEAK)
            except Exception:
                pass

    def _apply_rate_to_supported_outputs(self) -> None:
        if self._driver is None:
            return
        outputs = getattr(self._driver, "outputs", [])
        for output in outputs:
            has_rate = getattr(output, "has_rate", None)
            set_rate = getattr(output, "set_rate", None)
            min_rate = getattr(output, "min_rate", None)
            max_rate = getattr(output, "max_rate", None)
            if not callable(has_rate) or not callable(set_rate) or not callable(min_rate) or not callable(max_rate):
                continue
            try:
                if not has_rate():
                    continue
                minimum = float(min_rate())
                maximum = float(max_rate())
                target = minimum + (maximum - minimum) * (0.42 + self._speed_factor * 0.4)
                set_rate(target)
            except Exception:
                continue


class Audio:
    def __init__(self, settings: dict):
        self.settings = settings
        self.sounds: dict[str, pygame.mixer.Sound] = {}
        self.sound_paths: dict[str, str] = {}
        self.channels: dict[str, pygame.mixer.Channel] = {}
        self._next_channel_index = 0
        self._output_device_name = normalize_output_device_name(settings.get("audio_output_device"))
        self._mixer_ready = pygame.mixer.get_init() is not None
        self._music_catalog: dict[str, str] = {}
        self._music_current_track: str | None = None
        self._music_pending_track: str | None = None
        self._music_fade_level = 0.0
        self._music_transition: str | None = None
        self.hrtf = OpenALHrtfEngine(settings.get("sfx_volume", 1.0), self._output_device_name)
        self._load()

    def _load_sound(self, key: str, path: str) -> None:
        if not os.path.exists(path):
            return
        self.sound_paths[key] = path
        try:
            self.hrtf.register_sound(key, path)
        except Exception:
            pass
        if not self._mixer_ready:
            return
        try:
            sound = pygame.mixer.Sound(path)
        except Exception:
            return
        sound.set_volume(float(self.settings["sfx_volume"]))
        self.sounds[key] = sound

    def _pick_menu_sound(self, base_name: str) -> str:
        for extension in (".ogg", ".wav"):
            candidate = resource_path("assets", "menu", f"{base_name}{extension}")
            if os.path.exists(candidate):
                return candidate
        return resource_path("assets", "menu", f"{base_name}.wav")

    def _load(self) -> None:
        sfx_path = lambda name: resource_path("assets", "sfx", name)

        self._load_sound("coin", sfx_path("coin.wav"))
        self._load_sound("coin_gui", sfx_path("coin_gui.wav"))
        self._load_sound("jump", sfx_path("jump.wav"))
        self._load_sound("roll", sfx_path("roll.wav"))
        self._load_sound("dodge", sfx_path("dodge.wav"))
        self._load_sound("landing", sfx_path("landing.wav"))
        self._load_sound("stumble", sfx_path("stumble.wav"))
        self._load_sound("crash", sfx_path("crash.wav"))
        self._load_sound("death", sfx_path("death.wav"))
        self._load_sound("death_bodyfall", sfx_path("death_bodyfall.wav"))
        self._load_sound("death_hitcam", sfx_path("death_hitcam.wav"))
        self._load_sound("guard_catch", sfx_path("guard_catch.wav"))
        self._load_sound("guard_loop", sfx_path("guard_loop.wav"))
        self._load_sound("powerup", sfx_path("powerup.wav"))
        self._load_sound("powerdown", sfx_path("powerdown.wav"))
        self._load_sound("magnet_loop", sfx_path("magnet_loop.wav"))
        self._load_sound("jetpack_loop", sfx_path("jetpack_loop.wav"))
        self._load_sound("mystery_box", sfx_path("mystery_box.wav"))
        self._load_sound("mystery_box_open", sfx_path("Hr_mysteryBoxOpen #20822.wav"))
        self._load_sound("mission_reward", sfx_path("mission_reward.wav"))
        self._load_sound("train_pass", sfx_path("train_pass.wav"))
        self._load_sound("intro_start", sfx_path("intro_start.wav"))
        self._load_sound("intro_shake", sfx_path("intro_shake.wav"))
        self._load_sound("intro_spray", sfx_path("intro_spray.wav"))
        self._load_sound("gui_cash", sfx_path("Hr_gui_cash #00120.wav"))
        self._load_sound("gui_close", sfx_path("gui_close.wav"))
        self._load_sound("gui_tap", sfx_path("gui_tap.wav"))
        self._load_sound("unlock", sfx_path("unlock.wav"))
        self._load_sound("left_foot", sfx_path("left_foot.wav"))
        self._load_sound("right_foot", sfx_path("right_foot.wav"))
        self._load_sound("sneakers_jump", sfx_path("sneakers_jump.wav"))
        self._load_sound("sneakers_left", sfx_path("sneakers_left.wav"))
        self._load_sound("sneakers_right", sfx_path("sneakers_right.wav"))
        self._load_sound("slide_letters", sfx_path("slide_letters.wav"))
        self._load_sound("mystery_combo", sfx_path("mystery_combo.wav"))
        self._load_sound("stumble_side", sfx_path("stumble_side.wav"))
        self._load_sound("stumble_bush", sfx_path("stumble_bush.wav"))
        self._load_sound("kick", sfx_path("kick.wav"))
        self._load_sound("land_h", sfx_path("land_h.wav"))
        self._load_sound("swish_short", sfx_path("swish_short.wav"))
        self._load_sound("swish_mid", sfx_path("swish_mid.wav"))
        self._load_sound("swish_long", sfx_path("swish_long.wav"))

        self._load_sound("menumove", self._pick_menu_sound("menumove"))
        self._load_sound("menuedge", self._pick_menu_sound("menuedge"))
        self._load_sound("menuopen", self._pick_menu_sound("menuopen"))
        self._load_sound("menuclose", self._pick_menu_sound("menuclose"))
        self._load_sound("confirm", self._pick_menu_sound("confirm"))
        self._load_sound("warning", self._pick_menu_sound("warning"))
        self._music_catalog = self._discover_music_catalog()

    def refresh_volumes(self) -> None:
        if not self._mixer_ready:
            self.hrtf.set_listener_gain(float(self.settings["sfx_volume"]))
            return
        sound_volume = float(self.settings["sfx_volume"])
        for sound in self.sounds.values():
            try:
                sound.set_volume(sound_volume)
            except Exception:
                continue
        self._apply_music_volume()
        self.hrtf.set_listener_gain(sound_volume)

    def output_device_choices(self) -> list[str | None]:
        devices = [None]
        current_device = normalize_output_device_name(self.settings.get("audio_output_device"))
        for device in list_output_devices():
            devices.append(device)
        if current_device is not None and current_device not in devices:
            devices.append(current_device)
        return devices

    def current_output_device_name(self) -> str | None:
        return normalize_output_device_name(self.settings.get("audio_output_device"))

    def output_device_display_name(self) -> str:
        return self.current_output_device_name() or SYSTEM_DEFAULT_OUTPUT_LABEL

    def cycle_output_device(self) -> tuple[str | None, str | None]:
        devices = self.output_device_choices()
        current_device = self.current_output_device_name()
        try:
            current_index = devices.index(current_device)
        except ValueError:
            current_index = 0
        requested_device = devices[(current_index + 1) % len(devices)]
        applied_device = self.apply_output_device(requested_device)
        return requested_device, applied_device

    def apply_output_device(self, device_name: str | None) -> str | None:
        requested_device = normalize_output_device_name(device_name)
        resume_music_track = self._music_pending_track or self._music_current_track
        self.shutdown()
        applied_device = initialize_mixer_output(requested_device)
        self._output_device_name = applied_device
        self.settings["audio_output_device"] = applied_device or ""
        self._mixer_ready = pygame.mixer.get_init() is not None
        self.hrtf = OpenALHrtfEngine(self.settings.get("sfx_volume", 1.0), applied_device)
        self.sounds.clear()
        self.sound_paths.clear()
        self.channels.clear()
        self._next_channel_index = 0
        self._load()
        self.refresh_volumes()
        if resume_music_track is not None:
            self.music_start(resume_music_track)
        return applied_device

    def shutdown(self) -> None:
        if self._mixer_ready:
            for channel in self.channels.values():
                try:
                    channel.stop()
                except Exception:
                    continue
            self._stop_music_immediately()
        self.channels.clear()
        self._next_channel_index = 0
        self.hrtf.shutdown()

    def _get_channel(self, name: str) -> Optional[pygame.mixer.Channel]:
        if not self._mixer_ready:
            return None
        existing = self.channels.get(name)
        if existing is not None:
            return existing
        index = self._next_channel_index
        self._next_channel_index += 1
        try:
            pygame.mixer.set_num_channels(max(16, self._next_channel_index + 1))
            channel = pygame.mixer.Channel(index)
        except Exception:
            return None
        self.channels[name] = channel
        return channel

    def play(
        self,
        key: str,
        pan: Optional[float] = None,
        loop: bool = False,
        channel: Optional[str] = None,
        gain: float = 1.0,
    ) -> None:
        gain = max(0.0, min(1.5, float(gain)))
        normalized_pan = self._normalize_pan_for_key(key, pan)
        sound_path = self.sound_paths.get(key)
        requested_channel = channel or f"sfx_{key}"
        target_channel = self._normalize_channel_for_key(key, requested_channel)
        if self.hrtf.available and sound_path is not None:
            x, y, z, pitch, relative = self._hrtf_profile(key, target_channel, normalized_pan)
            played = self.hrtf.play_sound(
                key=key,
                path=sound_path,
                channel=target_channel,
                x=x,
                y=y,
                z=z,
                gain=gain,
                pitch=pitch,
                loop=loop,
                relative=relative,
            )
            if played:
                return
        if not self._mixer_ready:
            return
        sound = self.sounds.get(key)
        if sound is None:
            return
        output_channel = self._get_channel(target_channel)
        if output_channel is None:
            return
        base_volume = float(self.settings["sfx_volume"]) * gain
        if normalized_pan is None:
            output_channel.set_volume(max(0.0, min(1.0, base_volume)))
        else:
            clamped_pan = max(-1.0, min(1.0, float(normalized_pan)))
            left = max(0.0, min(1.0, 1.0 - max(0.0, clamped_pan)))
            right = max(0.0, min(1.0, 1.0 + min(0.0, clamped_pan)))
            output_channel.set_volume(
                max(0.0, min(1.0, left * base_volume)),
                max(0.0, min(1.0, right * base_volume)),
            )
        try:
            output_channel.play(sound, loops=-1 if loop else 0)
        except Exception:
            return

    def stop(self, channel: str) -> None:
        self.hrtf.stop(channel)
        output_channel = self.channels.get(channel)
        if output_channel is None:
            return
        try:
            output_channel.stop()
        except Exception:
            return

    def play_spatial(
        self,
        key: str,
        channel: str,
        x: float,
        y: float,
        z: float,
        gain: float,
        pitch: float = 1.0,
        fallback_pan: Optional[float] = None,
        velocity_x: float = 0.0,
        velocity_y: float = 0.0,
        velocity_z: float = 0.0,
    ) -> None:
        sound_path = self.sound_paths.get(key)
        played = False
        if sound_path is not None:
            played = self.hrtf.play_sound(
                key=key,
                path=sound_path,
                channel=channel,
                x=x,
                y=y,
                z=z,
                gain=gain,
                pitch=pitch,
                velocity_x=velocity_x,
                velocity_y=velocity_y,
                velocity_z=velocity_z,
            )
        if played:
            return
        self.play(key, pan=fallback_pan, channel=channel, gain=gain)

    def update_spatial(
        self,
        channel: str,
        x: float,
        y: float,
        z: float,
        gain: float,
        pitch: float = 1.0,
        fallback_pan: Optional[float] = None,
        velocity_x: float = 0.0,
        velocity_y: float = 0.0,
        velocity_z: float = 0.0,
    ) -> None:
        if self.hrtf.update_source(
            channel=channel,
            x=x,
            y=y,
            z=z,
            gain=gain,
            pitch=pitch,
            velocity_x=velocity_x,
            velocity_y=velocity_y,
            velocity_z=velocity_z,
        ):
            return
        output_channel = self.channels.get(channel)
        if output_channel is None:
            return
        base_volume = float(self.settings["sfx_volume"]) * max(0.0, min(1.5, float(gain)))
        if fallback_pan is None:
            output_channel.set_volume(max(0.0, min(1.0, base_volume)))
            return
        clamped_pan = max(-1.0, min(1.0, float(fallback_pan)))
        left = max(0.0, min(1.0, 1.0 - max(0.0, clamped_pan)))
        right = max(0.0, min(1.0, 1.0 + min(0.0, clamped_pan)))
        output_channel.set_volume(
            max(0.0, min(1.0, left * base_volume)),
            max(0.0, min(1.0, right * base_volume)),
        )

    def _hrtf_profile(self, key: str, channel: str, pan: Optional[float]) -> tuple[float, float, float, float, bool]:
        clamped_pan = 0.0 if pan is None else max(-1.0, min(1.0, float(pan)))
        x = clamped_pan * 1.95
        y = 0.0
        z = -1.55
        pitch = 1.0
        relative = False

        if channel.startswith("ui") or channel.startswith("intro") or channel.startswith("boost"):
            x = clamped_pan * 0.6
            z = -0.9
            relative = True
        elif channel.startswith("move") or channel.startswith("act") or channel.startswith("foot") or channel.startswith("coin"):
            z = -1.8
        elif channel.startswith("loop_guard") or key in {"guard_loop", "guard_catch"}:
            z = 0.7
            x = clamped_pan * 1.2
        elif channel.startswith("loop_jetpack") or key == "jetpack_loop":
            z = -1.0
            y = 0.35
        elif channel.startswith("loop_magnet") or key == "magnet_loop":
            z = -1.2
            y = 0.1

        if key == "train_pass":
            z = -5.4
            x = clamped_pan * 2.6
            y = -0.08
            pitch = 0.9
        elif key in {"warning", "menumove", "menuedge", "menuopen", "menuclose", "confirm"}:
            z = -0.8
            relative = True
        elif key in {"left_foot", "right_foot", "sneakers_left", "sneakers_right"}:
            x = clamped_pan * 1.4
            y = -0.2
            z = -0.95
            relative = True
        elif key in CENTERED_PLAYER_KEYS:
            x = 0.0
            y = 0.0
            z = -1.05
            relative = True

        return x, y, z, pitch, relative

    @staticmethod
    def _normalize_pan_for_key(key: str, pan: Optional[float]) -> Optional[float]:
        fixed_pan = FIXED_FOOTSTEP_PAN.get(key)
        if fixed_pan is not None:
            return fixed_pan
        if key in CENTERED_PLAYER_KEYS:
            return 0.0
        return pan

    @staticmethod
    def _normalize_channel_for_key(key: str, channel: str) -> str:
        if key in FIXED_FOOTSTEP_PAN:
            return "player_footstep"
        if key in KEY_CHANNEL_OVERRIDES:
            return KEY_CHANNEL_OVERRIDES[key]
        return CHANNEL_FALLBACK_OVERRIDES.get(channel, channel)

    def _discover_music_catalog(self) -> dict[str, str]:
        catalog: dict[str, str] = {}
        for track_key, base_names in MUSIC_TRACK_CANDIDATES.items():
            resolved = self._resolve_music_track_path(base_names)
            if resolved is not None:
                catalog[track_key] = resolved
        return catalog

    def _resolve_music_track_path(self, base_names: tuple[str, ...]) -> str | None:
        for base_name in base_names:
            for extension in MUSIC_FILE_EXTENSIONS:
                candidate = resource_path("assets", "music", f"{base_name}{extension}")
                if os.path.exists(candidate):
                    return candidate
        return None

    def _target_music_volume(self) -> float:
        return max(0.0, min(1.0, float(self.settings.get("music_volume", 0.0))))

    def _apply_music_volume(self) -> None:
        if not self._mixer_ready:
            return
        try:
            pygame.mixer.music.set_volume(self._target_music_volume() * self._music_fade_level)
        except Exception:
            return

    def _stop_music_immediately(self) -> None:
        if self._mixer_ready:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self._music_current_track = None
        self._music_pending_track = None
        self._music_fade_level = 0.0
        self._music_transition = None

    def _play_music_track(self, track_key: str) -> bool:
        if not self._mixer_ready:
            return False
        track_path = self._music_catalog.get(track_key)
        if track_path is None:
            self._stop_music_immediately()
            return False
        try:
            pygame.mixer.music.load(track_path)
            pygame.mixer.music.play(-1)
        except Exception:
            self._stop_music_immediately()
            return False
        self._music_current_track = track_key
        self._music_pending_track = None
        self._music_fade_level = 0.0
        self._music_transition = "fade_in"
        self._apply_music_volume()
        return True

    def _begin_music_fade_out(self, next_track: str | None = None) -> None:
        if not self._mixer_ready:
            self._stop_music_immediately()
            return
        if self._music_current_track is None:
            if next_track is not None:
                self._play_music_track(next_track)
            else:
                self._stop_music_immediately()
            return
        self._music_pending_track = next_track
        self._music_transition = "fade_out"
        if self._music_fade_level <= 0.0:
            self._music_fade_level = 1.0
        self._apply_music_volume()

    def music_start(self, track_key: str = "gameplay") -> None:
        normalized_track = "menu" if str(track_key).strip().lower() == "menu" else "gameplay"
        if self._music_current_track == normalized_track and self._music_pending_track is None:
            if self._music_transition == "fade_out":
                self._music_transition = "fade_in"
            elif self._music_transition is None and self._music_fade_level < 1.0:
                self._music_transition = "fade_in"
            self._apply_music_volume()
            return
        if self._music_current_track is None:
            self._play_music_track(normalized_track)
            return
        self._begin_music_fade_out(normalized_track)

    def music_stop(self, immediate: bool = False) -> None:
        if immediate:
            self._stop_music_immediately()
            return
        self._begin_music_fade_out(None)

    def music_is_idle(self) -> bool:
        return self._music_current_track is None and self._music_pending_track is None and self._music_transition is None

    def update(self, delta_time: float) -> None:
        if not self._mixer_ready or self._music_transition is None:
            return
        if self._music_transition == "fade_in":
            self._music_fade_level = min(1.0, self._music_fade_level + (float(delta_time) / MUSIC_FADE_IN_SECONDS))
            self._apply_music_volume()
            if self._music_fade_level >= 1.0:
                self._music_transition = None
            return
        if self._music_transition != "fade_out":
            return
        self._music_fade_level = max(0.0, self._music_fade_level - (float(delta_time) / MUSIC_FADE_OUT_SECONDS))
        self._apply_music_volume()
        if self._music_fade_level > 0.0:
            return
        next_track = self._music_pending_track
        self._stop_music_immediately()
        if next_track is not None:
            self._play_music_track(next_track)
