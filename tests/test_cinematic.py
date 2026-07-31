from presentation_video.application.cinematic import compile_shots, validate_shots
from presentation_video.application.pipeline import _cinematic_visual_plan
from presentation_video.domain.models import (
    CharacterProfile,
    CreativeDirection,
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
    assert all("VISUAL PROGRESSION:" in shot.prompt for shot in shots)
    assert "wide establishing composition" in shots[0].prompt
    assert "medium action view" in shots[1].prompt
    assert "resolving composition" in shots[-1].prompt
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


def test_cinematic_plan_locks_recurring_character_bible_into_scene_prompt() -> None:
    plan = PresentationVisualPlan(
        creative_direction=CreativeDirection(
            characters=[
                CharacterProfile(
                    id="astrid",
                    narrative_role="protagonist",
                    physical_appearance=(
                        "adult woman in her forties, oval face, warm brown skin, "
                        "dark curly shoulder-length hair"
                    ),
                    wardrobe="weathered green wool coat and brown leather boots",
                    identity_markers=["small silver brooch", "braided leather wristband"],
                )
            ]
        ),
        scenes=[
            VisualScenePlan(
                scene_number=1,
                prompt="Astrid prepares a fishing line beside the fjord.",
                media_mode=MediaMode.VIDEO,
                recurring_character_ids=["astrid"],
            )
        ],
    )

    scene = _cinematic_visual_plan(plan).scenes[0]

    assert "LOCKED CHARACTER BIBLE" in scene.prompt
    assert "dark curly shoulder-length hair" in scene.prompt
    assert "weathered green wool coat" in scene.prompt
    assert "Do not cast a different person" in scene.prompt


def test_compiler_assigns_each_action_once_and_carries_completed_state_forward() -> None:
    script = SceneScript(
        scene_number=1,
        source_slide_numbers=[1],
        narration=(
            "Eirik empurra o barco. Em seguida pesca um peixe. "
            "Por fim devolve o peixe à água."
        ),
        target_seconds=21,
        media_mode=MediaMode.VIDEO,
    )
    scene = VisualScenePlan(
        scene_number=1,
        prompt="Eirik in a wooden boat on a misty fjord, holding a silver fish.",
        media_mode=MediaMode.VIDEO,
        action_progression=[
            "Eirik pushes the empty boat into the water; no fish is visible",
            "the fishing line tightens and Eirik lifts the fish; it remains in his hands",
            "Eirik releases the fish; it swims away and his hands are empty",
        ],
    )

    shots = compile_shots(scene, script, 21)

    assert "EXCLUSIVE ACTION NOW: Eirik pushes the empty boat" in shots[0].prompt
    assert "ALREADY COMPLETED — NEVER REPEAT: Eirik pushes" in shots[1].prompt
    assert "Eirik releases the fish" not in shots[1].prompt.split(
        "SCENE WORLD AND CAST REFERENCE:"
    )[0]
    assert "Eirik releases the fish" in shots[2].prompt
    assert shots[1].continuity_in == shots[0].continuity_out
    assert "without reenacting it" in shots[0].continuity_out
