from __future__ import annotations

import re
import math
from dataclasses import dataclass
from pathlib import Path

from presentation_video.domain.models import PresentationScript, SceneArtifact


@dataclass(frozen=True, slots=True)
class CaptionCue:
    start_seconds: float
    end_seconds: float
    text: str


def _split_caption_units(text: str, maximum_words: int = 12) -> list[str]:
    sentences = [
        part.strip()
        for part in re.split(r"(?<=[.!?])\s+", text.strip())
        if part.strip()
    ]
    units: list[str] = []
    for sentence in sentences:
        words = sentence.split()
        if len(words) <= maximum_words:
            units.append(sentence)
            continue
        chunk_count = math.ceil(len(words) / maximum_words)
        for index in range(chunk_count):
            start = round(index * len(words) / chunk_count)
            end = round((index + 1) * len(words) / chunk_count)
            units.append(" ".join(words[start:end]))
    return units or [text.strip()]


def build_caption_cues(
    script: PresentationScript,
    scenes: list[SceneArtifact],
) -> list[CaptionCue]:
    scripts = {scene.scene_number: scene for scene in script.scenes}
    cursor = 0.0
    cues: list[CaptionCue] = []
    for rendered in sorted(scenes, key=lambda scene: scene.scene_number):
        narration = scripts[rendered.scene_number].narration
        units = _split_caption_units(narration)
        weights = [max(len(unit.split()), 1) for unit in units]
        total_weight = sum(weights)
        local_cursor = cursor
        for index, (unit, weight) in enumerate(zip(units, weights, strict=True)):
            end = (
                cursor + rendered.duration_seconds
                if index == len(units) - 1
                else local_cursor + rendered.duration_seconds * weight / total_weight
            )
            cues.append(
                CaptionCue(
                    start_seconds=round(local_cursor, 3),
                    end_seconds=round(end, 3),
                    text=unit,
                )
            )
            local_cursor = end
        cursor += rendered.duration_seconds
    return cues


def _timestamp(seconds: float, *, srt: bool) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    whole_seconds, millis = divmod(remainder, 1000)
    separator = "," if srt else "."
    return (
        f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"
        f"{separator}{millis:03d}"
    )


def write_caption_files(
    cues: list[CaptionCue],
    output_dir: Path,
    language: str,
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_language = re.sub(r"[^A-Za-z0-9-]", "-", language) or "und"
    vtt_path = output_dir / f"captions.{safe_language}.vtt"
    srt_path = output_dir / f"captions.{safe_language}.srt"
    vtt_blocks = ["WEBVTT", ""]
    srt_blocks: list[str] = []
    for index, cue in enumerate(cues, start=1):
        vtt_blocks.extend(
            [
                str(index),
                f"{_timestamp(cue.start_seconds, srt=False)} --> "
                f"{_timestamp(cue.end_seconds, srt=False)}",
                cue.text,
                "",
            ]
        )
        srt_blocks.extend(
            [
                str(index),
                f"{_timestamp(cue.start_seconds, srt=True)} --> "
                f"{_timestamp(cue.end_seconds, srt=True)}",
                cue.text,
                "",
            ]
        )
    vtt_path.write_text("\n".join(vtt_blocks), encoding="utf-8")
    srt_path.write_text("\n".join(srt_blocks), encoding="utf-8")
    return vtt_path, srt_path
