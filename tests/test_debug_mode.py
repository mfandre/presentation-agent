from pathlib import Path

import pytest

from presentation_video.bootstrap import build_pipeline
from presentation_video.domain.models import PresentationDocument, SlideContent
from presentation_video.infrastructure.narrative import DebugNarrativeGenerator
from presentation_video.infrastructure.speech import EspeakSpeechSynthesizer
from presentation_video.infrastructure.visual_media import (
    FfmpegImageAnimator,
    SlideVisualAssetGenerator,
)
from presentation_video.infrastructure.visual_planning import DebugVisualPlanner
from presentation_video.settings import Settings


def _document(page_count: int = 55) -> PresentationDocument:
    return PresentationDocument(
        source_path=Path("presentation.pdf"),
        title="Governança de dados",
        slides=[
            SlideContent(
                number=number,
                title=f"Tópico {number}",
                body_text=f"Catálogo, permissão e linhagem da etapa {number}",
                image_path=Path(f"page-{number}.png"),
            )
            for number in range(1, page_count + 1)
        ],
    )


def test_debug_mode_bypasses_paid_provider_configuration(tmp_path: Path) -> None:
    settings = Settings(
        debug_mode=True,
        narrative_provider="replicate",
        visual_planner_provider="replicate",
        visual_image_provider="replicate",
        visual_media_provider="replicate",
        tts_provider="replicate",
        replicate_api_token=None,
        replicate_narrative_model=None,
        replicate_planner_model=None,
        replicate_image_model=None,
        replicate_video_model=None,
        replicate_tts_model=None,
        work_root=tmp_path / "work",
        output_root=tmp_path / "output",
    )

    pipeline = build_pipeline(settings)

    assert isinstance(pipeline._narrative_generator, DebugNarrativeGenerator)
    assert isinstance(pipeline._visual_planner, DebugVisualPlanner)
    assert isinstance(pipeline._visual_asset_generator, SlideVisualAssetGenerator)
    assert isinstance(pipeline._video_clip_generator, FfmpegImageAnimator)
    assert isinstance(pipeline._speech_synthesizer, EspeakSpeechSynthesizer)


def test_bootstrap_rejects_sending_source_slide_text_to_replicate_video() -> None:
    settings = Settings(
        debug_mode=False,
        visual_image_provider="slide",
        visual_media_provider="replicate",
    )

    with pytest.raises(ValueError, match="source-page text"):
        build_pipeline(settings)


@pytest.mark.asyncio
async def test_debug_narrative_groups_large_deck_without_external_llm() -> None:
    generator = DebugNarrativeGenerator(max_scenes=3)

    script = await generator.generate(
        _document(), 60, "pt-BR", "executive", "professional"
    )

    assert len(script.scenes) == 3
    assert sum(scene.target_seconds for scene in script.scenes) == 60
    assert [
        page for scene in script.scenes for page in scene.source_slide_numbers
    ] == list(range(1, 56))


@pytest.mark.asyncio
async def test_debug_visual_plan_is_grounded_and_rejects_metaphors() -> None:
    document = _document(2)
    script = await DebugNarrativeGenerator(max_scenes=1).generate(
        document, 30, "pt-BR", "executive", "professional"
    )

    plan = await DebugVisualPlanner().plan(document, script)

    assert "unchanged source page" in plan.scenes[0].prompt
    assert "3D diorama" in plan.scenes[0].negative_prompt
    assert plan.scenes[0].source_slide_numbers == [1, 2]
    assert plan.scenes[0].media_mode.value == "static"
    assert plan.scenes[0].source_slide_number == 1


@pytest.mark.asyncio
async def test_debug_storyboard_combines_video_and_static_scenes() -> None:
    script = await DebugNarrativeGenerator(max_scenes=3).generate(
        _document(9), 60, "pt-BR", "executive", "professional"
    )

    assert [scene.media_mode.value for scene in script.scenes] == [
        "video",
        "static",
        "static",
    ]
    assert script.scenes[0].story_beat == "opening"
    assert script.scenes[-1].story_beat == "conclusion"
    assert script.creative_direction.hook_question
    assert script.creative_direction.reveal_scene_number == len(script.scenes)
