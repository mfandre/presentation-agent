from __future__ import annotations

import asyncio
import base64
import json
import logging
import wave
from pathlib import Path
from typing import Any, Protocol, cast

from pydantic_ai.providers.google import GoogleProvider

from presentation_video.domain.models import AudioArtifact
from presentation_video.domain.ports import SpeechSynthesizer
from presentation_video.infrastructure.process import run_process
from presentation_video.infrastructure.replicate import ReplicatePredictionClient

logger = logging.getLogger(__name__)


class _AudioInteraction(Protocol):
    output_audio: Any


async def media_duration(path: Path) -> float:
    output = await run_process(
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        str(path),
    )
    duration = float(json.loads(output)["format"]["duration"])
    if duration <= 0:
        raise RuntimeError(f"Invalid media duration for {path}")
    return duration


class EspeakSpeechSynthesizer(SpeechSynthesizer):
    """Local development adapter. Replace with a production-grade TTS adapter without changing the pipeline."""

    def __init__(self, voice: str = "pt-br", rate: int = 155) -> None:
        self._voice = voice
        self._rate = rate

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        language: str | None = None,
        style: str | None = None,
    ) -> AudioArtifact:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await run_process(
            "espeak-ng",
            "-v",
            self._voice,
            "-s",
            str(self._rate),
            "-w",
            str(output_path),
            text,
        )
        return AudioArtifact(path=output_path, duration_seconds=await media_duration(output_path))


class ReplicateTTSSynthesizer(SpeechSynthesizer):
    """Generative TTS through a Replicate-hosted model that returns an audio URL."""

    def __init__(
        self,
        client: ReplicatePredictionClient,
        model: str,
        input_defaults: dict[str, object] | None = None,
        voice: str = "Kore",
        language_code: str = "pt-BR",
        style_prompt: str = "Narração profissional, natural, clara e envolvente.",
        max_retries: int = 2,
    ) -> None:
        self._client = client
        self._model = model
        self._input_defaults = input_defaults or {}
        self._voice = voice
        self._language_code = language_code
        self._style_prompt = style_prompt
        self._max_retries = max_retries

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        language: str | None = None,
        style: str | None = None,
    ) -> AudioArtifact:
        inputs = {
            **self._input_defaults,
            "text": text,
            "voice": self._voice,
            "prompt": _delivery_prompt(self._style_prompt, style),
            "language_code": language or self._language_code,
        }
        for attempt in range(self._max_retries + 1):
            try:
                logger.info(
                    "tts generation started provider=replicate model=%s voice=%s "
                    "language=%s text_characters=%s attempt=%s",
                    self._model,
                    self._voice,
                    inputs["language_code"],
                    len(text),
                    attempt + 1,
                )
                output = await self._client.run(self._model, inputs)
                await self._client.download(self._client.output_url(output), output_path)
                duration = await media_duration(output_path)
                logger.info(
                    "tts generation completed provider=replicate model=%s "
                    "duration_seconds=%.2f path=%s",
                    self._model,
                    duration,
                    output_path,
                )
                return AudioArtifact(path=output_path, duration_seconds=duration)
            except Exception:
                if attempt >= self._max_retries:
                    raise
                logger.warning(
                    "tts generation retry provider=replicate model=%s next_attempt=%s",
                    self._model,
                    attempt + 2,
                    exc_info=True,
                )
                await asyncio.sleep(min(2**attempt, 4))
        raise RuntimeError("Unreachable Replicate TTS retry state")


class PydanticAIGoogleTTSSynthesizer(SpeechSynthesizer):
    """Gemini TTS using the Google client configured by Pydantic AI's provider."""

    def __init__(
        self,
        model: str = "gemini-3.1-flash-tts-preview",
        voice: str = "Kore",
        language_code: str = "pt-BR",
        style_prompt: str = "Narração profissional, natural, clara e envolvente.",
        max_retries: int = 2,
        api_key: str | None = None,
        provider: GoogleProvider | None = None,
    ) -> None:
        if provider is None and not api_key:
            raise ValueError("GOOGLE_API_KEY is required when TTS_PROVIDER=pydantic_ai")
        self._provider = provider or GoogleProvider(api_key=api_key or "")
        self._model = model.removeprefix("google:")
        self._voice = voice
        self._language_code = language_code
        self._style_prompt = style_prompt
        self._max_retries = max_retries

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        language: str | None = None,
        style: str | None = None,
    ) -> AudioArtifact:
        spoken_language = language or self._language_code
        instruction = (
            "Synthesize speech only. Do not read the directions or section labels aloud.\n"
            f"LANGUAGE: {spoken_language}\n"
            f"DIRECTOR NOTES: {_delivery_prompt(self._style_prompt, style)}\n"
            f"TRANSCRIPT:\n{text}"
        )
        for attempt in range(self._max_retries + 1):
            try:
                logger.info(
                    "tts generation started provider=pydantic_ai model=%s voice=%s "
                    "language=%s text_characters=%s attempt=%s",
                    self._model,
                    self._voice,
                    spoken_language,
                    len(text),
                    attempt + 1,
                )
                interaction = cast(
                    _AudioInteraction,
                    await self._provider.client.aio.interactions.create(
                        model=self._model,
                        input=instruction,
                        stream=False,
                        response_format={"type": "audio"},
                        generation_config={"speech_config": [{"voice": self._voice}]},
                    ),
                )
                audio = interaction.output_audio
                if audio is None or audio.data is None:
                    raise RuntimeError("Gemini TTS returned no audio data")
                _write_pcm_wave(
                    output_path,
                    base64.b64decode(str(audio.data)),
                    channels=audio.channels or 1,
                    sample_rate=audio.sample_rate or 24_000,
                )
                duration = await media_duration(output_path)
                logger.info(
                    "tts generation completed provider=pydantic_ai model=%s "
                    "duration_seconds=%.2f path=%s",
                    self._model,
                    duration,
                    output_path,
                )
                return AudioArtifact(path=output_path, duration_seconds=duration)
            except Exception:
                if attempt >= self._max_retries:
                    raise
                logger.warning(
                    "tts generation retry provider=pydantic_ai model=%s next_attempt=%s",
                    self._model,
                    attempt + 2,
                    exc_info=True,
                )
                await asyncio.sleep(min(2**attempt, 4))
        raise RuntimeError("Unreachable Pydantic AI TTS retry state")


def _delivery_prompt(configured_prompt: str, runtime_style: str | None) -> str:
    if runtime_style and runtime_style.strip():
        return f"{configured_prompt.strip()} Tone requested for this video: {runtime_style.strip()}"
    return configured_prompt.strip()


def _write_pcm_wave(
    output_path: Path,
    pcm: bytes,
    channels: int = 1,
    sample_rate: int = 24_000,
    sample_width: int = 2,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(sample_rate)
        output.writeframes(pcm)
