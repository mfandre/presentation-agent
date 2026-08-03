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
    DESIGNING_CHARACTERS = "designing_characters"
    STORYBOARDING = "storyboarding"
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
    WHITEBOARD_EXPLAINER = "whiteboard_explainer"
    CORPORATE_TRAINING = "corporate_training"


class BrandAssetKind(StrEnum):
    LOGO = "logo"
    OPENING_IMAGE = "opening_image"
    CLOSING_IMAGE = "closing_image"


class BrandKit(BaseModel):
    name: str = Field(default="Identidade principal", min_length=1, max_length=120)
    version: int = Field(default=1, ge=1)
    primary_color: str = Field(default="#5424D6", pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary_color: str = Field(default="#23A877", pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: str = Field(default="#F2A900", pattern=r"^#[0-9A-Fa-f]{6}$")
    background_color: str = Field(default="#F7F7FB", pattern=r"^#[0-9A-Fa-f]{6}$")
    heading_font: str = Field(default="Inter", min_length=1, max_length=80)
    body_font: str = Field(default="Inter", min_length=1, max_length=80)
    visual_style: str = Field(
        default="editorial corporativo contemporâneo",
        min_length=1,
        max_length=500,
    )
    image_text_policy: str = Field(
        default="avoid",
        pattern=r"^(avoid|minimal|allowed)$",
    )
    watermark_enabled: bool = False
    watermark_position: str = Field(
        default="bottom_right",
        pattern=r"^(top_left|top_right|bottom_left|bottom_right)$",
    )
    watermark_opacity: float = Field(default=0.35, ge=0.05, le=1)
    watermark_width_percent: int = Field(default=10, ge=4, le=30)
    logo_path: Path | None = None
    opening_image_path: Path | None = None
    closing_image_path: Path | None = None


class InstructionalContentType(StrEnum):
    CONCEPT = "concept"
    PROCESS = "process"
    RULE = "rule"
    BEHAVIOR = "behavior"
    SYSTEM_DEMO = "system_demo"
    RECAP = "recap"


class CriticalInformationKind(StrEnum):
    APPROVAL_MATRIX = "approval_matrix"
    DEADLINE = "deadline"
    EXACT_NUMBERS = "exact_numbers"
    TABLE = "table"
    RULE = "rule"


class CriticalInformationUnit(BaseModel):
    id: str = Field(min_length=1, max_length=120, pattern=r"^[a-z0-9_-]+$")
    kind: CriticalInformationKind
    title: str = Field(min_length=1, max_length=180)
    source_slide_numbers: list[int] = Field(min_length=1)
    facts: list[str] = Field(min_length=1, max_length=12)
    keywords: list[str] = Field(default_factory=list, max_length=20)
    priority: int = Field(default=3, ge=1, le=5)
    exact_display_required: bool = False
    mandatory: bool = False


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


class VisualGenerationPurpose(StrEnum):
    SCENE_FRAME = "scene_frame"
    CHARACTER_REFERENCE = "character_reference"
    STORYBOARD = "storyboard"


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
    media_mode: MediaMode = MediaMode.VIDEO
    source_slide_number: int | None = Field(default=None, ge=1)
    preserve_source_frame: bool = False
    locked_static: bool = False
    critical_information: list[CriticalInformationUnit] = Field(default_factory=list)


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
                kind=(
                    VisualBeatKind.SOURCE_SLIDE
                    if allow_source_slide
                    else VisualBeatKind.GENERATED_IMAGE
                ),
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
    characters: list["CharacterProfile"] = Field(default_factory=list, max_length=8)


class CharacterProfile(BaseModel):
    id: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_-]+$")
    narrative_role: str = Field(min_length=1, max_length=160)
    physical_appearance: str = Field(min_length=1, max_length=500)
    wardrobe: str = Field(min_length=1, max_length=300)
    identity_markers: list[str] = Field(default_factory=list, max_length=6)


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
    critical_information: list[CriticalInformationUnit] = Field(default_factory=list)


class DialogueLine(BaseModel):
    character_id: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_-]+$")
    character_name: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=1200)
    emotion: str = Field(default="natural", min_length=1, max_length=80)


class SceneScript(BaseModel):
    scene_number: int = Field(ge=1)
    source_slide_numbers: list[int] = Field(min_length=1)
    narration: str = Field(min_length=1)
    dialogue: list[DialogueLine] = Field(default_factory=list, max_length=20)
    target_seconds: int = Field(ge=1, le=1800)
    short_caption: str = Field(default="", max_length=160)
    media_mode: MediaMode = MediaMode.STATIC
    story_beat: str = Field(default="development", max_length=120)
    visual_intent: str = Field(default="Preserve the source information clearly", max_length=500)
    transition_to_next: str = Field(default="Continue the narrative naturally", max_length=300)
    scene_purpose: str = Field(default="", max_length=300)
    relationship_to_thesis: str = Field(default="", max_length=400)
    narrative_progress: str = Field(default="", max_length=300)
    critical_information: list[CriticalInformationUnit] = Field(default_factory=list)


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
    generation_purpose: VisualGenerationPurpose = VisualGenerationPurpose.SCENE_FRAME
    content_language: str = Field(default="und", min_length=2, max_length=35)
    instructional_type: InstructionalContentType | None = None
    learning_objective: str = Field(default="", max_length=400)
    allow_readable_text: bool = False
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
    action_progression: list[str] = Field(default_factory=list, max_length=12)
    visible_evidence: list[str] = Field(default_factory=list, max_length=6)
    recurring_character_ids: list[str] = Field(default_factory=list, max_length=8)
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
    critical_information: list[CriticalInformationUnit] = Field(default_factory=list)


class PresentationVisualPlan(BaseModel):
    creative_direction: CreativeDirection = Field(default_factory=CreativeDirection)
    scenes: list[VisualScenePlan] = Field(min_length=1)


class VisualArtifact(BaseModel):
    scene_number: int = Field(ge=1)
    shot_number: int = Field(default=1, ge=1)
    path: Path
    start_path: Path | None = None
    kind: str = Field(pattern="^(image|video)$")
    revision: int = Field(default=1, ge=1)
    source_slide_number: int | None = Field(default=None, ge=1)
    locked_static: bool = False


class CharacterReferenceArtifact(BaseModel):
    character_id: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9_-]+$")
    path: Path
    prompt: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)


class StoryboardPanel(BaseModel):
    panel_number: int = Field(ge=1)
    scene_number: int = Field(ge=1)
    shot_number: int = Field(ge=1)
    sheet_number: int = Field(ge=1)
    cell_number: int = Field(ge=1)
    start_seconds: float = Field(ge=0)
    duration_seconds: float = Field(gt=0)
    camera: str = ""
    action: str = ""
    emotional_focus: str = ""
    continuity_in: str = ""
    continuity_out: str = ""
    character_ids: list[str] = Field(default_factory=list)
    image_path: Path | None = None


class StoryboardSheet(BaseModel):
    sheet_number: int = Field(ge=1)
    clean_path: Path
    review_path: Path
    rows: int = Field(ge=1, le=4)
    columns: int = Field(ge=1, le=4)
    panel_numbers: list[int] = Field(min_length=1)


class StoryboardBundle(BaseModel):
    panels: list[StoryboardPanel] = Field(min_length=1)
    sheets: list[StoryboardSheet] = Field(min_length=1)
    plan_path: Path


class VideoGeneratorCapabilities(BaseModel):
    supports_storyboard_reference: bool = False
    supports_multishot: bool = False
    minimum_output_seconds: float = Field(default=1, gt=0)
    maximum_output_seconds: float = Field(default=8, gt=0)
    maximum_reference_images: int = Field(default=1, ge=1)
    supports_first_frame: bool = True
    supports_last_frame: bool = False


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
    preset_options: dict[str, str] = Field(default_factory=dict)
    avatar_reference: Path | None = None
    brand_kit: BrandKit | None = None


class PreparedVideoJob(BaseModel):
    job_id: str
    request: VideoJobRequest
    document: PresentationDocument
    script: PresentationScript
    visual_plan: PresentationVisualPlan
    visual_images: list[VisualArtifact]
    character_references: list[CharacterReferenceArtifact] = Field(default_factory=list)
    storyboard: StoryboardBundle | None = None
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
