from presentation_video.domain.models import (
    MotionPreset,
    PresentationScript,
    SceneScript,
    VisualBeatKind,
    build_default_visual_beats,
)


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


def test_generated_static_scene_default_beat_does_not_fall_back_to_source_slide() -> None:
    beats = build_default_visual_beats(
        20,
        is_video=False,
        motion_preset=MotionPreset.NONE,
        allow_source_slide=False,
    )

    assert len(beats) == 1
    assert beats[0].kind == VisualBeatKind.GENERATED_IMAGE


def test_preserved_hybrid_static_scene_keeps_source_slide_beat() -> None:
    beats = build_default_visual_beats(
        20,
        is_video=False,
        motion_preset=MotionPreset.NONE,
        allow_source_slide=True,
    )

    assert len(beats) == 1
    assert beats[0].kind == VisualBeatKind.SOURCE_SLIDE


def test_hybrid_video_can_return_to_source_slide_when_explicitly_allowed() -> None:
    beats = build_default_visual_beats(
        30,
        is_video=True,
        motion_preset=MotionPreset.SLOW_PUSH,
        allow_source_slide=True,
    )

    assert [beat.kind for beat in beats] == [
        VisualBeatKind.GENERATED_VIDEO,
        VisualBeatKind.GENERATED_IMAGE,
        VisualBeatKind.SOURCE_SLIDE,
    ]
