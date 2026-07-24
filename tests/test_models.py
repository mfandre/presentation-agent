from presentation_video.domain.models import PresentationScript, SceneScript


def test_total_duration_is_recalculated() -> None:
    script = PresentationScript(
        title="Test",
        total_estimated_seconds=100,
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1, 2],
                narration="A",
                target_seconds=20,
            ),
            SceneScript(
                scene_number=2,
                source_slide_numbers=[3, 4],
                narration="B",
                target_seconds=30,
            ),
        ],
    )
    assert script.total_estimated_seconds == 50


def test_scene_duration_can_be_shorter_than_five_seconds() -> None:
    script = PresentationScript(
        title="Large deck",
        total_estimated_seconds=4,
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1, 2, 3],
                narration="Short",
                target_seconds=4,
            )
        ],
    )

    assert script.scenes[0].target_seconds == 4


def test_script_supports_api_maximum_duration() -> None:
    script = PresentationScript(
        title="Long video",
        total_estimated_seconds=1800,
        scenes=[
            SceneScript(
                scene_number=1,
                source_slide_numbers=[1],
                narration="Long",
                target_seconds=1800,
            )
        ],
    )

    assert script.total_estimated_seconds == 1800
