from __future__ import annotations

import asyncio
import logging
import math
import uuid
from pathlib import Path

from presentation_video.domain.models import (
    AudioArtifact,
    JobStatus,
    MediaMode,
    MotionPreset,
    PresentationScript,
    ProductionMode,
    PreparedVideoJob,
    SceneArtifact,
    VideoJobRequest,
    VideoJobResult,
    VisualArtifact,
    VisualBeat,
    VisualBeatKind,
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
from presentation_video.application.brand import apply_brand_kit
from presentation_video.application.captions import build_caption_cues, write_caption_files
from presentation_video.application.audio_cache import valid_wav_duration as _valid_wav_duration
from presentation_video.application.manifest import write_job_manifest
from presentation_video.application.production_policy import (
    enforce_cinematic_script as _cinematic_script,  # noqa: F401 - compatibility export
    enforce_cinematic_visual_plan as _cinematic_visual_plan,  # noqa: F401
    shots_or_default as _shots_or_default,
    validate_cinematic_has_no_source_frames as _validate_cinematic_has_no_source_frames,  # noqa: F401
)
from presentation_video.application.production_presets import (
    direct_narrative_tone,
    transform_script,
    transform_visual_plan,
    validate_preset_plan,
)
from presentation_video.application.script_policy import (
    compact_script as _compact_script,
    retime_script as _retime_script,
    word_count as _word_count,
)
from presentation_video.application.visual_checkpoints import (
    generate_from_source_slide,
    regenerate_visual,
    regenerate_whiteboard_scene,
    restore_prepared_job,
    select_source_slide,
)
from presentation_video.application.whiteboard import compile_whiteboard_shots
from presentation_video.application.whiteboard_states import (
    build_progressive_whiteboard_states,
)
from presentation_video.infrastructure.video_sequence import (
    compose_shot_clips as _compose_shot_clips,
)
from presentation_video.infrastructure.visual_planning import validate_sequence

logger = logging.getLogger(__name__)

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
        max_parallel_images: int | None = None,
        max_parallel_speech: int | None = None,
        max_parallel_animations: int | None = None,
        max_parallel_renders: int | None = None,
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
        # max_parallel_scenes remains as a backwards-compatible fallback for callers
        # that have not adopted per-stage limits yet.
        self._image_semaphore = asyncio.Semaphore(
            max_parallel_images or max_parallel_scenes
        )
        self._speech_semaphore = asyncio.Semaphore(
            max_parallel_speech or max_parallel_scenes
        )
        self._animation_semaphore = asyncio.Semaphore(
            max_parallel_animations or max_parallel_scenes
        )
        self._render_semaphore = asyncio.Semaphore(
            max_parallel_renders or max_parallel_scenes
        )
        self._maximum_shot_seconds = maximum_shot_seconds
        self._duration_tolerance_percent = duration_tolerance_percent
        self._words_per_minute = words_per_minute

    async def restore(self, request: VideoJobRequest, job_id: str) -> PreparedVideoJob:
        return await restore_prepared_job(
            request,
            job_id,
            work_root=self._work_root,
            output_root=self._output_root,
            ingestor_factory=self._ingestor_factory,
            visual_asset_generator=self._visual_asset_generator,
            reporter=self._reporter,
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
                    tone=direct_narrative_tone(request.production_mode, request.tone),
                )
                script = transform_script(request.production_mode, script)
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
            # Normalize the provider plan against the narrative before applying a production
            # preset. validate_sequence intentionally restores media choices from the script;
            # running it after a preset would undo whiteboard/cinematic transformations.
            validate_sequence(visual_plan, script, document)
            visual_plan = transform_visual_plan(
                request.production_mode,
                visual_plan,
                request.preset_options,
                script,
            )
            visual_plan = apply_brand_kit(visual_plan, request.brand_kit)
            visual_plan = visual_plan.model_copy(
                update={
                    "scenes": [
                        scene.model_copy(update={"content_language": request.language})
                        for scene in visual_plan.scenes
                    ]
                }
            )
            if request.production_mode in {
                ProductionMode.CINEMATIC_STORY,
                ProductionMode.CORPORATE_TRAINING,
            }:
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
            elif request.production_mode == ProductionMode.WHITEBOARD_EXPLAINER:
                scripts_by_number = {item.scene_number: item for item in script.scenes}
                compiled_scenes = []
                continuity = "an empty pure-white board"
                for visual_scene in visual_plan.scenes:
                    shots = compile_whiteboard_shots(
                        visual_scene,
                        scripts_by_number[visual_scene.scene_number],
                        aligned_audio_durations[visual_scene.scene_number],
                        continuity_in=continuity,
                        maximum_shot_seconds=self._maximum_shot_seconds,
                    )
                    continuity = shots[-1].continuity_out
                    compiled_scenes.append(visual_scene.model_copy(update={"shots": shots}))
                visual_plan = visual_plan.model_copy(update={"scenes": compiled_scenes})
            validate_preset_plan(request.production_mode, visual_plan)
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
                async with self._image_semaphore:
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
                            source_slide_number=selected_number,
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
            if request.production_mode == ProductionMode.WHITEBOARD_EXPLAINER:
                async def generate_whiteboard_scene(
                    scene_number: int,
                ) -> list[VisualArtifact]:
                    nonlocal completed_images
                    async with self._image_semaphore:
                        scene_plan = plans[scene_number]
                        scene_script = scripts[scene_number]
                        scene_sources = [
                            source_slides[number]
                            for number in scene_script.source_slide_numbers
                        ]
                        master_plan = scene_plan.model_copy(
                            update={"shots": [], "shot_number": 1}
                        )
                        await self._reporter.update(
                            job_id,
                            JobStatus.GENERATING_IMAGES,
                            f"whiteboard_master completed={completed_images} "
                            f"total={len(image_tasks)} | gerando quadro mestre da cena "
                            f"{scene_number}",
                        )
                        master = await self._visual_asset_generator.generate(
                            master_plan,
                            scene_sources,
                            work_dir / "images",
                        )
                        states = await asyncio.to_thread(
                            build_progressive_whiteboard_states,
                            master,
                            len(scene_plan.shots),
                            work_dir / "images",
                        )
                        completed_images += len(states)
                        await self._reporter.update(
                            job_id,
                            JobStatus.GENERATING_IMAGES,
                            f"whiteboard_states completed={completed_images} "
                            f"total={len(image_tasks)}"
                            f" | quadro mestre e {len(states)} estados progressivos "
                            f"da cena {scene_number} preparados",
                        )
                        return states

                state_groups = await asyncio.gather(
                    *(
                        generate_whiteboard_scene(scene.scene_number)
                        for scene in visual_plan.scenes
                    )
                )
                images = [image for group in state_groups for image in group]
            else:
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
        if prepared.request.production_mode == ProductionMode.WHITEBOARD_EXPLAINER:
            return await regenerate_whiteboard_scene(
                prepared,
                scene_number,
                shot_number,
                prompt,
                visual_asset_generator=self._visual_asset_generator,
                semaphore=self._image_semaphore,
            )
        return await regenerate_visual(
            prepared,
            scene_number,
            shot_number,
            prompt,
            visual_asset_generator=self._visual_asset_generator,
            semaphore=self._image_semaphore,
        )

    def use_source_slide(
        self,
        prepared: PreparedVideoJob,
        scene_number: int,
        shot_number: int,
        source_slide_number: int,
    ) -> VisualArtifact:
        return select_source_slide(
            prepared, scene_number, shot_number, source_slide_number
        )

    async def generate_image_from_slide(
        self,
        prepared: PreparedVideoJob,
        scene_number: int,
        shot_number: int,
        source_slide_number: int,
        prompt: str | None = None,
    ) -> VisualArtifact:
        return await generate_from_source_slide(
            prepared,
            scene_number,
            shot_number,
            source_slide_number,
            prompt,
            visual_asset_generator=self._visual_asset_generator,
            semaphore=self._image_semaphore,
        )

    async def finalize(self, prepared: PreparedVideoJob) -> VideoJobResult:
        job_id = prepared.job_id
        validate_preset_plan(prepared.request.production_mode, prepared.visual_plan)
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
                        plan.preserve_source_frame
                        or (
                            prepared.request.production_mode
                            == ProductionMode.HYBRID_PRESENTATION
                            and plan.media_mode == MediaMode.VIDEO
                        )
                    ),
                )
        images = {(item.scene_number, item.shot_number): item for item in prepared.visual_images}
        try:
            logger.info("job=%s finalization started approved_images=%s", job_id, len(images))
            completed_audio = 0

            async def synthesize(scene_number: int) -> tuple[int, AudioArtifact, Path | None]:
                nonlocal completed_audio
                async with self._speech_semaphore:
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
                async with self._animation_semaphore:
                    scene_plan = plans[scene_number]
                    shot = scene_plan.shots[shot_number - 1] if scene_plan.shots else None
                    plan = materialize_shot(scene_plan, shot) if shot else scene_plan
                    image = images[(scene_number, shot_number)]
                    if plan.media_mode == MediaMode.STATIC or image.source_slide_number is not None:
                        clip = image
                        logger.info(
                            "job=%s image-to-video skipped scene=%s shot=%s reason=static_or_source_image",
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
                async with self._render_semaphore:
                    logger.info("job=%s scene rendering started scene=%s", job_id, scene_number)
                    await self._reporter.update(
                        job_id,
                        JobStatus.RENDERING,
                        f"completed={completed_scenes} total={total_scenes}"
                        f" | renderizando cena {scene_number}",
                    )
                    selected_source_number = (
                        plans[scene_number].source_slide_number
                        or scripts[scene_number].source_slide_numbers[0]
                    )
                    source_slide = source_slides[selected_source_number]
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
            write_job_manifest(
                prepared,
                video_path=video_path,
                duration_seconds=duration,
                captions_vtt_path=captions_vtt_path,
                captions_srt_path=captions_srt_path,
                caption_cue_count=len(caption_cues),
                scenes=scenes,
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
