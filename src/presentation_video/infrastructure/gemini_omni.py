from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any

from presentation_video.domain.models import (
    MediaMode,
    VideoGeneratorCapabilities,
    VisualArtifact,
    VisualScenePlan,
)
from presentation_video.infrastructure.visual_media import _visible_language_guard

logger = logging.getLogger(__name__)


def _parse_gcs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError(f"Expected a gs:// URI, got {uri!r}")
    bucket, _, object_name = uri.removeprefix("gs://").partition("/")
    if not bucket:
        raise ValueError(f"Invalid GCS URI: {uri!r}")
    return bucket, object_name


def _video_content(interaction: dict[str, Any]) -> dict[str, Any]:
    for step in interaction.get("steps", []):
        if step.get("type") != "model_output":
            continue
        for content in step.get("content", []):
            if content.get("type") == "video":
                return content
    raise RuntimeError("Gemini Omni interaction returned no video output")


class GeminiOmniVideoAssetGenerator:
    """Animate approved images through the Agent Platform Interactions API."""

    def __init__(
        self,
        project: str,
        output_gcs_uri: str,
        *,
        model: str = "gemini-omni-flash-preview",
        aspect_ratio: str = "16:9",
        clip_duration_seconds: int = 8,
        timeout_seconds: float = 900,
        store_output_in_gcs: bool = False,
        storage_client: Any | None = None,
        session: Any | None = None,
    ) -> None:
        if not project.strip():
            raise ValueError("A Google Cloud project is required for Gemini Omni")
        _parse_gcs_uri(output_gcs_uri)
        if aspect_ratio not in {"16:9", "9:16"}:
            raise ValueError("Gemini Omni aspect_ratio must be '16:9' or '9:16'")
        if not 3 <= clip_duration_seconds <= 10:
            raise ValueError("Gemini Omni duration_seconds must be between 3 and 10")
        self._project = project.strip()
        self._output_gcs_uri = output_gcs_uri.rstrip("/")
        self._model = model.removeprefix("google-cloud:").strip()
        self._aspect_ratio = aspect_ratio
        self._clip_duration_seconds = clip_duration_seconds
        self._timeout_seconds = timeout_seconds
        self._store_output_in_gcs = store_output_in_gcs
        self._storage_client = storage_client
        self._session = session

    @property
    def capabilities(self) -> VideoGeneratorCapabilities:
        return VideoGeneratorCapabilities(
            supports_storyboard_reference=True,
            supports_multishot=True,
            minimum_output_seconds=float(self._clip_duration_seconds),
            maximum_output_seconds=float(self._clip_duration_seconds),
            maximum_reference_images=10,
            supports_first_frame=True,
            supports_last_frame=False,
        )

    async def animate(
        self,
        plan: VisualScenePlan,
        image: VisualArtifact,
        output_dir: Path,
        duration_seconds: float,
    ) -> VisualArtifact:
        if plan.media_mode != MediaMode.VIDEO:
            raise ValueError("Static scenes must bypass image-to-video")
        if not image.path.is_file() or image.path.stat().st_size == 0:
            raise ValueError(f"Approved image does not exist or is empty: {image.path}")

        request_id = uuid.uuid4().hex
        stem = f"scene-{image.scene_number:03d}"
        if image.shot_number is not None:
            stem += f"-shot-{image.shot_number:02d}"
        input_uri = f"{self._output_gcs_uri}/omni-input/{stem}-{request_id}{image.path.suffix}"
        output_uri = f"{self._output_gcs_uri}/omni-output/{stem}-{request_id}/"
        await self._upload(image.path, input_uri)

        prompt = self._prompt(plan)
        response_format: dict[str, Any] = {
            "type": "video",
            "aspect_ratio": self._aspect_ratio,
            "duration": f"{self._clip_duration_seconds}s",
        }
        if self._store_output_in_gcs:
            response_format.update(
                {
                    "delivery": "uri",
                    "gcs_uri": output_uri,
                }
            )
        body = {
            "model": self._model,
            "input": [
                {"type": "text", "text": prompt},
                {
                    "type": "image",
                    "uri": input_uri,
                    "mime_type": mimetypes.guess_type(image.path.name)[0] or "image/png",
                },
            ],
            "response_format": [response_format],
            "generation_config": {"video_config": {"task": "image_to_video"}},
        }
        endpoint = (
            "https://aiplatform.googleapis.com/v1beta1/projects/"
            f"{self._project}/locations/global/interactions"
        )
        started_at = time.monotonic()
        logger.info(
            "Gemini Omni interaction submitting scene=%s model=%s duration=%ss",
            image.scene_number,
            self._model,
            self._clip_duration_seconds,
        )
        response = await asyncio.to_thread(self._post, endpoint, body)
        status = str(response.get("status", "")).lower()
        if status and status != "completed":
            raise RuntimeError(
                f"Gemini Omni synchronous interaction ended with status {status!r}"
            )
        video = _video_content(response)
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"{stem}.mp4"
        if video.get("uri"):
            await self._download(str(video["uri"]), destination)
        elif video.get("data"):
            destination.write_bytes(base64.b64decode(video["data"]))
        else:
            raise RuntimeError("Gemini Omni returned neither video URI nor inline data")
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("Gemini Omni produced an empty video artifact")
        logger.info(
            "Gemini Omni interaction completed scene=%s elapsed_seconds=%.1f bytes=%s",
            image.scene_number,
            time.monotonic() - started_at,
            destination.stat().st_size,
        )
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
        if not plans:
            raise ValueError("Storyboard animation requires at least one shot plan")
        if any(plan.media_mode != MediaMode.VIDEO for plan in plans):
            raise ValueError("Static shots must bypass storyboard-to-video")
        if not storyboard.path.is_file() or storyboard.path.stat().st_size == 0:
            raise ValueError(f"Storyboard does not exist or is empty: {storyboard.path}")
        maximum_duration = self.capabilities.maximum_output_seconds
        if duration_seconds > maximum_duration + 0.001:
            raise ValueError(
                f"Storyboard segment duration {duration_seconds:.2f}s exceeds the model "
                f"maximum of {maximum_duration:.2f}s"
            )

        request_id = uuid.uuid4().hex
        scene_number = storyboard.scene_number
        stem = f"scene-{scene_number:03d}-storyboard-segment-{segment_number:03d}"
        input_items: list[dict[str, str]] = []
        storyboard_uri = (
            f"{self._output_gcs_uri}/omni-input/{stem}-{request_id}{storyboard.path.suffix}"
        )
        await self._upload(storyboard.path, storyboard_uri)
        input_items.append(
            {
                "type": "image",
                "uri": storyboard_uri,
                "mime_type": mimetypes.guess_type(storyboard.path.name)[0] or "image/jpeg",
            }
        )
        prompt = self._storyboard_prompt(plans, duration_seconds)
        output_uri = f"{self._output_gcs_uri}/omni-output/{stem}-{request_id}/"
        response_format: dict[str, Any] = {
            "type": "video",
            "aspect_ratio": self._aspect_ratio,
            "duration": f"{self._clip_duration_seconds}s",
        }
        if self._store_output_in_gcs:
            response_format.update({"delivery": "uri", "gcs_uri": output_uri})
        body = {
            "model": self._model,
            "input": [{"type": "text", "text": prompt}, *input_items],
            "response_format": [response_format],
            "generation_config": {"video_config": {"task": "image_to_video"}},
        }
        endpoint = (
            "https://aiplatform.googleapis.com/v1beta1/projects/"
            f"{self._project}/locations/global/interactions"
        )
        started_at = time.monotonic()
        logger.info(
            "Gemini Omni storyboard interaction submitting scene=%s segment=%s panels=%s "
            "references=%s model=%s duration=%ss",
            scene_number,
            segment_number,
            len(plans),
            len(input_items),
            self._model,
            self._clip_duration_seconds,
        )
        response = await asyncio.to_thread(self._post, endpoint, body)
        status = str(response.get("status", "")).lower()
        if status and status != "completed":
            raise RuntimeError(
                f"Gemini Omni synchronous interaction ended with status {status!r}"
            )
        video = _video_content(response)
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"{stem}.mp4"
        if video.get("uri"):
            await self._download(str(video["uri"]), destination)
        elif video.get("data"):
            destination.write_bytes(base64.b64decode(video["data"]))
        else:
            raise RuntimeError("Gemini Omni returned neither video URI nor inline data")
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("Gemini Omni produced an empty storyboard video artifact")
        logger.info(
            "Gemini Omni storyboard interaction completed scene=%s segment=%s "
            "elapsed_seconds=%.1f bytes=%s",
            scene_number,
            segment_number,
            time.monotonic() - started_at,
            destination.stat().st_size,
        )
        return VisualArtifact(
            scene_number=scene_number,
            shot_number=storyboard.shot_number,
            path=destination,
            kind="video",
            revision=storyboard.revision,
        )

    def _prompt(self, plan: VisualScenePlan) -> str:
        if "whiteboard animation" in plan.visual_style.lower():
            return (
                "Animate the supplied whiteboard illustration as a progressive marker drawing. "
                f"{_visible_language_guard(plan)}"
                "Reveal the approved black line artwork in a logical teaching order and preserve "
                "the final composition. Keep the background pure white. "
                f"Drawing behavior: {plan.entrance_motion}. Action: {plan.focal_action}. "
                f"Camera: {plan.camera_motion}. End by {plan.transition_out}. "
                "Animate only strokes that form non-text shapes, objects, icons, arrows, unlabeled "
                "chart marks, or diagram connections. Never animate handwriting or character "
                "formation. Do not add typography, glyphs, alphabetic characters, words, letters, "
                "digits, labels, titles, captions, legends, axis labels, annotations, logos, "
                "watermarks, photography, or 3D objects."
            )
        return (
            "Animate the supplied approved image into a coherent documentary shot. Preserve all "
            f"{_visible_language_guard(plan)}"
            "subjects, objects, positions, proportions, and identities. Use natural motion at "
            f"normal speed. Camera: {plan.camera_motion}. Entrance: {plan.entrance_motion}. "
            f"Action: {plan.focal_action}. End by {plan.transition_out}. "
            "Do not add text, captions, logos, interfaces, charts, or watermarks."
        )

    def _storyboard_prompt(
        self,
        plans: list[VisualScenePlan],
        duration_seconds: float,
    ) -> str:
        elapsed = 0.0
        beats: list[str] = []
        explicit_durations = [
            plan.emphasis_beats_seconds[0]
            if plan.emphasis_beats_seconds
            else 0.0
            for plan in plans
        ]
        use_explicit_durations = (
            all(duration > 0 for duration in explicit_durations)
            and sum(explicit_durations) <= duration_seconds + 0.01
        )
        for index, plan in enumerate(plans, start=1):
            remaining = max(duration_seconds - elapsed, 0.0)
            beat_duration = (
                explicit_durations[index - 1]
                if use_explicit_durations
                else remaining / max(len(plans) - index + 1, 1)
            )
            beats.append(
                f"Panel {index}, {elapsed:.1f}s to {elapsed + beat_duration:.1f}s: "
                f"{plan.focal_action}; camera {plan.camera_motion}; enter from "
                f"{plan.entrance_motion}; finish in {plan.transition_out}."
            )
            elapsed += beat_duration
        return (
            "Animate the first supplied image as one continuous cinematic multi-shot sequence. "
            "It is a clean storyboard grid read left-to-right and top-to-bottom; each cell is a "
            "successive moment, never a simultaneous collage in the output video. Use one cell per "
            "directed beat, with clean motivated cuts and continuous action across the cuts. Do not "
            "show the storyboard borders or grid. Do not repeat, reverse, restart, or prematurely "
            "complete an action. Preserve the environment, props, lighting, palette, spatial state, "
            "and causal continuity from one beat to the next. Preserve every character identity, "
            "face, body proportion, hairstyle, wardrobe, color, and accessory already established "
            "inside the storyboard across all shots; never merge or recast them. Natural motion at "
            "normal speed. No slow motion. "
            "No audio. No text, captions, titles, labels, logos, interfaces, or watermarks. "
            f"The target narrative interval is {duration_seconds:.1f} seconds. "
            f"Shot directions: {' '.join(beats)}"
        )

    def _storage(self) -> Any:
        if self._storage_client is None:
            from google.cloud import storage  # type: ignore[import-untyped]

            self._storage_client = storage.Client(project=self._project)
        return self._storage_client

    async def _upload(self, source: Path, uri: str) -> None:
        bucket, object_name = _parse_gcs_uri(uri)
        blob = self._storage().bucket(bucket).blob(object_name)
        await asyncio.to_thread(blob.upload_from_filename, str(source))

    async def _download(self, uri: str, destination: Path) -> None:
        bucket, object_name = _parse_gcs_uri(uri)
        if not object_name:
            raise RuntimeError(f"Gemini Omni returned an invalid video URI: {uri}")
        blob = self._storage().bucket(bucket).blob(object_name)
        await asyncio.to_thread(blob.download_to_filename, str(destination))

    def _post(self, endpoint: str, body: dict[str, Any]) -> dict[str, Any]:
        session = self._session
        if session is None:
            import google.auth
            from google.auth.transport.requests import AuthorizedSession

            credentials, _ = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"],
                quota_project_id=self._project,
            )
            session = AuthorizedSession(credentials)
            self._session = session
        response = session.post(endpoint, json=body, timeout=self._timeout_seconds)
        response.raise_for_status()
        result: dict[str, Any] = response.json()
        return result
