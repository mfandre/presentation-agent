from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import uuid
import wave
from pathlib import Path

from presentation_video.domain.models import (
    AudioArtifact,
    JobStatus,
    MediaMode,
    MotionPreset,
    PresentationScript,
    PresentationVisualPlan,
    ProductionMode,
    PreparedVideoJob,
    SceneArtifact,
    VideoJobRequest,
    VideoJobResult,
    VisualArtifact,
    VisualBeat,
    VisualBeatKind,
    VisualShotPlan,
    build_default_visual_beats,
)
from presentation_video.domain.errors import DurationReviewRequired
from presentation_video.domain.ports import (
    AvatarRenderer,
    DocumentIngestorFactory,
    JobReporter,
    NarrativeGenerator,
    SceneRenderer,
    SpeechSynthesizer,
    VideoAssembler,
    VideoClipGenerator,
    VisualAssetGenerator,
    VisualPlanner,
)
from presentation_video.application.cinematic import compile_shots, materialize_shot
from presentation_video.application.captions import build_caption_cues, write_caption_files
from presentation_video.infrastructure.visual_planning import _validate_sequence
from presentation_video.infrastructure.process import run_process
from presentation_video.infrastructure.speech import media_duration

logger = logging.getLogger(__name__)


def _shots_or_default(shots: list[VisualShotPlan]) -> list[VisualShotPlan | None]:
    return list(shots) if shots else [None]


def _word_count(script: PresentationScript) -> int:
    return sum(len(scene.narration.split()) for scene in script.scenes)


def _compact_script(
    script: PresentationScript,
    target_seconds: int,
    words_per_minute: int,
) -> PresentationScript:
    maximum_words = math.floor(target_seconds * words_per_minute / 60)
    current_counts = [max(len(scene.narration.split()), 1) for scene in script.scenes]
    total = sum(current_counts)
    raw_budgets = [maximum_words * count / total for count in current_counts]
    budgets = [max(1, math.floor(value)) for value in raw_budgets]
    while sum(budgets) > maximum_words:
        largest = max(range(len(budgets)), key=budgets.__getitem__)
        budgets[largest] -= 1
    for index in sorted(
        range(len(budgets)),
        key=lambda item: raw_budgets[item] - math.floor(raw_budgets[item]),
        reverse=True,
    ):
        if sum(budgets) >= maximum_words:
            break
        budgets[index] += 1

    scenes = []
    for scene, budget in zip(script.scenes, budgets, strict=True):
        words = scene.narration.split()
        if len(words) <= budget:
            narration = scene.narration
        else:
            sentences = re.split(r"(?<=[.!?])\s+", scene.narration.strip())
            selected: list[str] = []
            used = 0
            for sentence in sentences:
                sentence_words = sentence.split()
                if used + len(sentence_words) > budget:
                    break
                selected.append(sentence)
                used += len(sentence_words)
            narration = (
                " ".join(selected)
                if selected
                else " ".join(words[:budget]).rstrip(".,;:") + "."
            )
        scenes.append(scene.model_copy(update={"narration": narration}))
    weights = [max(len(scene.narration.split()), 1) for scene in scenes]
    durations = [max(1, round(target_seconds * weight / sum(weights))) for weight in weights]
    durations[-1] += target_seconds - sum(durations)
    return script.model_copy(
        update={
            "scenes": [
                scene.model_copy(update={"target_seconds": duration})
                for scene, duration in zip(scenes, durations, strict=True)
            ],
            "total_estimated_seconds": target_seconds,
        }
    )


def _retime_script(script: PresentationScript, target_seconds: int) -> PresentationScript:
    weights = [max(len(scene.narration.split()), 1) for scene in script.scenes]
    durations = [max(1, round(target_seconds * weight / sum(weights))) for weight in weights]
    durations[-1] += target_seconds - sum(durations)
    return script.model_copy(
        update={
            "scenes": [
                scene.model_copy(update={"target_seconds": duration})
                for scene, duration in zip(script.scenes, durations, strict=True)
            ],
            "total_estimated_seconds": target_seconds,
        }
    )


def _cinematic_script(script: PresentationScript) -> PresentationScript:
    return script.model_copy(
        update={
            "scenes": [
                scene.model_copy(
                    update={
                        "media_mode": MediaMode.VIDEO,
                        "visual_intent": (
                            f"{scene.visual_intent}. Create an original cinematic scene "
                            "grounded in the source meaning; never reproduce a source page "
                            "or document."
                        ),
                    }
                )
                for scene in script.scenes
            ]
        }
    )


def _cinematic_visual_plan(plan: PresentationVisualPlan) -> PresentationVisualPlan:
    def adapt(scene):
        return scene.model_copy(
            update={
                "media_mode": MediaMode.VIDEO,
                "source_slide_number": None,
                "preserve_source_frame": False,
                "visual_beats": [
                    beat.model_copy(
                        update={
                            "kind": (
                                VisualBeatKind.GENERATED_IMAGE
                                if beat.kind == VisualBeatKind.SOURCE_SLIDE
                                else beat.kind
                            )
                        }
                    )
                    for beat in scene.visual_beats
                ],
                "prompt": (
                    f"{scene.prompt} Original cinematic shot only. No slide, page, "
                    "document, presentation layout, readable text, caption, or interface."
                ),
            }
        )

    return plan.model_copy(
        update={
            "scenes": [adapt(scene) for scene in plan.scenes]
        }
    )


def _validate_cinematic_has_no_source_frames(plan: PresentationVisualPlan) -> None:
    invalid = [
        scene.scene_number
        for scene in plan.scenes
        if (
            scene.media_mode != MediaMode.VIDEO
            or scene.preserve_source_frame
            or scene.source_slide_number is not None
            or any(beat.kind == VisualBeatKind.SOURCE_SLIDE for beat in scene.visual_beats)
        )
    ]
    if invalid:
        raise ValueError(
            "cinematic_story cannot contain source pages or static scenes; "
            f"invalid scenes: {invalid}"
        )


async def _compose_shot_clips(
    scene_number: int,
    shots: list[tuple[VisualArtifact, float]],
    output_path: Path,
) -> VisualArtifact:
    if not shots:
        raise ValueError(f"scene {scene_number} has no generated shots")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    input_args: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for index, (artifact, duration) in enumerate(shots):
        if not artifact.path.is_file() or artifact.path.stat().st_size == 0:
            raise ValueError(
                f"visual QA failed for scene {scene_number}, shot {artifact.shot_number}"
            )
        actual_duration = await media_duration(artifact.path)
        if actual_duration <= 0:
            raise ValueError(
                f"visual QA found an empty clip for scene {scene_number}, "
                f"shot {artifact.shot_number}"
            )
        input_args.extend(["-i", str(artifact.path)])
        filters.append(
            f"[{index}:v]scale=1920:1080:force_original_aspect_ratio=decrease,"
            "pad=1920:1080:(ow-iw)/2:(oh-ih)/2,"
            f"trim=duration={min(duration, actual_duration):.3f},"
            "setpts=PTS-STARTPTS,fps=30,setsar=1,format=yuv420p"
            f"[shot{index}]"
        )
        labels.append(f"[shot{index}]")
    filters.append(f"{''.join(labels)}concat=n={len(shots)}:v=1:a=0[outv]")
    await run_process(
        "ffmpeg",
        "-y",
        *input_args,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[outv]",
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-movflags",
        "+faststart",
        str(output_path),
    )
    return VisualArtifact(
        scene_number=scene_number,
        path=output_path,
        kind="video",
    )


class CreatePresentationVideo:
    """Two-phase use case: prepare reviewable images, then animate approved images."""

    def __init__(
        self,
        ingestor_factory: DocumentIngestorFactory,
        narrative_generator: NarrativeGenerator,
        visual_planner: VisualPlanner,
        visual_asset_generator: VisualAssetGenerator,
        video_clip_generator: VideoClipGenerator,
        speech_synthesizer: SpeechSynthesizer,
        avatar_renderer: AvatarRenderer,
        scene_renderer: SceneRenderer,
        video_assembler: VideoAssembler,
        reporter: JobReporter,
        work_root: Path,
        output_root: Path,
        max_parallel_scenes: int = 3,
        maximum_shot_seconds: float = 8,
        duration_tolerance_percent: float = 5,
        words_per_minute: int = 155,
    ) -> None:
        self._ingestor_factory = ingestor_factory
        self._narrative_generator = narrative_generator
        self._visual_planner = visual_planner
        self._visual_asset_generator = visual_asset_generator
        self._video_clip_generator = video_clip_generator
        self._speech_synthesizer = speech_synthesizer
        self._avatar_renderer = avatar_renderer
        self._scene_renderer = scene_renderer
        self._video_assembler = video_assembler
        self._reporter = reporter
        self._work_root = work_root
        self._output_root = output_root
        self._semaphore = asyncio.Semaphore(max_parallel_scenes)
        self._maximum_shot_seconds = maximum_shot_seconds
        self._duration_tolerance_percent = duration_tolerance_percent
        self._words_per_minute = words_per_minute

    async def restore(self, request: VideoJobRequest, job_id: str) -> PreparedVideoJob:
        """Rebuild a prepared job, generating only visual artifacts missing from its checkpoint."""
        work_dir = self._work_root / job_id
        output_dir = self._output_root / job_id
        script_path = output_dir / "script.json"
        visual_plan_path = output_dir / "visual-plan.json"
        if not request.source_path.is_file():
            raise FileNotFoundError(f"Source file not found for job {job_id}")
        if not script_path.is_file() or not visual_plan_path.is_file():
            raise FileNotFoundError(f"Job {job_id} has not completed visual preparation")

        document = await self._ingestor_factory.create(request.source_path).ingest(
            request.source_path, work_dir
        )
        script = PresentationScript.model_validate_json(script_path.read_text(encoding="utf-8"))
        visual_plan = PresentationVisualPlan.model_validate_json(
            visual_plan_path.read_text(encoding="utf-8")
        )
        _validate_sequence(visual_plan, script, document)
        visual_plan_path.write_text(
            visual_plan.model_dump_json(indent=2),
            encoding="utf-8",
        )
        slides = {slide.number: slide for slide in document.slides}
        scripts = {scene.scene_number: scene for scene in script.scenes}
        images: list[VisualArtifact] = []
        completed_images = 0
        shot_total = sum(max(len(plan.shots), 1) for plan in visual_plan.scenes)
        for scene_plan in visual_plan.scenes:
            for shot in _shots_or_default(scene_plan.shots):
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
                        image = await self._visual_asset_generator.generate(
                            plan,
                            source_slides,
                            work_dir / "images",
                        )
                        image_path = image.path
                        revision = image.revision
                    else:
                        image_path = candidates[-1]
                        match = image_path.stem.rsplit("-r", 1)
                        revision = int(match[1]) if len(match) == 2 and match[1].isdigit() else 1
                artifact = VisualArtifact(
                    scene_number=plan.scene_number,
                    shot_number=shot_number,
                    path=image_path,
                    kind="image",
                    revision=revision,
                )
                images.append(artifact)
                completed_images += 1
                await self._reporter.update(
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

    async def prepare(
        self,
        request: VideoJobRequest,
        job_id: str | None = None,
        duration_decision: str | None = None,
    ) -> PreparedVideoJob:
        job_id = job_id or uuid.uuid4().hex
        work_dir = self._work_root / job_id
        output_dir = self._output_root / job_id
        work_dir.mkdir(parents=True, exist_ok=True)
        output_dir.mkdir(parents=True, exist_ok=True)
        try:
            logger.info("job=%s prepare started source=%s", job_id, request.source_path)
            await self._reporter.update(job_id, JobStatus.INGESTING, "Lendo o documento")
            document = await self._ingestor_factory.create(request.source_path).ingest(
                request.source_path, work_dir
            )
            source_page_count = len(document.slides)

            script_path = output_dir / "script.json"
            if duration_decision and script_path.is_file():
                script = PresentationScript.model_validate_json(
                    script_path.read_text(encoding="utf-8")
                )
                if duration_decision == "summarize":
                    script = _compact_script(
                        script, request.target_seconds, self._words_per_minute
                    )
                elif duration_decision != "accept":
                    raise ValueError(f"unsupported duration decision {duration_decision!r}")
            else:
                await self._reporter.update(
                    job_id,
                    JobStatus.SCRIPTING,
                    f"Criando storytelling a partir de {source_page_count} páginas",
                )
                script = await self._narrative_generator.generate(
                    document=document,
                    target_seconds=request.target_seconds,
                    language=request.language,
                    audience=request.audience,
                    tone=(
                        request.tone
                        if request.production_mode == ProductionMode.HYBRID_PRESENTATION
                        else (
                            f"{request.tone}. Build a continuous cinematic story from beginning "
                            "to end, with a strong hook, escalating development, recurring visual "
                            "motifs, meaningful transitions, and a clear resolution."
                        )
                    ),
                )
                if request.production_mode == ProductionMode.CINEMATIC_STORY:
                    script = _cinematic_script(script)
            script_path.write_text(script.model_dump_json(indent=2), encoding="utf-8")
            await self._reporter.update(
                job_id,
                JobStatus.DURATION_VALIDATING,
                "Verificando a duração estimada da história",
            )
            estimated_seconds = math.ceil(_word_count(script) * 60 / self._words_per_minute)
            allowed_seconds = math.floor(
                request.target_seconds * (1 + self._duration_tolerance_percent / 100)
            )
            if duration_decision is None and estimated_seconds > allowed_seconds:
                await self._reporter.update(
                    job_id,
                    JobStatus.AWAITING_DURATION_APPROVAL,
                    f"requested={request.target_seconds} estimated={estimated_seconds} "
                    f"words={_word_count(script)}",
                )
                raise DurationReviewRequired(
                    request.target_seconds, estimated_seconds, _word_count(script)
                )
            if duration_decision == "accept":
                request = request.model_copy(update={"target_seconds": estimated_seconds})
                script = _retime_script(script, estimated_seconds)
                script_path.write_text(script.model_dump_json(indent=2), encoding="utf-8")
            total_scenes = len(script.scenes)
            aligned_audio_durations: dict[int, float] = {}
            aligned_audio: dict[int, AudioArtifact] = {}
            await self._reporter.update(
                job_id,
                JobStatus.SYNTHESIZING,
                f"completed=0 total={total_scenes} | alinhando narração antes das cenas",
            )
            for completed, scene in enumerate(script.scenes, start=1):
                audio_path = work_dir / "audio" / f"scene-{scene.scene_number:03d}.wav"
                duration = _valid_wav_duration(audio_path)
                if duration is None:
                    audio = await self._speech_synthesizer.synthesize(
                        scene.narration,
                        audio_path,
                        language=request.language,
                        style=request.tone,
                    )
                    duration = audio.duration_seconds
                else:
                    audio = AudioArtifact(path=audio_path, duration_seconds=duration)
                aligned_audio_durations[scene.scene_number] = duration
                aligned_audio[scene.scene_number] = audio
                await self._reporter.update(
                    job_id,
                    JobStatus.SYNTHESIZING,
                    f"completed={completed} total={total_scenes}"
                    f" | áudio da cena {scene.scene_number} alinhado",
                )
            logger.info(
                "job=%s script ready source_pages=%s narrative_scenes=%s requested_seconds=%s "
                "estimated_seconds=%s path=%s",
                job_id,
                source_page_count,
                total_scenes,
                request.target_seconds,
                script.total_estimated_seconds,
                script_path,
            )

            await self._reporter.update(
                job_id,
                JobStatus.SCENE_PLANNING,
                "Planejando cenas a partir da narração alinhada",
            )
            await self._reporter.update(
                job_id,
                JobStatus.VISUAL_PLANNING,
                "Criando o planejamento visual das cenas",
            )
            visual_plan = await self._visual_planner.plan(document, script)
            if request.production_mode == ProductionMode.CINEMATIC_STORY:
                visual_plan = _cinematic_visual_plan(visual_plan)
                _validate_cinematic_has_no_source_frames(visual_plan)
                scripts_by_number = {item.scene_number: item for item in script.scenes}
                compiled_scenes = []
                continuity = "open the film in the visual world defined by creative direction"
                for visual_scene in visual_plan.scenes:
                    if visual_scene.media_mode == MediaMode.STATIC:
                        compiled_scenes.append(visual_scene.model_copy(update={"shots": []}))
                        continue
                    shots = compile_shots(
                        visual_scene,
                        scripts_by_number[visual_scene.scene_number],
                        aligned_audio_durations[visual_scene.scene_number],
                        continuity_in=continuity,
                        maximum_shot_seconds=self._maximum_shot_seconds,
                    )
                    continuity = shots[-1].continuity_out
                    compiled_scenes.append(visual_scene.model_copy(update={"shots": shots}))
                visual_plan = visual_plan.model_copy(update={"scenes": compiled_scenes})
            await self._reporter.update(
                job_id,
                JobStatus.PROMPT_COMPILING,
                "Compilando prompts e continuidade dos takes",
            )
            await self._reporter.update(
                job_id,
                JobStatus.RULE_VALIDATING,
                "Validando duração, cobertura e regras de mídia",
            )
            _validate_sequence(visual_plan, script, document)
            visual_plan_path = output_dir / "visual-plan.json"
            visual_plan_path.write_text(visual_plan.model_dump_json(indent=2), encoding="utf-8")
            logger.info(
                "job=%s visual plan ready scenes=%s static_scenes=%s video_scenes=%s path=%s",
                job_id,
                len(visual_plan.scenes),
                sum(plan.media_mode == MediaMode.STATIC for plan in visual_plan.scenes),
                sum(plan.media_mode == MediaMode.VIDEO for plan in visual_plan.scenes),
                visual_plan_path,
            )
            plans = {item.scene_number: item for item in visual_plan.scenes}
            scripts = {item.scene_number: item for item in script.scenes}
            source_slides = {slide.number: slide for slide in document.slides}

            await self._reporter.update(
                job_id,
                JobStatus.GENERATING_IMAGES,
                f"completed=0 total={total_scenes} | iniciando geração das imagens",
            )
            completed_images = 0

            async def generate_image(scene_number: int, shot_number: int = 1) -> VisualArtifact:
                nonlocal completed_images
                async with self._semaphore:
                    scene_script = scripts[scene_number]
                    scene_sources = [
                        source_slides[number] for number in scene_script.source_slide_numbers
                    ]
                    logger.info(
                        "job=%s visual frame preparation started scene=%s media_mode=%s "
                        "source_pages=%s",
                        job_id,
                        scene_number,
                        plans[scene_number].media_mode.value,
                        scene_script.source_slide_numbers,
                    )
                    scene_plan = plans[scene_number]
                    plan = (
                        materialize_shot(
                            scene_plan,
                            scene_plan.shots[shot_number - 1],
                        )
                        if scene_plan.shots
                        else scene_plan
                    )
                    if plan.media_mode == MediaMode.STATIC and plan.preserve_source_frame:
                        selected_number = plan.source_slide_number or scene_sources[0].number
                        selected_slide = source_slides[selected_number]
                        image = VisualArtifact(
                            scene_number=scene_number,
                            shot_number=shot_number,
                            path=selected_slide.image_path,
                            kind="image",
                        )
                        logger.info(
                            "job=%s static source frame selected scene=%s source_page=%s path=%s",
                            job_id,
                            scene_number,
                            selected_number,
                            selected_slide.image_path,
                        )
                    else:
                        image = await self._visual_asset_generator.generate(
                            plan,
                            scene_sources,
                            work_dir / "images",
                        )
                    logger.info(
                        "job=%s image generation completed scene=%s shot=%s revision=%s "
                        "path=%s bytes=%s",
                        job_id,
                        scene_number,
                        shot_number,
                        image.revision,
                        image.path,
                        image.path.stat().st_size,
                    )
                    completed_images += 1
                    await self._reporter.update(
                        job_id,
                        JobStatus.GENERATING_IMAGES,
                        f"completed={completed_images} total={total_scenes}"
                        f" | frame da cena {scene_number}, take {shot_number} preparado",
                    )
                    return image

            image_tasks = [
                (scene.scene_number, shot.shot_number if shot else 1)
                for scene in visual_plan.scenes
                for shot in _shots_or_default(scene.shots)
            ]
            images = await asyncio.gather(
                *(
                    generate_image(scene_number, shot_number)
                    for scene_number, shot_number in image_tasks
                )
            )
            prepared = PreparedVideoJob(
                job_id=job_id,
                request=request,
                document=document,
                script=script,
                visual_plan=visual_plan,
                visual_images=images,
                aligned_audio=aligned_audio,
                work_dir=work_dir,
                output_dir=output_dir,
                script_path=script_path,
                visual_plan_path=visual_plan_path,
            )
            logger.info(
                "job=%s preparation completed images=%s awaiting_visual_approval=true",
                job_id,
                len(images),
            )
            return prepared
        except DurationReviewRequired:
            raise
        except Exception as exc:
            logger.exception("job=%s preparation failed", job_id)
            await self._reporter.update(job_id, JobStatus.FAILED, str(exc))
            raise

    async def regenerate_image(
        self,
        prepared: PreparedVideoJob,
        scene_number: int,
        shot_number: int = 1,
        prompt: str | None = None,
    ) -> VisualArtifact:
        slides = {slide.number: slide for slide in prepared.document.slides}
        scripts = {scene.scene_number: scene for scene in prepared.script.scenes}
        plans = {plan.scene_number: plan for plan in prepared.visual_plan.scenes}
        images = {
            (image.scene_number, image.shot_number): image for image in prepared.visual_images
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
            "job=%s image regeneration started scene=%s shot=%s revision=%s prompt_updated=%s "
            "prompt_characters=%s",
            prepared.job_id,
            scene_number,
            shot_number,
            revision,
            prompt is not None,
            len(generation_plan.prompt),
        )
        try:
            async with self._semaphore:
                replacement = await self._visual_asset_generator.generate(
                    generation_plan,
                    [slides[number] for number in scripts[scene_number].source_slide_numbers],
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
            replacement if (image.scene_number, image.shot_number) == image_key else image
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

    async def finalize(self, prepared: PreparedVideoJob) -> VideoJobResult:
        job_id = prepared.job_id
        if prepared.request.production_mode == ProductionMode.CINEMATIC_STORY:
            _validate_cinematic_has_no_source_frames(prepared.visual_plan)
        total_scenes = len(prepared.script.scenes)
        source_slides = {slide.number: slide for slide in prepared.document.slides}
        scripts = {item.scene_number: item for item in prepared.script.scenes}
        plans = {item.scene_number: item for item in prepared.visual_plan.scenes}
        for scene_number, plan in plans.items():
            if not plan.visual_beats and not plan.shots:
                plan.visual_beats = build_default_visual_beats(
                    scripts[scene_number].target_seconds,
                    is_video=plan.media_mode == MediaMode.VIDEO,
                    motion_preset=plan.motion_preset,
                    allow_source_slide=(
                        prepared.request.production_mode == ProductionMode.HYBRID_PRESENTATION
                    ),
                )
        images = {(item.scene_number, item.shot_number): item for item in prepared.visual_images}
        try:
            logger.info("job=%s finalization started approved_images=%s", job_id, len(images))
            completed_audio = 0

            async def synthesize(scene_number: int) -> tuple[int, AudioArtifact, Path | None]:
                nonlocal completed_audio
                async with self._semaphore:
                    logger.info("job=%s speech synthesis started scene=%s", job_id, scene_number)
                    audio_path = prepared.work_dir / "audio" / f"scene-{scene_number:03d}.wav"
                    cached_audio = prepared.aligned_audio.get(scene_number)
                    duration = _valid_wav_duration(audio_path)
                    if cached_audio is not None:
                        audio = cached_audio
                    elif duration is None:
                        audio = await self._speech_synthesizer.synthesize(
                            scripts[scene_number].narration,
                            audio_path,
                            language=prepared.request.language,
                            style=prepared.request.tone,
                        )
                    else:
                        audio = AudioArtifact(path=audio_path, duration_seconds=duration)
                        logger.info(
                            "job=%s speech synthesis reused scene=%s path=%s",
                            job_id,
                            scene_number,
                            audio_path,
                        )
                    presenter = await self._avatar_renderer.render(
                        prepared.request.avatar_reference,
                        audio,
                        prepared.work_dir / "avatars" / f"scene-{scene_number:03d}.mp4",
                    )
                    logger.info(
                        "job=%s speech synthesis completed scene=%s duration_seconds=%.2f path=%s",
                        job_id,
                        scene_number,
                        audio.duration_seconds,
                        audio.path,
                    )
                    completed_audio += 1
                    return scene_number, audio, presenter

            audio_by_scene = {
                number: (audio, presenter)
                for number, audio, presenter in await asyncio.gather(
                    *(synthesize(scene.scene_number) for scene in prepared.script.scenes)
                )
            }

            await self._reporter.update(
                job_id,
                JobStatus.GENERATING_VIDEO,
                f"completed=0 total={total_scenes} | iniciando animação das imagens",
            )
            completed_clips = 0

            shot_tasks = [
                (scene.scene_number, shot.shot_number if shot else 1)
                for scene in prepared.visual_plan.scenes
                for shot in _shots_or_default(scene.shots)
            ]
            total_shots = len(shot_tasks)

            async def animate(scene_number: int, shot_number: int) -> VisualArtifact:
                nonlocal completed_clips
                async with self._semaphore:
                    scene_plan = plans[scene_number]
                    shot = scene_plan.shots[shot_number - 1] if scene_plan.shots else None
                    plan = materialize_shot(scene_plan, shot) if shot else scene_plan
                    image = images[(scene_number, shot_number)]
                    if plan.media_mode == MediaMode.STATIC:
                        clip = image
                        logger.info(
                            "job=%s image-to-video skipped scene=%s shot=%s reason=static_image",
                            job_id,
                            scene_number,
                            shot_number,
                        )
                    else:
                        stem = f"scene-{scene_number:03d}"
                        if shot_number > 1:
                            stem += f"-shot-{shot_number:03d}"
                        cached_clip = prepared.work_dir / "clips" / f"{stem}.mp4"
                        source_image = image.path
                        cache_is_current = (
                            cached_clip.is_file()
                            and cached_clip.stat().st_size > 0
                            and cached_clip.stat().st_mtime >= source_image.stat().st_mtime
                        )
                        if cache_is_current:
                            clip = VisualArtifact(
                                scene_number=scene_number,
                                shot_number=shot_number,
                                path=cached_clip,
                                kind="video",
                                revision=image.revision,
                            )
                            logger.info(
                                "job=%s image-to-video reused scene=%s shot=%s path=%s bytes=%s",
                                job_id,
                                scene_number,
                                shot_number,
                                cached_clip,
                                cached_clip.stat().st_size,
                            )
                        else:
                            logger.info(
                                "job=%s image-to-video started scene=%s shot=%s",
                                job_id,
                                scene_number,
                                shot_number,
                            )
                            target_duration = (
                                shot.duration_seconds
                                if shot
                                else audio_by_scene[scene_number][0].duration_seconds
                            )
                            clip = await self._video_clip_generator.animate(
                                plan,
                                image,
                                prepared.work_dir / "clips",
                                duration_seconds=target_duration,
                            )
                            logger.info(
                                "job=%s image-to-video completed scene=%s shot=%s "
                                "target_duration_seconds=%.2f path=%s bytes=%s",
                                job_id,
                                scene_number,
                                shot_number,
                                target_duration,
                                clip.path,
                                clip.path.stat().st_size,
                            )
                    completed_clips += 1
                    await self._reporter.update(
                        job_id,
                        JobStatus.GENERATING_VIDEO,
                        f"completed={completed_clips} total={total_shots}"
                        + (
                            f" | imagem estática da cena {scene_number} preparada"
                            if plan.media_mode == MediaMode.STATIC
                            else (f" | vídeo da cena {scene_number}, take {shot_number} concluído")
                        ),
                    )
                    return clip

            clips = await asyncio.gather(
                *(animate(scene_number, shot_number) for scene_number, shot_number in shot_tasks)
            )
            clips_by_key = {(clip.scene_number, clip.shot_number): clip for clip in clips}
            clips_by_scene: dict[int, VisualArtifact] = {}
            for scene_number, plan in plans.items():
                if plan.shots:
                    clips_by_scene[scene_number] = await _compose_shot_clips(
                        scene_number,
                        [
                            (
                                clips_by_key[(scene_number, shot.shot_number)],
                                shot.duration_seconds,
                            )
                            for shot in plan.shots
                        ],
                        prepared.work_dir / "clips" / f"scene-{scene_number:03d}-sequence.mp4",
                    )
                else:
                    clips_by_scene[scene_number] = clips_by_key[(scene_number, 1)]

            await self._reporter.update(
                job_id,
                JobStatus.VISUAL_QA,
                f"Verificando {total_shots} take(s) antes da composição",
            )
            for clip in clips:
                if not clip.path.is_file() or clip.path.stat().st_size == 0:
                    raise ValueError(
                        f"visual QA found an empty artifact for scene {clip.scene_number}, "
                        f"shot {clip.shot_number}"
                    )

            await self._reporter.update(
                job_id,
                JobStatus.RENDERING,
                f"completed=0 total={total_scenes} | iniciando renderização das cenas",
            )
            completed_scenes = 0

            async def render(scene_number: int) -> SceneArtifact:
                nonlocal completed_scenes
                async with self._semaphore:
                    logger.info("job=%s scene rendering started scene=%s", job_id, scene_number)
                    await self._reporter.update(
                        job_id,
                        JobStatus.RENDERING,
                        f"completed={completed_scenes} total={total_scenes}"
                        f" | renderizando cena {scene_number}",
                    )
                    source_slide = source_slides[scripts[scene_number].source_slide_numbers[0]]
                    audio, presenter = audio_by_scene[scene_number]
                    render_plan = plans[scene_number]
                    if render_plan.shots:
                        render_plan = render_plan.model_copy(
                            update={
                                "visual_beats": [
                                    VisualBeat(
                                        beat_number=1,
                                        kind=VisualBeatKind.GENERATED_VIDEO,
                                        duration_seconds=audio.duration_seconds,
                                        motion_preset=MotionPreset.NONE,
                                    )
                                ],
                                "shots": [],
                            }
                        )
                    scene = await self._scene_renderer.render(
                        scene_number=scene_number,
                        source_slide=source_slide,
                        audio=audio,
                        output_path=(
                            prepared.work_dir / "scenes" / f"scene-{scene_number:03d}.mp4"
                        ),
                        presenter_video=presenter,
                        visual=clips_by_scene[scene_number],
                        visual_image=images[(scene_number, 1)],
                        visual_plan=render_plan,
                    )
                    logger.info(
                        "job=%s scene rendering completed scene=%s duration_seconds=%.2f path=%s",
                        job_id,
                        scene_number,
                        scene.duration_seconds,
                        scene.path,
                    )
                    completed_scenes += 1
                    await self._reporter.update(
                        job_id,
                        JobStatus.RENDERING,
                        f"completed={completed_scenes} total={total_scenes}"
                        f" | cena {scene_number} concluída",
                    )
                    return scene

            scenes = await asyncio.gather(
                *(render(scene.scene_number) for scene in prepared.script.scenes)
            )
            scenes.sort(key=lambda scene: scene.scene_number)
            await self._reporter.update(
                job_id,
                JobStatus.ASSEMBLING,
                f"Montando {total_scenes} cenas no vídeo final",
            )
            video_path = prepared.output_dir / "presentation.mp4"
            duration = await self._video_assembler.assemble(scenes, video_path)
            await self._reporter.update(
                job_id,
                JobStatus.CAPTIONING,
                "Gerando legendas WebVTT e SRT",
            )
            caption_cues = build_caption_cues(prepared.script, scenes)
            captions_vtt_path, captions_srt_path = write_caption_files(
                caption_cues,
                prepared.output_dir,
                prepared.request.language,
            )
            logger.info(
                "job=%s video assembled scenes=%s duration_seconds=%.2f path=%s",
                job_id,
                len(scenes),
                duration,
                video_path,
            )
            manifest = {
                "job_id": job_id,
                "source": str(prepared.request.source_path),
                "video": str(video_path),
                "duration_seconds": duration,
                "target_seconds": prepared.request.target_seconds,
                "language": prepared.request.language,
                "audience": prepared.request.audience,
                "tone": prepared.request.tone,
                "production_mode": prepared.request.production_mode.value,
                "approved_images": [
                    image.model_dump(mode="json") for image in prepared.visual_images
                ],
                "storyboard": [
                    plan.model_dump(mode="json") for plan in prepared.visual_plan.scenes
                ],
                "captions": {
                    "vtt": str(captions_vtt_path),
                    "srt": str(captions_srt_path),
                    "cue_count": len(caption_cues),
                },
                "scenes": [scene.model_dump(mode="json") for scene in scenes],
            }
            (prepared.output_dir / "manifest.json").write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            await self._reporter.update(job_id, JobStatus.COMPLETED)
            return VideoJobResult(
                job_id=job_id,
                video_path=video_path,
                script_path=prepared.script_path,
                visual_plan_path=prepared.visual_plan_path,
                duration_seconds=duration,
                captions_vtt_path=captions_vtt_path,
                captions_srt_path=captions_srt_path,
            )
        except Exception as exc:
            logger.exception("job=%s finalization failed", job_id)
            await self._reporter.update(job_id, JobStatus.FAILED, str(exc))
            raise

    async def execute(self, request: VideoJobRequest, job_id: str | None = None) -> VideoJobResult:
        """CLI convenience: prepare and immediately approve all first-generation images."""
        prepared = await self.prepare(request, job_id)
        return await self.finalize(prepared)


def _valid_wav_duration(path: Path) -> float | None:
    if not path.is_file() or path.stat().st_size <= 44:
        return None
    try:
        with wave.open(str(path), "rb") as audio:
            frame_rate = audio.getframerate()
            duration = audio.getnframes() / frame_rate if frame_rate else 0
    except (OSError, EOFError, wave.Error):
        return None
    return duration if duration > 0 else None
