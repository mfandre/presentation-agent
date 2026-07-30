from __future__ import annotations

import math

from presentation_video.application.cinematic import MAX_SHOT_SECONDS, _split_semantic_units
from presentation_video.domain.models import (
    MotionPreset,
    SceneScript,
    TransitionPreset,
    VisualScenePlan,
    VisualShotPlan,
)


def compile_whiteboard_shots(
    scene: VisualScenePlan,
    script: SceneScript,
    duration_seconds: float,
    continuity_in: str | None = None,
    maximum_shot_seconds: float = MAX_SHOT_SECONDS,
) -> list[VisualShotPlan]:
    """Split a narrated whiteboard scene into cumulative draw-on takes."""
    if not 0 < maximum_shot_seconds <= MAX_SHOT_SECONDS:
        raise ValueError("maximum whiteboard take duration must be between 0 and 8 seconds")
    shot_count = max(1, math.ceil(duration_seconds / maximum_shot_seconds))
    excerpts = _split_semantic_units(script.narration, shot_count)
    base_duration = duration_seconds / shot_count
    cursor = 0.0
    previous_state = continuity_in or "an empty pure-white board ready for the first marker stroke"
    shots: list[VisualShotPlan] = []
    for index, excerpt in enumerate(excerpts):
        duration = duration_seconds - cursor if index == shot_count - 1 else round(base_duration, 3)
        duration = round(min(max(duration, 0.1), maximum_shot_seconds), 3)
        is_last = index == shot_count - 1
        next_state = (
            f"the exact supplied final frame for take {index + 1}, containing only all earlier "
            "marks plus the new marks introduced during this take"
        )
        prompt = (
            f"NARRATION NOW: {excerpt}\n"
            f"TAKE NUMBER: {index + 1} of {shot_count}.\n"
            f"BOARD STATE IN: {previous_state}.\n"
            f"BOARD STATE OUT: {next_state}.\n"
            "NARRATION NOW is semantic guidance only. Never copy, spell, trace, write, or display "
            "any part of it on the board. Do not animate handwriting or character formation. "
            "Create one progressive whiteboard draw-on take for exactly this narration excerpt. "
            "The supplied approved image is the exact cumulative END STATE for this take. Begin "
            "from BOARD STATE IN and finish on the supplied image without changing its composition. "
            "Preserve every useful line already present in BOARD STATE IN, then add only the next "
            "doodles, icons, arrows, unlabeled chart marks, or diagram connections needed for this "
            "excerpt. Do not interpret the narration visually during animation: the supplied "
            "final frame already contains every permitted stroke. "
            "The final frame is a hard visual boundary: never draw, preview, foreshadow, or briefly "
            "show any object, stroke, mark, symbol, connection, or accent that is absent from it. "
            "Do not use concepts from later narration excerpts or later takes. "
            "Show one natural human hand holding one black marker. The marker tip may only trace "
            "the exact black strokes already defined by the final frame. Use a locked overhead "
            "view, pure white background, and crisp solid-black marker lines. No photography, "
            "3D, scenery, presenter, "
            "slide, document, typography, glyph, alphabetic character, word, letter, digit, label, "
            "title, caption, legend, annotation, logo, watermark, repeated motion, or loop."
        )
        shots.append(
            VisualShotPlan(
                shot_number=index + 1,
                start_seconds=cursor,
                duration_seconds=duration,
                narration_excerpt=excerpt,
                story_function=(
                    "establish_board"
                    if index == 0
                    else "resolve_takeaway"
                    if is_last
                    else "add_teaching_layer"
                ),
                prompt=prompt,
                negative_prompt=(
                    f"{scene.negative_prompt}, extra hands, extra fingers, extra markers, "
                    "marker not touching a permitted final stroke, photography, cinematic "
                    "lighting, 3D, colored "
                    "background, slide, document, handwriting, writing motion, typography, glyphs, "
                    "alphabetic characters, words, letters, digits, labels, titles, captions, "
                    "legends, axis labels, annotations, logos, watermarks, future elements, "
                    "premature elements, foreshadowing, transient extra marks, loop"
                ),
                continuity_in=previous_state,
                continuity_out=next_state,
                camera_motion="locked overhead whiteboard view; marker drawing motion only",
                motion_preset=MotionPreset.NONE,
                transition=(
                    TransitionPreset.DISSOLVE if is_last else TransitionPreset.CUT
                ),
                # Scene-wide required concepts leak future teaching beats into every take.
                # The exact start/end frames are the visual contract for whiteboard animation.
                required_concepts=[],
            )
        )
        cursor = round(cursor + duration, 3)
        previous_state = next_state
    validate_whiteboard_shots(shots, duration_seconds)
    return shots


def validate_whiteboard_shots(
    shots: list[VisualShotPlan],
    duration_seconds: float,
) -> None:
    if not shots:
        raise ValueError("whiteboard scenes require at least one take")
    cursor = 0.0
    for expected_number, shot in enumerate(shots, start=1):
        if shot.shot_number != expected_number:
            raise ValueError("whiteboard take numbers must be contiguous")
        if shot.duration_seconds > MAX_SHOT_SECONDS:
            raise ValueError("whiteboard takes cannot exceed 8 seconds")
        if abs(shot.start_seconds - cursor) > 0.02:
            raise ValueError("whiteboard take timeline contains a gap or overlap")
        if "BOARD STATE IN:" not in shot.prompt or "NARRATION NOW:" not in shot.prompt:
            raise ValueError("whiteboard take prompt lacks narration or board continuity")
        cursor += shot.duration_seconds
    if abs(cursor - duration_seconds) > 0.05:
        raise ValueError("whiteboard takes do not cover the complete narration duration")
