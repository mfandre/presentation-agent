from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from presentation_video.application.cinematic import materialize_shot
from presentation_video.application.production_policy import shots_or_default
from presentation_video.application.production_presets import validate_preset_plan
from presentation_video.application.production_presets import transform_visual_plan
from presentation_video.application.brand import apply_brand_kit
from presentation_video.application.whiteboard_states import (
    build_progressive_whiteboard_states,
)
from presentation_video.domain.models import (
    JobStatus,
    MediaMode,
    PreparedVideoJob,
    PresentationScript,
    PresentationVisualPlan,
    ProductionMode,
    VideoJobRequest,
    VisualArtifact,
)
from presentation_video.domain.ports import (
    DocumentIngestorFactory,
    JobReporter,
    VisualAssetGenerator,
)
from presentation_video.infrastructure.visual_planning import validate_sequence

logger = logging.getLogger(__name__)


async def restore_prepared_job(
    request: VideoJobRequest,
    job_id: str,
    *,
    work_root: Path,
    output_root: Path,
    ingestor_factory: DocumentIngestorFactory,
    visual_asset_generator: VisualAssetGenerator,
    reporter: JobReporter,
) -> PreparedVideoJob:
    """Rebuild a prepared job and generate only missing visual artifacts."""

    work_dir = work_root / job_id
    output_dir = output_root / job_id
    script_path = output_dir / "script.json"
    visual_plan_path = output_dir / "visual-plan.json"
    if not request.source_path.is_file():
        raise FileNotFoundError(f"Source file not found for job {job_id}")
    if not script_path.is_file() or not visual_plan_path.is_file():
        raise FileNotFoundError(f"Job {job_id} has not completed visual preparation")

    document = await ingestor_factory.create(request.source_path).ingest(
        request.source_path,
        work_dir,
    )
    script = PresentationScript.model_validate_json(script_path.read_text(encoding="utf-8"))
    visual_plan = PresentationVisualPlan.model_validate_json(
        visual_plan_path.read_text(encoding="utf-8")
    )
    if request.production_mode == ProductionMode.HYBRID_PRESENTATION:
        validate_sequence(visual_plan, script, document)
    else:
        if request.production_mode == ProductionMode.CORPORATE_TRAINING:
            visual_plan = transform_visual_plan(
                request.production_mode,
                visual_plan,
                request.preset_options,
                script,
            )
            visual_plan = apply_brand_kit(visual_plan, request.brand_kit)
        validate_preset_plan(request.production_mode, visual_plan)
    visual_plan_path.write_text(visual_plan.model_dump_json(indent=2), encoding="utf-8")
    slides = {slide.number: slide for slide in document.slides}
    scripts = {scene.scene_number: scene for scene in script.scenes}
    images: list[VisualArtifact] = []
    completed_images = 0
    shot_total = sum(max(len(plan.shots), 1) for plan in visual_plan.scenes)
    if request.production_mode == ProductionMode.WHITEBOARD_EXPLAINER:
        for scene_plan in visual_plan.scenes:
            state_count = len(scene_plan.shots)
            state_candidates = [
                sorted(
                    (work_dir / "images").glob(
                        f"scene-{scene_plan.scene_number:03d}-whiteboard-state-"
                        f"{state_number:03d}-r*.png"
                    )
                )
                for state_number in range(0, state_count + 1)
            ]
            if all(state_candidates):
                revision_text = state_candidates[-1][-1].stem.rsplit("-r", 1)[-1]
                revision = int(revision_text) if revision_text.isdigit() else 1
                states = [
                    VisualArtifact(
                        scene_number=scene_plan.scene_number,
                        shot_number=state_number,
                        path=state_candidates[state_number][-1],
                        start_path=state_candidates[state_number - 1][-1],
                        kind="image",
                        revision=revision,
                    )
                    for state_number in range(1, state_count + 1)
                ]
            else:
                scene_script = scripts[scene_plan.scene_number]
                master = await visual_asset_generator.generate(
                    scene_plan.model_copy(update={"shots": [], "shot_number": 1}),
                    [slides[number] for number in scene_script.source_slide_numbers],
                    work_dir / "images",
                )
                states = await asyncio.to_thread(
                    build_progressive_whiteboard_states,
                    master,
                    state_count,
                    work_dir / "images",
                )
            images.extend(states)
            completed_images += len(states)
            await reporter.update(
                job_id,
                JobStatus.GENERATING_IMAGES,
                f"whiteboard_states completed={completed_images} total={shot_total}"
                f" | estados progressivos da cena {scene_plan.scene_number} recuperados",
            )
        return PreparedVideoJob(
            job_id=job_id,
            request=request,
            document=document,
            script=script,
            visual_plan=visual_plan,
            visual_images=images,
            work_dir=work_dir,
            output_dir=output_dir,
            script_path=script_path,
            visual_plan_path=visual_plan_path,
        )

    for scene_plan in visual_plan.scenes:
        for shot in shots_or_default(scene_plan.shots):
            shot_number = shot.shot_number if shot else 1
            plan = materialize_shot(scene_plan, shot) if shot else scene_plan
            if plan.media_mode == MediaMode.STATIC and plan.preserve_source_frame:
                source_number = plan.source_slide_number or plan.source_slide_numbers[0]
                try:
                    image_path = slides[source_number].image_path
                except KeyError as exc:
                    raise FileNotFoundError(
                        f"Source slide {source_number} is unavailable for scene "
                        f"{plan.scene_number}"
                    ) from exc
                revision = 1
            else:
                stem = f"scene-{plan.scene_number:03d}"
                if shot_number > 1:
                    stem += f"-shot-{shot_number:03d}"
                candidates = sorted((work_dir / "images").glob(f"{stem}-r*.*"))
                if not candidates:
                    scene_script = scripts[plan.scene_number]
                    source_slides = [
                        slides[number] for number in scene_script.source_slide_numbers
                    ]
                    logger.info(
                        "job=%s restoring missing generated image scene=%s shot=%s",
                        job_id,
                        plan.scene_number,
                        shot_number,
                    )
                    image = await visual_asset_generator.generate(
                        plan,
                        source_slides,
                        work_dir / "images",
                    )
                    image_path = image.path
                    revision = image.revision
                else:
                    image_path = candidates[-1]
                    match = image_path.stem.rsplit("-r", 1)
                    revision = (
                        int(match[1])
                        if len(match) == 2 and match[1].isdigit()
                        else 1
                    )
            images.append(
                VisualArtifact(
                    scene_number=plan.scene_number,
                    shot_number=shot_number,
                    path=image_path,
                    kind="image",
                    revision=revision,
                    source_slide_number=(
                        source_number
                        if plan.media_mode == MediaMode.STATIC
                        and plan.preserve_source_frame
                        else None
                    ),
                )
            )
            completed_images += 1
            await reporter.update(
                job_id,
                JobStatus.GENERATING_IMAGES,
                f"completed={completed_images} total={shot_total}"
                f" | frame da cena {plan.scene_number}, take {shot_number} recuperado",
            )

    return PreparedVideoJob(
        job_id=job_id,
        request=request,
        document=document,
        script=script,
        visual_plan=visual_plan,
        visual_images=images,
        work_dir=work_dir,
        output_dir=output_dir,
        script_path=script_path,
        visual_plan_path=visual_plan_path,
    )


async def regenerate_visual(
    prepared: PreparedVideoJob,
    scene_number: int,
    shot_number: int,
    prompt: str | None,
    *,
    visual_asset_generator: VisualAssetGenerator,
    semaphore: asyncio.Semaphore,
) -> VisualArtifact:
    """Regenerate one reviewable visual while preserving the approved checkpoint."""

    slides = {slide.number: slide for slide in prepared.document.slides}
    scripts = {scene.scene_number: scene for scene in prepared.script.scenes}
    plans = {plan.scene_number: plan for plan in prepared.visual_plan.scenes}
    images = {
        (image.scene_number, image.shot_number): image
        for image in prepared.visual_images
    }
    image_key = (scene_number, shot_number)
    if scene_number not in scripts or scene_number not in plans or image_key not in images:
        raise ValueError(f"Scene {scene_number}, shot {shot_number} does not exist")
    if (
        plans[scene_number].media_mode == MediaMode.STATIC
        and plans[scene_number].preserve_source_frame
    ):
        raise ValueError(
            "Static scenes preserve an original source page and cannot be regenerated "
            "from a prompt"
        )
    scene_plan = plans[scene_number]
    shot = scene_plan.shots[shot_number - 1] if scene_plan.shots else None
    generation_plan = materialize_shot(scene_plan, shot) if shot else scene_plan
    previous_prompt = generation_plan.prompt
    if prompt is not None:
        if shot:
            shot.prompt = prompt.strip()
            generation_plan = materialize_shot(scene_plan, shot)
        else:
            scene_plan.prompt = prompt.strip()
            generation_plan = scene_plan
    revision = images[image_key].revision + 1
    logger.info(
        "job=%s image regeneration started scene=%s shot=%s revision=%s "
        "prompt_updated=%s prompt_characters=%s",
        prepared.job_id,
        scene_number,
        shot_number,
        revision,
        prompt is not None,
        len(generation_plan.prompt),
    )
    try:
        async with semaphore:
            source_slides = [
                slides[number] for number in scripts[scene_number].source_slide_numbers
            ]
            replacement = await visual_asset_generator.generate(
                generation_plan,
                source_slides,
                prepared.work_dir / "images",
                revision=revision,
            )
    except Exception:
        if shot:
            shot.prompt = previous_prompt
        else:
            scene_plan.prompt = previous_prompt
        raise
    prepared.visual_plan_path.write_text(
        prepared.visual_plan.model_dump_json(indent=2),
        encoding="utf-8",
    )
    prepared.visual_images = [
        replacement
        if (image.scene_number, image.shot_number) == image_key
        else image
        for image in prepared.visual_images
    ]
    logger.info(
        "job=%s image regeneration completed scene=%s shot=%s revision=%s path=%s",
        prepared.job_id,
        scene_number,
        shot_number,
        revision,
        replacement.path,
    )
    return replacement


def select_source_slide(
    prepared: PreparedVideoJob,
    scene_number: int,
    shot_number: int,
    source_slide_number: int,
) -> VisualArtifact:
    """Make an explicitly selected source page the approved asset for one take."""
    slide = next(
        (item for item in prepared.document.slides if item.number == source_slide_number),
        None,
    )
    plan = next(
        (item for item in prepared.visual_plan.scenes if item.scene_number == scene_number),
        None,
    )
    current = next(
        (
            item
            for item in prepared.visual_images
            if item.scene_number == scene_number and item.shot_number == shot_number
        ),
        None,
    )
    if slide is None or plan is None or current is None:
        raise ValueError("Scene, take, or source page does not exist")
    replacement = VisualArtifact(
        scene_number=scene_number,
        shot_number=shot_number,
        path=slide.image_path,
        kind="image",
        revision=current.revision + 1,
        source_slide_number=source_slide_number,
    )
    if not plan.shots:
        plan.source_slide_number = source_slide_number
        plan.preserve_source_frame = True
    prepared.visual_images = [
        replacement
        if (item.scene_number, item.shot_number) == (scene_number, shot_number)
        else item
        for item in prepared.visual_images
    ]
    prepared.visual_plan_path.write_text(
        prepared.visual_plan.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return replacement


async def generate_from_source_slide(
    prepared: PreparedVideoJob,
    scene_number: int,
    shot_number: int,
    source_slide_number: int,
    prompt: str | None,
    *,
    visual_asset_generator: VisualAssetGenerator,
    semaphore: asyncio.Semaphore,
) -> VisualArtifact:
    """Generate a new approved frame grounded in one selected source page."""
    slide = next(
        (item for item in prepared.document.slides if item.number == source_slide_number),
        None,
    )
    plan = next(
        (item for item in prepared.visual_plan.scenes if item.scene_number == scene_number),
        None,
    )
    current = next(
        (
            item
            for item in prepared.visual_images
            if item.scene_number == scene_number and item.shot_number == shot_number
        ),
        None,
    )
    if slide is None or plan is None or current is None:
        raise ValueError("Scene, take, or source page does not exist")
    shot = plan.shots[shot_number - 1] if plan.shots else None
    generation_plan = materialize_shot(plan, shot) if shot else plan
    instruction = (
        (prompt or generation_plan.prompt).strip()
        + f" Use source page {source_slide_number} as the sole semantic and visual reference. "
        "Create a new video-ready composition in the established identity; preserve its factual "
        "meaning but do not copy the page layout or long text."
    )
    generation_plan = generation_plan.model_copy(
        update={
            "prompt": instruction,
            "source_slide_numbers": [source_slide_number],
            "source_slide_number": None,
            "preserve_source_frame": False,
        }
    )
    async with semaphore:
        replacement = await visual_asset_generator.generate(
            generation_plan,
            [slide],
            prepared.work_dir / "images",
            revision=current.revision + 1,
        )
    if shot:
        shot.prompt = instruction
    else:
        plan.source_slide_number = None
        plan.preserve_source_frame = False
        plan.source_slide_numbers = [source_slide_number]
        plan.prompt = instruction
    prepared.visual_images = [
        replacement
        if (item.scene_number, item.shot_number) == (scene_number, shot_number)
        else item
        for item in prepared.visual_images
    ]
    prepared.visual_plan_path.write_text(
        prepared.visual_plan.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return replacement


async def regenerate_whiteboard_scene(
    prepared: PreparedVideoJob,
    scene_number: int,
    requested_shot_number: int,
    prompt: str | None,
    *,
    visual_asset_generator: VisualAssetGenerator,
    semaphore: asyncio.Semaphore,
) -> VisualArtifact:
    """Regenerate one locked master and rebuild every cumulative state in its scene."""

    scene_plan = next(
        (
            plan
            for plan in prepared.visual_plan.scenes
            if plan.scene_number == scene_number
        ),
        None,
    )
    scene_script = next(
        (
            scene
            for scene in prepared.script.scenes
            if scene.scene_number == scene_number
        ),
        None,
    )
    if scene_plan is None or scene_script is None or not scene_plan.shots:
        raise ValueError(f"Whiteboard scene {scene_number} does not exist")
    if not 1 <= requested_shot_number <= len(scene_plan.shots):
        raise ValueError(
            f"Whiteboard scene {scene_number}, shot {requested_shot_number} does not exist"
        )
    slides = {slide.number: slide for slide in prepared.document.slides}
    revision = (
        max(
            (
                image.revision
                for image in prepared.visual_images
                if image.scene_number == scene_number
            ),
            default=0,
        )
        + 1
    )
    previous_prompt = scene_plan.prompt
    if prompt is not None:
        scene_plan.prompt = prompt.strip()
    master_plan = scene_plan.model_copy(
        update={"shots": [], "shot_number": 1}
    )
    try:
        async with semaphore:
            master = await visual_asset_generator.generate(
                master_plan,
                [slides[number] for number in scene_script.source_slide_numbers],
                prepared.work_dir / "images",
                revision=revision,
            )
            states = await asyncio.to_thread(
                build_progressive_whiteboard_states,
                master,
                len(scene_plan.shots),
                prepared.work_dir / "images",
            )
    except Exception:
        scene_plan.prompt = previous_prompt
        raise
    prepared.visual_images = [
        image for image in prepared.visual_images if image.scene_number != scene_number
    ] + states
    prepared.visual_plan_path.write_text(
        prepared.visual_plan.model_dump_json(indent=2),
        encoding="utf-8",
    )
    return next(
        image
        for image in states
        if image.shot_number == requested_shot_number
    )
