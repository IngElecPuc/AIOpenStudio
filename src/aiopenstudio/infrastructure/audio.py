"""Optional local microphone capture using sounddevice."""

from __future__ import annotations

import asyncio
import importlib.util
import math
import wave
from importlib import import_module
from pathlib import Path
from threading import RLock
from typing import Any

from aiopenstudio.core.errors import AudioCaptureUnavailableError, RuntimeRequestError


class PyAVAudioInspector:
    """Inspect duration through the already pinned PyAV container reader."""

    async def duration_seconds(self, source: Path) -> float:
        return await asyncio.to_thread(self._duration_blocking, source)

    @staticmethod
    def _duration_blocking(source: Path) -> float:
        if not source.is_file():
            raise RuntimeRequestError("El archivo de audio seleccionado no existe.")
        if importlib.util.find_spec("av") is None:
            raise RuntimeRequestError("La vista por fragmentos requiere PyAV.")
        av = import_module("av")
        try:
            with av.open(str(source), mode="r") as container:
                if container.duration is not None:
                    duration = float(container.duration / av.time_base)
                else:
                    candidates = [
                        float(stream.duration * stream.time_base)
                        for stream in container.streams.audio
                        if stream.duration is not None and stream.time_base is not None
                    ]
                    duration = max(candidates, default=0.0)
        except Exception as error:
            raise RuntimeRequestError(
                "No fue posible leer la duración del audio con PyAV."
            ) from error
        if not math.isfinite(duration) or duration <= 0:
            raise RuntimeRequestError("El contenedor no informa una duración de audio válida.")
        return duration


class SoundDeviceAudioRecorder:
    """Capture mono PCM without importing sounddevice during application import."""

    def __init__(self, *, sample_rate: int = 16_000, channels: int = 1) -> None:
        self._sample_rate = sample_rate
        self._channels = channels
        self._frames: list[bytes] = []
        self._stream: Any | None = None
        self._lock = RLock()

    @property
    def available(self) -> bool:
        return importlib.util.find_spec("sounddevice") is not None

    async def start(self) -> None:
        await asyncio.to_thread(self._start_blocking)

    async def stop(self, destination: Path) -> Path:
        return await asyncio.to_thread(self._stop_blocking, destination)

    async def cancel(self) -> None:
        await asyncio.to_thread(self._cancel_blocking)

    def _start_blocking(self) -> None:
        if not self.available:
            raise AudioCaptureUnavailableError(
                "La captura por micrófono requiere la dependencia opcional sounddevice."
            )
        if self._stream is not None:
            raise RuntimeRequestError("Ya existe una grabación activa.")
        sd = import_module("sounddevice")

        with self._lock:
            self._frames.clear()

        def callback(indata: Any, frames: int, time_info: Any, status: Any) -> None:
            del frames, time_info, status
            with self._lock:
                self._frames.append(bytes(indata))

        self._stream = sd.RawInputStream(
            samplerate=self._sample_rate,
            channels=self._channels,
            dtype="int16",
            callback=callback,
        )
        self._stream.start()

    def _stop_blocking(self, destination: Path) -> Path:
        if self._stream is None:
            raise RuntimeRequestError("No existe una grabación activa.")
        self._stream.stop()
        self._stream.close()
        self._stream = None
        destination = destination.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".partial")
        with self._lock:
            frames = tuple(self._frames)
            self._frames.clear()
        with wave.open(str(temporary), "wb") as output:
            output.setnchannels(self._channels)
            output.setsampwidth(2)
            output.setframerate(self._sample_rate)
            output.writeframes(b"".join(frames))
        temporary.replace(destination)
        return destination

    def _cancel_blocking(self) -> None:
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            self._frames.clear()
