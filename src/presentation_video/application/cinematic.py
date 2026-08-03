from __future__ import annotations

import math
import re

from presentation_video.application.content_audit import assign_information_to_shots
from presentation_video.domain.models import (
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


def _visual_progression(index: int, total: int) -> str:
    if total == 1:
        return (
            "Use one decisive medium-wide composition that captures the exact narrated action "
            "without summarizing earlier or later events."
        )
    if index == 0:
        return (
            "Use a wide establishing composition and show the initial state or the beginning of "
            "the action. Do not reveal the outcome yet."
        )
    if index == total - 1:
        return (
            "Use a clearly new resolving composition, wider or reverse-angle, showing the visible "
            "outcome of this excerpt and a clean transition forward."
        )
    if index % 2:
        return (
            "Cut to a medium action view from a new angle. Show the next physical action in this "
            "excerpt, not the established view and not the final outcome."
        )
    return (
        "Cut to a close evidence-rich detail of the object, hands, expression, or consequence "
        "named in this excerpt. Do not reuse the previous camera position."
    )


def compile_shots(
    scene: VisualScenePlan,
    script: SceneScript,
    duration_seconds: float,
    continuity_in: str | None = None,
    maximum_shot_seconds: float = MAX_SHOT_SECONDS,
    preserve_exact_source_frame: bool = False,
    exact_information_enabled: bool = True,
) -> list[VisualShotPlan]:
    """Compile one narrative scene into grounded, continuous shots of at most eight seconds."""

    if not 0 < maximum_shot_seconds <= MAX_SHOT_SECONDS:
        raise ValueError("maximum shot duration must be between 0 and 8 seconds")
    shot_count = max(1, math.ceil(duration_seconds / maximum_shot_seconds))
    excerpts = _split_semantic_units(script.narration, shot_count)
    dialogue_directions = _dialogue_directions(script, shot_count)
    action_steps = (
        scene.action_progression
        if len(scene.action_progression) == shot_count
        else [
            f"Show only the physical event described by this narration excerpt: {excerpt}"
            for excerpt in excerpts
        ]
    )
    base_duration = duration_seconds / shot_count
    shots: list[VisualShotPlan] = []
    cursor = 0.0
    previous_state = continuity_in or (f"begin {scene.story_beat} in the established visual world")
    for index in range(shot_count):
        duration = duration_seconds - cursor if index == shot_count - 1 else round(base_duration, 3)
        duration = round(min(max(duration, 0.1), maximum_shot_seconds), 3)
        story_function = _story_function(index, shot_count, scene.story_beat)
        progression = _visual_progression(index, shot_count)
        action_now = action_steps[index]
        completed_actions = action_steps[:index]
        next_state = (
            f"After shot {index + 1}, this action is complete: {action_now}. "
            "The next shot must continue from this visible state without reenacting it."
        )
        prompt = (
            f"STATE BEFORE THIS SHOT: {previous_state}.\n"
            f"EXCLUSIVE ACTION NOW: {action_now}.\n"
            f"ALREADY COMPLETED — NEVER REPEAT: "
            f"{'; '.join(completed_actions) or 'nothing; this is the first action'}.\n"
            f"REQUIRED END STATE: {next_state}\n"
            f"NARRATION NOW: {excerpts[index]}\n"
            f"DIALOGUE PERFORMANCE NOW: {dialogue_directions[index]}\n"
            f"SHOT FUNCTION: {story_function}.\n"
            f"VISUAL PROGRESSION: {progression}\n"
            f"REQUIRED CONCEPTS: {', '.join(scene.must_show_concepts) or 'source-grounded action'}.\n"
            f"SCENE WORLD AND CAST REFERENCE: {scene.prompt}\n"
            "Create one distinct cinematic shot at natural speed. Show a concrete action that "
            "visibly expresses EXCLUSIVE ACTION NOW and reaches REQUIRED END STATE exactly once. "
            "The scene-world reference defines setting, cast, props, palette, and style only. "
            "Ignore its action verbs, pose, and completed outcome whenever they conflict with the "
            "exclusive action assigned above. Preserve recurring people, era, wardrobe, palette, "
            "lighting, materials, and screen direction, while changing shot size, camera position, "
            "pose, staging, and moment according to VISUAL PROGRESSION. Begin from STATE BEFORE "
            "THIS SHOT; never reset the story, repeat an already completed action, copy the previous "
            "frame's composition, or reveal a future action early. "
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
    if exact_information_enabled:
        shots = assign_information_to_shots(
            shots,
            script.critical_information,
            preserve_source_frame=preserve_exact_source_frame,
        )
    validate_shots(shots, duration_seconds)
    return shots


def _dialogue_directions(script: SceneScript, shot_count: int) -> list[str]:
    if not script.dialogue:
        return ["none; this scene uses voice-over narration"] * shot_count
    total_words = sum(max(len(line.text.split()), 1) for line in script.dialogue)
    directions: list[str] = []
    for shot_index in range(shot_count):
        start = total_words * shot_index / shot_count
        end = total_words * (shot_index + 1) / shot_count
        cursor = 0
        active: list[str] = []
        for line in script.dialogue:
            words = line.text.split()
            line_start = cursor
            line_end = cursor + max(len(words), 1)
            overlap_start = max(start, line_start)
            overlap_end = min(end, line_end)
            if overlap_end > overlap_start:
                local_start = max(0, int(overlap_start - line_start))
                local_end = min(len(words), max(local_start + 1, round(overlap_end - line_start)))
                excerpt = " ".join(words[local_start:local_end]) or line.text
                active.append(
                    f"{line.character_name} speaks with {line.emotion} delivery: {excerpt}"
                )
            cursor = line_end
        directions.append(
            "; ".join(active)
            + ". Only the named active speaker moves their mouth naturally; other characters "
            "listen and react. The final voice track is added separately, so generate no audio "
            "and invent no additional dialogue."
        )
    return directions


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
        if (
            shot.required_concepts
            and not shot.locked_static
            and "REQUIRED CONCEPTS:" not in shot.prompt
        ):
            raise ValueError("shot prompt omits its required concepts")
        if not shot.locked_static and "VISUAL PROGRESSION:" not in shot.prompt:
            raise ValueError("cinematic shot prompt omits its distinct visual progression")
        if not shot.locked_static and "EXCLUSIVE ACTION NOW:" not in shot.prompt:
            raise ValueError("cinematic shot prompt omits its exclusive chronological action")
        if not shot.locked_static and "ALREADY COMPLETED — NEVER REPEAT:" not in shot.prompt:
            raise ValueError("cinematic shot prompt omits completed-action continuity")
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
            "media_mode": shot.media_mode,
            "preserve_source_frame": shot.preserve_source_frame,
            "source_slide_number": shot.source_slide_number,
            "camera_motion": shot.camera_motion,
            "motion_preset": shot.motion_preset,
            "transition_preset": shot.transition,
            "visual_beats": [],
            "shots": [],
            "critical_information": shot.critical_information,
        }
    )
