from __future__ import annotations

import os
from typing import Optional

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


class Speaker:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._driver = None
        self._speed_factor = 0.0
        if not enabled:
            return
        try:
            from accessible_output2.outputs.auto import Auto

            self._driver = Auto()
            self._apply_rate_to_supported_outputs()
        except Exception:
            self._driver = None

    def speak(self, text: str, interrupt: bool = True) -> None:
        if not self.enabled:
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
        self._apply_rate_to_supported_outputs()

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
        self._mixer_ready = pygame.mixer.get_init() is not None
        self.hrtf = OpenALHrtfEngine(settings.get("sfx_volume", 1.0))
        self._load()

    def _load_sound(self, key: str, path: str) -> None:
        if not os.path.exists(path):
            return
        self.sound_paths[key] = path
        self.hrtf.register_sound(key, path)
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
        self._load_sound("mission_reward", sfx_path("mission_reward.wav"))
        self._load_sound("train_pass", sfx_path("train_pass.wav"))
        self._load_sound("intro_start", sfx_path("intro_start.wav"))
        self._load_sound("intro_shake", sfx_path("intro_shake.wav"))
        self._load_sound("intro_spray", sfx_path("intro_spray.wav"))
        self._load_sound("gui_cash", sfx_path("gui_cash.wav"))
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
        self._load_sound("menuclose", self._pick_menu_sound("menuclose"))
        self._load_sound("confirm", self._pick_menu_sound("confirm"))
        self._load_sound("warning", self._pick_menu_sound("warning"))

    def refresh_volumes(self) -> None:
        if not self._mixer_ready:
            return
        sound_volume = float(self.settings["sfx_volume"])
        for sound in self.sounds.values():
            try:
                sound.set_volume(sound_volume)
            except Exception:
                continue
        try:
            pygame.mixer.music.set_volume(float(self.settings["music_volume"]))
        except Exception:
            return
        self.hrtf.set_listener_gain(sound_volume)

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
        x = clamped_pan * 1.8
        y = 0.0
        z = -1.4
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
            z = -4.0
            pitch = 0.92
        elif key in {"warning", "menumove", "menuedge", "menuclose", "confirm"}:
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

    def music_start(self) -> None:
        if not self._mixer_ready:
            return
        theme_path = resource_path("assets", "music", "theme.ogg")
        if not os.path.exists(theme_path):
            return
        try:
            pygame.mixer.music.load(theme_path)
            pygame.mixer.music.set_volume(float(self.settings["music_volume"]))
            pygame.mixer.music.play(-1)
        except Exception:
            return

    def music_stop(self) -> None:
        if not self._mixer_ready:
            return
        try:
            pygame.mixer.music.stop()
        except Exception:
            return
