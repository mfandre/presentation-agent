from presentation_video.application.instructional_design import (
    direct_corporate_training_plan,
    validate_corporate_training_plan,
)
from presentation_video.domain.models import (
    InstructionalContentType,
    MediaMode,
    PresentationScript,
    PresentationVisualPlan,
    SceneScript,
    VisualScenePlan,
)


def _script(*narrations: str) -> PresentationScript:
    return PresentationScript(
        title="Treinamento",
        scenes=[
            SceneScript(
                scene_number=index,
                source_slide_numbers=[index],
                narration=narration,
                target_seconds=12,
            )
            for index, narration in enumerate(narrations, start=1)
        ],
        total_estimated_seconds=12 * len(narrations),
    )


def test_training_turns_rule_page_into_editorial_instructional_still() -> None:
    script = _script("O prazo obrigatório para solicitar o reembolso é de trinta dias.")
    plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=1,
                source_slide_numbers=[1],
                source_slide_number=1,
                preserve_source_frame=True,
                media_mode=MediaMode.STATIC,
                prompt="Tabela com limites, prazos e regras da política.",
                scene_purpose="Ensinar os limites da política",
            )
        ]
    )

    directed = direct_corporate_training_plan(plan, script)
    scene = directed.scenes[0]

    assert scene.instructional_type == InstructionalContentType.RULE
    assert scene.media_mode == MediaMode.STATIC
    assert scene.preserve_source_frame is False
    assert scene.source_slide_number is None
    assert scene.allow_readable_text is False
    assert "Do not reproduce the source page" in scene.prompt
    assert "Maintain the production identity" in scene.prompt
    validate_corporate_training_plan(directed)


def test_training_turns_process_into_generated_text_free_scene() -> None:
    script = _script("O processo começa com o envio e segue para a aprovação do gestor.")
    plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=1,
                source_slide_numbers=[1],
                source_slide_number=1,
                preserve_source_frame=True,
                media_mode=MediaMode.STATIC,
                prompt="Como fazer a solicitação em três etapas.",
                story_beat="procedure",
            )
        ]
    )

    directed = direct_corporate_training_plan(plan, script)
    scene = directed.scenes[0]

    assert scene.instructional_type == InstructionalContentType.PROCESS
    assert scene.media_mode == MediaMode.VIDEO
    assert scene.preserve_source_frame is False
    assert scene.source_slide_number is None
    assert scene.allow_readable_text is False
    assert "without interfaces or readable text" in scene.prompt
    validate_corporate_training_plan(directed)


def test_generic_system_mention_does_not_preserve_a_source_page() -> None:
    script = _script("O sistema de reembolso dá autonomia ao colaborador.")
    plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=1,
                source_slide_numbers=[1],
                source_slide_number=1,
                preserve_source_frame=True,
                media_mode=MediaMode.STATIC,
                prompt="Conceito do sistema de reembolso.",
                scene_purpose="Explicar o conceito",
            )
        ]
    )

    scene = direct_corporate_training_plan(plan, script).scenes[0]

    assert scene.instructional_type != InstructionalContentType.SYSTEM_DEMO
    assert scene.preserve_source_frame is False
    assert scene.media_mode == MediaMode.VIDEO


def test_system_demo_is_redesigned_as_static_training_artwork() -> None:
    script = _script(
        "Na tela do portal, selecione o campo de solicitação e clique no botão de envio."
    )
    plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=1,
                source_slide_numbers=[1],
                source_slide_number=1,
                preserve_source_frame=True,
                media_mode=MediaMode.STATIC,
                prompt="Tela do portal com formulário e botão de envio.",
                scene_purpose="Ensinar a interação no portal",
            )
        ]
    )

    directed = direct_corporate_training_plan(plan, script)
    scene = directed.scenes[0]

    assert scene.instructional_type == InstructionalContentType.SYSTEM_DEMO
    assert scene.media_mode == MediaMode.STATIC
    assert scene.preserve_source_frame is False
    assert scene.source_slide_number is None
    assert scene.allow_readable_text is False
    assert "redesign the composition" in scene.prompt
    validate_corporate_training_plan(directed)


def test_consecutive_rules_alternate_still_and_animated_treatments() -> None:
    script = _script(
        "O prazo obrigatório é de trinta dias.",
        "O comprovante também é obrigatório.",
    )
    plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=number,
                source_slide_numbers=[number],
                source_slide_number=number,
                preserve_source_frame=True,
                media_mode=MediaMode.STATIC,
                prompt=f"Regra {number} da política.",
                scene_purpose=f"Ensinar a regra {number}",
            )
            for number in (1, 2)
        ]
    )

    scenes = direct_corporate_training_plan(plan, script).scenes

    assert [scene.media_mode for scene in scenes] == [MediaMode.STATIC, MediaMode.VIDEO]
    assert all(scene.preserve_source_frame is False for scene in scenes)
