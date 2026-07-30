from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import time
import uuid
from pathlib import Path
from typing import Any

from presentation_video.domain.models import MediaMode, VisualArtifact, VisualScenePlan
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
