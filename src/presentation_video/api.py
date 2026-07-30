from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import shutil
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from presentation_video.bootstrap import build_pipeline
from presentation_video.application.captions import build_caption_cues, write_caption_files
from presentation_video.application.production_presets import (
    ProductionPreset,
    get_production_preset,
    list_production_presets,
    validate_preset_plan,
)
from presentation_video.domain.errors import DurationReviewRequired, UserFacingError
from presentation_video.domain.models import (
    BrandAssetKind,
    BrandKit,
    CreativeDirection,
    JobStatus,
    MediaMode,
    PreparedVideoJob,
    PresentationScript,
    ProductionMode,
    SceneArtifact,
    VideoJobRequest,
    VisualArtifact,
    VisualBeat,
    PresentationVisualPlan,
)
from presentation_video.infrastructure.brand_kit import FileBrandKitRepository
from presentation_video.infrastructure.reporting import (
    CallbackJobReporter,
    CompositeJobReporter,
    LoggingJobReporter,
)
from presentation_video.settings import Settings
from presentation_video.workflow.loader import WorkflowLoader
from presentation_video.workflow.models import WorkflowDefinition, WorkflowSnapshot
from presentation_video.workflow.models import RunStatus, StepStatus
from presentation_video.workflow.sqlite_state import (
    SQLiteWorkflowStateRepository,
    sqlite_path_from_url,
)
from presentation_video.workflow.tracker import WorkflowJobTracker

logger = logging.getLogger(__name__)

settings = Settings()
workflow_loader = WorkflowLoader(settings.workflow_root)
workflow_definition = workflow_loader.load(settings.default_workflow)
workflow_state_repository = SQLiteWorkflowStateRepository(
    sqlite_path_from_url(settings.workflow_database_url)
)
workflow_tracker = WorkflowJobTracker(workflow_state_repository, workflow_definition)
brand_kit_repository = FileBrandKitRepository(settings.work_root / "brand")
app = FastAPI(title="Presentation Video AI", version="0.4.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SceneImageView(BaseModel):
    scene_number: int
    shot_number: int = 1
    shot_duration_seconds: float | None = None
    narration_excerpt: str = ""
    story_function: str = ""
    source_slide_numbers: list[int]
    image_url: str
    prompt: str
    camera_motion: str
    motion_preset: str
    entrance_motion: str
    focal_action: str
    transition_out: str
    transition_preset: str
    visual_beats: list[VisualBeat] = Field(default_factory=list)
    revision: int
    media_mode: MediaMode
    story_beat: str
    must_show_concepts: list[str] = Field(default_factory=list)
    concept_visualization: str = ""
    scene_purpose: str = ""
    relationship_to_thesis: str = ""
    narrative_progress: str = ""
    visible_evidence: list[str] = Field(default_factory=list)
    forbidden_substitutions: list[str] = Field(default_factory=list)
    source_slide_number: int | None = None
    preserve_source_frame: bool = True
    instructional_type: str | None = None
    learning_objective: str = ""
    allow_readable_text: bool = False


class RegenerateSceneRequest(BaseModel):
    prompt: str | None = Field(default=None, min_length=3, max_length=4_000)


class SourceSlideSelectionRequest(BaseModel):
    source_slide_number: int = Field(ge=1)
    prompt: str | None = Field(default=None, min_length=3, max_length=4_000)


class BrandKitView(BaseModel):
    name: str
    version: int
    primary_color: str
    secondary_color: str
    accent_color: str
    background_color: str
    heading_font: str
    body_font: str
    visual_style: str
    image_text_policy: str
    logo_url: str | None = None
    opening_image_url: str | None = None
    closing_image_url: str | None = None


class BrandKitUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    primary_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    secondary_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    accent_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    background_color: str = Field(pattern=r"^#[0-9A-Fa-f]{6}$")
    heading_font: str = Field(min_length=1, max_length=80)
    body_font: str = Field(min_length=1, max_length=80)
    visual_style: str = Field(min_length=1, max_length=500)
    image_text_policy: Literal["avoid", "minimal", "allowed"] = "avoid"


class SourcePageView(BaseModel):
    number: int
    title: str
    image_url: str


class DurationDecisionRequest(BaseModel):
    decision: Literal["summarize", "accept", "cancel"]


class RuntimeConfigView(BaseModel):
    debug_mode: bool
    debug_max_scenes: int | None = None
    debug_replay_job_id: str | None = None


class JobView(BaseModel):
    job_id: str
    status: JobStatus
    progress_percent: int = Field(default=0, ge=0, le=100)
    detail: str = ""
    file_name: str
    target_seconds: int
    requested_target_seconds: int | None = None
    estimated_duration_seconds: int | None = None
    narration_word_count: int | None = None
    language: str
    audience: str
    tone: str
    production_mode: ProductionMode = ProductionMode.HYBRID_PRESENTATION
    preset_options: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
    start_datetime: datetime
    end_datetime: datetime | None = None
    duration_seconds: float | None = None
    video_url: str | None = None
    script_url: str | None = None
    visual_plan_url: str | None = None
    captions_vtt_url: str | None = None
    captions_srt_url: str | None = None
    scene_images: list[SceneImageView] = Field(default_factory=list)
    regenerating_scene_numbers: list[int] = Field(default_factory=list)
    debug_mode: bool = False
    creative_direction: CreativeDirection | None = None
    brand_kit: BrandKitView | None = None
    source_pages: list[SourcePageView] = Field(default_factory=list)


@dataclass(slots=True)
class JobRecord:
    view: JobView
    source_path: Path
    video_path: Path | None = None
    script_path: Path | None = None
    visual_plan_path: Path | None = None
    captions_vtt_path: Path | None = None
    captions_srt_path: Path | None = None
    prepared: PreparedVideoJob | None = None
    regenerating_scenes: set[int] = field(default_factory=set)
    finalization_started: bool = False
    active_task: asyncio.Task[None] | None = None
    debug_replay_source_job_id: str | None = None
    debug_replay_images: dict[tuple[int, int], Path] = field(default_factory=dict)
    debug_duration_decision: str | None = None
    debug_duration_event: asyncio.Event = field(default_factory=asyncio.Event)
    debug_visual_event: asyncio.Event = field(default_factory=asyncio.Event)


_jobs: dict[str, JobRecord] = {}
_resuming_jobs: set[str] = set()
_jobs_lock = asyncio.Lock()
_upload_root = settings.work_root / "uploads"
_upload_root.mkdir(parents=True, exist_ok=True)
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024
_MAX_BRAND_ASSET_BYTES = 15 * 1024 * 1024


def _brand_kit_view(kit: BrandKit) -> BrandKitView:
    def url(kind: BrandAssetKind, path: Path | None) -> str | None:
        return f"/v1/brand-kit/assets/{kind.value}" if path and path.is_file() else None

    return BrandKitView(
        **kit.model_dump(
            exclude={"logo_path", "opening_image_path", "closing_image_path"}
        ),
        logo_url=url(BrandAssetKind.LOGO, kit.logo_path),
        opening_image_url=url(BrandAssetKind.OPENING_IMAGE, kit.opening_image_path),
        closing_image_url=url(BrandAssetKind.CLOSING_IMAGE, kit.closing_image_path),
    )

_STAGE_PROGRESS: dict[JobStatus, tuple[int, int]] = {
    JobStatus.RECEIVED: (0, 0),
    JobStatus.INGESTING: (5, 15),
    JobStatus.SCRIPTING: (15, 21),
    JobStatus.DURATION_VALIDATING: (21, 23),
    JobStatus.AWAITING_DURATION_APPROVAL: (23, 23),
    JobStatus.SYNTHESIZING: (23, 29),
    JobStatus.SCENE_PLANNING: (29, 31),
    JobStatus.VISUAL_PLANNING: (31, 35),
    JobStatus.PROMPT_COMPILING: (35, 37),
    JobStatus.RULE_VALIDATING: (37, 39),
    JobStatus.GENERATING_IMAGES: (39, 55),
    JobStatus.AWAITING_VISUAL_APPROVAL: (55, 55),
    JobStatus.GENERATING_VIDEO: (65, 82),
    JobStatus.VISUAL_QA: (82, 85),
    JobStatus.RENDERING: (85, 92),
    JobStatus.ASSEMBLING: (92, 97),
    JobStatus.CAPTIONING: (97, 99),
    JobStatus.COMPLETED: (100, 100),
    JobStatus.CANCELLED: (0, 0),
    JobStatus.FAILED: (0, 0),
}
_ITEM_PROGRESS_PATTERN = re.compile(r"(?:completed|slide)=(\d+)\s+total=(\d+)")
_JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
_WORKFLOW_STEP_TO_JOB_STATUS: dict[str, JobStatus] = {
    "ingest": JobStatus.INGESTING,
    "narrative": JobStatus.SCRIPTING,
    "duration_validate": JobStatus.DURATION_VALIDATING,
    "duration_review": JobStatus.AWAITING_DURATION_APPROVAL,
    "scene_plan": JobStatus.SCENE_PLANNING,
    "visual_plan": JobStatus.VISUAL_PLANNING,
    "instructional_design": JobStatus.VISUAL_PLANNING,
    "whiteboard_concept_plan": JobStatus.VISUAL_PLANNING,
    "prompt_compile": JobStatus.PROMPT_COMPILING,
    "rule_validate": JobStatus.RULE_VALIDATING,
    "generate_images": JobStatus.GENERATING_IMAGES,
    "whiteboard_master": JobStatus.GENERATING_IMAGES,
    "whiteboard_states": JobStatus.GENERATING_IMAGES,
    "visual_review": JobStatus.AWAITING_VISUAL_APPROVAL,
    "speech": JobStatus.SYNTHESIZING,
    "animate": JobStatus.GENERATING_VIDEO,
    "whiteboard_animate": JobStatus.GENERATING_VIDEO,
    "visual_qa": JobStatus.VISUAL_QA,
    "render": JobStatus.RENDERING,
    "assemble": JobStatus.ASSEMBLING,
    "captions": JobStatus.CAPTIONING,
}


def _public_error_detail(exc: Exception) -> str:
    if isinstance(exc, UserFacingError):
        return exc.user_message
    return str(exc)


def _recover_completed_job(
    job_id: str,
    *,
    output_dir: Path | None = None,
) -> JobRecord | None:
    if not _JOB_ID_PATTERN.fullmatch(job_id):
        return None
    output_dir = output_dir or settings.output_root / job_id
    manifest_path = output_dir / "manifest.json"
    video_path = output_dir / "presentation.mp4"
    if not manifest_path.is_file() or not video_path.is_file():
        return None
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        duration = float(manifest["duration_seconds"])
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        logger.exception("job=%s completed artifact recovery failed", job_id)
        return None
    source_path = Path(str(manifest.get("source") or f"{job_id}.pptx"))
    timestamp = datetime.fromtimestamp(manifest_path.stat().st_mtime, UTC)
    snapshot = workflow_tracker.snapshot(job_id)
    start_datetime = snapshot.run.start_datetime if snapshot is not None else timestamp
    end_datetime = (
        snapshot.run.end_datetime
        if snapshot is not None and snapshot.run.end_datetime is not None
        else timestamp
    )
    target_seconds = min(max(int(manifest.get("target_seconds") or round(duration)), 30), 1800)
    script_path = output_dir / "script.json"
    visual_plan_path = output_dir / "visual-plan.json"
    captions_vtt_path = output_dir / f"captions.{manifest.get('language') or 'pt-BR'}.vtt"
    captions_srt_path = output_dir / f"captions.{manifest.get('language') or 'pt-BR'}.srt"
    creative_direction = None
    if script_path.is_file():
        try:
            creative_direction = CreativeDirection.model_validate(
                json.loads(script_path.read_text(encoding="utf-8")).get("creative_direction", {})
            )
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            logger.warning("job=%s creative direction recovery failed", job_id)
    view = JobView(
        job_id=job_id,
        status=JobStatus.COMPLETED,
        progress_percent=100,
        detail="Resultado recuperado dos arquivos concluídos no servidor",
        file_name=source_path.name,
        target_seconds=target_seconds,
        debug_mode=settings.debug_mode,
        language=str(manifest.get("language") or "pt-BR"),
        audience=str(manifest.get("audience") or "executive"),
        tone=str(manifest.get("tone") or "professional and natural"),
        production_mode=ProductionMode(
            manifest.get("production_mode") or ProductionMode.HYBRID_PRESENTATION.value
        ),
        preset_options=dict(manifest.get("preset_options") or {}),
        created_at=timestamp,
        updated_at=timestamp,
        start_datetime=start_datetime,
        end_datetime=end_datetime,
        duration_seconds=duration,
        video_url=f"/v1/videos/{job_id}/download",
        script_url=f"/v1/videos/{job_id}/script" if script_path.is_file() else None,
        visual_plan_url=(
            f"/v1/videos/{job_id}/visual-plan" if visual_plan_path.is_file() else None
        ),
        captions_vtt_url=(
            f"/v1/videos/{job_id}/captions.vtt" if captions_vtt_path.is_file() else None
        ),
        captions_srt_url=(
            f"/v1/videos/{job_id}/captions.srt" if captions_srt_path.is_file() else None
        ),
        creative_direction=creative_direction,
    )
    logger.info(
        "job=%s recovered completed artifact duration_seconds=%.2f path=%s",
        job_id,
        duration,
        video_path,
    )
    return JobRecord(
        view=view,
        source_path=source_path,
        video_path=video_path,
        script_path=script_path if script_path.is_file() else None,
        visual_plan_path=visual_plan_path if visual_plan_path.is_file() else None,
        captions_vtt_path=captions_vtt_path if captions_vtt_path.is_file() else None,
        captions_srt_path=captions_srt_path if captions_srt_path.is_file() else None,
        finalization_started=True,
    )


def _recover_workflow_job(job_id: str) -> JobRecord | None:
    snapshot = workflow_tracker.snapshot(job_id)
    if snapshot is None:
        return None
    inputs = snapshot.run.inputs
    source_path = Path(str(inputs.get("source_path") or f"{job_id}.pdf"))
    active_step = next(
        (
            step
            for step in snapshot.steps
            if step.status in {StepStatus.RUNNING, StepStatus.WAITING, StepStatus.FAILED}
        ),
        None,
    )
    status = (
        JobStatus.FAILED
        if snapshot.run.status == RunStatus.FAILED
        else _WORKFLOW_STEP_TO_JOB_STATUS.get(
            active_step.step_id if active_step else "",
            JobStatus.RECEIVED,
        )
    )
    created_at = snapshot.run.created_at
    output_dir = settings.output_root / job_id
    script_path = output_dir / "script.json"
    visual_plan_path = output_dir / "visual-plan.json"
    narration_word_count = None
    estimated_duration_seconds = None
    if status == JobStatus.AWAITING_DURATION_APPROVAL and script_path.is_file():
        try:
            recovered_script = PresentationScript.model_validate_json(
                script_path.read_text(encoding="utf-8")
            )
            narration_word_count = sum(
                len(scene.narration.split()) for scene in recovered_script.scenes
            )
            duration_step = next(
                step for step in workflow_definition.steps if step.id == "duration_validate"
            )
            words_per_minute = int(duration_step.config.get("words_per_minute", 155))
            estimated_duration_seconds = math.ceil(
                narration_word_count * 60 / words_per_minute
            )
        except (OSError, ValueError, StopIteration):
            logger.warning("job=%s could not recover duration review metadata", job_id)
    return JobRecord(
        view=JobView(
            job_id=job_id,
            status=status,
            progress_percent=_STAGE_PROGRESS[status][0],
            detail=(
                snapshot.run.error
                or "Estado recuperado do workflow persistido; use retomar para continuar"
            ),
            file_name=source_path.name,
            target_seconds=int(inputs.get("target_seconds") or 600),
            requested_target_seconds=int(inputs.get("target_seconds") or 600),
            estimated_duration_seconds=estimated_duration_seconds,
            narration_word_count=narration_word_count,
            language=str(inputs.get("language") or "pt-BR"),
            audience=str(inputs.get("audience") or "executive"),
            tone=str(inputs.get("tone") or "professional and natural"),
            production_mode=ProductionMode(
                inputs.get("production_mode") or ProductionMode.HYBRID_PRESENTATION.value
            ),
            preset_options=dict(inputs.get("preset_options") or {}),
            created_at=created_at,
            updated_at=snapshot.run.updated_at,
            start_datetime=snapshot.run.start_datetime,
            end_datetime=snapshot.run.end_datetime,
            debug_mode=settings.debug_mode,
            script_url=f"/v1/videos/{job_id}/script" if script_path.is_file() else None,
            visual_plan_url=(
                f"/v1/videos/{job_id}/visual-plan" if visual_plan_path.is_file() else None
            ),
        ),
        source_path=source_path,
        script_path=script_path if script_path.is_file() else None,
        visual_plan_path=visual_plan_path if visual_plan_path.is_file() else None,
    )


def _find_uploaded_source(job_id: str) -> Path | None:
    if not _JOB_ID_PATTERN.fullmatch(job_id):
        return None
    return next(
        (
            path
            for suffix in (".pdf", ".pptx")
            if (path := _upload_root / f"{job_id}{suffix}").is_file()
        ),
        None,
    )


def _request_metadata_path(job_id: str) -> Path:
    return settings.output_root / job_id / "request.json"


def _finalization_has_started(job_id: str) -> bool:
    work_dir = settings.work_root / job_id
    return any(
        path.is_file()
        for directory in ("audio", "avatars", "clips", "scenes")
        for path in (work_dir / directory).glob("*")
    )


def _load_resume_request(job_id: str, source_path: Path) -> VideoJobRequest:
    metadata_path = _request_metadata_path(job_id)
    if metadata_path.is_file():
        return VideoJobRequest.model_validate_json(metadata_path.read_text(encoding="utf-8"))
    script_path = settings.output_root / job_id / "script.json"
    script = json.loads(script_path.read_text(encoding="utf-8"))
    target_seconds = sum(
        int(scene.get("target_seconds") or 0) for scene in script.get("scenes", [])
    )
    return VideoJobRequest(
        source_path=source_path,
        target_seconds=min(max(target_seconds, 30), 1800),
    )


def _calculate_progress(status: JobStatus, detail: str, current: int) -> int:
    if status == JobStatus.COMPLETED:
        return 100
    if status == JobStatus.FAILED:
        return current

    start, end = _STAGE_PROGRESS[status]
    match = _ITEM_PROGRESS_PATTERN.search(detail)
    if match and end > start:
        slide, total = (int(value) for value in match.groups())
        ratio = min(max(slide / max(total, 1), 0), 1)
        return max(current, round(start + ((end - start) * ratio)))
    return max(current, start)


async def _record_job_update(job_id: str, status: JobStatus, detail: str) -> None:
    # The application pipeline announces completion before the API has attached
    # the public artifact URLs. The API marks the terminal state in _run_job.
    if status == JobStatus.COMPLETED:
        return
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            return
        previous_progress = record.view.progress_percent
        record.view.status = status
        record.view.detail = detail
        record.view.progress_percent = _calculate_progress(
            status,
            detail,
            record.view.progress_percent,
        )
        record.view.updated_at = datetime.now(UTC)
        if status in {JobStatus.FAILED, JobStatus.CANCELLED}:
            record.view.end_datetime = record.view.updated_at
        logger.info(
            "job=%s frontend progress status=%s percent=%s previous_percent=%s detail=%s",
            job_id,
            status.value,
            record.view.progress_percent,
            previous_progress,
            detail,
        )


pipeline = build_pipeline(
    settings,
    reporter=CompositeJobReporter(
        [
            LoggingJobReporter(),
            CallbackJobReporter(_record_job_update),
            workflow_tracker,
        ]
    ),
    workflow=workflow_definition,
)


def _scene_image_views(prepared: PreparedVideoJob) -> list[SceneImageView]:
    return _scene_image_views_from_assets(
        prepared.job_id,
        prepared.visual_plan,
        prepared.visual_images,
    )


def _source_page_views(prepared: PreparedVideoJob) -> list[SourcePageView]:
    return [
        SourcePageView(
            number=slide.number,
            title=slide.title or f"Página {slide.number}",
            image_url=f"/v1/videos/{prepared.job_id}/source-pages/{slide.number}/image",
        )
        for slide in prepared.document.slides
    ]


def _scene_image_views_from_assets(
    job_id: str,
    visual_plan: PresentationVisualPlan,
    visual_images: list[VisualArtifact],
) -> list[SceneImageView]:
    plans = {plan.scene_number: plan for plan in visual_plan.scenes}
    views: list[SceneImageView] = []
    for image in sorted(
        visual_images,
        key=lambda item: (item.scene_number, item.shot_number),
    ):
        plan = plans[image.scene_number]
        shot = plan.shots[image.shot_number - 1] if plan.shots else None
        views.append(
            SceneImageView(
                scene_number=image.scene_number,
                shot_number=image.shot_number,
                shot_duration_seconds=shot.duration_seconds if shot else None,
                narration_excerpt=shot.narration_excerpt if shot else "",
                story_function=shot.story_function if shot else "",
                source_slide_numbers=plan.source_slide_numbers,
                image_url=(
                    f"/v1/videos/{job_id}/scenes/{image.scene_number}"
                    f"/shots/{image.shot_number}/image"
                    f"?revision={image.revision}"
                ),
                prompt=shot.prompt if shot else plan.prompt,
                camera_motion=shot.camera_motion if shot else plan.camera_motion,
                motion_preset=(shot.motion_preset if shot else plan.motion_preset).value,
                entrance_motion=plan.entrance_motion,
                focal_action=plan.focal_action,
                transition_out=plan.transition_out,
                transition_preset=(shot.transition if shot else plan.transition_preset).value,
                visual_beats=plan.visual_beats,
                revision=image.revision,
                media_mode=plan.media_mode,
                story_beat=plan.story_beat,
                must_show_concepts=plan.must_show_concepts,
                concept_visualization=plan.concept_visualization,
                scene_purpose=plan.scene_purpose,
                relationship_to_thesis=plan.relationship_to_thesis,
                narrative_progress=plan.narrative_progress,
                visible_evidence=plan.visible_evidence,
                forbidden_substitutions=plan.forbidden_substitutions,
                source_slide_number=plan.source_slide_number,
                preserve_source_frame=plan.preserve_source_frame,
                instructional_type=(
                    plan.instructional_type.value if plan.instructional_type else None
                ),
                learning_objective=plan.learning_objective,
                allow_readable_text=plan.allow_readable_text,
            )
        )
    return views


def _debug_job_output_dir(job_id: str) -> Path:
    return settings.debug_root / job_id / "output"


def _ensure_completed_job_captions(
    job_id: str,
    *,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    output_dir = output_dir or settings.output_root / job_id
    manifest_path = output_dir / "manifest.json"
    script_path = output_dir / "script.json"
    if not manifest_path.is_file() or not script_path.is_file():
        raise FileNotFoundError(f"Debug replay job {job_id} is missing manifest or script")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    script = PresentationScript.model_validate_json(script_path.read_text(encoding="utf-8"))
    language = str(manifest.get("language") or "pt-BR")
    vtt_path = output_dir / f"captions.{language}.vtt"
    srt_path = output_dir / f"captions.{language}.srt"
    if vtt_path.is_file() and srt_path.is_file():
        return vtt_path, srt_path
    scenes = [
        SceneArtifact.model_validate(scene)
        for scene in manifest.get("scenes", [])
    ]
    if not scenes:
        raise ValueError(f"Debug replay job {job_id} has no rendered scenes")
    return write_caption_files(build_caption_cues(script, scenes), output_dir, language)


async def _replay_debug_job(job_id: str, source_job_id: str) -> None:
    before_duration = [
        (JobStatus.INGESTING, "Replay: lendo o documento"),
        (JobStatus.SCRIPTING, "Replay: criando a narrativa"),
        (JobStatus.DURATION_VALIDATING, "Replay: validando a duração"),
    ]
    before_visual_review = [
        (JobStatus.SYNTHESIZING, "Replay: alinhando a voz"),
        (JobStatus.SCENE_PLANNING, "Replay: planejando cenas e takes"),
        (JobStatus.VISUAL_PLANNING, "Replay: dirigindo os visuais"),
        (JobStatus.PROMPT_COMPILING, "Replay: compilando prompts"),
        (JobStatus.RULE_VALIDATING, "Replay: validando regras"),
        (JobStatus.GENERATING_IMAGES, "Replay: preparando imagens"),
    ]
    after_visual_review = [
        (JobStatus.GENERATING_VIDEO, "Replay: preparando clipes"),
        (JobStatus.VISUAL_QA, "Replay: executando QA visual"),
        (JobStatus.RENDERING, "Replay: renderizando cenas"),
        (JobStatus.ASSEMBLING, "Replay: montando o vídeo final"),
        (JobStatus.CAPTIONING, "Replay: gerando legendas VTT e SRT"),
    ]
    try:
        source_output_dir = _debug_job_output_dir(source_job_id)
        source_record = _recover_completed_job(
            source_job_id,
            output_dir=source_output_dir,
        )
        if source_record is None or source_record.video_path is None:
            raise FileNotFoundError(
                f"Debug replay job {source_job_id} does not have a completed video "
                f"in {source_output_dir}"
            )
        captions_vtt_path, captions_srt_path = _ensure_completed_job_captions(
            source_job_id,
            output_dir=source_output_dir,
        )
        manifest = json.loads(
            (source_output_dir / "manifest.json").read_text(encoding="utf-8")
        )
        assert source_record.script_path is not None
        assert source_record.visual_plan_path is not None
        source_script = PresentationScript.model_validate_json(
            source_record.script_path.read_text(encoding="utf-8")
        )
        source_visual_plan = PresentationVisualPlan.model_validate_json(
            source_record.visual_plan_path.read_text(encoding="utf-8")
        )
        source_images = [
            VisualArtifact.model_validate(image)
            for image in manifest.get("approved_images", [])
        ]
        missing_images = [image.path for image in source_images if not image.path.is_file()]
        if missing_images:
            raise FileNotFoundError(
                f"Debug replay job {source_job_id} has missing review images: "
                f"{missing_images[0]}"
            )
        async with _jobs_lock:
            record = _jobs[job_id]
            record.debug_replay_source_job_id = source_job_id
            record.debug_replay_images = {
                (image.scene_number, image.shot_number): image.path
                for image in source_images
            }
            record.script_path = source_record.script_path
            record.visual_plan_path = source_record.visual_plan_path
            record.view.script_url = f"/v1/videos/{job_id}/script"
            record.view.visual_plan_url = f"/v1/videos/{job_id}/visual-plan"
            record.view.creative_direction = source_script.creative_direction

        for status, detail in before_duration:
            await _record_job_update(job_id, status, detail)
            await workflow_tracker.update(job_id, status, detail)
            await asyncio.sleep(max(settings.debug_replay_step_delay_seconds, 0.05))

        async with _jobs_lock:
            record = _jobs[job_id]
            requested_seconds = record.view.target_seconds
            estimated_seconds = max(
                round(source_record.view.duration_seconds or requested_seconds),
                requested_seconds + 7,
            )
            record.view.status = JobStatus.AWAITING_DURATION_APPROVAL
            record.view.progress_percent = 23
            record.view.detail = (
                f"Replay: história estimada em {estimated_seconds} segundos; "
                "aguardando sua decisão"
            )
            record.view.requested_target_seconds = requested_seconds
            record.view.estimated_duration_seconds = estimated_seconds
            record.view.narration_word_count = sum(
                len(scene.narration.split()) for scene in source_script.scenes
            )
            record.view.updated_at = datetime.now(UTC)
            duration_event = record.debug_duration_event
        await workflow_tracker.update(
            job_id,
            JobStatus.AWAITING_DURATION_APPROVAL,
            "Replay aguardando aprovação da duração",
        )
        await duration_event.wait()
        async with _jobs_lock:
            record = _jobs[job_id]
            if record.debug_duration_decision == "cancel":
                return

        for status, detail in before_visual_review:
            await _record_job_update(job_id, status, detail)
            await workflow_tracker.update(job_id, status, detail)
            await asyncio.sleep(max(settings.debug_replay_step_delay_seconds, 0.05))

        async with _jobs_lock:
            record = _jobs[job_id]
            record.view.status = JobStatus.AWAITING_VISUAL_APPROVAL
            record.view.progress_percent = 55
            record.view.detail = "Replay: revise e aprove as cenas do storyboard"
            record.view.scene_images = _scene_image_views_from_assets(
                job_id,
                source_visual_plan,
                source_images,
            )
            record.view.updated_at = datetime.now(UTC)
            visual_event = record.debug_visual_event
        await workflow_tracker.update(
            job_id,
            JobStatus.AWAITING_VISUAL_APPROVAL,
            "Replay aguardando aprovação das cenas",
        )
        await visual_event.wait()

        for status, detail in after_visual_review:
            await _record_job_update(job_id, status, detail)
            await workflow_tracker.update(job_id, status, detail)
            await asyncio.sleep(max(settings.debug_replay_step_delay_seconds, 0.05))
        replay_output = settings.output_root / job_id
        replay_output.mkdir(parents=True, exist_ok=True)

        def materialize(source: Path, name: str) -> Path:
            destination = replay_output / name
            if destination.exists():
                return destination
            try:
                os.link(source, destination)
            except OSError:
                shutil.copy2(source, destination)
            return destination

        video_path = materialize(source_record.video_path, "presentation.mp4")
        script_path = materialize(source_record.script_path, "script.json")
        visual_plan_path = materialize(source_record.visual_plan_path, "visual-plan.json")
        replay_vtt_path = materialize(captions_vtt_path, captions_vtt_path.name)
        replay_srt_path = materialize(captions_srt_path, captions_srt_path.name)
        source_manifest = manifest
        source_manifest["job_id"] = job_id
        source_manifest["video"] = str(video_path)
        source_manifest["captions"] = {
            "vtt": str(replay_vtt_path),
            "srt": str(replay_srt_path),
            "cue_count": source_manifest.get("captions", {}).get("cue_count"),
        }
        (replay_output / "manifest.json").write_text(
            json.dumps(source_manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        now = datetime.now(UTC)
        async with _jobs_lock:
            record = _jobs[job_id]
            record.video_path = video_path
            record.script_path = script_path
            record.visual_plan_path = visual_plan_path
            record.captions_vtt_path = replay_vtt_path
            record.captions_srt_path = replay_srt_path
            record.view.duration_seconds = source_record.view.duration_seconds
            record.view.video_url = f"/v1/videos/{job_id}/download"
            record.view.script_url = f"/v1/videos/{job_id}/script"
            record.view.visual_plan_url = f"/v1/videos/{job_id}/visual-plan"
            record.view.captions_vtt_url = f"/v1/videos/{job_id}/captions.vtt"
            record.view.captions_srt_url = f"/v1/videos/{job_id}/captions.srt"
            record.view.status = JobStatus.COMPLETED
            record.view.progress_percent = 100
            record.view.detail = f"Replay concluído a partir do job {source_job_id}"
            record.view.updated_at = now
            record.view.end_datetime = now
        await workflow_tracker.update(job_id, JobStatus.COMPLETED, "Replay debug concluído")
    except Exception as exc:
        logger.exception("job=%s debug replay failed source_job=%s", job_id, source_job_id)
        await _record_job_update(job_id, JobStatus.FAILED, str(exc))
        await workflow_tracker.update(job_id, JobStatus.FAILED, str(exc))


async def _prepare_job(
    job_id: str,
    request: VideoJobRequest,
    duration_decision: str | None = None,
) -> None:
    try:
        prepared = await pipeline.prepare(
            request,
            job_id=job_id,
            duration_decision=duration_decision,
        )
        async with _jobs_lock:
            record = _jobs[job_id]
            record.prepared = prepared
            record.script_path = prepared.script_path
            record.visual_plan_path = prepared.visual_plan_path
            record.view.script_url = f"/v1/videos/{job_id}/script"
            record.view.visual_plan_url = f"/v1/videos/{job_id}/visual-plan"
            record.view.scene_images = _scene_image_views(prepared)
            record.view.source_pages = _source_page_views(prepared)
            record.view.creative_direction = prepared.script.creative_direction
            if prepared.request.brand_kit is not None:
                record.view.brand_kit = _brand_kit_view(prepared.request.brand_kit)
            record.view.target_seconds = prepared.request.target_seconds
            record.view.status = JobStatus.AWAITING_VISUAL_APPROVAL
            record.view.progress_percent = 55
            record.view.detail = "Revise os slides fixos e os frames dos vídeos"
            record.view.updated_at = datetime.now(UTC)
            logger.info(
                "job=%s awaiting visual approval images=%s progress_percent=55",
                job_id,
                len(record.view.scene_images),
            )
        _request_metadata_path(job_id).write_text(
            prepared.request.model_dump_json(indent=2),
            encoding="utf-8",
        )
    except DurationReviewRequired as exc:
        logger.info(
            "job=%s awaiting duration approval requested=%s estimated=%s words=%s",
            job_id,
            exc.requested_seconds,
            exc.estimated_seconds,
            exc.word_count,
        )
        async with _jobs_lock:
            record = _jobs.get(job_id)
            if record is not None:
                record.script_path = settings.output_root / job_id / "script.json"
                record.view.script_url = f"/v1/videos/{job_id}/script"
                record.view.status = JobStatus.AWAITING_DURATION_APPROVAL
                record.view.progress_percent = 23
                record.view.detail = (
                    f"A história foi estimada em {exc.estimated_seconds} segundos, acima dos "
                    f"{exc.requested_seconds} segundos solicitados."
                )
                record.view.requested_target_seconds = exc.requested_seconds
                record.view.estimated_duration_seconds = exc.estimated_seconds
                record.view.narration_word_count = exc.word_count
                record.view.updated_at = datetime.now(UTC)
    except Exception as exc:
        logger.exception("job=%s background preparation failed", job_id)
        async with _jobs_lock:
            failed_record = _jobs.get(job_id)
            if failed_record is not None:
                failed_record.view.status = JobStatus.FAILED
                failed_record.view.detail = _public_error_detail(exc)
                failed_record.view.updated_at = datetime.now(UTC)
                failed_record.view.end_datetime = failed_record.view.updated_at


async def _finalize_job(job_id: str, prepared: PreparedVideoJob) -> None:
    try:
        result = await pipeline.finalize(prepared)
        async with _jobs_lock:
            record = _jobs[job_id]
            record.video_path = result.video_path
            record.view.duration_seconds = result.duration_seconds
            record.view.video_url = f"/v1/videos/{job_id}/download"
            record.captions_vtt_path = result.captions_vtt_path
            record.captions_srt_path = result.captions_srt_path
            record.view.captions_vtt_url = f"/v1/videos/{job_id}/captions.vtt"
            record.view.captions_srt_url = f"/v1/videos/{job_id}/captions.srt"
            record.view.status = JobStatus.COMPLETED
            record.view.progress_percent = 100
            record.view.updated_at = datetime.now(UTC)
            record.view.end_datetime = record.view.updated_at
            logger.info("job=%s completed video_url=%s", job_id, record.view.video_url)
        await workflow_tracker.update(job_id, JobStatus.COMPLETED, "Vídeo final concluído")
    except Exception as exc:
        logger.exception("job=%s background finalization failed", job_id)
        async with _jobs_lock:
            failed_record = _jobs.get(job_id)
            if failed_record is not None:
                failed_record.view.status = JobStatus.FAILED
                failed_record.view.detail = _public_error_detail(exc)
                failed_record.view.updated_at = datetime.now(UTC)
                failed_record.view.end_datetime = failed_record.view.updated_at


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/config", response_model=RuntimeConfigView)
async def runtime_config() -> RuntimeConfigView:
    narrative_step = next(step for step in workflow_definition.steps if step.id == "narrative")
    debug_config = narrative_step.config.get("debug", {})
    return RuntimeConfigView(
        debug_mode=settings.debug_mode,
        debug_max_scenes=(int(debug_config.get("max_scenes", 3)) if settings.debug_mode else None),
        debug_replay_job_id=(
            settings.debug_replay_job_id if settings.debug_mode else None
        ),
    )


@app.get("/v1/brand-kit", response_model=BrandKitView)
async def get_brand_kit() -> BrandKitView:
    return _brand_kit_view(brand_kit_repository.get())


@app.put("/v1/brand-kit", response_model=BrandKitView)
async def update_brand_kit(payload: BrandKitUpdate) -> BrandKitView:
    current = brand_kit_repository.get()
    updated = brand_kit_repository.update(
        current.model_copy(update=payload.model_dump())
    )
    return _brand_kit_view(updated)


@app.post("/v1/brand-kit/assets/{kind}", response_model=BrandKitView)
async def upload_brand_asset(
    kind: BrandAssetKind,
    file: UploadFile = File(...),
) -> BrandKitView:
    suffix = Path(file.filename or "").suffix.lower()
    allowed = {".png", ".jpg", ".jpeg", ".webp", ".svg"}
    if suffix not in allowed:
        raise HTTPException(
            status_code=415,
            detail=f"Brand assets must use one of: {sorted(allowed)}",
        )
    content = await file.read(_MAX_BRAND_ASSET_BYTES + 1)
    await file.close()
    if len(content) > _MAX_BRAND_ASSET_BYTES:
        raise HTTPException(status_code=413, detail="Brand asset exceeds 15 MB")
    return _brand_kit_view(brand_kit_repository.save_asset(kind, content, suffix))


@app.get("/v1/brand-kit/assets/{kind}")
async def get_brand_asset(kind: BrandAssetKind) -> FileResponse:
    path = brand_kit_repository.asset_path(kind)
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Brand asset not found")
    return FileResponse(path)


@app.get("/v1/production-presets", response_model=list[ProductionPreset])
async def production_presets() -> list[ProductionPreset]:
    return list_production_presets()


@app.get("/v1/workflows", response_model=list[WorkflowDefinition])
async def list_workflows() -> list[WorkflowDefinition]:
    return workflow_loader.list()


@app.get("/v1/workflow-runs/{job_id}", response_model=WorkflowSnapshot)
async def get_workflow_run(job_id: str) -> WorkflowSnapshot:
    snapshot = workflow_tracker.snapshot(job_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Workflow run not found")
    return snapshot


@app.post("/v1/videos", response_model=JobView, status_code=202)
async def create_video(
    file: UploadFile = File(...),
    target_seconds: int = Form(default=600),
    language: str = Form(default="pt-BR"),
    audience: str = Form(default="executive"),
    tone: str = Form(default="professional and natural"),
    production_mode: ProductionMode = Form(default=ProductionMode.HYBRID_PRESENTATION),
    preset_options: str = Form(default="{}"),
) -> JobView:
    try:
        parsed_preset_options = json.loads(preset_options)
        if not isinstance(parsed_preset_options, dict) or not all(
            isinstance(key, str) and isinstance(value, str)
            for key, value in parsed_preset_options.items()
        ):
            raise ValueError
    except (json.JSONDecodeError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="preset_options must be a JSON object containing string values",
        ) from None
    preset = get_production_preset(production_mode)
    allowed_options = {option.id: option for option in preset.options}
    unknown_options = sorted(set(parsed_preset_options) - set(allowed_options))
    if unknown_options:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown options for {production_mode.value}: {unknown_options}",
        )
    normalized_preset_options = {
        option.id: parsed_preset_options.get(option.id, option.default)
        for option in preset.options
    }
    for option in preset.options:
        allowed_values = {choice.value for choice in option.choices}
        if normalized_preset_options[option.id] not in allowed_values:
            raise HTTPException(
                status_code=422,
                detail=f"Invalid value for preset option {option.id}",
            )
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".pptx", ".pdf"}:
        raise HTTPException(status_code=415, detail="Only .pptx and .pdf files are accepted")
    if not 30 <= target_seconds <= 1800:
        raise HTTPException(status_code=422, detail="target_seconds must be between 30 and 1800")

    job_id = uuid.uuid4().hex
    source_path = _upload_root / f"{job_id}{suffix}"
    size = 0
    try:
        with source_path.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="File exceeds the 100 MB limit")
                destination.write(chunk)
    except Exception:
        source_path.unlink(missing_ok=True)
        raise
    finally:
        await file.close()

    now = datetime.now(UTC)
    brand_snapshot = brand_kit_repository.get().model_copy(deep=True)
    view = JobView(
        job_id=job_id,
        status=JobStatus.RECEIVED,
        progress_percent=0,
        file_name=file.filename or source_path.name,
        target_seconds=target_seconds,
        requested_target_seconds=target_seconds,
        debug_mode=settings.debug_mode,
        language=language,
        audience=audience,
        tone=tone,
        production_mode=production_mode,
        preset_options=normalized_preset_options,
        created_at=now,
        updated_at=now,
        start_datetime=now,
        brand_kit=_brand_kit_view(brand_snapshot),
    )
    async with _jobs_lock:
        _jobs[job_id] = JobRecord(view=view, source_path=source_path)

    request = VideoJobRequest(
        source_path=source_path,
        target_seconds=target_seconds,
        language=language,
        audience=audience,
        tone=tone,
        production_mode=production_mode,
        preset_options=normalized_preset_options,
        brand_kit=brand_snapshot,
    )
    workflow_tracker.initialize(
        job_id,
        {
            "source_path": str(source_path),
            "target_seconds": target_seconds,
            "language": language,
            "audience": audience,
            "tone": tone,
            "production_mode": production_mode.value,
            "preset_options": normalized_preset_options,
            "brand_kit": brand_snapshot.model_dump(mode="json"),
        },
    )
    metadata_path = _request_metadata_path(job_id)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(request.model_dump_json(indent=2), encoding="utf-8")
    if settings.debug_mode and settings.debug_replay_job_id:
        task = asyncio.create_task(
            _replay_debug_job(job_id, settings.debug_replay_job_id)
        )
        logger.info(
            "job=%s debug replay accepted source_job=%s",
            job_id,
            settings.debug_replay_job_id,
        )
    else:
        task = asyncio.create_task(_prepare_job(job_id, request))
    async with _jobs_lock:
        _jobs[job_id].active_task = task
    logger.info(
        "job=%s accepted file=%s target_seconds=%s language=%s",
        job_id,
        view.file_name,
        target_seconds,
        language,
    )
    return view


@app.get("/v1/videos/{job_id}", response_model=JobView)
async def get_video(job_id: str) -> JobView:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            record = _recover_completed_job(job_id)
            if record is None:
                record = _recover_workflow_job(job_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Job not found")
            _jobs[job_id] = record
        return record.view.model_copy(deep=True)


@app.post("/v1/videos/{job_id}/cancel", response_model=JobView, status_code=202)
async def cancel_video(job_id: str) -> JobView:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if record.view.status == JobStatus.CANCELLED:
            return record.view.model_copy(deep=True)
        if record.view.status in {JobStatus.COMPLETED, JobStatus.FAILED}:
            raise HTTPException(
                status_code=409,
                detail=f"Job cannot be cancelled from status {record.view.status.value}",
            )
        task = record.active_task
        record.active_task = None
        record.debug_duration_event.set()
        record.debug_visual_event.set()
        now = datetime.now(UTC)
        record.view.status = JobStatus.CANCELLED
        record.view.detail = "Processamento cancelado pelo usuário"
        record.view.updated_at = now
        record.view.end_datetime = now
        view = record.view.model_copy(deep=True)

    if task is not None and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await workflow_tracker.update(
        job_id,
        JobStatus.CANCELLED,
        "Processamento cancelado pelo usuário",
    )
    logger.info("job=%s cancelled by user", job_id)
    return view


@app.post("/v1/videos/{job_id}/duration-decision", response_model=JobView, status_code=202)
async def decide_narrative_duration(
    job_id: str,
    payload: DurationDecisionRequest,
) -> JobView:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if record.view.status != JobStatus.AWAITING_DURATION_APPROVAL:
            raise HTTPException(
                status_code=409,
                detail="This job is not awaiting a duration decision",
            )
        if payload.decision == "cancel":
            record.view.status = JobStatus.CANCELLED
            record.view.detail = "Processamento cancelado antes da geração de mídia"
            record.view.updated_at = datetime.now(UTC)
            record.view.end_datetime = record.view.updated_at
            view = record.view.model_copy(deep=True)
            request = None
            if record.debug_replay_source_job_id is not None:
                record.debug_duration_decision = payload.decision
                record.debug_duration_event.set()
        else:
            if record.debug_replay_source_job_id is not None:
                request = None
                record.debug_duration_decision = payload.decision
                record.debug_duration_event.set()
            else:
                try:
                    request = _load_resume_request(job_id, record.source_path)
                except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                    raise HTTPException(
                        status_code=409,
                        detail="Job does not have a resumable narrative",
                    ) from exc
            record.view.status = JobStatus.DURATION_VALIDATING
            record.view.detail = (
                "Resumindo a história para o tempo solicitado"
                if payload.decision == "summarize"
                else "Prosseguindo com a duração estimada"
            )
            record.view.updated_at = datetime.now(UTC)
            view = record.view.model_copy(deep=True)
        workflow_tracker.resolve_duration(job_id, payload.decision)

    if payload.decision == "cancel":
        await workflow_tracker.update(
            job_id,
            JobStatus.CANCELLED,
            "Cancelado pelo usuário na revisão de duração",
        )
        return view

    async with _jobs_lock:
        replay_record = _jobs.get(job_id)
        is_debug_replay = (
            replay_record is not None
            and replay_record.debug_replay_source_job_id is not None
        )
    if is_debug_replay:
        return view

    assert request is not None
    task = asyncio.create_task(
        _prepare_job(job_id, request, duration_decision=payload.decision)
    )
    async with _jobs_lock:
        current = _jobs.get(job_id)
        if current is not None:
            current.active_task = task
    return view


@app.get("/v1/videos/{job_id}/scenes/{scene_number}/image")
async def get_scene_image(job_id: str, scene_number: int) -> FileResponse:
    return await get_shot_image(job_id, scene_number, 1)


@app.get("/v1/videos/{job_id}/source-pages/{source_slide_number}/image")
async def get_source_page_image(
    job_id: str,
    source_slide_number: int,
) -> FileResponse:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        slide = (
            next(
                (
                    item
                    for item in record.prepared.document.slides
                    if item.number == source_slide_number
                ),
                None,
            )
            if record and record.prepared
            else None
        )
    if slide is None or not slide.image_path.is_file():
        raise HTTPException(status_code=404, detail="Source page not found")
    return FileResponse(slide.image_path)


@app.get("/v1/videos/{job_id}/scenes/{scene_number}/shots/{shot_number}/image")
async def get_shot_image(job_id: str, scene_number: int, shot_number: int) -> FileResponse:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found")
        prepared = record.prepared
        image = (
            next(
                (
                    item
                    for item in prepared.visual_images
                    if item.scene_number == scene_number and item.shot_number == shot_number
                ),
                None,
            )
            if prepared
            else None
        )
        image_path = (
            image.path
            if image is not None
            else record.debug_replay_images.get((scene_number, shot_number))
        )
    if image_path is None or not image_path.exists():
        raise HTTPException(status_code=404, detail="Scene image not found")
    return FileResponse(image_path)


@app.post(
    "/v1/videos/{job_id}/scenes/{scene_number}/use-source-slide",
    response_model=JobView,
)
async def use_source_slide(
    job_id: str,
    scene_number: int,
    payload: SourceSlideSelectionRequest,
    shot_number: int = 1,
) -> JobView:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if (
            record is None
            or record.view.status != JobStatus.AWAITING_VISUAL_APPROVAL
            or record.prepared is None
        ):
            raise HTTPException(
                status_code=409,
                detail="Source pages can only be selected during visual review",
            )
        prepared = record.prepared
        if prepared.request.production_mode not in {
            ProductionMode.HYBRID_PRESENTATION,
        }:
            raise HTTPException(
                status_code=409,
                detail="This visual format does not allow original slides in the final video",
            )
        try:
            pipeline.use_source_slide(
                prepared,
                scene_number,
                shot_number,
                payload.source_slide_number,
            )
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        record.view.scene_images = _scene_image_views(prepared)
        record.view.updated_at = datetime.now(UTC)
        return record.view.model_copy(deep=True)


@app.post(
    "/v1/videos/{job_id}/scenes/{scene_number}/generate-from-source-slide",
    response_model=JobView,
)
async def generate_scene_from_source_slide(
    job_id: str,
    scene_number: int,
    payload: SourceSlideSelectionRequest,
    shot_number: int = 1,
) -> JobView:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if (
            record is None
            or record.view.status != JobStatus.AWAITING_VISUAL_APPROVAL
            or record.prepared is None
        ):
            raise HTTPException(
                status_code=409,
                detail="Images can only be generated during visual review",
            )
        if scene_number in record.regenerating_scenes:
            raise HTTPException(status_code=409, detail="This image is already being generated")
        record.regenerating_scenes.add(scene_number)
        record.view.regenerating_scene_numbers = sorted(record.regenerating_scenes)
        prepared = record.prepared
        if prepared.request.production_mode == ProductionMode.WHITEBOARD_EXPLAINER:
            record.regenerating_scenes.discard(scene_number)
            record.view.regenerating_scene_numbers = []
            raise HTTPException(
                status_code=409,
                detail="Whiteboard scenes must be regenerated as one progressive drawing",
            )
    try:
        await pipeline.generate_image_from_slide(
            prepared,
            scene_number,
            shot_number,
            payload.source_slide_number,
            payload.prompt,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Image generation from source page failed: {exc}",
        ) from exc
    finally:
        async with _jobs_lock:
            current = _jobs.get(job_id)
            if current is not None:
                current.regenerating_scenes.discard(scene_number)
                current.view.regenerating_scene_numbers = sorted(
                    current.regenerating_scenes
                )
    async with _jobs_lock:
        record = _jobs[job_id]
        record.view.scene_images = _scene_image_views(prepared)
        record.view.updated_at = datetime.now(UTC)
        return record.view.model_copy(deep=True)


@app.post("/v1/videos/{job_id}/scenes/{scene_number}/regenerate", response_model=JobView)
async def regenerate_scene_image(
    job_id: str,
    scene_number: int,
    payload: RegenerateSceneRequest | None = None,
    shot_number: int = 1,
) -> JobView:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found")
        if record.view.status != JobStatus.AWAITING_VISUAL_APPROVAL or record.prepared is None:
            raise HTTPException(
                status_code=409, detail="Images can only be regenerated during review"
            )
        if scene_number in record.regenerating_scenes:
            raise HTTPException(status_code=409, detail="This image is already being regenerated")
        plan = next(
            (
                item
                for item in record.prepared.visual_plan.scenes
                if item.scene_number == scene_number
            ),
            None,
        )
        if plan is not None and plan.media_mode == MediaMode.STATIC and plan.preserve_source_frame:
            raise HTTPException(
                status_code=409,
                detail="Slides fixos preservam a página original e não usam regeneração por prompt",
            )
        record.regenerating_scenes.add(scene_number)
        record.view.regenerating_scene_numbers = sorted(record.regenerating_scenes)
        prepared = record.prepared
        logger.info(
            "job=%s regeneration requested scene=%s prompt_updated=%s",
            job_id,
            scene_number,
            payload is not None and payload.prompt is not None,
        )
    try:
        await pipeline.regenerate_image(
            prepared,
            scene_number,
            shot_number,
            prompt=payload.prompt if payload is not None else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Image regeneration failed: {exc}") from exc
    finally:
        async with _jobs_lock:
            record = _jobs.get(job_id)
            if record is not None:
                record.regenerating_scenes.discard(scene_number)
                record.view.regenerating_scene_numbers = sorted(record.regenerating_scenes)
                record.view.updated_at = datetime.now(UTC)
    async with _jobs_lock:
        record = _jobs[job_id]
        if record.prepared is not None:
            record.view.scene_images = _scene_image_views(record.prepared)
        return record.view.model_copy(deep=True)


@app.post("/v1/videos/{job_id}/approve-visuals", response_model=JobView, status_code=202)
async def approve_visuals(job_id: str) -> JobView:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found")
        is_debug_replay = record.debug_replay_source_job_id is not None
        if (
            record.view.status != JobStatus.AWAITING_VISUAL_APPROVAL
            or (record.prepared is None and not is_debug_replay)
        ):
            raise HTTPException(status_code=409, detail="Visuals are not awaiting approval")
        if record.regenerating_scenes:
            raise HTTPException(status_code=409, detail="Wait for image regeneration to finish")
        if record.finalization_started:
            raise HTTPException(status_code=409, detail="Video generation has already started")
        if not is_debug_replay:
            assert record.prepared is not None
            try:
                validate_preset_plan(
                    record.prepared.request.production_mode,
                    record.prepared.visual_plan,
                )
            except ValueError as exc:
                record.view.status = JobStatus.FAILED
                record.view.detail = (
                    "O storyboard foi criado por uma versão anterior e precisa ser "
                    "reprocessado. Use “Retomar processamento” para reconstruí-lo."
                )
                record.view.updated_at = datetime.now(UTC)
                record.view.end_datetime = record.view.updated_at
                logger.warning("job=%s approval rejected invalid preset plan: %s", job_id, exc)
                raise HTTPException(status_code=409, detail=record.view.detail) from exc
        record.finalization_started = True
        record.view.status = JobStatus.GENERATING_VIDEO
        record.view.detail = "Storyboard aprovado; gerando apenas os takes animáveis"
        record.view.updated_at = datetime.now(UTC)
        prepared = record.prepared
        view = record.view.model_copy(deep=True)
        logger.info(
            "job=%s visuals approved images=%s starting finalization",
            job_id,
            len(prepared.visual_images) if prepared is not None else len(record.view.scene_images),
        )
        workflow_tracker.approve(job_id)
        if is_debug_replay:
            record.debug_visual_event.set()
            return view
        assert prepared is not None
    task = asyncio.create_task(_finalize_job(job_id, prepared))
    async with _jobs_lock:
        current_record = _jobs.get(job_id)
        if current_record is not None:
            current_record.active_task = task
    return view


@app.post("/v1/videos/{job_id}/resume", response_model=JobView, status_code=202)
async def resume_video(job_id: str) -> JobView:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        active_task = record.active_task if record is not None else None
        if job_id in _resuming_jobs or (active_task is not None and not active_task.done()):
            raise HTTPException(status_code=409, detail="Job is already running")
        if record is not None and record.view.status == JobStatus.COMPLETED:
            return record.view.model_copy(deep=True)
        if (
            record is not None
            and record.view.status == JobStatus.AWAITING_VISUAL_APPROVAL
            and record.prepared is not None
        ):
            return record.view.model_copy(deep=True)
        if record is not None and record.prepared is not None:
            prepared = record.prepared
            request = prepared.request
            source_path = record.source_path
        else:
            uploaded_source = _find_uploaded_source(job_id)
            if uploaded_source is None:
                raise HTTPException(status_code=404, detail="Job source file not found")
            source_path = uploaded_source
            try:
                request = _load_resume_request(job_id, source_path)
            except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                raise HTTPException(
                    status_code=409, detail="Job does not have resumable preparation artifacts"
                ) from exc
            prepared = None
        workflow_tracker.initialize(
            job_id,
            {
                "source_path": str(source_path),
                "target_seconds": request.target_seconds,
                "language": request.language,
                "audience": request.audience,
                "tone": request.tone,
                "production_mode": request.production_mode.value,
                "preset_options": request.preset_options,
            },
        )
        output_dir = settings.output_root / job_id
        preset_artifacts_invalid = False
        if request.production_mode != ProductionMode.HYBRID_PRESENTATION:
            try:
                stored_plan = (
                    prepared.visual_plan
                    if prepared is not None
                    else PresentationVisualPlan.model_validate_json(
                        (output_dir / "visual-plan.json").read_text(encoding="utf-8")
                    )
                )
                validate_preset_plan(request.production_mode, stored_plan)
            except (OSError, ValueError):
                preset_artifacts_invalid = True
        restart_preparation = not (
            (output_dir / "script.json").is_file() and (output_dir / "visual-plan.json").is_file()
        ) or preset_artifacts_invalid
        should_finalize = (
            record.finalization_started if record is not None else _finalization_has_started(job_id)
        )
        _resuming_jobs.add(job_id)

    if restart_preparation:
        now = datetime.now(UTC)
        async with _jobs_lock:
            record = _jobs.get(job_id)
            if record is None:
                record = JobRecord(
                    view=JobView(
                        job_id=job_id,
                        status=JobStatus.SCRIPTING,
                        progress_percent=15,
                        detail="Retomando a criação do roteiro",
                        file_name=source_path.name,
                        target_seconds=request.target_seconds,
                        language=request.language,
                        audience=request.audience,
                        tone=request.tone,
                        production_mode=request.production_mode,
                        created_at=now,
                        updated_at=now,
                        start_datetime=now,
                        debug_mode=settings.debug_mode,
                    ),
                    source_path=source_path,
                )
                _jobs[job_id] = record
            else:
                record.view.status = JobStatus.SCRIPTING
                record.view.detail = "Retomando a criação do roteiro"
                record.view.updated_at = now
                record.view.end_datetime = None
                record.prepared = None
                record.finalization_started = False
            view = record.view.model_copy(deep=True)
            _resuming_jobs.discard(job_id)
        task = asyncio.create_task(_prepare_job(job_id, request))
        async with _jobs_lock:
            _jobs[job_id].active_task = task
        logger.info(
            "job=%s restarting preparation incomplete=%s invalid_preset_artifacts=%s",
            job_id,
            not (
                (output_dir / "script.json").is_file()
                and (output_dir / "visual-plan.json").is_file()
            ),
            preset_artifacts_invalid,
        )
        return view

    try:
        if prepared is None:
            prepared = await pipeline.restore(request, job_id)
        now = datetime.now(UTC)
        async with _jobs_lock:
            record = _jobs.get(job_id)
            if record is None:
                record = JobRecord(
                    view=JobView(
                        job_id=job_id,
                        status=JobStatus.SYNTHESIZING,
                        progress_percent=55,
                        detail="Retomando o processamento pelos artefatos existentes",
                        file_name=source_path.name,
                        target_seconds=request.target_seconds,
                        language=request.language,
                        audience=request.audience,
                        tone=request.tone,
                        production_mode=request.production_mode,
                        created_at=now,
                        updated_at=now,
                        start_datetime=now,
                        debug_mode=settings.debug_mode,
                    ),
                    source_path=source_path,
                )
                _jobs[job_id] = record
            record.prepared = prepared
            record.script_path = prepared.script_path
            record.visual_plan_path = prepared.visual_plan_path
            record.view.status = (
                JobStatus.SYNTHESIZING if should_finalize else JobStatus.AWAITING_VISUAL_APPROVAL
            )
            record.view.end_datetime = None
            record.view.progress_percent = max(record.view.progress_percent, 55)
            record.view.detail = (
                "Retomando áudio, animação e renderização"
                if should_finalize
                else "Frames recuperados; revise os visuais antes de continuar"
            )
            record.view.updated_at = now
            record.view.script_url = f"/v1/videos/{job_id}/script"
            record.view.visual_plan_url = f"/v1/videos/{job_id}/visual-plan"
            record.view.scene_images = _scene_image_views(prepared)
            record.view.source_pages = _source_page_views(prepared)
            record.view.creative_direction = prepared.script.creative_direction
            if prepared.request.brand_kit is not None:
                record.view.brand_kit = _brand_kit_view(prepared.request.brand_kit)
            record.finalization_started = should_finalize
            view = record.view.model_copy(deep=True)
    except FileNotFoundError as exc:
        async with _jobs_lock:
            failed_record = _jobs.get(job_id)
            if failed_record is not None:
                failed_record.view.status = JobStatus.FAILED
                failed_record.view.detail = str(exc)
                failed_record.view.updated_at = datetime.now(UTC)
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("job=%s resume failed", job_id)
        async with _jobs_lock:
            failed_record = _jobs.get(job_id)
            if failed_record is not None:
                failed_record.view.status = JobStatus.FAILED
                failed_record.view.detail = _public_error_detail(exc)
                failed_record.view.updated_at = datetime.now(UTC)
        raise HTTPException(
            status_code=502,
            detail=f"Não foi possível retomar o job: {_public_error_detail(exc)}",
        ) from exc
    finally:
        async with _jobs_lock:
            _resuming_jobs.discard(job_id)

    if should_finalize:
        task = asyncio.create_task(_finalize_job(job_id, prepared))
        async with _jobs_lock:
            current_record = _jobs.get(job_id)
            if current_record is not None:
                current_record.active_task = task
    logger.info(
        "job=%s resumed from durable artifacts finalization_started=%s",
        job_id,
        should_finalize,
    )
    return view


@app.get("/v1/videos/{job_id}/download")
async def download_video(job_id: str) -> FileResponse:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            record = _recover_completed_job(job_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Job not found")
            _jobs[job_id] = record
        video_path = record.video_path
        original_stem = Path(record.view.file_name).stem
    if video_path is None or not video_path.exists():
        raise HTTPException(status_code=409, detail="Video is not ready")
    return FileResponse(
        video_path,
        media_type="video/mp4",
        filename=f"{original_stem}-narrado.mp4",
    )


@app.get("/v1/videos/{job_id}/script")
async def download_script(job_id: str) -> FileResponse:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found")
        script_path = record.script_path
        original_stem = Path(record.view.file_name).stem
    if script_path is None or not script_path.exists():
        raise HTTPException(status_code=409, detail="Script is not ready")
    return FileResponse(
        script_path,
        media_type="application/json",
        filename=f"{original_stem}-roteiro.json",
    )


@app.get("/v1/videos/{job_id}/captions.vtt")
async def download_captions_vtt(job_id: str) -> FileResponse:
    return await _download_captions(job_id, "vtt")


@app.get("/v1/videos/{job_id}/captions.srt")
async def download_captions_srt(job_id: str) -> FileResponse:
    return await _download_captions(job_id, "srt")


async def _download_captions(job_id: str, extension: Literal["vtt", "srt"]) -> FileResponse:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            record = _recover_completed_job(job_id)
            if record is None:
                raise HTTPException(status_code=404, detail="Job not found")
            _jobs[job_id] = record
        path = (
            record.captions_vtt_path
            if extension == "vtt"
            else record.captions_srt_path
        )
        original_stem = Path(record.view.file_name).stem
    if path is None or not path.is_file():
        raise HTTPException(status_code=409, detail="Captions are not ready")
    return FileResponse(
        path,
        media_type=("text/vtt" if extension == "vtt" else "application/x-subrip"),
        filename=f"{original_stem}-legendas.{extension}",
    )


@app.get("/v1/videos/{job_id}/visual-plan")
async def download_visual_plan(job_id: str) -> FileResponse:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found")
        visual_plan_path = record.visual_plan_path
        original_stem = Path(record.view.file_name).stem
    if visual_plan_path is None or not visual_plan_path.exists():
        raise HTTPException(status_code=409, detail="Visual plan is not ready")
    return FileResponse(
        visual_plan_path,
        media_type="application/json",
        filename=f"{original_stem}-plano-visual.json",
    )


# Serve the production frontend build when it is present. During development,
# Vite runs separately and proxies /v1 and /health to FastAPI.
_frontend_dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _frontend_dist.exists():
    _assets_dir = _frontend_dist / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="frontend-assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_frontend(full_path: str) -> FileResponse:
        requested = (_frontend_dist / full_path).resolve()
        if requested.is_file() and _frontend_dist.resolve() in requested.parents:
            headers = (
                {"Cache-Control": "no-cache, no-store, must-revalidate"}
                if requested.name == "index.html"
                else {"Cache-Control": "public, max-age=31536000, immutable"}
            )
            return FileResponse(requested, headers=headers)
        return FileResponse(
            _frontend_dist / "index.html",
            headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
        )
