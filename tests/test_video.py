from pathlib import Path

import pytest

from presentation_video.domain.models import AudioArtifact, SlideContent, VisualArtifact
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
