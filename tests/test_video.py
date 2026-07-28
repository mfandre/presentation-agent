from pathlib import Path

import pytest

from presentation_video.domain.models import (
    AudioArtifact,
    MediaMode,
    SlideContent,
    VisualArtifact,
    VisualBeat,
    VisualBeatKind,
    VisualScenePlan,
)
from presentation_video.infrastructure import video
from presentation_video.infrastructure.video import FfmpegSceneRenderer


@pytest.mark.asyncio
async def test_video_scene_loops_at_original_speed_and_is_trimmed_to_audio(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    async def fake_process(*args: str, **kwargs: object) -> str:
        captured.extend(args)
        return ""

    async def fake_duration(path: Path) -> float:
        return 12.5

    monkeypatch.setattr(video, "run_process", fake_process)
    monkeypatch.setattr(video, "media_duration", fake_duration)
    slide = SlideContent(number=1, image_path=tmp_path / "image.jpg")
    audio = AudioArtifact(path=tmp_path / "audio.wav", duration_seconds=12.5)
    visual = VisualArtifact(scene_number=1, path=tmp_path / "clip.mp4", kind="video")

    scene = await FfmpegSceneRenderer().render(
        scene_number=1,
        source_slide=slide,
        audio=audio,
        output_path=tmp_path / "scene.mp4",
        visual=visual,
    )

    assert scene.duration_seconds == 12.5
    assert captured[captured.index("-stream_loop") + 1] == "-1"
    assert "-t" in captured
    assert "12.500" in captured
    assert not any("tpad=stop_mode=clone" in argument for argument in captured)
    assert not any("setpts=1.000000000*PTS" in argument for argument in captured)
    assert any("trim=duration=12.500" in argument for argument in captured)


@pytest.mark.asyncio
async def test_visual_beat_timeline_uses_generated_clip_once_without_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    async def fake_process(*args: str, **kwargs: object) -> str:
        captured.extend(args)
        return ""

    async def fake_duration(path: Path) -> float:
        return 20

    monkeypatch.setattr(video, "run_process", fake_process)
    monkeypatch.setattr(video, "media_duration", fake_duration)
    slide = SlideContent(number=1, image_path=tmp_path / "slide.png")
    audio = AudioArtifact(path=tmp_path / "audio.wav", duration_seconds=20)
    generated_image = VisualArtifact(scene_number=1, path=tmp_path / "image.jpg", kind="image")
    generated_clip = VisualArtifact(scene_number=1, path=tmp_path / "clip.mp4", kind="video")
    plan = VisualScenePlan(
        scene_number=1,
        source_slide_numbers=[1],
        prompt="Visual timeline",
        media_mode=MediaMode.VIDEO,
        visual_beats=[
            VisualBeat(
                beat_number=1,
                kind=VisualBeatKind.GENERATED_VIDEO,
                duration_seconds=8,
            ),
            VisualBeat(
                beat_number=2,
                kind=VisualBeatKind.GENERATED_IMAGE,
                duration_seconds=6,
            ),
            VisualBeat(
                beat_number=3,
                kind=VisualBeatKind.SOURCE_SLIDE,
                duration_seconds=6,
            ),
        ],
    )

    await FfmpegSceneRenderer().render(
        scene_number=1,
        source_slide=slide,
        audio=audio,
        output_path=tmp_path / "scene.mp4",
        visual=generated_clip,
        visual_image=generated_image,
        visual_plan=plan,
    )

    assert "-stream_loop" not in captured
    filter_complex = captured[captured.index("-filter_complex") + 1]
    assert "concat=n=3:v=1:a=0" in filter_complex
    assert "trim=duration=8.000" in filter_complex
