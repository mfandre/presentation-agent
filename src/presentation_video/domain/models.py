from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class JobStatus(StrEnum):
    RECEIVED = "received"
    INGESTING = "ingesting"
    SCRIPTING = "scripting"
    VISUAL_PLANNING = "visual_planning"
    GENERATING_IMAGES = "generating_images"
    AWAITING_VISUAL_APPROVAL = "awaiting_visual_approval"
    GENERATING_VIDEO = "generating_video"
    SYNTHESIZING = "synthesizing"
    RENDERING = "rendering"
    ASSEMBLING = "assembling"
    COMPLETED = "completed"
    FAILED = "failed"


class MediaMode(StrEnum):
    STATIC = "static"
    VIDEO = "video"


class SlideContent(BaseModel):
    number: int = Field(ge=1)
    title: str = ""
    body_text: str = ""
    speaker_notes: str = ""
    image_path: Path


class PresentationDocument(BaseModel):
    source_path: Path
    title: str = ""
    slides: list[SlideContent] = Field(min_length=1)


class SceneScript(BaseModel):
    scene_number: int = Field(ge=1)
    source_slide_numbers: list[int] = Field(min_length=1)
    narration: str = Field(min_length=1)
    target_seconds: int = Field(ge=1, le=1800)
    short_caption: str = Field(default="", max_length=160)
    media_mode: MediaMode = MediaMode.STATIC
    story_beat: str = Field(default="development", max_length=120)
    visual_intent: str = Field(default="Preserve the source information clearly", max_length=500)
    transition_to_next: str = Field(default="Continue the narrative naturally", max_length=300)


class PresentationScript(BaseModel):
    title: str
    scenes: list[SceneScript] = Field(min_length=1)
    omitted_source_slide_numbers: list[int] = Field(default_factory=list)
    total_estimated_seconds: int = Field(ge=1, le=1800)

    @model_validator(mode="after")
    def validate_total(self) -> "PresentationScript":
        calculated = sum(scene.target_seconds for scene in self.scenes)
        if calculated > 1800:
            raise ValueError("the sum of scene durations cannot exceed 1800 seconds")
        self.total_estimated_seconds = calculated
        return self


class VisualScenePlan(BaseModel):
    scene_number: int = Field(ge=1)
    source_slide_numbers: list[int] = Field(default_factory=list)
    prompt: str = Field(min_length=1)
    media_mode: MediaMode = MediaMode.STATIC
    source_slide_number: int | None = Field(default=None, ge=1)
    story_beat: str = Field(default="development", max_length=120)
    negative_prompt: str = (
        "text, subtitles, logos, watermarks, distorted anatomy, generic corporate stock photo, "
        "decorative abstraction, floating icons, vague futuristic imagery, isometric view, "
        "3D diorama, miniature, toy model, clay render, model city, symbolic pipes, gates, "
        "bridges, shields, padlocks, conveyor belts, glossy infographic"
    )
    camera_motion: str = "subtle cinematic movement"
    visual_style: str = (
        "realistic documentary photography or faithful flat technical documentation, concrete "
        "operational evidence, natural materials and lighting, source-grounded"
    )


class PresentationVisualPlan(BaseModel):
    scenes: list[VisualScenePlan] = Field(min_length=1)


class VisualArtifact(BaseModel):
    scene_number: int = Field(ge=1)
    path: Path
    kind: str = Field(pattern="^(image|video)$")
    revision: int = Field(default=1, ge=1)


class AudioArtifact(BaseModel):
    path: Path
    duration_seconds: float = Field(gt=0)


class SceneArtifact(BaseModel):
    scene_number: int
    path: Path
    duration_seconds: float = Field(gt=0)


class VideoJobRequest(BaseModel):
    source_path: Path
    target_seconds: int = Field(default=600, ge=30, le=1800)
    language: str = "pt-BR"
    audience: str = "executive"
    tone: str = "professional and natural"
    avatar_reference: Path | None = None


class PreparedVideoJob(BaseModel):
    job_id: str
    request: VideoJobRequest
    document: PresentationDocument
    script: PresentationScript
    visual_plan: PresentationVisualPlan
    visual_images: list[VisualArtifact]
    work_dir: Path
    output_dir: Path
    script_path: Path
    visual_plan_path: Path


class VideoJobResult(BaseModel):
    job_id: str
    video_path: Path
    script_path: Path
    visual_plan_path: Path
    duration_seconds: float
