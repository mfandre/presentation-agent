from presentation_video.application.cinematic import compile_shots, validate_shots
from presentation_video.application.pipeline import _cinematic_visual_plan
from presentation_video.domain.models import (
    MediaMode,
    PresentationVisualPlan,
    SceneScript,
    VisualScenePlan,
)


def test_compiler_covers_narration_with_distinct_shots_of_at_most_eight_seconds() -> None:
    script = SceneScript(
        scene_number=1,
        source_slide_numbers=[1, 2],
        narration=(
            "Um motor central move todas as engrenagens. A fábrica inteira depende daquele "
            "único eixo. Motores distribuídos então devolvem autonomia a cada máquina."
        ),
        target_seconds=23,
        media_mode=MediaMode.VIDEO,
        story_beat="transformation",
    )
    scene = VisualScenePlan(
        scene_number=1,
        source_slide_numbers=[1, 2],
        prompt="Historical factory evolving from central steam power to distributed motors.",
        media_mode=MediaMode.VIDEO,
        preserve_source_frame=False,
        must_show_concepts=["central motor", "distributed autonomy"],
    )

    shots = compile_shots(scene, script, 23)

    assert len(shots) == 3
    assert sum(shot.duration_seconds for shot in shots) == 23
    assert all(0 < shot.duration_seconds <= 8 for shot in shots)
    assert [shot.shot_number for shot in shots] == [1, 2, 3]
    assert len({shot.story_function for shot in shots}) >= 2
    assert all("NARRATION NOW:" in shot.prompt for shot in shots)
    assert all("REQUIRED CONCEPTS:" in shot.prompt for shot in shots)
    assert shots[1].continuity_in == shots[0].continuity_out
    validate_shots(shots, 23)


def test_cinematic_plan_converts_informational_source_page_to_generated_video() -> None:
    informational = VisualScenePlan(
        scene_number=3,
        source_slide_numbers=[4, 6],
        prompt="Preserve the exact reimbursement flow and approval table.",
        media_mode=MediaMode.STATIC,
        source_slide_number=4,
        preserve_source_frame=True,
        concept_visualization="Unchanged source slide with deadlines and approval thresholds.",
    )

    result = _cinematic_visual_plan(
        PresentationVisualPlan(scenes=[informational])
    ).scenes[0]

    assert result.media_mode == MediaMode.VIDEO
    assert result.preserve_source_frame is False
    assert result.source_slide_number is None
    assert all(beat.kind.value != "source_slide" for beat in result.visual_beats)
