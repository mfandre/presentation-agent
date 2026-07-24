from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from presentation_video.bootstrap import build_pipeline
from presentation_video.domain.errors import UserFacingError
from presentation_video.domain.models import JobStatus, MediaMode, PreparedVideoJob, VideoJobRequest
from presentation_video.infrastructure.reporting import (
    CallbackJobReporter,
    CompositeJobReporter,
    LoggingJobReporter,
)
from presentation_video.settings import Settings

logger = logging.getLogger(__name__)

settings = Settings()
app = FastAPI(title="Presentation Video AI", version="0.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SceneImageView(BaseModel):
    scene_number: int
    source_slide_numbers: list[int]
    image_url: str
    prompt: str
    camera_motion: str
    revision: int
    media_mode: MediaMode
    story_beat: str
    source_slide_number: int | None = None


class RegenerateSceneRequest(BaseModel):
    prompt: str | None = Field(default=None, min_length=3, max_length=4_000)


class RuntimeConfigView(BaseModel):
    debug_mode: bool
    debug_max_scenes: int | None = None


class JobView(BaseModel):
    job_id: str
    status: JobStatus
    progress_percent: int = Field(default=0, ge=0, le=100)
    detail: str = ""
    file_name: str
    target_seconds: int
    language: str
    audience: str
    tone: str
    created_at: datetime
    updated_at: datetime
    duration_seconds: float | None = None
    video_url: str | None = None
    script_url: str | None = None
    visual_plan_url: str | None = None
    scene_images: list[SceneImageView] = Field(default_factory=list)
    regenerating_scene_numbers: list[int] = Field(default_factory=list)
    debug_mode: bool = False


@dataclass(slots=True)
class JobRecord:
    view: JobView
    source_path: Path
    video_path: Path | None = None
    script_path: Path | None = None
    visual_plan_path: Path | None = None
    prepared: PreparedVideoJob | None = None
    regenerating_scenes: set[int] = field(default_factory=set)
    finalization_started: bool = False


_jobs: dict[str, JobRecord] = {}
_jobs_lock = asyncio.Lock()
_upload_root = settings.work_root / "uploads"
_upload_root.mkdir(parents=True, exist_ok=True)
_MAX_UPLOAD_BYTES = 100 * 1024 * 1024

_STAGE_PROGRESS: dict[JobStatus, tuple[int, int]] = {
    JobStatus.RECEIVED: (0, 0),
    JobStatus.INGESTING: (5, 15),
    JobStatus.SCRIPTING: (15, 27),
    JobStatus.VISUAL_PLANNING: (27, 35),
    JobStatus.GENERATING_IMAGES: (35, 55),
    JobStatus.AWAITING_VISUAL_APPROVAL: (55, 55),
    JobStatus.SYNTHESIZING: (55, 65),
    JobStatus.GENERATING_VIDEO: (65, 82),
    JobStatus.RENDERING: (82, 92),
    JobStatus.ASSEMBLING: (92, 97),
    JobStatus.COMPLETED: (100, 100),
    JobStatus.FAILED: (0, 0),
}
_ITEM_PROGRESS_PATTERN = re.compile(r"(?:completed|slide)=(\d+)\s+total=(\d+)")
_JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def _public_error_detail(exc: Exception) -> str:
    if isinstance(exc, UserFacingError):
        return exc.user_message
    return str(exc)


def _recover_completed_job(job_id: str) -> JobRecord | None:
    if not _JOB_ID_PATTERN.fullmatch(job_id):
        return None
    output_dir = settings.output_root / job_id
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
    target_seconds = min(max(int(manifest.get("target_seconds") or round(duration)), 30), 1800)
    script_path = output_dir / "script.json"
    visual_plan_path = output_dir / "visual-plan.json"
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
        created_at=timestamp,
        updated_at=timestamp,
        duration_seconds=duration,
        video_url=f"/v1/videos/{job_id}/download",
        script_url=f"/v1/videos/{job_id}/script" if script_path.is_file() else None,
        visual_plan_url=(
            f"/v1/videos/{job_id}/visual-plan" if visual_plan_path.is_file() else None
        ),
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
        finalization_started=True,
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
        ]
    ),
)


def _scene_image_views(prepared: PreparedVideoJob) -> list[SceneImageView]:
    plans = {plan.scene_number: plan for plan in prepared.visual_plan.scenes}
    return [
        SceneImageView(
            scene_number=image.scene_number,
            source_slide_numbers=plans[image.scene_number].source_slide_numbers,
            image_url=(
                f"/v1/videos/{prepared.job_id}/scenes/{image.scene_number}/image"
                f"?revision={image.revision}"
            ),
            prompt=plans[image.scene_number].prompt,
            camera_motion=plans[image.scene_number].camera_motion,
            revision=image.revision,
            media_mode=plans[image.scene_number].media_mode,
            story_beat=plans[image.scene_number].story_beat,
            source_slide_number=plans[image.scene_number].source_slide_number,
        )
        for image in sorted(prepared.visual_images, key=lambda item: item.scene_number)
    ]


async def _prepare_job(job_id: str, request: VideoJobRequest) -> None:
    try:
        prepared = await pipeline.prepare(request, job_id=job_id)
        async with _jobs_lock:
            record = _jobs[job_id]
            record.prepared = prepared
            record.script_path = prepared.script_path
            record.visual_plan_path = prepared.visual_plan_path
            record.view.script_url = f"/v1/videos/{job_id}/script"
            record.view.visual_plan_url = f"/v1/videos/{job_id}/visual-plan"
            record.view.scene_images = _scene_image_views(prepared)
            record.view.status = JobStatus.AWAITING_VISUAL_APPROVAL
            record.view.progress_percent = 55
            record.view.detail = "Revise os slides fixos e os frames dos vídeos"
            record.view.updated_at = datetime.now(UTC)
            logger.info(
                "job=%s awaiting visual approval images=%s progress_percent=55",
                job_id,
                len(record.view.scene_images),
            )
    except Exception as exc:
        logger.exception("job=%s background preparation failed", job_id)
        async with _jobs_lock:
            failed_record = _jobs.get(job_id)
            if failed_record is not None:
                failed_record.view.status = JobStatus.FAILED
                failed_record.view.detail = _public_error_detail(exc)
                failed_record.view.updated_at = datetime.now(UTC)


async def _finalize_job(job_id: str, prepared: PreparedVideoJob) -> None:
    try:
        result = await pipeline.finalize(prepared)
        async with _jobs_lock:
            record = _jobs[job_id]
            record.video_path = result.video_path
            record.view.duration_seconds = result.duration_seconds
            record.view.video_url = f"/v1/videos/{job_id}/download"
            record.view.status = JobStatus.COMPLETED
            record.view.progress_percent = 100
            record.view.updated_at = datetime.now(UTC)
            logger.info("job=%s completed video_url=%s", job_id, record.view.video_url)
    except Exception as exc:
        logger.exception("job=%s background finalization failed", job_id)
        async with _jobs_lock:
            failed_record = _jobs.get(job_id)
            if failed_record is not None:
                failed_record.view.status = JobStatus.FAILED
                failed_record.view.detail = _public_error_detail(exc)
                failed_record.view.updated_at = datetime.now(UTC)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/v1/config", response_model=RuntimeConfigView)
async def runtime_config() -> RuntimeConfigView:
    return RuntimeConfigView(
        debug_mode=settings.debug_mode,
        debug_max_scenes=settings.debug_max_scenes if settings.debug_mode else None,
    )


@app.post("/v1/videos", response_model=JobView, status_code=202)
async def create_video(
    file: UploadFile = File(...),
    target_seconds: int = Form(default=600),
    language: str = Form(default="pt-BR"),
    audience: str = Form(default="executive"),
    tone: str = Form(default="professional and natural"),
) -> JobView:
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
    view = JobView(
        job_id=job_id,
        status=JobStatus.RECEIVED,
        progress_percent=0,
        file_name=file.filename or source_path.name,
        target_seconds=target_seconds,
        debug_mode=settings.debug_mode,
        language=language,
        audience=audience,
        tone=tone,
        created_at=now,
        updated_at=now,
    )
    async with _jobs_lock:
        _jobs[job_id] = JobRecord(view=view, source_path=source_path)

    request = VideoJobRequest(
        source_path=source_path,
        target_seconds=target_seconds,
        language=language,
        audience=audience,
        tone=tone,
    )
    asyncio.create_task(_prepare_job(job_id, request))
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
                raise HTTPException(status_code=404, detail="Job not found")
            _jobs[job_id] = record
        return record.view.model_copy(deep=True)


@app.get("/v1/videos/{job_id}/scenes/{scene_number}/image")
async def get_scene_image(job_id: str, scene_number: int) -> FileResponse:
    async with _jobs_lock:
        record = _jobs.get(job_id)
        if record is None:
            raise HTTPException(status_code=404, detail="Job not found")
        prepared = record.prepared
        image = (
            next(
                (item for item in prepared.visual_images if item.scene_number == scene_number),
                None,
            )
            if prepared
            else None
        )
    if image is None or not image.path.exists():
        raise HTTPException(status_code=404, detail="Scene image not found")
    return FileResponse(image.path)


@app.post("/v1/videos/{job_id}/scenes/{scene_number}/regenerate", response_model=JobView)
async def regenerate_scene_image(
    job_id: str,
    scene_number: int,
    payload: RegenerateSceneRequest | None = None,
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
        if plan is not None and plan.media_mode == MediaMode.STATIC:
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
        if record.view.status != JobStatus.AWAITING_VISUAL_APPROVAL or record.prepared is None:
            raise HTTPException(status_code=409, detail="Visuals are not awaiting approval")
        if record.regenerating_scenes:
            raise HTTPException(status_code=409, detail="Wait for image regeneration to finish")
        if record.finalization_started:
            raise HTTPException(status_code=409, detail="Video generation has already started")
        record.finalization_started = True
        record.view.status = JobStatus.SYNTHESIZING
        record.view.detail = "Storyboard aprovado; preparando áudio, slides fixos e vídeos"
        record.view.updated_at = datetime.now(UTC)
        prepared = record.prepared
        view = record.view.model_copy(deep=True)
        logger.info(
            "job=%s visuals approved images=%s starting finalization",
            job_id,
            len(prepared.visual_images),
        )
    asyncio.create_task(_finalize_job(job_id, prepared))
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
