from __future__ import annotations

import hashlib
import wave
from pathlib import Path

from presentation_video.application.audio_cache import valid_wav_duration
from presentation_video.domain.models import (
    AudioArtifact,
    PresentationScript,
    ProductionMode,
    SceneScript,
)
from presentation_video.domain.ports import SpeechSynthesizer


DEFAULT_DIALOGUE_VOICES = (
    "Kore",
    "Puck",
    "Aoede",
    "Charon",
    "Fenrir",
    "Leda",
    "Orus",
    "Zephyr",
)


def uses_character_dialogue(
    production_mode: ProductionMode,
    preset_options: dict[str, str],
) -> bool:
    return (
        production_mode == ProductionMode.CINEMATIC_STORY
        and preset_options.get("speech_mode") == "character_dialogue"
    )


def voice_for_character(character_id: str, voices: tuple[str, ...]) -> str:
    if not voices:
        raise ValueError("at least one dialogue voice must be configured")
    digest = hashlib.sha256(character_id.encode("utf-8")).digest()
    return voices[int.from_bytes(digest[:4], "big") % len(voices)]


def build_character_voice_map(
    script: PresentationScript,
    voices: tuple[str, ...],
) -> dict[str, str]:
    if not voices:
        raise ValueError("at least one dialogue voice must be configured")
    character_ids = sorted(
        {
            line.character_id
            for scene in script.scenes
            for line in scene.dialogue
        }
    )
    return {
        character_id: voices[index % len(voices)]
        for index, character_id in enumerate(character_ids)
    }


async def synthesize_scene_audio(
    scene: SceneScript,
    output_path: Path,
    synthesizer: SpeechSynthesizer,
    *,
    language: str,
    style: str,
    dialogue_mode: bool,
    voices: tuple[str, ...] = DEFAULT_DIALOGUE_VOICES,
    voice_map: dict[str, str] | None = None,
    pause_seconds: float = 0.18,
) -> AudioArtifact:
    if not dialogue_mode:
        return await synthesizer.synthesize(
            scene.narration,
            output_path,
            language=language,
            style=style,
        )
    if not scene.dialogue:
        raise ValueError(
            f"scene {scene.scene_number} uses character dialogue but has no dialogue lines"
        )

    line_dir = output_path.parent / "dialogue" / f"scene-{scene.scene_number:03d}"
    line_artifacts: list[AudioArtifact] = []
    for line_number, line in enumerate(scene.dialogue, start=1):
        line_path = line_dir / f"line-{line_number:03d}.wav"
        cached_duration = valid_wav_duration(line_path)
        if cached_duration is not None:
            line_artifacts.append(
                AudioArtifact(path=line_path, duration_seconds=cached_duration)
            )
            continue
        delivery = (
            f"{style}. Perform as {line.character_name}. "
            f"Emotion and intention: {line.emotion}. Natural character dialogue; "
            "do not announce the character name."
        )
        line_artifacts.append(
            await synthesizer.synthesize(
                line.text,
                line_path,
                language=language,
                style=delivery,
                voice=(voice_map or {}).get(line.character_id)
                or voice_for_character(line.character_id, voices),
            )
        )

    duration = _join_pcm_waves(
        [artifact.path for artifact in line_artifacts],
        output_path,
        pause_seconds=pause_seconds,
    )
    return AudioArtifact(path=output_path, duration_seconds=duration)


def _join_pcm_waves(
    inputs: list[Path],
    output_path: Path,
    *,
    pause_seconds: float,
) -> float:
    if not inputs:
        raise ValueError("dialogue audio requires at least one line")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    parameters: tuple[int, int, int] | None = None
    chunks: list[bytes] = []
    total_frames = 0
    for path in inputs:
        try:
            with wave.open(str(path), "rb") as source:
                current = (
                    source.getnchannels(),
                    source.getsampwidth(),
                    source.getframerate(),
                )
                if parameters is None:
                    parameters = current
                elif current != parameters:
                    raise ValueError(
                        "dialogue line WAV formats differ; all voices must use the same "
                        "channel count, sample width, and sample rate"
                    )
                frame_count = source.getnframes()
                chunks.append(source.readframes(frame_count))
                total_frames += frame_count
        except (OSError, EOFError, wave.Error) as exc:
            raise ValueError(f"dialogue TTS did not return a readable PCM WAV: {path}") from exc

    assert parameters is not None
    channels, sample_width, frame_rate = parameters
    pause_frames = max(0, round(frame_rate * pause_seconds))
    silence = b"\x00" * pause_frames * channels * sample_width
    total_frames += pause_frames * max(len(chunks) - 1, 0)
    with wave.open(str(output_path), "wb") as destination:
        destination.setnchannels(channels)
        destination.setsampwidth(sample_width)
        destination.setframerate(frame_rate)
        for index, chunk in enumerate(chunks):
            if index:
                destination.writeframes(silence)
            destination.writeframes(chunk)
    duration = total_frames / frame_rate
    if duration <= 0:
        raise ValueError("dialogue TTS returned empty audio")
    return duration
