from __future__ import annotations

from pathlib import Path
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    debug_mode: bool = False
    debug_replay_job_id: str | None = None
    debug_replay_step_delay_seconds: float = 1.2
    debug_root: Path = Path("./debug")
    replicate_api_token: str | None = None
    google_api_key: str | None = None
    google_cloud_project: str | None = None
    vertex_video_output_gcs_uri: str | None = None
    work_root: Path = Path("./work")
    output_root: Path = Path("./output")
    workflow_root: Path = Path("./workflows")
    workflow_database_url: str = "sqlite:///./work/workflow-state.db"
    default_workflow: str = "presentation-video"
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    def secret(self, environment_name: str) -> str | None:
        """Resolve only allow-listed credentials declared by a workflow step."""

        secrets = {
            "REPLICATE_API_TOKEN": self.replicate_api_token,
            "GOOGLE_API_KEY": self.google_api_key,
        }
        if environment_name not in secrets:
            raise ValueError(
                f"workflow requested unsupported secret {environment_name!r}"
            )
        return secrets[environment_name]

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
