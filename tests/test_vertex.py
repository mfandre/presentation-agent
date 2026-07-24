from __future__ import annotations

import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.genai.errors import ClientError

from presentation_video.domain.models import (
    MediaMode,
    SlideContent,
    VisualArtifact,
    VisualScenePlan,
)
from presentation_video.infrastructure.vertex import (
    VertexClientFactory,
    VertexImageAssetGenerator,
    VertexSpeechSynthesizer,
    VertexVideoAssetGenerator,
)


class FakeAsyncClient:
    def __init__(self) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def test_client_factory_creates_explicit_vertex_client_per_location() -> None:
    calls: list[dict[str, object]] = []
    clients: list[FakeAsyncClient] = []

    def build_client(**kwargs: object) -> object:
        calls.append(kwargs)
        async_client = FakeAsyncClient()
        clients.append(async_client)
        return SimpleNamespace(aio=async_client)

    credentials = object()
    factory = VertexClientFactory(
        project="video-project",
        credentials=credentials,
        client_builder=build_client,
    )

    global_client = factory.client("global")
    assert factory.client("global") is global_client
    regional_client = factory.client("us-central1")

    assert regional_client is not global_client
    assert calls == [
        {
            "vertexai": True,
            "project": "video-project",
            "location": "global",
            "credentials": credentials,
        },
        {
            "vertexai": True,
            "project": "video-project",
            "location": "us-central1",
            "credentials": credentials,
        },
    ]


@pytest.mark.asyncio
async def test_client_factory_closes_created_async_clients() -> None:
    async_client = FakeAsyncClient()
    factory = VertexClientFactory(
        project="video-project",
        client_builder=lambda **_: SimpleNamespace(aio=async_client),
    )
    factory.client("global")

    await factory.aclose()

    assert async_client.closed is True


class FakeContentModels:
    def __init__(self, response: object, failures: int = 0) -> None:
        self.response = response
        self.failures = failures
        self.calls: list[dict[str, object]] = []

    async def generate_content(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        if len(self.calls) <= self.failures:
            raise ConnectionError("temporary Vertex failure")
        return self.response


def _inline_response(data: bytes, mime_type: str) -> object:
    inline_data = SimpleNamespace(data=data, mime_type=mime_type)
    image_part = SimpleNamespace(inline_data=inline_data)
    text_part = SimpleNamespace(inline_data=None, text="ignored model commentary")
    content = SimpleNamespace(parts=[text_part, image_part])
    return SimpleNamespace(candidates=[SimpleNamespace(content=content, finish_reason="STOP")])


def _video_plan(media_mode: MediaMode = MediaMode.VIDEO) -> VisualScenePlan:
    return VisualScenePlan(
        scene_number=3,
        source_slide_numbers=[2],
        prompt="Mostre a equipe conferindo a qualidade dos dados.",
        media_mode=media_mode,
        camera_motion="slow dolly forward",
    )


@pytest.mark.asyncio
async def test_image_generator_uses_source_pages_as_inline_references_and_saves_bytes(
    tmp_path: Path,
) -> None:
    generated_png = b"\x89PNG\r\nvertex-generated-image"
    models = FakeContentModels(_inline_response(generated_png, "image/png"), failures=1)
    client = SimpleNamespace(models=models)
    source_path = tmp_path / "source-slide.png"
    source_path.write_bytes(b"\x89PNG\r\nsource-page")
    slide = SlideContent(
        number=2,
        title="Qualidade",
        body_text="Regras e acompanhamento operacional.",
        image_path=source_path,
    )
    generator = VertexImageAssetGenerator(
        client,
        model="google-cloud:gemini-3.1-flash-image",
        aspect_ratio="16:9",
        image_size="2K",
        max_retries=1,
        timeout_seconds=1,
        retry_backoff_seconds=0,
    )

    artifact = await generator.generate(
        _video_plan(),
        [slide],
        tmp_path / "images",
        revision=2,
    )

    assert artifact.scene_number == 3
    assert artifact.kind == "image"
    assert artifact.revision == 2
    assert artifact.path.name == "scene-003-r2.png"
    assert artifact.path.read_bytes() == generated_png
    assert len(models.calls) == 2
    call = models.calls[-1]
    assert call["model"] == "gemini-3.1-flash-image"
    contents = call["contents"]
    assert isinstance(contents, list)
    assert "source-page images are grounding references" in contents[0]
    assert contents[1].inline_data.data == b"\x89PNG\r\nsource-page"
    assert contents[1].inline_data.mime_type == "image/png"
    config = call["config"]
    assert config.response_modalities == ["IMAGE"]
    assert config.image_config.aspect_ratio == "16:9"
    assert config.image_config.image_size == "2K"


@pytest.mark.asyncio
async def test_image_generator_rejects_static_scene(tmp_path: Path) -> None:
    generator = VertexImageAssetGenerator(
        SimpleNamespace(),
        max_retries=0,
        retry_backoff_seconds=0,
    )

    with pytest.raises(ValueError, match="Static scenes"):
        await generator.generate(_video_plan(MediaMode.STATIC), [], tmp_path)


@pytest.mark.asyncio
async def test_image_generator_does_not_retry_permanent_vertex_4xx(tmp_path: Path) -> None:
    class PermanentlyFailingModels:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_content(self, **_: object) -> object:
            self.calls += 1
            raise ClientError(
                400,
                {"error": {"message": "invalid image request", "status": "INVALID_ARGUMENT"}},
            )

    models = PermanentlyFailingModels()
    generator = VertexImageAssetGenerator(
        SimpleNamespace(models=models),
        max_retries=3,
        timeout_seconds=1,
        retry_backoff_seconds=0,
    )

    with pytest.raises(ClientError, match="400 INVALID_ARGUMENT"):
        await generator.generate(_video_plan(), [], tmp_path)

    assert models.calls == 1


class FakeVideoModels:
    def __init__(self, operation: object) -> None:
        self.operation = operation
        self.calls: list[dict[str, object]] = []

    async def generate_videos(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self.operation


class FakeOperations:
    def __init__(self, completed_operation: object) -> None:
        self.completed_operation = completed_operation
        self.calls: list[object] = []

    async def get(self, *, operation: object) -> object:
        self.calls.append(operation)
        return self.completed_operation


class FakeBlob:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.destinations: list[str] = []

    def download_to_filename(self, destination: str) -> None:
        self.destinations.append(destination)
        Path(destination).write_bytes(self.payload)


class FakeBucket:
    def __init__(self, blob: FakeBlob) -> None:
        self._blob = blob
        self.object_names: list[str] = []

    def blob(self, object_name: str) -> FakeBlob:
        self.object_names.append(object_name)
        return self._blob


class FakeStorageClient:
    def __init__(self, bucket: FakeBucket) -> None:
        self._bucket = bucket
        self.bucket_names: list[str] = []

    def bucket(self, bucket_name: str) -> FakeBucket:
        self.bucket_names.append(bucket_name)
        return self._bucket


@pytest.mark.asyncio
async def test_video_generator_polls_veo_and_downloads_gcs_result(
    tmp_path: Path,
) -> None:
    pending = SimpleNamespace(
        name="projects/p/locations/us-central1/operations/123",
        done=False,
        metadata={"progressPercent": 25},
        error=None,
    )
    video = SimpleNamespace(
        uri="gs://video-results/jobs/scene-003/output-0.mp4",
        video_bytes=None,
    )
    completed = SimpleNamespace(
        name=pending.name,
        done=True,
        metadata={"progressPercent": 100},
        error=None,
        result=SimpleNamespace(
            generated_videos=[SimpleNamespace(video=video)],
            rai_media_filtered_reasons=None,
        ),
        response=None,
    )
    models = FakeVideoModels(pending)
    operations = FakeOperations(completed)
    client = SimpleNamespace(models=models, operations=operations)
    blob = FakeBlob(b"vertex-veo-video")
    bucket = FakeBucket(blob)
    storage = FakeStorageClient(bucket)
    source_image = tmp_path / "approved.png"
    source_image.write_bytes(b"\x89PNG\r\napproved-image")
    generator = VertexVideoAssetGenerator(
        client,
        output_gcs_uri="gs://video-results/jobs",
        storage_client=storage,
        model="google-cloud:veo-3.1-fast-generate-001",
        aspect_ratio="16:9",
        resolution="720p",
        clip_duration_seconds=8,
        poll_interval_seconds=0,
        timeout_seconds=1,
        max_retries=0,
        retry_backoff_seconds=0,
        request_id_factory=lambda: "test-request",
    )

    artifact = await generator.animate(
        _video_plan(),
        VisualArtifact(
            scene_number=3,
            path=source_image,
            kind="image",
            revision=2,
        ),
        tmp_path / "clips",
        duration_seconds=47.5,
    )

    assert artifact.kind == "video"
    assert artifact.revision == 2
    assert artifact.path.read_bytes() == b"vertex-veo-video"
    assert len(operations.calls) == 1
    call = models.calls[0]
    assert call["model"] == "veo-3.1-fast-generate-001"
    assert call["image"].image_bytes == source_image.read_bytes()
    assert call["image"].mime_type == "image/png"
    assert "absolutely no words, letters, numbers" in call["prompt"]
    assert "Camera direction: slow dolly forward" in call["prompt"]
    assert "Mostre" not in call["prompt"]
    config = call["config"]
    assert config.duration_seconds == 8
    assert config.duration_seconds != 47.5
    assert config.aspect_ratio == "16:9"
    assert config.resolution == "720p"
    assert config.generate_audio is False
    assert config.output_gcs_uri == "gs://video-results/jobs/scene-003-r2-test-request"
    assert storage.bucket_names == ["video-results"]
    assert bucket.object_names == ["jobs/scene-003/output-0.mp4"]


@pytest.mark.asyncio
async def test_video_generator_requests_inline_output_and_saves_it_locally(
    tmp_path: Path,
) -> None:
    video = SimpleNamespace(uri=None, video_bytes=b"inline-veo-video")
    completed = SimpleNamespace(
        name="projects/p/locations/us-central1/operations/inline",
        done=True,
        metadata={"progressPercent": 100},
        error=None,
        result=SimpleNamespace(
            generated_videos=[SimpleNamespace(video=video)],
            rai_media_filtered_reasons=None,
        ),
        response=None,
    )
    models = FakeVideoModels(completed)
    source_image = tmp_path / "approved.png"
    source_image.write_bytes(b"\x89PNG\r\napproved-image")

    def unexpected_request_id() -> str:
        raise AssertionError("GCS request ID must not be created for inline delivery")

    generator = VertexVideoAssetGenerator(
        SimpleNamespace(models=models),
        output_gcs_uri=None,
        poll_interval_seconds=0,
        timeout_seconds=1,
        max_retries=0,
        retry_backoff_seconds=0,
        request_id_factory=unexpected_request_id,
    )

    artifact = await generator.animate(
        _video_plan(),
        VisualArtifact(
            scene_number=3,
            path=source_image,
            kind="image",
            revision=2,
        ),
        tmp_path / "clips",
        duration_seconds=31,
    )

    assert artifact.path == tmp_path / "clips" / "scene-003.mp4"
    assert artifact.path.read_bytes() == b"inline-veo-video"
    assert models.calls[0]["config"].output_gcs_uri is None


@pytest.mark.asyncio
async def test_video_generator_reports_missing_inline_result(tmp_path: Path) -> None:
    video = SimpleNamespace(uri=None, video_bytes=b"")
    completed = SimpleNamespace(
        name="projects/p/locations/us-central1/operations/empty",
        done=True,
        metadata=None,
        error=None,
        result=SimpleNamespace(
            generated_videos=[SimpleNamespace(video=video)],
            rai_media_filtered_reasons=None,
        ),
        response=None,
    )
    models = FakeVideoModels(completed)
    source_image = tmp_path / "approved.png"
    source_image.write_bytes(b"\x89PNG\r\napproved-image")
    generator = VertexVideoAssetGenerator(
        SimpleNamespace(models=models),
        output_gcs_uri=None,
        max_retries=0,
    )

    with pytest.raises(RuntimeError, match="neither video bytes nor a GCS URI"):
        await generator.animate(
            _video_plan(),
            VisualArtifact(scene_number=3, path=source_image, kind="image"),
            tmp_path / "clips",
            duration_seconds=20,
        )


def test_video_generator_rejects_unsupported_veo_duration() -> None:
    with pytest.raises(ValueError, match="4, 6, or 8"):
        VertexVideoAssetGenerator(
            SimpleNamespace(),
            output_gcs_uri="gs://bucket/prefix",
            clip_duration_seconds=5,
        )


@pytest.mark.asyncio
async def test_video_generator_rejects_static_scene(tmp_path: Path) -> None:
    image_path = tmp_path / "approved.png"
    image_path.write_bytes(b"image")
    generator = VertexVideoAssetGenerator(
        SimpleNamespace(),
        output_gcs_uri="gs://bucket/prefix",
        storage_client=SimpleNamespace(),
        max_retries=0,
    )

    with pytest.raises(ValueError, match="Static scenes"):
        await generator.animate(
            _video_plan(MediaMode.STATIC),
            VisualArtifact(scene_number=3, path=image_path, kind="image"),
            tmp_path,
            duration_seconds=20,
        )


@pytest.mark.asyncio
async def test_speech_synthesizer_generates_vertex_audio_and_wraps_pcm_as_wave(
    tmp_path: Path,
) -> None:
    pcm = b"\x00\x01" * 2_400
    models = FakeContentModels(_inline_response(pcm, "audio/L16;codec=pcm;rate=24000"))
    synthesizer = VertexSpeechSynthesizer(
        SimpleNamespace(models=models),
        model="google-cloud:gemini-3.1-flash-tts-preview",
        voice="Kore",
        language_code="pt-BR",
        max_retries=0,
        timeout_seconds=1,
        retry_backoff_seconds=0,
    )
    output_path = tmp_path / "audio" / "scene.wav"

    artifact = await synthesizer.synthesize(
        "Uma narrativa clara.",
        output_path,
        language="pt-BR",
        style="calmo e confiante",
    )

    assert artifact.path == output_path
    assert artifact.duration_seconds == pytest.approx(0.1)
    call = models.calls[0]
    assert call["model"] == "gemini-3.1-flash-tts-preview"
    assert "TRANSCRIPT:\nUma narrativa clara." in call["contents"]
    assert "calmo e confiante" in call["contents"]
    config = call["config"]
    assert config.response_modalities == ["AUDIO"]
    assert config.speech_config.language_code == "pt-BR"
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    with wave.open(str(output_path), "rb") as generated:
        assert generated.getnchannels() == 1
        assert generated.getsampwidth() == 2
        assert generated.getframerate() == 24_000
        assert generated.readframes(2_400) == pcm


@pytest.mark.asyncio
async def test_speech_synthesizer_reports_empty_audio_response(tmp_path: Path) -> None:
    empty_response = SimpleNamespace(candidates=[])
    models = FakeContentModels(empty_response)
    synthesizer = VertexSpeechSynthesizer(
        SimpleNamespace(models=models),
        max_retries=0,
        timeout_seconds=1,
        retry_backoff_seconds=0,
    )

    with pytest.raises(RuntimeError, match="no audio bytes"):
        await synthesizer.synthesize("Texto.", tmp_path / "empty.wav")
