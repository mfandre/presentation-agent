from pathlib import Path

import pytest

from presentation_video.domain.models import VisualArtifact
from presentation_video.infrastructure import video_sequence


@pytest.mark.asyncio
async def test_static_shot_uses_planned_duration_without_media_probe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_path = tmp_path / "closing.png"
    image_path.write_bytes(b"\x89PNG\r\n")
    output_path = tmp_path / "sequence.mp4"
    calls: list[tuple[str, ...]] = []

    async def fail_duration(_: Path) -> float:
        raise AssertionError("static images must not be probed for media duration")

    async def fake_process(*arguments: str) -> str:
        calls.append(arguments)
        output_path.write_bytes(b"video")
        return ""

    monkeypatch.setattr(video_sequence, "media_duration", fail_duration)
    monkeypatch.setattr(video_sequence, "run_process", fake_process)

    result = await video_sequence.compose_shot_clips(
        3,
        [
            (
                VisualArtifact(
                    scene_number=3,
                    shot_number=1,
                    path=image_path,
                    kind="image",
                    locked_static=True,
                ),
                6.5,
            )
        ],
        output_path,
    )

    assert result.path == output_path
    assert ("-loop", "1", "-t", "6.500", "-i", str(image_path)) == calls[0][2:8]
    assert "trim=duration=6.500" in calls[0][calls[0].index("-filter_complex") + 1]
