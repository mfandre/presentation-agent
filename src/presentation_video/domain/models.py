from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class JobStatus(StrEnum):
    RECEIVED = "received"
    INGESTING = "ingesting"
    SCRIPTING = "scripting"
    DURATION_VALIDATING = "duration_validating"
    AWAITING_DURATION_APPROVAL = "awaiting_duration_approval"
    SYNTHESIZING = "synthesizing"
    SCENE_PLANNING = "scene_planning"
    VISUAL_PLANNING = "visual_planning"
    PROMPT_COMPILING = "prompt_compiling"
    RULE_VALIDATING = "rule_validating"
    GENERATING_IMAGES = "generating_images"
    AWAITING_VISUAL_APPROVAL = "awaiting_visual_approval"
    GENERATING_VIDEO = "generating_video"
    RENDERING = "rendering"
    VISUAL_QA = "visual_qa"
    ASSEMBLING = "assembling"
    CAPTIONING = "captioning"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class MediaMode(StrEnum):
    STATIC = "static"
    VIDEO = "video"


class ProductionMode(StrEnum):
    HYBRID_PRESENTATION = "hybrid_presentation"
    CINEMATIC_STORY = "cinematic_story"


class MotionPreset(StrEnum):
    NONE = "none"
    SLOW_PUSH = "slow_push"
    PULL_BACK = "pull_back"
    PAN_LEFT = "pan_left"
    PAN_RIGHT = "pan_right"
    DRIFT_UP = "drift_up"


class TransitionPreset(StrEnum):
    CUT = "cut"
    DISSOLVE = "dissolve"
    FADE = "fade"
    PAGE_WIPE = "page_wipe"


class VisualBeatKind(StrEnum):
    GENERATED_VIDEO = "generated_video"
    GENERATED_IMAGE = "generated_image"
    SOURCE_SLIDE = "source_slide"
    MOTION_GRAPHIC = "motion_graphic"


class VisualBeat(BaseModel):
    beat_number: int = Field(ge=1)
    kind: VisualBeatKind
    duration_seconds: float = Field(gt=0)
    motion_preset: MotionPreset = MotionPreset.SLOW_PUSH
    transition: TransitionPreset = TransitionPreset.DISSOLVE


class VisualShotPlan(BaseModel):
    shot_number: int = Field(ge=1)
    start_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0, le=8)
    narration_excerpt: str = Field(min_length=1)
    story_function: str = Field(min_length=1, max_length=120)
    prompt: str = Field(min_length=1)
    negative_prompt: str = ""
    continuity_in: str = ""
    continuity_out: str = ""
    camera_motion: str = "subtle cinematic movement"
    motion_preset: MotionPreset = MotionPreset.SLOW_PUSH
    transition: TransitionPreset = TransitionPreset.CUT
    required_concepts: list[str] = Field(default_factory=list)


def build_default_visual_beats(
    duration_seconds: int,
    *,
    is_video: bool,
    motion_preset: MotionPreset,
    allow_source_slide: bool = True,
) -> list[VisualBeat]:
    if not is_video:
        return [
            VisualBeat(
                beat_number=1,
                kind=VisualBeatKind.SOURCE_SLIDE,
                duration_seconds=duration_seconds,
                motion_preset=MotionPreset.NONE,
            )
        ]
    video_duration = min(float(duration_seconds), 8.0)
    remaining = max(float(duration_seconds) - video_duration, 0)
    beats = [
        VisualBeat(
            beat_number=1,
            kind=VisualBeatKind.GENERATED_VIDEO,
            duration_seconds=video_duration,
            motion_preset=motion_preset,
        )
    ]
    if remaining:
        image_duration = remaining if remaining <= 10 else remaining / 2
        beats.append(
            VisualBeat(
                beat_number=2,
                kind=VisualBeatKind.GENERATED_IMAGE,
                duration_seconds=image_duration,
                motion_preset=motion_preset,
            )
        )
        final_duration = remaining - image_duration
        if final_duration > 0:
            beats.append(
                VisualBeat(
                    beat_number=3,
                    kind=(
                        VisualBeatKind.SOURCE_SLIDE
                        if allow_source_slide
                        else VisualBeatKind.MOTION_GRAPHIC
                    ),
                    duration_seconds=final_duration,
                    motion_preset=(MotionPreset.NONE if allow_source_slide else motion_preset),
                )
            )
    return beats


class CreativeDirection(BaseModel):
    hook_question: str = ""
    throughline: str = ""
    visual_motif: str = "source-grounded editorial documentary"
    palette: list[str] = Field(default_factory=list)
    accent_color: str = ""
    pacing: str = Field(default="measured", pattern="^(measured|dynamic|cinematic)$")
    reveal_scene_number: int | None = Field(default=None, ge=1)
    central_thesis: str = Field(default="", max_length=500)
    narrative_device: str = Field(default="", max_length=300)
    transformation_from: str = Field(default="", max_length=300)
    transformation_to: str = Field(default="", max_length=300)
    recurring_visual_principle: str = Field(default="", max_length=500)
    concept_mappings: list["ConceptMapping"] = Field(default_factory=list, max_length=8)


class ConceptMapping(BaseModel):
    source_concept: str = Field(min_length=1, max_length=160)
    target_concept: str = Field(min_length=1, max_length=160)
    narrative_meaning: str = Field(default="", max_length=300)


class SlideContent(BaseModel):
    number: int = Field(ge=1)
    title: str = ""
    body_text: str = ""
    speaker_notes: str = ""
    image_path: Path
    source_frame_suitable: bool = True


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
    scene_purpose: str = Field(default="", max_length=300)
    relationship_to_thesis: str = Field(default="", max_length=400)
    narrative_progress: str = Field(default="", max_length=300)


class PresentationScript(BaseModel):
    title: str
    creative_direction: CreativeDirection = Field(default_factory=CreativeDirection)
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
    shot_number: int = Field(default=1, ge=1)
    source_slide_numbers: list[int] = Field(default_factory=list)
    prompt: str = Field(min_length=1)
    media_mode: MediaMode = MediaMode.STATIC
    source_slide_number: int | None = Field(default=None, ge=1)
    preserve_source_frame: bool = True
    story_beat: str = Field(default="development", max_length=120)
    must_show_concepts: list[str] = Field(default_factory=list)
    concept_visualization: str = Field(default="", max_length=800)
    scene_purpose: str = Field(default="", max_length=300)
    relationship_to_thesis: str = Field(default="", max_length=400)
    narrative_progress: str = Field(default="", max_length=300)
    visible_evidence: list[str] = Field(default_factory=list, max_length=6)
    forbidden_substitutions: list[str] = Field(default_factory=list, max_length=6)
    negative_prompt: str = (
        "text, subtitles, logos, watermarks, distorted anatomy, generic corporate stock photo, "
        "decorative abstraction, floating icons, vague futuristic imagery, isometric view, "
        "3D diorama, miniature, toy model, clay render, model city, symbolic pipes, gates, "
        "bridges, shields, padlocks, conveyor belts, glossy infographic"
    )
    camera_motion: str = "subtle cinematic movement"
    motion_preset: MotionPreset = MotionPreset.SLOW_PUSH
    entrance_motion: str = "gentle ease-in"
    focal_action: str = "guide attention to the scene's primary evidence"
    transition_out: str = "resolve cleanly into the next scene"
    transition_preset: TransitionPreset = TransitionPreset.DISSOLVE
    emphasis_beats_seconds: list[float] = Field(default_factory=list)
    visual_beats: list[VisualBeat] = Field(default_factory=list)
    visual_style: str = (
        "realistic documentary photography or faithful flat technical documentation, concrete "
        "operational evidence, natural materials and lighting, source-grounded"
    )
    shots: list[VisualShotPlan] = Field(default_factory=list)


class PresentationVisualPlan(BaseModel):
    creative_direction: CreativeDirection = Field(default_factory=CreativeDirection)
    scenes: list[VisualScenePlan] = Field(min_length=1)


class VisualArtifact(BaseModel):
    scene_number: int = Field(ge=1)
    shot_number: int = Field(default=1, ge=1)
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
    production_mode: ProductionMode = ProductionMode.HYBRID_PRESENTATION
    avatar_reference: Path | None = None


class PreparedVideoJob(BaseModel):
    job_id: str
    request: VideoJobRequest
    document: PresentationDocument
    script: PresentationScript
    visual_plan: PresentationVisualPlan
    visual_images: list[VisualArtifact]
    aligned_audio: dict[int, AudioArtifact] = Field(default_factory=dict)
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
    captions_vtt_path: Path
    captions_srt_path: Path
