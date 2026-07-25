import base64
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pydantic_ai.providers.google import GoogleProvider

from presentation_video.infrastructure import speech
from presentation_video.infrastructure.replicate import ReplicateAPIError, ReplicatePredictionClient
from presentation_video.infrastructure.speech import (
    PydanticAIGoogleTTSSynthesizer,
    ReplicateTTSSynthesizer,
)


async def _fake_duration(path: Path) -> float:
    assert path.exists()
    return 1.5


class FakeReplicateClient:
    def __init__(self) -> None:
        self.inputs: dict[str, object] = {}

    async def run(self, model: str, inputs: dict[str, object]) -> str:
        assert model == "google/gemini-3.1-flash-tts"
        self.inputs = inputs
        return "https://example.test/audio.wav"

    @staticmethod
    def output_url(output: Any) -> str:
        return str(output)

    @staticmethod
    async def download(url: str, destination: Path) -> None:
        assert url == "https://example.test/audio.wav"
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"fake-wave")


class RateLimitedReplicateClient(FakeReplicateClient):
    def __init__(self) -> None:
        super().__init__()
        self.attempts = 0

    async def run(self, model: str, inputs: dict[str, object]) -> str:
        self.attempts += 1
        if self.attempts == 1:
            raise ReplicateAPIError(
                "throttled",
                status_code=429,
                retry_after_seconds=7,
            )
        return await super().run(model, inputs)


@pytest.mark.asyncio
async def test_replicate_tts_sends_voice_language_and_style(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(speech, "media_duration", _fake_duration)
    fake = FakeReplicateClient()
    synthesizer = ReplicateTTSSynthesizer(
        cast(ReplicatePredictionClient, fake),
        "google/gemini-3.1-flash-tts",
        voice="Kore",
        style_prompt="Professional narrator.",
    )

    artifact = await synthesizer.synthesize(
        "Olá, mundo.", tmp_path / "voice.wav", language="pt-BR", style="executive"
    )

    assert artifact.duration_seconds == 1.5
    assert fake.inputs["text"] == "Olá, mundo."
    assert fake.inputs["voice"] == "Kore"
    assert fake.inputs["language_code"] == "pt-BR"
    assert "executive" in str(fake.inputs["prompt"])


@pytest.mark.asyncio
async def test_replicate_tts_respects_server_retry_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(speech, "media_duration", _fake_duration)
    monkeypatch.setattr(speech.asyncio, "sleep", fake_sleep)
    fake = RateLimitedReplicateClient()
    synthesizer = ReplicateTTSSynthesizer(
        cast(ReplicatePredictionClient, fake),
        "google/gemini-3.1-flash-tts",
        max_retries=2,
    )

    artifact = await synthesizer.synthesize("Texto.", tmp_path / "voice.wav")

    assert artifact.duration_seconds == 1.5
    assert fake.attempts == 2
    assert delays == [7.5]


class FakeInteractions:
    def __init__(self, pcm: bytes) -> None:
        self.pcm = pcm
        self.arguments: dict[str, object] = {}

    async def create(self, **kwargs: object) -> object:
        self.arguments = kwargs
        audio = SimpleNamespace(
            data=base64.b64encode(self.pcm).decode(),
            channels=1,
            sample_rate=24_000,
        )
        return SimpleNamespace(output_audio=audio)


@pytest.mark.asyncio
async def test_pydantic_ai_google_tts_wraps_pcm_as_wave(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(speech, "media_duration", _fake_duration)
    pcm = b"\x00\x00" * 2_400
    interactions = FakeInteractions(pcm)
    provider = SimpleNamespace(
        client=SimpleNamespace(aio=SimpleNamespace(interactions=interactions))
    )
    synthesizer = PydanticAIGoogleTTSSynthesizer(
        provider=cast(GoogleProvider, provider),
        voice="Kore",
    )
    output_path = tmp_path / "gemini.wav"

    artifact = await synthesizer.synthesize(
        "Texto narrado.", output_path, language="pt-BR", style="natural"
    )

    assert artifact.duration_seconds == 1.5
    assert interactions.arguments["model"] == "gemini-3.1-flash-tts-preview"
    assert interactions.arguments["response_format"] == {"type": "audio"}
    assert "TRANSCRIPT:\nTexto narrado." in str(interactions.arguments["input"])
    with wave.open(str(output_path), "rb") as generated:
        assert generated.getframerate() == 24_000
        assert generated.getnchannels() == 1
        assert generated.readframes(2_400) == pcm
