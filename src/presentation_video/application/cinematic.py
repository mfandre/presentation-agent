from __future__ import annotations

import math
import re

from presentation_video.domain.models import (
    MediaMode,
    MotionPreset,
    SceneScript,
    TransitionPreset,
    VisualScenePlan,
    VisualShotPlan,
)

MAX_SHOT_SECONDS = 8.0
_MOTIONS = (
    MotionPreset.SLOW_PUSH,
    MotionPreset.PAN_RIGHT,
    MotionPreset.PULL_BACK,
    MotionPreset.PAN_LEFT,
    MotionPreset.DRIFT_UP,
)


def _split_words(text: str, count: int) -> list[str]:
    words = text.split()
    if count <= 1 or len(words) <= 1:
        return [text.strip()]
    chunks: list[str] = []
    for index in range(count):
        start = round(index * len(words) / count)
        end = round((index + 1) * len(words) / count)
        chunks.append(" ".join(words[start:end]).strip() or text.strip())
    return chunks


def _split_semantic_units(text: str, count: int) -> list[str]:
    clauses = [
        part.strip()
        for part in re.split(r"(?<=[.!?;:])\s+|(?<=,)\s+(?=[A-ZÀ-Ý])", text.strip())
        if part.strip()
    ]
    if count <= 1:
        return [text.strip()]
    if len(clauses) < count:
        return _split_words(text, count)

    total_words = sum(len(clause.split()) for clause in clauses)
    target_words = total_words / count
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for index, clause in enumerate(clauses):
        remaining_clauses = len(clauses) - index
        remaining_chunks = count - len(chunks)
        clause_words = len(clause.split())
        should_close = (
            current
            and current_words + clause_words > target_words
            and remaining_clauses >= remaining_chunks
        )
        if should_close:
            chunks.append(" ".join(current))
            current = []
            current_words = 0
        current.append(clause)
        current_words += clause_words
    if current:
        chunks.append(" ".join(current))
    if len(chunks) != count:
        return _split_words(text, count)
    return chunks


def _story_function(index: int, total: int, story_beat: str) -> str:
    if index == 0:
        return "establish_context"
    if index == total - 1:
        return "resolve_and_transition"
    if story_beat in {"opening", "hook"}:
        return "reveal"
    if story_beat in {"conclusion", "resolution"}:
        return "demonstrate_outcome"
    return "develop_argument"


def compile_shots(
    scene: VisualScenePlan,
    script: SceneScript,
    duration_seconds: float,
    continuity_in: str | None = None,
    maximum_shot_seconds: float = MAX_SHOT_SECONDS,
) -> list[VisualShotPlan]:
    """Compile one narrative scene into grounded, continuous shots of at most eight seconds."""

    if not 0 < maximum_shot_seconds <= MAX_SHOT_SECONDS:
        raise ValueError("maximum shot duration must be between 0 and 8 seconds")
    shot_count = max(1, math.ceil(duration_seconds / maximum_shot_seconds))
    excerpts = _split_semantic_units(script.narration, shot_count)
    base_duration = duration_seconds / shot_count
    shots: list[VisualShotPlan] = []
    cursor = 0.0
    previous_state = continuity_in or (f"begin {scene.story_beat} in the established visual world")
    for index in range(shot_count):
        duration = duration_seconds - cursor if index == shot_count - 1 else round(base_duration, 3)
        duration = round(min(max(duration, 0.1), maximum_shot_seconds), 3)
        story_function = _story_function(index, shot_count, scene.story_beat)
        next_state = (
            f"shot {index + 1} completes {story_function} and leaves visible momentum "
            f"toward shot {index + 2 if index + 1 < shot_count else 'the next scene'}"
        )
        prompt = (
            f"{scene.prompt}\n"
            f"NARRATION NOW: {excerpts[index]}\n"
            f"SHOT FUNCTION: {story_function}.\n"
            f"REQUIRED CONCEPTS: {', '.join(scene.must_show_concepts) or 'source-grounded action'}.\n"
            f"CONTINUITY IN: {previous_state}.\n"
            f"CONTINUITY OUT: {next_state}.\n"
            "Create one distinct cinematic shot at natural speed. Show a concrete action that "
            "visibly expresses this exact narration excerpt. Preserve recurring people, setting, "
            "era, wardrobe, palette, lighting, materials, and screen direction from continuity. "
            "No slide, page, document, presentation layout, readable text, caption, interface, "
            "logo, watermark, montage, split screen, or loop."
        )
        shots.append(
            VisualShotPlan(
                shot_number=index + 1,
                start_seconds=cursor,
                duration_seconds=duration,
                narration_excerpt=excerpts[index],
                story_function=story_function,
                prompt=prompt,
                negative_prompt=(
                    f"{scene.negative_prompt}, slide, page, document, presentation, readable text, "
                    "caption, interface, logo, watermark, repeated motion, loop"
                ),
                continuity_in=previous_state,
                continuity_out=next_state,
                camera_motion=scene.camera_motion,
                motion_preset=_MOTIONS[index % len(_MOTIONS)],
                transition=(
                    TransitionPreset.DISSOLVE if index == shot_count - 1 else TransitionPreset.CUT
                ),
                required_concepts=scene.must_show_concepts,
            )
        )
        cursor = round(cursor + duration, 3)
        previous_state = next_state
    validate_shots(shots, duration_seconds)
    return shots


def validate_shots(shots: list[VisualShotPlan], duration_seconds: float) -> None:
    if not shots:
        raise ValueError("cinematic scenes require at least one shot")
    cursor = 0.0
    for expected_number, shot in enumerate(shots, start=1):
        if shot.shot_number != expected_number:
            raise ValueError("shot numbers must be contiguous")
        if shot.duration_seconds > MAX_SHOT_SECONDS:
            raise ValueError("generated shots cannot exceed 8 seconds")
        if abs(shot.start_seconds - cursor) > 0.02:
            raise ValueError("shot timeline contains a gap or overlap")
        if re.search(r"\b(slide|powerpoint|presentation page)\b", shot.prompt, re.I):
            # The compiler contains these words only inside an explicit negative rule.
            if "No slide" not in shot.prompt:
                raise ValueError("cinematic prompt requests a forbidden source slide")
        if shot.required_concepts and "REQUIRED CONCEPTS:" not in shot.prompt:
            raise ValueError("shot prompt omits its required concepts")
        cursor += shot.duration_seconds
    if abs(cursor - duration_seconds) > 0.05:
        raise ValueError("shots do not cover the complete narration duration")


def materialize_shot(scene: VisualScenePlan, shot: VisualShotPlan) -> VisualScenePlan:
    """Present one shot through the existing image/video provider ports."""

    return scene.model_copy(
        update={
            "shot_number": shot.shot_number,
            "prompt": shot.prompt,
            "negative_prompt": shot.negative_prompt,
            "media_mode": MediaMode.VIDEO,
            "preserve_source_frame": False,
            "source_slide_number": None,
            "camera_motion": shot.camera_motion,
            "motion_preset": shot.motion_preset,
            "transition_preset": shot.transition,
            "visual_beats": [],
            "shots": [],
        }
    )
