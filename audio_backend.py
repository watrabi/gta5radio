"""
Wraps the "main" audio stream (song / dj / ad / news) behind one small
API, regardless of which pygame backend actually ends up playing it.

Why this exists: pygame.mixer.music.play(start=...) is how you start a
clip partway through, but SDL_mixer's support for that `start` offset
is spotty for WAV specifically (it works reliably for mp3/ogg, but WAV
frequently just ignores it and starts from 0:00 -- which is exactly
the case the app needs for "always-on stations" to feel right when you
tune into a station mid-song).

To work around that, when we need a mid-file start on a .wav, we slice
the raw PCM data ourselves with the stdlib `wave` module and hand the
trimmed audio to pygame.mixer.Sound on a dedicated channel instead of
pygame.mixer.music. Everything else (mp3/ogg/flac, or any wav that
happens to start at 0:00) goes through pygame.mixer.music as before.

This class also doubles as the single source of truth for "how far
into the current clip are we", which is what drives the progress bar.
"""

import io
import time
import wave
from pathlib import Path

import pygame

MAIN_CHANNEL_ID = 0  # reserved via pygame.mixer.set_reserved() so Sound.play()
                      # auto-allocation (used for DJ overlays) never steals it


def slice_wav(path: Path, start_offset: float):
    """Return trimmed WAV bytes (with a valid header) for `path`
    starting at `start_offset` seconds in, or None if slicing fails
    for any reason (caller falls back to playing from the top)."""
    try:
        with wave.open(str(path), "rb") as src:
            rate = src.getframerate()
            n_frames = src.getnframes()
            start_frame = min(int(start_offset * rate), n_frames)
            src.setpos(start_frame)
            frames = src.readframes(n_frames - start_frame)
            channels, sampwidth = src.getnchannels(), src.getsampwidth()

        if not frames:
            return None

        buf = io.BytesIO()
        with wave.open(buf, "wb") as dst:
            dst.setnchannels(channels)
            dst.setsampwidth(sampwidth)
            dst.setframerate(rate)
            dst.writeframes(frames)
        buf.seek(0)
        return buf.read()
    except (wave.Error, EOFError, OSError):
        return None


class MainPlayer:
    """Plays one clip at a time and tracks playback position for the UI."""

    def __init__(self):
        self._channel = pygame.mixer.Channel(MAIN_CHANNEL_ID)
        self._mode = None          # "music" or "channel"
        self._start_offset = 0.0   # seconds into the file playback began at
        self._play_time = None     # time.time() when playback began
        self._paused_at = None     # frozen get_position() value while paused
        self.used_slice_fallback = False  # True if the last play() used the wav-slice path

    def play(self, path: Path, start_offset=0.0, fade_ms=200, volume=1.0):
        """Start playing `path`, resuming from `start_offset` seconds in
        when possible. Returns the offset actually achieved (0.0 if the
        format/backend couldn't honor the request)."""
        self.used_slice_fallback = False

        if path.suffix.lower() == ".wav" and start_offset > 0.01:
            sliced = slice_wav(path, start_offset)
            if sliced is not None:
                try:
                    sound = pygame.mixer.Sound(io.BytesIO(sliced))
                    self._channel.set_volume(volume)
                    self._channel.play(sound, fade_ms=fade_ms)
                    self._mode = "channel"
                    self.used_slice_fallback = True
                    self._start_offset = start_offset
                    self._play_time = time.time()
                    self._paused_at = None
                    return start_offset
                except pygame.error:
                    pass  # fall through to the pygame.mixer.music path below

        pygame.mixer.music.load(str(path))
        pygame.mixer.music.set_volume(volume)
        try:
            pygame.mixer.music.play(fade_ms=fade_ms, start=start_offset)
            achieved_offset = start_offset
        except (pygame.error, NotImplementedError):
            pygame.mixer.music.play(fade_ms=fade_ms)
            achieved_offset = 0.0

        self._mode = "music"
        self._start_offset = achieved_offset
        self._play_time = time.time()
        self._paused_at = None
        return achieved_offset

    def pause(self):
        if self._mode == "music":
            pygame.mixer.music.pause()
        elif self._mode == "channel":
            self._channel.pause()
        if self._play_time is not None and self._paused_at is None:
            self._paused_at = self.get_position()

    def unpause(self):
        if self._mode == "music":
            pygame.mixer.music.unpause()
        elif self._mode == "channel":
            self._channel.unpause()
        if self._paused_at is not None:
            self._start_offset = self._paused_at
            self._play_time = time.time()
            self._paused_at = None

    def fadeout(self, ms):
        if self._mode == "music":
            pygame.mixer.music.fadeout(ms)
        elif self._mode == "channel":
            self._channel.fadeout(ms)

    def stop(self):
        if self._mode == "music":
            pygame.mixer.music.stop()
        elif self._mode == "channel":
            self._channel.stop()
        self._play_time = None
        self._paused_at = None

    def set_volume(self, v):
        if self._mode == "music":
            pygame.mixer.music.set_volume(v)
        elif self._mode == "channel":
            self._channel.set_volume(v)

    def get_busy(self):
        if self._mode == "music":
            return pygame.mixer.music.get_busy()
        if self._mode == "channel":
            return self._channel.get_busy()
        return False

    def get_position(self):
        """Seconds into the current clip right now, accounting for the
        offset it started at and any time spent paused. This is a wall
        -clock estimate (pygame doesn't expose true sample position for
        either backend here), which is accurate enough for a UI progress
        bar."""
        if self._paused_at is not None:
            return self._paused_at
        if self._play_time is None:
            return 0.0
        return self._start_offset + (time.time() - self._play_time)
