from __future__ import annotations

import asyncio
from pathlib import Path

from faster_whisper import WhisperModel

from tg_assistant.config import settings


class SpeechToTextService:
    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        language: str | None = None,
    ) -> None:
        self._model_name = model_name or settings.whisper_model
        self._device = device or settings.whisper_device
        self._compute_type = compute_type or settings.whisper_compute_type
        self._language = language if language is not None else settings.whisper_language
        self._model: WhisperModel | None = None
        self._lock = asyncio.Lock()

    async def _get_model(self) -> WhisperModel:
        if self._model is not None:
            return self._model

        async with self._lock:
            if self._model is None:
                self._model = await asyncio.to_thread(
                    WhisperModel,
                    self._model_name,
                    device=self._device,
                    compute_type=self._compute_type,
                )
        return self._model

    async def transcribe(self, audio_path: Path) -> str:
        model = await self._get_model()

        def _run() -> str:
            segments, _info = model.transcribe(
                str(audio_path),
                language=self._language,
                vad_filter=True,
            )
            return " ".join(segment.text.strip() for segment in segments).strip()

        return await asyncio.to_thread(_run)
