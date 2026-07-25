import json
from pathlib import Path
from typing import Any, cast

import pytest

from presentation_video.domain.errors import NarrativeGenerationError
from presentation_video.domain.models import PresentationDocument, SlideContent
from presentation_video.infrastructure.narrative import ReplicateNarrativeGenerator
from presentation_video.infrastructure.replicate import ReplicatePredictionClient


class FakeReplicateClient:
    def __init__(self, outputs: list[dict[str, object] | str]) -> None:
        self.outputs = [
            json.dumps(output, ensure_ascii=False) if isinstance(output, dict) else output
            for output in outputs
        ]
        self.prompts: list[str] = []

    async def run(self, model: str, inputs: dict[str, object]) -> str:
        self.prompts.append(str(inputs["prompt"]))
        return self.outputs.pop(0)

    @staticmethod
    def output_text(output: Any) -> str:
        return str(output)


def _document(page_count: int = 1) -> PresentationDocument:
    return PresentationDocument(
        source_path=Path("presentation.pdf"),
        slides=[
            SlideContent(
                number=number,
                title=f"Topic {number}",
                body_text=f"Source content {number}",
                image_path=Path("slide.png"),
            )
            for number in range(1, page_count + 1)
        ],
    )


def _scene(
    number: int,
    narration: str,
    source_slides: list[int] | None = None,
) -> dict[str, object]:
    return {
        "scene_number": number,
        "source_slide_numbers": source_slides or [1],
        "narration": narration,
        "short_caption": f"Scene {number}",
        "media_mode": "video" if number % 2 else "static",
        "story_beat": "opening" if number == 1 else "development",
        "visual_intent": (
            "A concrete word-free action" if number % 2 else "Preserve the exact source page"
        ),
        "transition_to_next": "Connect naturally to the next idea",
    }


@pytest.mark.asyncio
async def test_replicate_narrative_revises_script_that_exceeds_duration() -> None:
    too_long = {
        "title": "Presentation",
        "scenes": [_scene(1, " ".join(["word"] * 80)), _scene(2, "Context")],
    }
    corrected = {
        "title": "Presentation",
        "scenes": [_scene(1, "Concise"), _scene(2, "version")],
    }
    fake = FakeReplicateClient([too_long, corrected])
    generator = ReplicateNarrativeGenerator(
        cast(ReplicatePredictionClient, fake), "owner/model", max_revisions=2
    )

    result = await generator.generate(_document(), 30, "pt-BR", "executive", "professional")

    assert result.total_estimated_seconds == 30
    assert len(fake.prompts) == 2
    assert '"requested_duration_seconds": 30' in fake.prompts[0]
    assert "fit within exactly 30 seconds or less" in fake.prompts[0]
    assert "Return exactly 69 spoken words" in fake.prompts[0]
    assert "hard limit of 77 words" in fake.prompts[0]
    assert "narration has 81 words" in fake.prompts[1]
    assert "remove at least 12 words" in fake.prompts[1]
    assert "do not mirror the source page count" in fake.prompts[1]
    assert "Condense wording" in fake.prompts[1]
    assert "fluid hybrid rhythm" in fake.prompts[1]


@pytest.mark.asyncio
async def test_narrative_accepts_small_final_word_budget_variance() -> None:
    within_tolerance = {
        "title": "Presentation",
        "scenes": [
            _scene(1, " ".join(["palavra"] * 79)),
            _scene(2, "Síntese"),
        ],
    }
    fake = FakeReplicateClient([within_tolerance])
    generator = ReplicateNarrativeGenerator(
        cast(ReplicatePredictionClient, fake), "owner/model", max_revisions=0
    )

    result = await generator.generate(
        _document(), 30, "pt-BR", "executive", "professional"
    )

    assert len(fake.prompts) == 1
    assert sum(len(scene.narration.split()) for scene in result.scenes) == 80


@pytest.mark.asyncio
async def test_replicate_narrative_compacts_final_word_budget_overflow() -> None:
    narration = " ".join(["palavra"] * 191)
    too_long = {
        "title": "Presentation",
        "scenes": [_scene(1, narration), _scene(2, "Síntese")],
    }
    fake = FakeReplicateClient([too_long])
    generator = ReplicateNarrativeGenerator(
        cast(ReplicatePredictionClient, fake), "owner/model", max_revisions=0
    )

    result = await generator.generate(_document(), 60, "pt-BR", "executive", "professional")

    assert sum(len(scene.narration.split()) for scene in result.scenes) <= 155
    assert result.total_estimated_seconds == 60


@pytest.mark.asyncio
async def test_large_deck_is_synthesized_into_fewer_narrative_scenes() -> None:
    generated = {
        "title": "Presentation",
        "scenes": [
            _scene(number, f"Explicação temática da cena {number}", source_slides)
            for number, source_slides in enumerate(
                (
                    list(range(1, 10)),
                    list(range(10, 19)),
                    list(range(19, 28)),
                    list(range(28, 37)),
                    list(range(37, 46)),
                    list(range(46, 56)),
                ),
                start=1,
            )
        ],
    }
    fake = FakeReplicateClient([generated])
    generator = ReplicateNarrativeGenerator(
        cast(ReplicatePredictionClient, fake), "owner/model", max_revisions=0
    )

    result = await generator.generate(_document(55), 240, "pt-BR", "executive", "professional")

    assert len(fake.prompts) == 1
    assert len(result.scenes) == 6
    assert result.scenes[0].source_slide_numbers == list(range(1, 10))
    assert all(scene.target_seconds >= 1 for scene in result.scenes)
    assert sum(scene.target_seconds for scene in result.scenes) == 240
    assert result.total_estimated_seconds == 240
    assert "The source has 55 pages" in fake.prompts[0]
    assert "must not mirror that page count" in fake.prompts[0]
    assert {scene.media_mode.value for scene in result.scenes} == {"static", "video"}


@pytest.mark.asyncio
async def test_narrative_can_explicitly_omit_repetitive_source_pages() -> None:
    generated = {
        "title": "Presentation",
        "scenes": [_scene(1, "Tema principal", [1]), _scene(2, "Síntese", [1])],
        "omitted_source_slide_numbers": [2, 3],
    }
    fake = FakeReplicateClient([generated])
    generator = ReplicateNarrativeGenerator(
        cast(ReplicatePredictionClient, fake), "owner/model", max_revisions=0
    )

    result = await generator.generate(_document(3), 30, "pt-BR", "executive", "professional")

    assert result.omitted_source_slide_numbers == [2, 3]


@pytest.mark.asyncio
async def test_short_video_can_summarize_many_pages_into_hybrid_beats() -> None:
    generated = {
        "title": "Presentation",
        "scenes": [
            _scene(1, "Síntese do tema central", list(range(1, 28))),
            _scene(2, "Conclusão principal", list(range(28, 56))),
        ],
    }
    fake = FakeReplicateClient([generated])
    generator = ReplicateNarrativeGenerator(
        cast(ReplicatePredictionClient, fake), "owner/model", max_revisions=0
    )

    result = await generator.generate(_document(55), 30, "pt-BR", "executive", "professional")

    assert len(fake.prompts) == 1
    assert len(result.scenes) == 2
    assert sum(scene.target_seconds for scene in result.scenes) == 30
    assert result.scenes[0].source_slide_numbers == list(range(1, 28))
    assert result.scenes[1].source_slide_numbers == list(range(28, 56))


@pytest.mark.asyncio
async def test_narrative_revises_single_scene_into_hybrid_storyboard() -> None:
    single_beat = {
        "title": "Presentation",
        "scenes": [_scene(1, "Abertura única")],
    }
    hybrid = {
        "title": "Presentation",
        "scenes": [_scene(1, "Abertura em movimento"), _scene(2, "Síntese legível")],
    }
    fake = FakeReplicateClient([single_beat, hybrid])
    generator = ReplicateNarrativeGenerator(
        cast(ReplicatePredictionClient, fake), "owner/model", max_revisions=1
    )

    result = await generator.generate(_document(), 30, "pt-BR", "executive", "professional")

    assert [scene.media_mode.value for scene in result.scenes] == ["video", "static"]
    assert "fewer than two scenes" in fake.prompts[1]


@pytest.mark.asyncio
async def test_replicate_narrative_revises_invalid_source_references() -> None:
    invalid_sources = {
        "title": "Presentation",
        "scenes": [_scene(1, "Initial version", [1, 99]), _scene(2, "Context", [2])],
    }
    corrected = {
        "title": "Presentation",
        "scenes": [_scene(1, "Corrected version", [1]), _scene(2, "Context", [2])],
    }
    fake = FakeReplicateClient([invalid_sources, corrected])
    generator = ReplicateNarrativeGenerator(
        cast(ReplicatePredictionClient, fake), "owner/model", max_revisions=1
    )

    result = await generator.generate(_document(2), 30, "pt-BR", "executive", "professional")

    assert result.scenes[0].source_slide_numbers == [1]
    assert "non-existent source slides [99]" in fake.prompts[1]


@pytest.mark.asyncio
async def test_replicate_narrative_repairs_invalid_structured_output() -> None:
    invalid = (
        '{"title":"Presentation","scenes":[{"scene_number":1,'
        '"source_slide_numbers":[1],"narration":""}]}'
    )
    corrected = {
        "title": "Presentation",
        "scenes": [_scene(1, "Versão válida"), _scene(2, "Conclusão válida")],
    }
    fake = FakeReplicateClient([invalid, corrected])
    generator = ReplicateNarrativeGenerator(
        cast(ReplicatePredictionClient, fake), "owner/model", max_revisions=1
    )

    result = await generator.generate(_document(), 30, "pt-BR", "executive", "professional")

    assert result.scenes[0].narration == "Versão válida"
    assert result.scenes[0].target_seconds == 15
    assert result.total_estimated_seconds == 30
    assert len(fake.prompts) == 2
    assert "Correct the previous response" in fake.prompts[1]
    assert "scenes.0.narration" in fake.prompts[1]


@pytest.mark.asyncio
async def test_replicate_narrative_exposes_friendly_structured_output_error() -> None:
    invalid = (
        '{"title":"Presentation","scenes":[{"scene_number":1,'
        '"source_slide_numbers":[1],"narration":""}]}'
    )
    fake = FakeReplicateClient([invalid])
    generator = ReplicateNarrativeGenerator(
        cast(ReplicatePredictionClient, fake), "owner/model", max_revisions=0
    )

    with pytest.raises(NarrativeGenerationError) as captured:
        await generator.generate(_document(), 30, "pt-BR", "executive", "professional")

    assert "scenes.0.narration" in str(captured.value)
    assert captured.value.user_message == (
        "Não foi possível estruturar o roteiro automaticamente. Tente novamente."
    )
