from __future__ import annotations

import audioop
import hashlib
import os
import wave
from pathlib import Path

from subway_blind.config import resource_path


class OpenALHrtfEngine:
    def __init__(self, sfx_volume: float):
        self.available = False
        self._al = None
        self._device = None
        self._context = None
        self._buffers: dict[str, object] = {}
        self._buffer_paths: dict[str, str] = {}
        self._sources: dict[str, object] = {}
        self._channel_keys: dict[str, str] = {}
        self._listener_gain = max(0.0, min(1.0, float(sfx_volume)))
        try:
            import pyopenalsoft as openal
        except Exception:
            return
        self._al = openal
        try:
            self._configure_openal_soft()
            self._al.init()
            self._device = self._al.Device()
            self._context = self._al.Context(self._device)
            self._al.Listener.reset()
            self._al.Listener.set_position(0.0, 0.0, 0.0)
            self._al.Listener.set_velocity(0.0, 0.0, 0.0)
            self._al.Listener.set_orientation(0.0, 0.0, -1.0, 0.0, 1.0, 0.0)
            self.available = True
        except Exception:
            self.available = False

    def _configure_openal_soft(self) -> None:
        config_root = Path(resource_path("data", "openal"))
        config_root.mkdir(parents=True, exist_ok=True)
        config_path = config_root / "alsoft.ini"
        config_path.write_text(
            "[general]\n"
            "stereo-mode = headphones\n"
            "hrtf = true\n"
            "sources = 128\n"
            "slots = 16\n",
            encoding="utf-8",
        )
        os.environ["APPDATA"] = str(config_root)

    def register_sound(self, key: str, path: str) -> None:
        if not self.available or self._al is None:
            return
        if not Path(path).exists():
            return
        mono_path = self._prepare_mono_path(path)
        if self._buffer_paths.get(key) == mono_path and key in self._buffers:
            return
        audio_data = self._al.AudioData(mono_path)
        self._buffers[key] = self._al.Buffer(audio_data)
        self._buffer_paths[key] = mono_path

    def _prepare_mono_path(self, path: str) -> str:
        source = Path(path)
        if source.suffix.lower() != ".wav":
            return path
        try:
            with wave.open(str(source), "rb") as reader:
                channels = reader.getnchannels()
                sample_width = reader.getsampwidth()
                frame_rate = reader.getframerate()
                frames = reader.readframes(reader.getnframes())
        except Exception:
            return path

        if channels == 1:
            return path

        cache_directory = Path(resource_path("data", "openal_cache"))
        cache_directory.mkdir(parents=True, exist_ok=True)
        fingerprint = hashlib.sha1(
            f"{source.resolve()}::{source.stat().st_mtime_ns}::{source.stat().st_size}".encode("utf-8")
        ).hexdigest()[:16]
        cache_path = cache_directory / f"{source.stem}_{fingerprint}_mono.wav"
        if cache_path.exists():
            return str(cache_path)

        try:
            mono_frames = self._downmix_to_mono(frames, channels, sample_width)
            with wave.open(str(cache_path), "wb") as writer:
                writer.setnchannels(1)
                writer.setsampwidth(sample_width)
                writer.setframerate(frame_rate)
                writer.writeframes(mono_frames)
            return str(cache_path)
        except Exception:
            return path

    def _downmix_to_mono(self, frames: bytes, channels: int, sample_width: int) -> bytes:
        if channels <= 1:
            return frames
        if channels == 2:
            return audioop.tomono(frames, sample_width, 0.5, 0.5)

        frame_step = sample_width * channels
        mono_chunks: list[bytes] = []
        for offset in range(0, len(frames), frame_step):
            frame = frames[offset : offset + frame_step]
            if len(frame) < frame_step:
                break
            mono = audioop.tomono(frame[: sample_width * 2], sample_width, 0.5, 0.5)
            mono_chunks.append(mono)
        return b"".join(mono_chunks)

    def set_listener_gain(self, sfx_volume: float) -> None:
        self._listener_gain = max(0.0, min(1.0, float(sfx_volume)))

    def _stop_source(self, source) -> None:
        try:
            source.stop()
        except Exception:
            return

    def update_source(
        self,
        channel: str,
        x: float,
        y: float,
        z: float,
        gain: float,
        pitch: float = 1.0,
        relative: bool = False,
        velocity_x: float = 0.0,
        velocity_y: float = 0.0,
        velocity_z: float = 0.0,
    ) -> bool:
        if not self.available:
            return False
        source = self._sources.get(channel)
        if source is None or not getattr(source, "playing", False):
            return False
        source.relative = relative
        source.gain = max(0.0, min(1.2, self._listener_gain * float(gain)))
        source.pitch = max(0.5, min(1.5, float(pitch)))
        source.set_position(float(x), float(y), float(z))
        source.set_velocity(float(velocity_x), float(velocity_y), float(velocity_z))
        return True

    def play_sound(
        self,
        key: str,
        path: str,
        channel: str,
        x: float,
        y: float,
        z: float,
        gain: float,
        pitch: float = 1.0,
        loop: bool = False,
        relative: bool = False,
        velocity_x: float = 0.0,
        velocity_y: float = 0.0,
        velocity_z: float = 0.0,
    ) -> bool:
        if not self.available or self._al is None:
            return False
        self.register_sound(key, path)
        buffer = self._buffers.get(key)
        if buffer is None:
            return False
        source = self._sources.get(channel)
        if source is None:
            source = self._al.Source()
            source.reference_distance = 1.5
            source.rolloff_factor = 1.0
            source.max_distance = 48.0
            self._sources[channel] = source
        current_key = self._channel_keys.get(channel)
        if current_key != key:
            self._stop_source(source)
            source.set_buffer(buffer)
            self._channel_keys[channel] = key
        elif not loop:
            self._stop_source(source)
        source.relative = relative
        source.looping = loop
        source.gain = max(0.0, min(1.2, self._listener_gain * float(gain)))
        source.pitch = max(0.5, min(1.5, float(pitch)))
        source.set_position(float(x), float(y), float(z))
        source.set_velocity(float(velocity_x), float(velocity_y), float(velocity_z))
        if loop and source.playing:
            return True
        source.play()
        return True

    def stop(self, channel: str) -> None:
        source = self._sources.get(channel)
        if source is None:
            return
        self._stop_source(source)
        self._channel_keys.pop(channel, None)
