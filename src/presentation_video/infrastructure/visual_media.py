from __future__ import annotations

import base64
import logging
import math
import mimetypes
from pathlib import Path

from presentation_video.domain.models import (
    MediaMode,
    SlideContent,
    VisualArtifact,
    VisualGenerationPurpose,
    VisualScenePlan,
    VideoGeneratorCapabilities,
)
from presentation_video.infrastructure.process import run_process
from presentation_video.infrastructure.replicate import ReplicatePredictionClient
from presentation_video.infrastructure.concept_grounding import (
    default_concept_visualization,
    infer_required_concepts,
)
from presentation_video.infrastructure.video_capabilities import (
    video_model_capabilities,
    video_model_last_frame_input_key,
    video_model_output_duration,
)

logger = logging.getLogger(__name__)


def _visible_language_guard(plan: VisualScenePlan) -> str:
    language = plan.content_language.strip() or "und"
    return (
        f"The requested visible-content language is {language}. The production instructions may "
        "be written in English, but English is not visible content. This scene must be completely "
        "text-free: render no word, letter, digit, subtitle, label, sign, or pseudo-text in any "
        f"language. If an unavoidable textual mark appears despite this rule, it must be correctly "
        f"spelled in {language}; never default to English. "
    )


def _artifact_stem(scene_number: int, shot_number: int) -> str:
    base = f"scene-{scene_number:03d}"
    return base if shot_number == 1 else f"{base}-shot-{shot_number:03d}"


_SAFE_VISUAL_POLICY = (
    "Safety and fidelity constraints: depict routine, calm, lawful professional activity only. "
    "Use anonymous adults with faces de-emphasized and correct PPE when the source supports an "
    "industrial setting. No public figures, recognizable real people, politics, protests, crowds, "
    "conflict, weapons, military content, fire, smoke, explosions, injuries, emergencies, dangerous "
    "acts, medical procedures, minors, nudity, sexual content, drugs, or hateful symbols. "
)


def _visual_prompt(plan: VisualScenePlan, source_slides: list[SlideContent] | None = None) -> str:
    if plan.generation_purpose == VisualGenerationPurpose.CHARACTER_REFERENCE:
        return (
            f"{plan.prompt} "
            "This is a fictional production-design reference for benign visual storytelling. "
            "Keep the four views consistent and isolated on the requested neutral background. "
            "Do not include typography, labels, logos, watermarks, props, additional people, "
            "weapons, injuries, nudity, hateful symbols, or unsafe activity."
        )
    if plan.generation_purpose == VisualGenerationPurpose.STORYBOARD:
        return (
            f"{plan.prompt} "
            "This is a benign pre-production storyboard. Preserve causal continuity and canonical "
            "character identities across every cell. Do not include typography, captions, labels, "
            "logos, watermarks, graphic injury, weapons, nudity, hateful symbols, or unsafe acts."
        )
    source_context = ""
    grounded_text = ""
    if source_slides:
        grounded_pages = " ".join(
            f"Page {slide.number} — {slide.title}: {slide.body_text} {slide.speaker_notes}"
            for slide in source_slides
        )
        grounded_text = " ".join(grounded_pages.split())[:4_000]
        source_context = (
            f" Source pages: {plan.source_slide_numbers}. "
            f"Facts and concepts from those pages that must ground the visual: "
            f"{grounded_text or 'none'}."
        )
    concepts = plan.must_show_concepts or infer_required_concepts(
        f"{plan.prompt} {plan.focal_action} {grounded_text}"
    )
    concept_visualization = plan.concept_visualization or default_concept_visualization(concepts)
    concept_contract = ""
    if concepts:
        concept_contract = (
            f" Required concepts that must be visually unmistakable: {', '.join(concepts)}. "
            f"Rendering contract: {concept_visualization}. Do not substitute these concepts with "
            "generic teamwork, a person inspecting equipment, or decorative technology imagery. "
        )
    narrative_contract = (
        f"Scene purpose: {plan.scene_purpose}. "
        f"Relationship to the presentation thesis: {plan.relationship_to_thesis}. "
        f"Narrative progress: {plan.narrative_progress}. "
    )
    if plan.visible_evidence:
        narrative_contract += (
            f"Concrete visible evidence required: {'; '.join(plan.visible_evidence)}. "
        )
    if plan.forbidden_substitutions:
        narrative_contract += f"Do not substitute with: {'; '.join(plan.forbidden_substitutions)}. "
    if "whiteboard animation" in plan.visual_style.lower():
        return (
            "Create the clean final frame of an educational whiteboard animation. "
            f"{_visible_language_guard(plan)}"
            f"Scene request: {plan.prompt}. {concept_contract}{narrative_contract}"
            f"Drawing action: {plan.focal_action}. Entrance: {plan.entrance_motion}. "
            f"Exit continuity: {plan.transition_out}. Style: {plan.visual_style}.{source_context} "
            "Keep a pure white background and crisp black marker line art. Use simple doodles, "
            "icons, arrows, comparisons, timelines, charts, and explanatory diagrams only when "
            "they directly teach the cited source concepts. Use one coherent composition with "
            "ample whitespace. The final image must look hand-sketched, not photographic. "
            "Strictly forbid handwriting and all text-like marks: no words, letters, digits, "
            "alphabetic characters, typography, glyphs, labels, titles, captions, legends, axis "
            "labels, annotations, logos, or watermarks. Charts and diagrams must be completely "
            "unlabeled. Express meaning only through recognizable symbols, shapes, scale, grouping, "
            "sequence, arrows, and spatial relationships. "
            f"{_SAFE_VISUAL_POLICY}Also exclude: {plan.negative_prompt}."
        )
    return (
        "Create a grounded, plausible real-world image that can become a short moving shot. "
        f"{_visible_language_guard(plan)}"
        "This is one precise documentary beat, not a montage or a summary of the narration. "
        "Use exactly one location, one primary source-grounded subject, and one observable action. "
        "Do not combine multiple moments, departments, environments, or time periods. "
        "Show concrete physical action, environment, equipment, materials, or human behavior "
        "directly supported by the source. The image must communicate through subjects, action, "
        "composition, color, and lighting alone. "
        f"Every visible element must help teach a source concept. Scene request: {plan.prompt}. "
        f"{concept_contract}{narrative_contract}"
        f"Editorial focal action: {plan.focal_action}. Entrance: {plan.entrance_motion}. "
        f"Exit continuity: {plan.transition_out}. "
        f"Style: {plan.visual_style}.{source_context} "
        "Treat the cited source as a strict whitelist: do not invent a plant, laboratory, control "
        "room, machine, geological sample, uniform, executive, device, or workflow unless that "
        "specific element is supported by the cited pages or scene request. "
        "Prefer a close, evidence-rich view of the actual artifact, material, hands, or routine "
        "task over a generic team, meeting, leadership pose, or symbolic decision gesture. "
        f"{_SAFE_VISUAL_POLICY}"
        "Do not translate abstract concepts into decorative physical metaphors. "
        "Never create an isometric view, 3D diorama, miniature, clay render, toy model, model city, "
        "symbolic factory, bridge, conveyor belt, generic floating icon grid, or glossy infographic unless "
        "that physical object is literally present in the source. Avoid generic corporate scenes. "
        "Hard text-free constraint: show no words, letters, numbers, typography, captions, logos, "
        "signage, documents, presentation pages, fake interface copy, gauges with numbers, or any "
        "other readable element. A clean non-readable software workflow is allowed only to express "
        "the required AI, agent, orchestration, data-flow, or governance concepts. Never depict AI "
        "as a robot, humanoid, glowing brain, hologram, magic orb, or generic neon network. "
        f"Also exclude: {plan.negative_prompt}."
    )


class SlideVisualAssetGenerator:
    """Development fallback: the source page is the intermediate image."""

    async def generate(
        self,
        plan: VisualScenePlan,
        source_slides: list[SlideContent],
        output_dir: Path,
        revision: int = 1,
    ) -> VisualArtifact:
        source_slide = source_slides[0]
        return VisualArtifact(
            scene_number=plan.scene_number,
            shot_number=plan.shot_number,
            path=source_slide.image_path,
            kind="image",
            revision=revision,
        )


class ReplicateImageAssetGenerator:
    def __init__(
        self,
        client: ReplicatePredictionClient,
        model: str,
        input_defaults: dict[str, object] | None = None,
        reference_input_key: str | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._input_defaults = input_defaults or {}
        self._reference_input_key = reference_input_key

    async def generate(
        self,
        plan: VisualScenePlan,
        source_slides: list[SlideContent],
        output_dir: Path,
        revision: int = 1,
    ) -> VisualArtifact:
        if plan.media_mode == MediaMode.STATIC and plan.preserve_source_frame:
            raise ValueError("Preserved static scenes must use an unchanged source page")
        inputs: dict[str, object] = {
            **self._input_defaults,
            "prompt": _visual_prompt(plan, source_slides),
        }
        if plan.generation_purpose in {
            VisualGenerationPurpose.CHARACTER_REFERENCE,
            VisualGenerationPurpose.STORYBOARD,
        }:
            # A square grid only preserves 16:9 inside every cell when the complete sheet is 16:9.
            inputs["aspect_ratio"] = "16:9"
        if self._reference_input_key:
            reference_images = [
                slide.image_path
                for slide in source_slides
                if slide.title.startswith("Character identity reference")
                and slide.image_path.is_file()
            ]
            if reference_images:
                inputs[self._reference_input_key] = [
                    _image_data_url(path) for path in reference_images
                ]
        output = await self._client.run(self._model, inputs)
        url = self._client.output_url(output)
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".webp"
        destination = output_dir / (
            f"{_artifact_stem(plan.scene_number, plan.shot_number)}-r{revision}{suffix}"
        )
        await self._client.download(url, destination)
        if not destination.exists() or destination.stat().st_size == 0:
            raise RuntimeError(f"Replicate returned an empty image for scene {plan.scene_number}")
        return VisualArtifact(
            scene_number=plan.scene_number,
            shot_number=plan.shot_number,
            path=destination,
            kind="image",
            revision=revision,
        )


def _image_data_url(path: Path) -> str:
    mime_type = mimetypes.guess_type(path.name)[0] or "image/png"
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


class ReplicateVideoAssetGenerator:
    """Animates an approved image using an image-to-video model on Replicate."""

    def __init__(
        self,
        client: ReplicatePredictionClient,
        model: str,
        image_input_key: str = "image",
        input_defaults: dict[str, object] | None = None,
    ) -> None:
        self._client = client
        self._model = model
        self._image_input_key = image_input_key
        self._input_defaults = _sanitize_video_inputs(model, input_defaults or {})

    @property
    def capabilities(self) -> VideoGeneratorCapabilities:
        return video_model_capabilities(self._model)

    async def animate(
        self,
        plan: VisualScenePlan,
        image: VisualArtifact,
        output_dir: Path,
        duration_seconds: float,
    ) -> VisualArtifact:
        if plan.media_mode != MediaMode.VIDEO:
            raise ValueError("Static scenes must bypass image-to-video")
        capabilities = self.capabilities
        first_frame_path = image.start_path or image.path
        if not first_frame_path.is_file() or first_frame_path.stat().st_size == 0:
            raise ValueError(f"First frame does not exist or is empty: {first_frame_path}")
        input_path = first_frame_path
        target_dimensions = {
            "16:9": (1280, 720),
            "9:16": (720, 1280),
            "1:1": (1024, 1024),
        }.get(str(self._input_defaults.get("aspect_ratio", "")))
        if target_dimensions is not None or input_path.stat().st_size > 900_000:
            compressed = output_dir / (
                f"{_artifact_stem(image.scene_number, image.shot_number)}-input.jpg"
            )
            compressed.parent.mkdir(parents=True, exist_ok=True)
            if target_dimensions is not None:
                width, height = target_dimensions
                video_filter = (
                    f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                    f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
                )
            else:
                video_filter = "scale='min(1280,iw)':-2"
            await run_process(
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-vf",
                video_filter,
                "-q:v",
                "5",
                str(compressed),
            )
            input_path = compressed
        mime_type = mimetypes.guess_type(input_path.name)[0] or "image/png"
        encoded = base64.b64encode(input_path.read_bytes()).decode("ascii")
        data_url = f"data:{mime_type};base64,{encoded}"
        last_frame_input_key = video_model_last_frame_input_key(self._model)
        has_last_frame = (
            image.start_path is not None
            and capabilities.supports_last_frame
            and last_frame_input_key is not None
        )
        frame_contract = (
            "The first supplied image is the exact opening frame and the last supplied image is "
            "the exact ending frame. Create a natural continuous transition between them without "
            "restarting or completing the ending action early. "
            if has_last_frame
            else ""
        )
        inputs = {
            **self._input_defaults,
            "prompt": (
                f"{frame_contract}"
                f"{plan.camera_motion}. Animate the approved input image faithfully at a natural, "
                "normal speed. Preserve the same location, subjects, clothing, equipment, layout, "
                "lighting, palette, and action visible in the input image. Do not introduce any new "
                "person, face, object, machine, room, landscape, sign, event, or storyline. "
                "Use one continuous restrained camera move and one subtle source-grounded action. "
                "Do not slow the action to fill the narration duration; the application will "
                "use the clip once inside a longer visual timeline. "
                f"{_SAFE_VISUAL_POLICY}"
                f"{_visual_prompt(plan)}"
            ),
            self._image_input_key: data_url,
        }
        requested_duration = video_model_output_duration(self._model, duration_seconds)
        if requested_duration is not None:
            inputs["duration"] = requested_duration
        if has_last_frame:
            assert last_frame_input_key is not None
            inputs[last_frame_input_key] = _image_data_url(image.path)
        try:
            output = await self._client.run(self._model, inputs)
        except RuntimeError as exc:
            if not _is_sensitive_generation_error(exc):
                raise
            logger.warning(
                "replicate video blocked by content safety; using local motion fallback "
                "model=%s scene=%s",
                self._model,
                image.scene_number,
            )
            return await FfmpegImageAnimator().animate(
                plan,
                image.model_copy(update={"path": first_frame_path, "start_path": None}),
                output_dir,
                duration_seconds,
            )
        url = self._client.output_url(output)
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        if suffix not in {".mp4", ".webm", ".mov"}:
            suffix = ".mp4"
        destination = output_dir / (
            f"{_artifact_stem(image.scene_number, image.shot_number)}{suffix}"
        )
        await self._client.download(url, destination)
        if not destination.exists() or destination.stat().st_size == 0:
            raise RuntimeError(f"Replicate returned an empty video for scene {image.scene_number}")
        return VisualArtifact(
            scene_number=image.scene_number,
            shot_number=image.shot_number,
            path=destination,
            kind="video",
            revision=image.revision,
        )

    async def animate_storyboard(
        self,
        plans: list[VisualScenePlan],
        storyboard: VisualArtifact,
        output_dir: Path,
        duration_seconds: float,
        segment_number: int,
    ) -> VisualArtifact:
        capabilities = self.capabilities
        if not capabilities.supports_storyboard_reference or not capabilities.supports_multishot:
            raise ValueError(f"Model {self._model!r} does not support storyboard multi-shot input")
        if not plans or any(plan.media_mode != MediaMode.VIDEO for plan in plans):
            raise ValueError("Storyboard animation requires one or more dynamic shot plans")
        if not storyboard.path.is_file() or storyboard.path.stat().st_size == 0:
            raise ValueError(f"Storyboard does not exist or is empty: {storyboard.path}")
        if duration_seconds > capabilities.maximum_output_seconds + 0.001:
            raise ValueError(
                f"Storyboard segment duration {duration_seconds:.2f}s exceeds the model maximum "
                f"of {capabilities.maximum_output_seconds:.2f}s"
            )

        inputs = {
            key: value
            for key, value in self._input_defaults.items()
            if key not in {self._image_input_key, "image", "last_frame", "last_frame_image"}
        }
        inputs.update(
            {
                "prompt": _replicate_storyboard_prompt(plans),
                "reference_images": [_image_data_url(storyboard.path)],
                "duration": min(
                    max(
                        math.ceil(duration_seconds),
                        math.ceil(capabilities.minimum_output_seconds),
                    ),
                    math.floor(capabilities.maximum_output_seconds),
                ),
                "generate_audio": False,
            }
        )
        output = await self._client.run(self._model, inputs)
        url = self._client.output_url(output)
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        if suffix not in {".mp4", ".webm", ".mov"}:
            suffix = ".mp4"
        destination = output_dir / (
            f"scene-{storyboard.scene_number:03d}-storyboard-segment-"
            f"{segment_number:03d}{suffix}"
        )
        await self._client.download(url, destination)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError(
                f"Replicate returned an empty storyboard video for scene "
                f"{storyboard.scene_number}"
            )
        return VisualArtifact(
            scene_number=storyboard.scene_number,
            shot_number=storyboard.shot_number,
            path=destination,
            kind="video",
            revision=storyboard.revision,
        )


def _replicate_storyboard_prompt(
    plans: list[VisualScenePlan],
) -> str:
    has_dialogue = any("DIALOGUE PERFORMANCE:" in plan.prompt for plan in plans)
    directions = []
    for index, plan in enumerate(plans, start=1):
        directions.append(
            f"Shot {index}: {plan.focal_action}; camera {plan.camera_motion}; "
            f"enter from {plan.entrance_motion}; end in {plan.transition_out}; "
            f"locked visual style={plan.visual_style}."
        )
    return (
        "[Image1] is a clean storyboard grid, read left-to-right and top-to-bottom. Animate it as "
        "one continuous cinematic multi-shot sequence. Each storyboard cell is a successive shot, "
        "never a collage in the output. Hide all grid borders. Use motivated cuts and continuous "
        "causal action. Never restart, repeat, reverse, or prematurely complete an action. Preserve "
        "environment, props, screen direction, lighting, palette, rendering medium, art style, "
        "spatial state, and every "
        "character identity already established inside the storyboard between shots. Use natural "
        "normal-speed motion. "
        "No slow motion or generated audio. "
        + (
            "Perform the explicitly scripted dialogue through natural speaker mouth movement, "
            "body language, and listener reactions; do not invent additional lines. "
            if has_dialogue
            else "Do not make characters appear to speak or hold a conversation. "
        )
        + "No text, captions, titles, labels, logos, interfaces, or "
        f"watermarks. {' '.join(directions)}"
    )[:4_000]


def _sanitize_video_inputs(
    model: str,
    inputs: dict[str, object],
) -> dict[str, object]:
    model_name = model.partition(":")[0]
    if model_name != "google/veo-3.1-lite":
        return dict(inputs)
    supported = {
        "prompt",
        "image",
        "last_frame",
        "aspect_ratio",
        "duration",
        "resolution",
        "seed",
    }
    removed = sorted(set(inputs) - supported)
    if removed:
        logger.warning(
            "replicate video inputs removed unsupported fields model=%s fields=%s",
            model_name,
            removed,
        )
    return {key: value for key, value in inputs.items() if key in supported}


def _is_sensitive_generation_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "flagged as sensitive" in message
        or "content safety" in message
        or "nsfw" in message
        or "(e005)" in message
    )


class FfmpegImageAnimator:
    """Local fallback that turns the approved image into a short Ken Burns clip."""

    async def animate(
        self,
        plan: VisualScenePlan,
        image: VisualArtifact,
        output_dir: Path,
        duration_seconds: float,
    ) -> VisualArtifact:
        if plan.media_mode != MediaMode.VIDEO:
            raise ValueError("Static scenes must bypass local image animation")
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / (f"{_artifact_stem(image.scene_number, image.shot_number)}.mp4")
        cycle_duration = min(max(duration_seconds, 5.0), 8.0)
        frame_count = max(round(cycle_duration * 30), 1)
        motion_filter = _local_motion_filter(plan.motion_preset.value, frame_count)
        await run_process(
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image.path),
            "-vf",
            f"scale=2048:1152:force_original_aspect_ratio=increase,crop=2048:1152,"
            f"{motion_filter}:d=1:s=1920x1080:fps=30,format=yuv420p",
            "-t",
            f"{cycle_duration:.3f}",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "medium",
            str(destination),
        )
        return VisualArtifact(
            scene_number=image.scene_number,
            shot_number=image.shot_number,
            path=destination,
            kind="video",
            revision=image.revision,
        )


def _local_motion_filter(preset: str, frame_count: int) -> str:
    progress = f"on/{max(frame_count - 1, 1)}"
    filters = {
        "pull_back": (
            "zoompan=z='max(1.0,1.08-0.08*"
            + progress
            + ")':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
        ),
        "pan_left": ("zoompan=z='1.06':x='(iw-iw/zoom)*(1-" + progress + ")':y='ih/2-(ih/zoom/2)'"),
        "pan_right": ("zoompan=z='1.06':x='(iw-iw/zoom)*" + progress + "':y='ih/2-(ih/zoom/2)'"),
        "drift_up": ("zoompan=z='1.05':x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*(1-" + progress + ")'"),
        "none": "zoompan=z='1.0':x='0':y='0'",
    }
    return filters.get(
        preset,
        "zoompan=z='min(1.08,1.0+0.08*" + progress + ")':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    )
