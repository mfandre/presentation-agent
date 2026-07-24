from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path

from presentation_video.domain.models import (
    AudioArtifact,
    JobStatus,
    MediaMode,
    PreparedVideoJob,
    SceneArtifact,
    VideoJobRequest,
    VideoJobResult,
    VisualArtifact,
)
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

    async def prepare(
        self, request: VideoJobRequest, job_id: str | None = None
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
                tone=request.tone,
            )
            script_path = output_dir / "script.json"
            script_path.write_text(script.model_dump_json(indent=2), encoding="utf-8")
            total_scenes = len(script.scenes)
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
                JobStatus.VISUAL_PLANNING,
                "Criando o planejamento visual das cenas",
            )
            visual_plan = await self._visual_planner.plan(document, script)
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

            async def generate_image(scene_number: int) -> VisualArtifact:
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
                    plan = plans[scene_number]
                    if plan.media_mode == MediaMode.STATIC:
                        selected_number = plan.source_slide_number or scene_sources[0].number
                        selected_slide = source_slides[selected_number]
                        image = VisualArtifact(
                            scene_number=scene_number,
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
                        "job=%s image generation completed scene=%s revision=%s path=%s bytes=%s",
                        job_id,
                        scene_number,
                        image.revision,
                        image.path,
                        image.path.stat().st_size,
                    )
                    completed_images += 1
                    await self._reporter.update(
                        job_id,
                        JobStatus.GENERATING_IMAGES,
                        f"completed={completed_images} total={total_scenes}"
                        f" | frame da cena {scene_number} preparado",
                    )
                    return image

            images = await asyncio.gather(
                *(generate_image(scene.scene_number) for scene in script.scenes)
            )
            prepared = PreparedVideoJob(
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
            logger.info(
                "job=%s preparation completed images=%s awaiting_visual_approval=true",
                job_id,
                len(images),
            )
            return prepared
        except Exception as exc:
            logger.exception("job=%s preparation failed", job_id)
            await self._reporter.update(job_id, JobStatus.FAILED, str(exc))
            raise

    async def regenerate_image(
        self,
        prepared: PreparedVideoJob,
        scene_number: int,
        prompt: str | None = None,
    ) -> VisualArtifact:
        slides = {slide.number: slide for slide in prepared.document.slides}
        scripts = {scene.scene_number: scene for scene in prepared.script.scenes}
        plans = {plan.scene_number: plan for plan in prepared.visual_plan.scenes}
        images = {image.scene_number: image for image in prepared.visual_images}
        if scene_number not in scripts or scene_number not in plans or scene_number not in images:
            raise ValueError(f"Scene {scene_number} does not exist")
        if plans[scene_number].media_mode == MediaMode.STATIC:
            raise ValueError(
                "Static scenes preserve an original source page and cannot be regenerated "
                "from a prompt"
            )
        previous_prompt = plans[scene_number].prompt
        if prompt is not None:
            plans[scene_number].prompt = prompt.strip()
        revision = images[scene_number].revision + 1
        logger.info(
            "job=%s image regeneration started scene=%s revision=%s prompt_updated=%s "
            "prompt_characters=%s",
            prepared.job_id,
            scene_number,
            revision,
            prompt is not None,
            len(plans[scene_number].prompt),
        )
        try:
            async with self._semaphore:
                replacement = await self._visual_asset_generator.generate(
                    plans[scene_number],
                    [slides[number] for number in scripts[scene_number].source_slide_numbers],
                    prepared.work_dir / "images",
                    revision=revision,
                )
        except Exception:
            plans[scene_number].prompt = previous_prompt
            raise
        prepared.visual_plan_path.write_text(
            prepared.visual_plan.model_dump_json(indent=2),
            encoding="utf-8",
        )
        prepared.visual_images = [
            replacement if image.scene_number == scene_number else image
            for image in prepared.visual_images
        ]
        logger.info(
            "job=%s image regeneration completed scene=%s revision=%s path=%s",
            prepared.job_id,
            scene_number,
            revision,
            replacement.path,
        )
        return replacement

    async def finalize(self, prepared: PreparedVideoJob) -> VideoJobResult:
        job_id = prepared.job_id
        total_scenes = len(prepared.script.scenes)
        source_slides = {slide.number: slide for slide in prepared.document.slides}
        scripts = {item.scene_number: item for item in prepared.script.scenes}
        plans = {item.scene_number: item for item in prepared.visual_plan.scenes}
        images = {item.scene_number: item for item in prepared.visual_images}
        try:
            logger.info("job=%s finalization started approved_images=%s", job_id, len(images))
            await self._reporter.update(
                job_id,
                JobStatus.SYNTHESIZING,
                f"completed=0 total={total_scenes} | iniciando síntese de voz",
            )
            completed_audio = 0

            async def synthesize(scene_number: int) -> tuple[int, AudioArtifact, Path | None]:
                nonlocal completed_audio
                async with self._semaphore:
                    logger.info("job=%s speech synthesis started scene=%s", job_id, scene_number)
                    audio = await self._speech_synthesizer.synthesize(
                        scripts[scene_number].narration,
                        prepared.work_dir / "audio" / f"scene-{scene_number:03d}.wav",
                        language=prepared.request.language,
                        style=prepared.request.tone,
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
                    await self._reporter.update(
                        job_id,
                        JobStatus.SYNTHESIZING,
                        f"completed={completed_audio} total={total_scenes}"
                        f" | áudio da cena {scene_number} concluído",
                    )
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

            async def animate(scene_number: int) -> VisualArtifact:
                nonlocal completed_clips
                async with self._semaphore:
                    plan = plans[scene_number]
                    if plan.media_mode == MediaMode.STATIC:
                        clip = images[scene_number]
                        logger.info(
                            "job=%s image-to-video skipped scene=%s reason=static_source_frame",
                            job_id,
                            scene_number,
                        )
                    else:
                        logger.info("job=%s image-to-video started scene=%s", job_id, scene_number)
                        audio_duration = audio_by_scene[scene_number][0].duration_seconds
                        clip = await self._video_clip_generator.animate(
                            plan,
                            images[scene_number],
                            prepared.work_dir / "clips",
                            duration_seconds=audio_duration,
                        )
                        logger.info(
                            "job=%s image-to-video completed scene=%s "
                            "target_duration_seconds=%.2f path=%s bytes=%s",
                            job_id,
                            scene_number,
                            audio_duration,
                            clip.path,
                            clip.path.stat().st_size,
                        )
                    completed_clips += 1
                    await self._reporter.update(
                        job_id,
                        JobStatus.GENERATING_VIDEO,
                        f"completed={completed_clips} total={total_scenes}"
                        + (
                            f" | slide fixo da cena {scene_number} preservado"
                            if plan.media_mode == MediaMode.STATIC
                            else f" | vídeo da cena {scene_number} concluído"
                        ),
                    )
                    return clip

            clips = await asyncio.gather(
                *(animate(scene.scene_number) for scene in prepared.script.scenes)
            )
            clips_by_scene = {clip.scene_number: clip for clip in clips}

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
                    scene = await self._scene_renderer.render(
                        scene_number=scene_number,
                        source_slide=source_slide,
                        audio=audio,
                        output_path=(
                            prepared.work_dir / "scenes" / f"scene-{scene_number:03d}.mp4"
                        ),
                        presenter_video=presenter,
                        visual=clips_by_scene[scene_number],
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
                "approved_images": [
                    image.model_dump(mode="json") for image in prepared.visual_images
                ],
                "storyboard": [
                    plan.model_dump(mode="json") for plan in prepared.visual_plan.scenes
                ],
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
            )
        except Exception as exc:
            logger.exception("job=%s finalization failed", job_id)
            await self._reporter.update(job_id, JobStatus.FAILED, str(exc))
            raise

    async def execute(self, request: VideoJobRequest, job_id: str | None = None) -> VideoJobResult:
        """CLI convenience: prepare and immediately approve all first-generation images."""
        prepared = await self.prepare(request, job_id)
        return await self.finalize(prepared)
