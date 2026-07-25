from pathlib import Path
from typing import cast

from presentation_video.domain.models import (
    CreativeDirection,
    MediaMode,
    PresentationDocument,
    PresentationScript,
    PresentationVisualPlan,
    SceneScript,
    SlideContent,
    VisualScenePlan,
)
from presentation_video.infrastructure.replicate import ReplicatePredictionClient
from presentation_video.infrastructure.visual_planning import (
    ReplicateVisualPlanner,
    _payload,
    _validate_sequence,
)


def test_visual_plan_receives_all_source_pages_grouped_by_narrative_scene() -> None:
    document = PresentationDocument(
        source_path=Path("presentation.pdf"),
        slides=[
            SlideContent(
                number=number,
                title=f"Page {number}",
                body_text=f"Content {number}",
                image_path=Path(f"page-{number}.png"),
            )
            for number in range(1, 4)
        ],
    )
    script = PresentationScript(
        title="Story",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1, 2],
                narration="Opening theme",
                target_seconds=20,
            ),
            SceneScript(
                scene_number=2,
                source_slide_numbers=[3],
                narration="Conclusion",
                target_seconds=10,
            ),
        ],
        total_estimated_seconds=30,
    )

    payload = _payload(document, script)

    scenes = payload["scenes"]
    assert isinstance(scenes, list)
    assert scenes[0]["scene_number"] == 1
    assert scenes[0]["source_slide_numbers"] == [1, 2]
    assert [page["title"] for page in scenes[0]["source_pages"]] == ["Page 1", "Page 2"]


def test_visual_plan_source_references_are_owned_by_narrative_script() -> None:
    script = PresentationScript(
        title="Story",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[2, 3],
                narration="Theme",
                target_seconds=30,
                media_mode=MediaMode.VIDEO,
                story_beat="demonstration",
            )
        ],
        total_estimated_seconds=30,
    )
    plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=1,
                prompt="Concrete explanation",
                media_mode=MediaMode.STATIC,
                source_slide_number=2,
            )
        ]
    )

    _validate_sequence(plan, script)

    assert plan.scenes[0].source_slide_numbers == [2, 3]
    assert plan.scenes[0].media_mode == MediaMode.VIDEO
    assert plan.scenes[0].source_slide_number is None
    assert "readable UI, fake interface text" in plan.scenes[0].negative_prompt


def test_text_only_static_source_becomes_generated_editorial_image() -> None:
    document = PresentationDocument(
        source_path=Path("article.pdf"),
        slides=[
            SlideContent(
                number=1,
                title="Long-form article",
                body_text="Continuous prose " * 100,
                image_path=Path("page-1.png"),
                source_frame_suitable=False,
            )
        ],
    )
    script = PresentationScript(
        title="Story",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1],
                narration="A narrative explanation grounded in the article.",
                target_seconds=20,
                media_mode=MediaMode.STATIC,
            )
        ],
        total_estimated_seconds=20,
    )
    plan = PresentationVisualPlan(
        scenes=[
            VisualScenePlan(
                scene_number=1,
                prompt="Create an editorial storytelling image",
                media_mode=MediaMode.STATIC,
                source_slide_number=1,
            )
        ]
    )

    _validate_sequence(plan, script, document)

    scene = plan.scenes[0]
    assert scene.preserve_source_frame is False
    assert scene.source_slide_number is None
    assert scene.visual_beats[0].kind.value == "generated_image"


def test_creative_direction_has_no_fixed_corporate_blue_palette() -> None:
    direction = CreativeDirection()

    assert direction.palette == []
    assert direction.accent_color == ""


class _MalformedThenValidClient:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def run(self, model: str, inputs: dict[str, object]) -> str:
        self.prompts.append(str(inputs["prompt"]))
        if len(self.prompts) == 1:
            return '{"scenes": ['
        return (
            '{"scenes":[{"scene_number":1,"prompt":"Show AI agents coordinating tools",'
            '"media_mode":"video","scene_purpose":"Show distributed agent autonomy",'
            '"relationship_to_thesis":"Agents replace centralized assistance with specialized '
            'autonomy","narrative_progress":"Demonstrate the target operating model",'
            '"visible_evidence":["separate agent modules use distinct tools"],'
            '"forbidden_substitutions":["generic industrial worker"]}]}'
        )

    @staticmethod
    def output_text(output: object) -> str:
        return str(output)


async def test_replicate_visual_planner_repairs_malformed_json() -> None:
    document = PresentationDocument(
        source_path=Path("presentation.pdf"),
        slides=[
            SlideContent(
                number=1,
                title="Agentic AI",
                body_text="Agentes de IA coordenam ferramentas com aprovação humana.",
                image_path=Path("page-1.png"),
            )
        ],
    )
    script = PresentationScript(
        title="Story",
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1],
                narration="Agentes de IA coordenam tarefas.",
                target_seconds=10,
                media_mode=MediaMode.VIDEO,
            )
        ],
        total_estimated_seconds=10,
    )
    client = _MalformedThenValidClient()
    planner = ReplicateVisualPlanner(cast(ReplicatePredictionClient, client), "owner/model")

    plan = await planner.plan(document, script)

    assert len(client.prompts) == 2
    assert "Repair the previous response" in client.prompts[1]
    assert plan.scenes[0].must_show_concepts == ["Agentic AI"]
