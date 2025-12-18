from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


try:
    import pygame  # type: ignore
except Exception:
    pygame = None


@dataclass(slots=True)
class PlaybackState:
    ready: bool
    paused: bool


class AudioPlayer:
    def __init__(self, volume: float = 0.7) -> None:
        self._ready = False
        self._paused = False
        self._init(volume)

    def _init(self, volume: float) -> None:
        if pygame is None:
            self._ready = False
            return
        try:
            pygame.mixer.init()
            pygame.mixer.music.set_volume(float(volume))
            self._ready = True
        except Exception:
            self._ready = False

    def is_ready(self) -> bool:
        return bool(self._ready)

    def is_paused(self) -> bool:
        return bool(self._paused)

    def is_playing(self) -> bool:
        if not self._ready:
            return False
        try:
            return bool(pygame.mixer.music.get_busy())
        except Exception:
            return False

    def set_volume(self, volume: float) -> None:
        if not self._ready:
            return
        try:
            pygame.mixer.music.set_volume(float(volume))
        except Exception:
            pass

    def play(self, path: str, volume: float) -> None:
        if not self._ready:
            raise RuntimeError("Audio not available (pygame missing or failed init)")
        self.stop()
        pygame.mixer.music.load(path)
        pygame.mixer.music.set_volume(float(volume))
        pygame.mixer.music.play()
        self._paused = False

    def pause(self) -> None:
        if not self._ready:
            return
        try:
            pygame.mixer.music.pause()
            self._paused = True
        except Exception:
            pass

    def resume(self) -> None:
        if not self._ready:
            return
        try:
            pygame.mixer.music.unpause()
            self._paused = False
        except Exception:
            pass

    def stop(self) -> None:
        if not self._ready:
            return
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        self._paused = False

    def shutdown(self) -> None:
        if not self._ready:
            return
        try:
            pygame.mixer.music.stop()
            pygame.mixer.quit()
        except Exception:
            pass
