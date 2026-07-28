from __future__ import annotations

from presentation_video.domain.models import (
    MediaMode,
    PresentationScript,
    PresentationVisualPlan,
    VisualBeatKind,
    VisualShotPlan,
)


def shots_or_default(
    shots: list[VisualShotPlan],
) -> list[VisualShotPlan | None]:
    return list(shots) if shots else [None]


def enforce_cinematic_script(script: PresentationScript) -> PresentationScript:
    return script.model_copy(
        update={
            "scenes": [
                scene.model_copy(
                    update={
                        "media_mode": MediaMode.VIDEO,
                        "visual_intent": (
                            f"{scene.visual_intent}. Create an original cinematic scene "
                            "grounded in the source meaning; never reproduce a source page "
                            "or document."
                        ),
                    }
                )
                for scene in script.scenes
            ]
        }
    )


def enforce_cinematic_visual_plan(
    plan: PresentationVisualPlan,
) -> PresentationVisualPlan:
    def adapt(scene):
        return scene.model_copy(
            update={
                "media_mode": MediaMode.VIDEO,
                "source_slide_number": None,
                "preserve_source_frame": False,
                "visual_beats": [
                    beat.model_copy(
                        update={
                            "kind": (
                                VisualBeatKind.GENERATED_IMAGE
                                if beat.kind == VisualBeatKind.SOURCE_SLIDE
                                else beat.kind
                            )
                        }
                    )
                    for beat in scene.visual_beats
                ],
                "prompt": (
                    f"{scene.prompt} Original cinematic shot only. No slide, page, "
                    "document, presentation layout, readable text, caption, or interface."
                ),
            }
        )

    return plan.model_copy(update={"scenes": [adapt(scene) for scene in plan.scenes]})


def validate_cinematic_has_no_source_frames(
    plan: PresentationVisualPlan,
) -> None:
    invalid = [
        scene.scene_number
        for scene in plan.scenes
        if (
            scene.media_mode != MediaMode.VIDEO
            or scene.preserve_source_frame
            or scene.source_slide_number is not None
            or any(beat.kind == VisualBeatKind.SOURCE_SLIDE for beat in scene.visual_beats)
        )
    ]
    if invalid:
        raise ValueError(
            "cinematic_story cannot contain source pages or static scenes; "
            f"invalid scenes: {invalid}"
        )
