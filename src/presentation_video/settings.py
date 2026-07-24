from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    debug_mode: bool = False
    debug_max_scenes: int = Field(default=3, ge=1, le=50)
    debug_tts_voice: str = "pt-br"

    llm_model: str = "openai:gpt-5.2"
    narrative_provider: str = "pydantic_ai"
    narrative_max_revisions: int = Field(default=2, ge=0, le=5)
    visual_planner_provider: str = "pydantic_ai"
    visual_planner_model: str = "openai:gpt-5.2"
    visual_image_provider: str = "slide"
    visual_media_provider: str = "ffmpeg"
    replicate_api_token: str | None = None
    google_api_key: str | None = None
    replicate_planner_model: str | None = None
    replicate_narrative_model: str | None = None
    replicate_video_model: str | None = None
    replicate_image_model: str | None = None
    replicate_tts_model: str | None = None
    replicate_planner_input: dict[str, object] = Field(default_factory=dict)
    replicate_narrative_input: dict[str, object] = Field(default_factory=dict)
    replicate_video_input: dict[str, object] = Field(default_factory=dict)
    replicate_image_input: dict[str, object] = Field(default_factory=dict)
    replicate_tts_input: dict[str, object] = Field(default_factory=dict)
    replicate_video_image_input_key: str = "image"
    replicate_poll_interval_seconds: float = Field(default=2.0, ge=0.2, le=30)
    replicate_timeout_seconds: int = Field(default=900, ge=30, le=3600)
    google_cloud_project: str | None = None
    vertex_text_location: str = Field(default="global", min_length=1)
    vertex_image_model: str = Field(default="gemini-3.1-flash-image", min_length=1)
    vertex_image_location: str = Field(default="global", min_length=1)
    vertex_image_aspect_ratio: Literal[
        "1:1",
        "2:3",
        "3:2",
        "3:4",
        "4:3",
        "4:5",
        "5:4",
        "9:16",
        "16:9",
        "21:9",
    ] = "16:9"
    vertex_image_size: Literal["512", "1K", "2K", "4K"] = "2K"
    vertex_image_timeout_seconds: int = Field(default=180, ge=30, le=3600)
    vertex_video_model: str = Field(
        default="veo-3.1-fast-generate-001",
        min_length=1,
    )
    vertex_video_location: str = Field(default="us-central1", min_length=1)
    vertex_video_output_gcs_uri: str | None = None
    vertex_video_aspect_ratio: Literal["16:9", "9:16"] = "16:9"
    vertex_video_resolution: Literal["720p", "1080p"] = "720p"
    vertex_video_duration_seconds: int = 8
    vertex_video_poll_interval_seconds: float = Field(default=5.0, ge=0.2, le=60)
    vertex_video_timeout_seconds: int = Field(default=900, ge=30, le=3600)
    vertex_tts_location: str = Field(default="global", min_length=1)
    vertex_tts_timeout_seconds: int = Field(default=180, ge=30, le=3600)
    target_seconds: int = Field(default=600, ge=30, le=1800)
    words_per_minute: int = Field(default=155, ge=80, le=220)
    work_root: Path = Path("./work")
    output_root: Path = Path("./output")
    tts_provider: str = "replicate"
    tts_model: str = "gemini-3.1-flash-tts-preview"
    tts_voice: str = "Kore"
    tts_language_code: str = "pt-BR"
    tts_prompt: str = "Narração profissional, natural, clara e envolvente."
    tts_max_retries: int = Field(default=2, ge=0, le=5)
    tts_rate: int = Field(default=155, ge=80, le=300)
    max_parallel_scenes: int = Field(default=3, ge=1, le=20)
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    @field_validator("google_cloud_project")
    @classmethod
    def validate_google_cloud_project(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("GOOGLE_CLOUD_PROJECT cannot be empty")
        return normalized

    @field_validator("vertex_video_output_gcs_uri")
    @classmethod
    def validate_vertex_video_output_gcs_uri(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().rstrip("/")
        if not normalized:
            return None
        if not normalized.startswith("gs://"):
            raise ValueError("VERTEX_VIDEO_OUTPUT_GCS_URI must start with gs://")
        bucket_and_prefix = normalized.removeprefix("gs://")
        bucket, _, prefix = bucket_and_prefix.partition("/")
        if not bucket or any(character.isspace() for character in bucket_and_prefix):
            raise ValueError("VERTEX_VIDEO_OUTPUT_GCS_URI must contain a valid bucket name")
        if prefix in {".", ".."}:
            raise ValueError("VERTEX_VIDEO_OUTPUT_GCS_URI contains an invalid object prefix")
        return normalized

    @field_validator("vertex_video_duration_seconds")
    @classmethod
    def validate_vertex_video_duration_seconds(cls, value: int) -> int:
        if value not in {4, 6, 8}:
            raise ValueError("VERTEX_VIDEO_DURATION_SECONDS must be 4, 6, or 8")
        return value
