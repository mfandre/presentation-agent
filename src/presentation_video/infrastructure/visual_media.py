from __future__ import annotations

import base64
import logging
import mimetypes
from pathlib import Path

from presentation_video.domain.models import MediaMode, SlideContent, VisualArtifact, VisualScenePlan
from presentation_video.infrastructure.process import run_process
from presentation_video.infrastructure.replicate import ReplicatePredictionClient
from presentation_video.infrastructure.concept_grounding import (
    default_concept_visualization,
    infer_required_concepts,
)

logger = logging.getLogger(__name__)

_SAFE_VISUAL_POLICY = (
    "Safety and fidelity constraints: depict routine, calm, lawful professional activity only. "
    "Use anonymous adults with faces de-emphasized and correct PPE when the source supports an "
    "industrial setting. No public figures, recognizable real people, politics, protests, crowds, "
    "conflict, weapons, military content, fire, smoke, explosions, injuries, emergencies, dangerous "
    "acts, medical procedures, minors, nudity, sexual content, drugs, or hateful symbols. "
)


def _visual_prompt(plan: VisualScenePlan, source_slides: list[SlideContent] | None = None) -> str:
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
        narrative_contract += (
            f"Do not substitute with: {'; '.join(plan.forbidden_substitutions)}. "
        )
    return (
        "Create a grounded, plausible real-world image that can become a short moving shot. "
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
    ) -> None:
        self._client = client
        self._model = model
        self._input_defaults = input_defaults or {}

    async def generate(
        self,
        plan: VisualScenePlan,
        source_slides: list[SlideContent],
        output_dir: Path,
        revision: int = 1,
    ) -> VisualArtifact:
        if plan.media_mode == MediaMode.STATIC and plan.preserve_source_frame:
            raise ValueError("Preserved static scenes must use an unchanged source page")
        output = await self._client.run(
            self._model,
            {**self._input_defaults, "prompt": _visual_prompt(plan, source_slides)},
        )
        url = self._client.output_url(output)
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            suffix = ".webp"
        destination = output_dir / f"scene-{plan.scene_number:03d}-r{revision}{suffix}"
        await self._client.download(url, destination)
        if not destination.exists() or destination.stat().st_size == 0:
            raise RuntimeError(f"Replicate returned an empty image for scene {plan.scene_number}")
        return VisualArtifact(
            scene_number=plan.scene_number,
            path=destination,
            kind="image",
            revision=revision,
        )


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

    async def animate(
        self,
        plan: VisualScenePlan,
        image: VisualArtifact,
        output_dir: Path,
        duration_seconds: float,
    ) -> VisualArtifact:
        if plan.media_mode != MediaMode.VIDEO:
            raise ValueError("Static scenes must bypass image-to-video")
        input_path = image.path
        target_dimensions = {
            "16:9": (1280, 720),
            "9:16": (720, 1280),
            "1:1": (1024, 1024),
        }.get(str(self._input_defaults.get("aspect_ratio", "")))
        if target_dimensions is not None or input_path.stat().st_size > 900_000:
            compressed = output_dir / f"scene-{image.scene_number:03d}-input.jpg"
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
        inputs = {
            **self._input_defaults,
            "prompt": (
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
                image,
                output_dir,
                duration_seconds,
            )
        url = self._client.output_url(output)
        suffix = Path(url.split("?", 1)[0]).suffix.lower()
        if suffix not in {".mp4", ".webm", ".mov"}:
            suffix = ".mp4"
        destination = output_dir / f"scene-{image.scene_number:03d}{suffix}"
        await self._client.download(url, destination)
        if not destination.exists() or destination.stat().st_size == 0:
            raise RuntimeError(f"Replicate returned an empty video for scene {image.scene_number}")
        return VisualArtifact(
            scene_number=image.scene_number,
            path=destination,
            kind="video",
            revision=image.revision,
        )


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
        destination = output_dir / f"scene-{image.scene_number:03d}.mp4"
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
        "pan_left": (
            "zoompan=z='1.06':x='(iw-iw/zoom)*(1-"
            + progress
            + ")':y='ih/2-(ih/zoom/2)'"
        ),
        "pan_right": (
            "zoompan=z='1.06':x='(iw-iw/zoom)*"
            + progress
            + "':y='ih/2-(ih/zoom/2)'"
        ),
        "drift_up": (
            "zoompan=z='1.05':x='iw/2-(iw/zoom/2)':y='(ih-ih/zoom)*(1-"
            + progress
            + ")'"
        ),
        "none": "zoompan=z='1.0':x='0':y='0'",
    }
    return filters.get(
        preset,
        "zoompan=z='min(1.08,1.0+0.08*"
        + progress
        + ")':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'",
    )
