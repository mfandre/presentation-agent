import base64
from pathlib import Path

import pytest

from presentation_video.domain.models import (
    MediaMode,
    VisualArtifact,
    VisualScenePlan,
)
from presentation_video.infrastructure.gemini_omni import (
    GeminiOmniVideoAssetGenerator,
)


class FakeBlob:
    def upload_from_filename(self, source: str) -> None:
        assert Path(source).is_file()

    def download_to_filename(self, destination: str) -> None:
        Path(destination).write_bytes(b"gcs-video")


class FakeBucket:
    def blob(self, _name: str) -> FakeBlob:
        return FakeBlob()


class FakeStorage:
    def bucket(self, _name: str) -> FakeBucket:
        return FakeBucket()


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self._payload


class FakeSession:
    def __init__(self, video: dict[str, str]) -> None:
        self.video = video
        self.body: dict[str, object] = {}

    def post(
        self,
        _endpoint: str,
        *,
        json: dict[str, object],
        timeout: float,
    ) -> FakeResponse:
        assert timeout > 0
        self.body = json
        return FakeResponse(
            {
                "status": "completed",
                "steps": [
                    {
                        "type": "model_output",
                        "content": [{"type": "video", **self.video}],
                    }
                ],
            }
        )


@pytest.mark.asyncio
async def test_omni_returns_inline_video_when_output_gcs_is_disabled(
    tmp_path: Path,
) -> None:
    session = FakeSession(
        {"data": base64.b64encode(b"inline-video").decode(), "mime_type": "video/mp4"}
    )
    result = await _generator(session, store_output=False).animate(
        _plan(),
        _image(tmp_path),
        tmp_path / "output",
        3,
    )

    response_format = session.body["response_format"][0]  # type: ignore[index]
    assert "gcs_uri" not in response_format
    assert "delivery" not in response_format
    assert result.path.read_bytes() == b"inline-video"


@pytest.mark.asyncio
async def test_omni_can_still_store_output_in_gcs(tmp_path: Path) -> None:
    session = FakeSession({"uri": "gs://bucket/output/video.mp4"})
    result = await _generator(session, store_output=True).animate(
        _plan(),
        _image(tmp_path),
        tmp_path / "output",
        3,
    )

    response_format = session.body["response_format"][0]  # type: ignore[index]
    assert response_format["delivery"] == "uri"
    assert response_format["gcs_uri"].startswith("gs://bucket/staging/omni-output/")
    assert result.path.read_bytes() == b"gcs-video"


@pytest.mark.asyncio
async def test_omni_accepts_only_storyboard_for_multishot(
    tmp_path: Path,
) -> None:
    session = FakeSession(
        {"data": base64.b64encode(b"multi-shot-video").decode(), "mime_type": "video/mp4"}
    )
    storyboard = _image(tmp_path)
    result = await _generator(session, store_output=False, duration=8).animate_storyboard(
        [_plan(), _plan().model_copy(update={"shot_number": 2})],
        storyboard,
        tmp_path / "output",
        duration_seconds=6,
        segment_number=1,
    )

    inputs = session.body["input"]
    assert isinstance(inputs, list)
    assert [item["type"] for item in inputs] == ["text", "image"]
    assert "continuous cinematic multi-shot sequence" in inputs[0]["text"]
    assert "character sheets" not in inputs[0]["text"]
    assert "No audio" in inputs[0]["text"]
    assert result.path.read_bytes() == b"multi-shot-video"


def _generator(
    session: FakeSession,
    *,
    store_output: bool,
    duration: int = 3,
) -> GeminiOmniVideoAssetGenerator:
    return GeminiOmniVideoAssetGenerator(
        project="project",
        output_gcs_uri="gs://bucket/staging",
        clip_duration_seconds=duration,
        store_output_in_gcs=store_output,
        storage_client=FakeStorage(),
        session=session,
    )


def _plan() -> VisualScenePlan:
    return VisualScenePlan(
        scene_number=1,
        prompt="Animate the approved frame",
        media_mode=MediaMode.VIDEO,
        preserve_source_frame=False,
    )


def _image(tmp_path: Path) -> VisualArtifact:
    path = tmp_path / "frame.png"
    path.write_bytes(b"image")
    return VisualArtifact(scene_number=1, path=path, kind="image")
