from pathlib import Path

from presentation_video.domain.models import (
    MediaMode,
    PresentationDocument,
    PresentationScript,
    PresentationVisualPlan,
    SceneScript,
    SlideContent,
    VisualScenePlan,
)
from presentation_video.infrastructure.visual_planning import _payload, _validate_sequence


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
    assert "monitors, screens, UI" in plan.scenes[0].negative_prompt
