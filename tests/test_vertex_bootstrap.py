from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

import presentation_video.bootstrap as bootstrap
from presentation_video.settings import Settings


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "_env_file": None,
        "narrative_provider": "pydantic_ai",
        "llm_model": "openai:gpt-5.2",
        "visual_planner_provider": "pydantic_ai",
        "visual_planner_model": "openai:gpt-5.2",
        "visual_image_provider": "slide",
        "visual_media_provider": "ffmpeg",
        "tts_provider": "espeak",
    }
    values.update(overrides)
    return Settings(**values)


def test_vertex_settings_have_service_specific_models_regions_and_timeouts() -> None:
    settings = Settings(_env_file=None)

    assert settings.vertex_text_location == "global"
    assert settings.vertex_image_model == "gemini-3.1-flash-image"
    assert settings.vertex_image_location == "global"
    assert settings.vertex_image_aspect_ratio == "16:9"
    assert settings.vertex_image_size == "2K"
    assert settings.vertex_image_timeout_seconds == 180
    assert settings.vertex_video_model == "veo-3.1-fast-generate-001"
    assert settings.vertex_video_location == "us-central1"
    assert settings.vertex_video_aspect_ratio == "16:9"
    assert settings.vertex_video_resolution == "720p"
    assert settings.vertex_video_duration_seconds == 8
    assert settings.vertex_video_poll_interval_seconds == 5
    assert settings.vertex_video_timeout_seconds == 900
    assert settings.vertex_tts_location == "global"
    assert settings.vertex_tts_timeout_seconds == 180


def test_google_only_env_example_is_loadable() -> None:
    env_file = Path(__file__).parents[1] / ".env.vertex-google-only.example"

    settings = Settings(_env_file=env_file)

    assert settings.llm_model == "google-cloud:gemini-3.5-flash"
    assert settings.visual_planner_model == "google-cloud:gemini-3.5-flash"
    assert settings.visual_image_provider == "vertex_ai"
    assert settings.visual_media_provider == "vertex_ai"
    assert settings.vertex_video_output_gcs_uri is None
    assert settings.vertex_video_duration_seconds == 8
    assert settings.tts_provider == "vertex_ai"


def test_vertex_gcs_uri_is_normalized_and_validated() -> None:
    settings = Settings(
        _env_file=None,
        vertex_video_output_gcs_uri="  gs://video-bucket/presentation-video/  ",
    )

    assert settings.vertex_video_output_gcs_uri == "gs://video-bucket/presentation-video"
    assert (
        Settings(_env_file=None, vertex_video_output_gcs_uri="").vertex_video_output_gcs_uri is None
    )

    with pytest.raises(ValidationError, match="must start with gs://"):
        Settings(
            _env_file=None,
            vertex_video_output_gcs_uri="https://storage.googleapis.com/video-bucket",
        )


def test_google_cloud_model_uses_explicit_project_and_text_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, Any] = {}
    provider = object()
    model = object()

    def google_cloud_provider(**kwargs: Any) -> object:
        calls["provider"] = kwargs
        return provider

    def google_model(model_name: str, **kwargs: Any) -> object:
        calls["model"] = (model_name, kwargs)
        return model

    monkeypatch.setattr(bootstrap, "GoogleCloudProvider", google_cloud_provider)
    monkeypatch.setattr(bootstrap, "GoogleModel", google_model)

    result = bootstrap._pydantic_ai_model(
        "google-cloud:gemini-3.5-flash",
        _settings(
            google_cloud_project="video-project",
            vertex_text_location="southamerica-east1",
            google_api_key="must-not-be-used",
        ),
    )

    assert result is model
    assert calls["provider"] == {
        "project": "video-project",
        "location": "southamerica-east1",
    }
    assert calls["model"] == ("gemini-3.5-flash", {"provider": provider})


def test_google_cloud_model_requires_project() -> None:
    with pytest.raises(ValueError, match="GOOGLE_CLOUD_PROJECT"):
        bootstrap._pydantic_ai_model(
            "google-cloud:gemini-3.5-flash",
            _settings(google_cloud_project=None),
        )


def test_bootstrap_wires_vertex_clients_by_service_region(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, Any] = {"clients": []}
    narrative = object()
    planner = object()
    image = object()
    video = object()
    speech = object()

    class FakeFactory:
        def __init__(self, project: str) -> None:
            calls["project"] = project

        def client(self, location: str) -> str:
            calls["clients"].append(location)
            return f"client:{location}"

    def constructor(name: str, result: object) -> Any:
        def build(*args: Any, **kwargs: Any) -> object:
            calls[name] = (args, kwargs)
            return result

        return build

    monkeypatch.setattr(bootstrap, "VertexClientFactory", FakeFactory)
    monkeypatch.setattr(
        bootstrap,
        "PydanticAINarrativeGenerator",
        constructor("narrative", narrative),
    )
    monkeypatch.setattr(
        bootstrap,
        "PydanticAIVisualPlanner",
        constructor("planner", planner),
    )
    monkeypatch.setattr(
        bootstrap,
        "VertexImageAssetGenerator",
        constructor("image", image),
    )
    monkeypatch.setattr(
        bootstrap,
        "VertexVideoAssetGenerator",
        constructor("video", video),
    )
    monkeypatch.setattr(
        bootstrap,
        "VertexSpeechSynthesizer",
        constructor("speech", speech),
    )

    pipeline = bootstrap.build_pipeline(
        _settings(
            google_cloud_project="video-project",
            visual_image_provider="vertex_ai",
            visual_media_provider="vertex_ai",
            tts_provider="vertex_ai",
            tts_model="google-cloud:gemini-3.1-flash-tts-preview",
            vertex_image_location="global",
            vertex_video_location="us-central1",
            vertex_video_output_gcs_uri="gs://video-bucket/jobs",
            vertex_tts_location="global",
            vertex_image_aspect_ratio="16:9",
            vertex_image_size="4K",
            vertex_image_timeout_seconds=240,
            vertex_video_resolution="1080p",
            vertex_video_duration_seconds=6,
            vertex_video_poll_interval_seconds=3,
            vertex_video_timeout_seconds=1200,
            vertex_tts_timeout_seconds=240,
        )
    )

    assert calls["project"] == "video-project"
    assert calls["clients"] == ["global", "us-central1", "global"]
    assert calls["image"] == (
        ("client:global",),
        {
            "model": "gemini-3.1-flash-image",
            "aspect_ratio": "16:9",
            "image_size": "4K",
            "timeout_seconds": 240,
        },
    )
    assert calls["video"] == (
        ("client:us-central1", "gs://video-bucket/jobs"),
        {
            "model": "veo-3.1-fast-generate-001",
            "aspect_ratio": "16:9",
            "resolution": "1080p",
            "clip_duration_seconds": 6,
            "poll_interval_seconds": 3.0,
            "timeout_seconds": 1200,
        },
    )
    assert calls["speech"][0] == ("client:global",)
    assert calls["speech"][1]["model"] == "gemini-3.1-flash-tts-preview"
    assert calls["speech"][1]["timeout_seconds"] == 240
    assert pipeline._narrative_generator is narrative
    assert pipeline._visual_planner is planner
    assert pipeline._visual_asset_generator is image
    assert pipeline._video_clip_generator is video
    assert pipeline._speech_synthesizer is speech


def test_vertex_video_allows_inline_output_without_gcs_uri(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeFactory:
        def __init__(self, project: str) -> None:
            pass

        def client(self, location: str) -> object:
            return object()

    monkeypatch.setattr(bootstrap, "VertexClientFactory", FakeFactory)
    monkeypatch.setattr(
        bootstrap,
        "PydanticAINarrativeGenerator",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        bootstrap,
        "PydanticAIVisualPlanner",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        bootstrap,
        "VertexImageAssetGenerator",
        lambda *args, **kwargs: object(),
    )

    pipeline = bootstrap.build_pipeline(
        _settings(
            google_cloud_project="video-project",
            visual_image_provider="vertex_ai",
            visual_media_provider="vertex_ai",
            vertex_video_output_gcs_uri=None,
        )
    )

    assert isinstance(pipeline._video_clip_generator, bootstrap.VertexVideoAssetGenerator)
    assert pipeline._video_clip_generator._output_gcs_uri is None
