from presentation_video.application.whiteboard import compile_whiteboard_shots
from presentation_video.domain.models import MediaMode, SceneScript, VisualScenePlan


def test_whiteboard_uses_more_atomic_states_for_shorter_model_aware_takes() -> None:
    scene = VisualScenePlan(
        scene_number=1,
        prompt="A cumulative whiteboard explanation",
        media_mode=MediaMode.VIDEO,
    )
    script = SceneScript(
        scene_number=1,
        source_slide_numbers=[1],
        narration=(
            "Primeiro surge a pessoa. Depois aparece o desafio. "
            "Uma seta conecta o desafio à solução. Por fim aparece o resultado."
        ),
        target_seconds=20,
        media_mode=MediaMode.VIDEO,
    )

    guarded = compile_whiteboard_shots(
        scene,
        script,
        duration_seconds=20,
        maximum_shot_seconds=4,
    )
    previous = compile_whiteboard_shots(
        scene,
        script,
        duration_seconds=20,
        maximum_shot_seconds=8,
    )

    assert len(guarded) == 5
    assert len(previous) == 3
    assert all(shot.duration_seconds <= 4 for shot in guarded)
    assert all("only one small coherent cluster" in shot.prompt for shot in guarded)
    assert sum(shot.duration_seconds for shot in guarded) == 20
