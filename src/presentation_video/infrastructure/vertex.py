from __future__ import annotations

import asyncio
import base64
import logging
import mimetypes
import time
import uuid
import wave
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any, TypeVar
from urllib.parse import urlparse

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from presentation_video.domain.models import (
    AudioArtifact,
    MediaMode,
    SlideContent,
    VisualArtifact,
    VisualScenePlan,
)
from presentation_video.infrastructure.speech import _delivery_prompt
from presentation_video.infrastructure.visual_media import _visual_prompt

logger = logging.getLogger(__name__)

T = TypeVar("T")


class VertexClientFactory:
    """Creates explicit Google Gen AI Vertex clients, cached independently by region."""

    def __init__(
        self,
        project: str,
        credentials: Any | None = None,
        client_builder: Callable[..., Any] | None = None,
    ) -> None:
        if not project.strip():
            raise ValueError("A Google Cloud project is required for Vertex AI")
        self._project = project.strip()
        self._credentials = credentials
        self._client_builder = client_builder or genai.Client
        self._sdk_clients: dict[str, Any] = {}
        self._async_clients: dict[str, Any] = {}

    def client(self, location: str) -> Any:
        normalized_location = location.strip()
        if not normalized_location:
            raise ValueError("A Google Cloud location is required for Vertex AI")
        cached = self._async_clients.get(normalized_location)
        if cached is not None:
            return cached

        arguments: dict[str, Any] = {
            "vertexai": True,
            "project": self._project,
            "location": normalized_location,
        }
        if self._credentials is not None:
            arguments["credentials"] = self._credentials
        logger.info(
            "vertex client creating project=%s location=%s vertexai=true",
            self._project,
            normalized_location,
        )
        sdk_client = self._client_builder(**arguments)
        async_client = sdk_client.aio
        self._sdk_clients[normalized_location] = sdk_client
        self._async_clients[normalized_location] = async_client
        return async_client

    async def aclose(self) -> None:
        for location, client in list(self._async_clients.items()):
            close = getattr(client, "aclose", None)
            if close is not None:
                logger.debug("vertex client closing location=%s", location)
                await close()
        self._sdk_clients.clear()
        self._async_clients.clear()


class VertexImageAssetGenerator:
    """Generates a source-grounded intermediate image with Gemini on Vertex AI."""

    def __init__(
        self,
        client: Any,
        model: str = "gemini-3.1-flash-image",
        aspect_ratio: str = "16:9",
        image_size: str | None = "2K",
        max_reference_images: int = 4,
        max_retries: int = 2,
        timeout_seconds: float = 180,
        retry_backoff_seconds: float = 1,
    ) -> None:
        if max_reference_images < 0:
            raise ValueError("max_reference_images cannot be negative")
        _validate_retry_settings(max_retries, timeout_seconds, retry_backoff_seconds)
        self._client = client
        self._model = _strip_google_prefix(model)
        self._aspect_ratio = aspect_ratio
        self._image_size = image_size
        self._max_reference_images = max_reference_images
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds
        self._retry_backoff_seconds = retry_backoff_seconds

    async def generate(
        self,
        plan: VisualScenePlan,
        source_slides: list[SlideContent],
        output_dir: Path,
        revision: int = 1,
    ) -> VisualArtifact:
        if plan.media_mode == MediaMode.STATIC and plan.preserve_source_frame:
            raise ValueError("Preserved static scenes must use an unchanged source page")

        prompt = (
            f"{_visual_prompt(plan, source_slides)} "
            "The attached source-page images are grounding references only. Preserve their factual "
            "meaning and recognizable subject matter, but do not copy their typography, captions, "
            "interfaces, charts, logos, or page layout into the generated image."
        )
        contents: list[Any] = [prompt]
        reference_count = 0
        for slide in source_slides[: self._max_reference_images]:
            if not slide.image_path.is_file():
                logger.warning(
                    "vertex image source reference skipped scene=%s source_page=%s path=%s "
                    "reason=file_not_found",
                    plan.scene_number,
                    slide.number,
                    slide.image_path,
                )
                continue
            mime_type = mimetypes.guess_type(slide.image_path.name)[0] or "image/png"
            contents.append(
                types.Part.from_bytes(
                    data=slide.image_path.read_bytes(),
                    mime_type=mime_type,
                )
            )
            reference_count += 1

        config = types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            image_config=types.ImageConfig(
                aspect_ratio=self._aspect_ratio,
                image_size=self._image_size,
            ),
        )
        started_at = time.monotonic()
        logger.info(
            "vertex image generation started scene=%s revision=%s model=%s "
            "aspect_ratio=%s image_size=%s references=%s prompt_characters=%s",
            plan.scene_number,
            revision,
            self._model,
            self._aspect_ratio,
            self._image_size,
            reference_count,
            len(prompt),
        )

        response = await _run_with_retries(
            operation_name=f"vertex image generation scene={plan.scene_number}",
            operation=lambda: self._client.models.generate_content(
                model=self._model,
                contents=contents,
                config=config,
            ),
            max_retries=self._max_retries,
            timeout_seconds=self._timeout_seconds,
            retry_backoff_seconds=self._retry_backoff_seconds,
        )
        image_bytes, mime_type = _extract_inline_media(response, media_prefix="image/")
        suffix = {
            "image/jpeg": ".jpg",
            "image/png": ".png",
            "image/webp": ".webp",
        }.get(mime_type.lower(), ".png")
        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"scene-{plan.scene_number:03d}-r{revision}{suffix}"
        destination.write_bytes(image_bytes)
        _validate_artifact(destination, "image", plan.scene_number)
        logger.info(
            "vertex image generation completed scene=%s revision=%s model=%s "
            "elapsed_seconds=%.1f bytes=%s mime_type=%s path=%s",
            plan.scene_number,
            revision,
            self._model,
            time.monotonic() - started_at,
            destination.stat().st_size,
            mime_type,
            destination,
        )
        return VisualArtifact(
            scene_number=plan.scene_number,
            path=destination,
            kind="image",
            revision=revision,
        )


class VertexVideoAssetGenerator:
    """Animates an approved image and stores the returned Veo clip locally."""

    def __init__(
        self,
        client: Any,
        output_gcs_uri: str | None = None,
        storage_client: Any | None = None,
        model: str = "veo-3.1-fast-generate-001",
        aspect_ratio: str = "16:9",
        resolution: str = "720p",
        clip_duration_seconds: int = 8,
        poll_interval_seconds: float = 5,
        timeout_seconds: float = 900,
        max_retries: int = 2,
        retry_backoff_seconds: float = 1,
        request_id_factory: Callable[[], str] | None = None,
    ) -> None:
        if output_gcs_uri is not None:
            _parse_gcs_uri(output_gcs_uri)
        if clip_duration_seconds not in {4, 6, 8}:
            raise ValueError("Veo clip_duration_seconds must be one of 4, 6, or 8")
        if poll_interval_seconds < 0:
            raise ValueError("poll_interval_seconds cannot be negative")
        _validate_retry_settings(max_retries, timeout_seconds, retry_backoff_seconds)
        self._client = client
        self._output_gcs_uri = output_gcs_uri.rstrip("/") if output_gcs_uri else None
        self._storage_client = storage_client
        self._model = _strip_google_prefix(model)
        self._aspect_ratio = aspect_ratio
        self._resolution = resolution
        self._clip_duration_seconds = clip_duration_seconds
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._request_id_factory = request_id_factory or (lambda: uuid.uuid4().hex[:12])

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

        mime_type = mimetypes.guess_type(image.path.name)[0] or "image/png"
        source_image = types.Image(
            image_bytes=image.path.read_bytes(),
            mime_type=mime_type,
        )
        prompt = (
            "Animate the supplied approved image into a short, coherent documentary shot. "
            "Preserve the identity, appearance, position, proportions, and relationships of all "
            "subjects and objects. Use subtle natural subject motion and restrained camera motion "
            f"at normal speed. Camera direction: {plan.camera_motion.strip()}. "
            f"Entrance: {plan.entrance_motion}. Focal action: {plan.focal_action}. "
            f"End by {plan.transition_out}. Apply that direction "
            "only as camera or subject motion; do not turn it into a new object or concept. "
            "Do not morph objects, invent interfaces, or add new visual concepts. "
            "The clip must contain absolutely no words, letters, numbers, captions, subtitles, "
            "logos, signs, documents, screens, user interfaces, charts, tables, or watermarks."
        )
        scene_output_uri = None
        if self._output_gcs_uri:
            request_id = self._request_id_factory().strip()
            if not request_id or "/" in request_id:
                raise ValueError("Vertex video request ID must be a non-empty GCS path segment")
            scene_output_uri = (
                f"{self._output_gcs_uri}/scene-{image.scene_number:03d}"
                f"-r{image.revision}-{request_id}"
            )
        config = types.GenerateVideosConfig(
            number_of_videos=1,
            output_gcs_uri=scene_output_uri,
            duration_seconds=self._clip_duration_seconds,
            aspect_ratio=self._aspect_ratio,
            resolution=self._resolution,
            generate_audio=False,
            negative_prompt=(
                "text, words, letters, numbers, captions, subtitles, logos, signage, documents, "
                "screens, interfaces, dashboards, charts, tables, watermarks, distorted objects, "
                "morphing, flicker, slow motion"
            ),
        )
        started_at = time.monotonic()
        logger.info(
            "vertex video generation submitting scene=%s revision=%s model=%s "
            "clip_duration_seconds=%s narration_duration_seconds=%.2f aspect_ratio=%s "
            "resolution=%s delivery_mode=%s output_gcs_uri=%s",
            image.scene_number,
            image.revision,
            self._model,
            self._clip_duration_seconds,
            duration_seconds,
            self._aspect_ratio,
            self._resolution,
            "gcs" if scene_output_uri else "inline",
            scene_output_uri,
        )
        operation = await _run_with_retries(
            operation_name=f"vertex video submission scene={image.scene_number}",
            operation=lambda: self._client.models.generate_videos(
                model=self._model,
                prompt=prompt,
                image=source_image,
                config=config,
            ),
            max_retries=self._max_retries,
            timeout_seconds=self._timeout_seconds,
            retry_backoff_seconds=self._retry_backoff_seconds,
        )
        operation_name = getattr(operation, "name", None) or "unknown"
        logger.info(
            "vertex video generation submitted scene=%s operation=%s",
            image.scene_number,
            operation_name,
        )
        operation = await self._poll_operation(
            operation,
            scene_number=image.scene_number,
            started_at=started_at,
        )
        video = _extract_generated_video(operation)

        output_dir.mkdir(parents=True, exist_ok=True)
        destination = output_dir / f"scene-{image.scene_number:03d}.mp4"
        video_bytes = getattr(video, "video_bytes", None)
        video_uri = getattr(video, "uri", None)
        if video_bytes:
            destination.write_bytes(_as_bytes(video_bytes))
            logger.info(
                "vertex video result received inline scene=%s operation=%s bytes=%s",
                image.scene_number,
                operation_name,
                destination.stat().st_size,
            )
        elif video_uri:
            logger.info(
                "vertex video result downloading scene=%s operation=%s uri=%s",
                image.scene_number,
                operation_name,
                video_uri,
            )
            await self._download_gcs(str(video_uri), destination)
        else:
            raise RuntimeError(
                f"Veo operation {operation_name} returned neither video bytes nor a GCS URI"
            )

        _validate_artifact(destination, "video", image.scene_number)
        logger.info(
            "vertex video generation completed scene=%s revision=%s model=%s operation=%s "
            "elapsed_seconds=%.1f bytes=%s path=%s",
            image.scene_number,
            image.revision,
            self._model,
            operation_name,
            time.monotonic() - started_at,
            destination.stat().st_size,
            destination,
        )
        return VisualArtifact(
            scene_number=image.scene_number,
            path=destination,
            kind="video",
            revision=image.revision,
        )

    async def _poll_operation(
        self,
        operation: Any,
        scene_number: int,
        started_at: float,
    ) -> Any:
        deadline = started_at + self._timeout_seconds
        poll_number = 0
        while not bool(getattr(operation, "done", False)):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                operation_name = getattr(operation, "name", None) or "unknown"
                raise TimeoutError(
                    f"Veo operation {operation_name} timed out after {self._timeout_seconds:.0f}s"
                )
            if self._poll_interval_seconds:
                await asyncio.sleep(min(self._poll_interval_seconds, remaining))
            poll_number += 1
            operation = await _run_with_retries(
                operation_name=f"vertex video polling scene={scene_number}",
                operation=lambda: self._client.operations.get(operation=operation),
                max_retries=self._max_retries,
                timeout_seconds=max(0.001, deadline - time.monotonic()),
                retry_backoff_seconds=self._retry_backoff_seconds,
            )
            logger.info(
                "vertex video generation polling scene=%s operation=%s poll=%s done=%s "
                "elapsed_seconds=%.1f metadata=%s",
                scene_number,
                getattr(operation, "name", None) or "unknown",
                poll_number,
                bool(getattr(operation, "done", False)),
                time.monotonic() - started_at,
                _operation_progress(getattr(operation, "metadata", None)),
            )

        error = getattr(operation, "error", None)
        if error:
            raise RuntimeError(
                f"Veo operation {getattr(operation, 'name', None) or 'unknown'} failed: {error}"
            )
        return operation

    async def _download_gcs(self, uri: str, destination: Path) -> None:
        bucket_name, object_name = _parse_gcs_uri(uri)
        if not object_name:
            raise RuntimeError(f"Veo returned a GCS bucket without an object name: {uri}")
        storage_client = self._storage_client
        if storage_client is None:
            try:
                from google.cloud import storage  # type: ignore[import-untyped]
            except ImportError as exc:
                raise RuntimeError(
                    "google-cloud-storage is required to download Veo output from GCS"
                ) from exc
            logger.info("vertex video storage client creating from ADC")
            storage_client = storage.Client()
            self._storage_client = storage_client

        destination.parent.mkdir(parents=True, exist_ok=True)
        blob = storage_client.bucket(bucket_name).blob(object_name)
        await asyncio.to_thread(blob.download_to_filename, str(destination))
        logger.info(
            "vertex video GCS download completed bucket=%s object=%s destination=%s bytes=%s",
            bucket_name,
            object_name,
            destination,
            destination.stat().st_size if destination.exists() else 0,
        )


class VertexSpeechSynthesizer:
    """Generates Gemini TTS audio through Vertex and wraps raw 24 kHz PCM in WAV."""

    def __init__(
        self,
        client: Any,
        model: str = "gemini-3.1-flash-tts-preview",
        voice: str = "Kore",
        language_code: str = "pt-BR",
        style_prompt: str = "Narração profissional, natural, clara e envolvente.",
        max_retries: int = 2,
        timeout_seconds: float = 180,
        retry_backoff_seconds: float = 1,
    ) -> None:
        _validate_retry_settings(max_retries, timeout_seconds, retry_backoff_seconds)
        self._client = client
        self._model = _strip_google_prefix(model)
        self._voice = voice
        self._language_code = language_code
        self._style_prompt = style_prompt
        self._max_retries = max_retries
        self._timeout_seconds = timeout_seconds
        self._retry_backoff_seconds = retry_backoff_seconds

    async def synthesize(
        self,
        text: str,
        output_path: Path,
        language: str | None = None,
        style: str | None = None,
    ) -> AudioArtifact:
        spoken_language = language or self._language_code
        instruction = (
            "Synthesize speech only. Do not read these directions or section labels aloud.\n"
            f"LANGUAGE: {spoken_language}\n"
            f"DIRECTOR NOTES: {_delivery_prompt(self._style_prompt, style)}\n"
            f"TRANSCRIPT:\n{text}"
        )
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                language_code=spoken_language,
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._voice)
                ),
            ),
        )
        started_at = time.monotonic()
        logger.info(
            "vertex TTS generation started model=%s voice=%s language=%s "
            "text_characters=%s path=%s",
            self._model,
            self._voice,
            spoken_language,
            len(text),
            output_path,
        )
        response = await _run_with_retries(
            operation_name="vertex TTS generation",
            operation=lambda: self._client.models.generate_content(
                model=self._model,
                contents=instruction,
                config=config,
            ),
            max_retries=self._max_retries,
            timeout_seconds=self._timeout_seconds,
            retry_backoff_seconds=self._retry_backoff_seconds,
        )
        pcm, mime_type = _extract_inline_media(response, media_prefix="audio/")
        channels = 1
        sample_rate = 24_000
        sample_width = 2
        _write_pcm_wave(
            output_path,
            pcm,
            channels=channels,
            sample_rate=sample_rate,
            sample_width=sample_width,
        )
        duration_seconds = len(pcm) / (channels * sample_width * sample_rate)
        if duration_seconds <= 0:
            raise RuntimeError("Gemini TTS returned empty PCM audio")
        logger.info(
            "vertex TTS generation completed model=%s voice=%s language=%s "
            "duration_seconds=%.2f elapsed_seconds=%.1f pcm_bytes=%s mime_type=%s path=%s",
            self._model,
            self._voice,
            spoken_language,
            duration_seconds,
            time.monotonic() - started_at,
            len(pcm),
            mime_type,
            output_path,
        )
        return AudioArtifact(path=output_path, duration_seconds=duration_seconds)


async def _run_with_retries(
    *,
    operation_name: str,
    operation: Callable[[], Awaitable[T]],
    max_retries: int,
    timeout_seconds: float,
    retry_backoff_seconds: float,
) -> T:
    for attempt in range(max_retries + 1):
        try:
            async with asyncio.timeout(timeout_seconds):
                return await operation()
        except Exception as exc:
            if not _is_retryable(exc):
                logger.error(
                    "%s failed with a permanent error attempt=%s",
                    operation_name,
                    attempt + 1,
                    exc_info=True,
                )
                raise
            if attempt >= max_retries:
                logger.exception(
                    "%s failed attempts=%s timeout_seconds=%s",
                    operation_name,
                    attempt + 1,
                    timeout_seconds,
                )
                raise
            delay = min(retry_backoff_seconds * (2**attempt), 8)
            logger.warning(
                "%s retry requested attempt=%s next_attempt=%s delay_seconds=%.1f",
                operation_name,
                attempt + 1,
                attempt + 2,
                delay,
                exc_info=True,
            )
            if delay:
                await asyncio.sleep(delay)
    raise RuntimeError(f"Unreachable retry state for {operation_name}")


def _extract_inline_media(response: Any, media_prefix: str) -> tuple[bytes, str]:
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            inline_data = getattr(part, "inline_data", None)
            if inline_data is None:
                continue
            mime_type = str(getattr(inline_data, "mime_type", "") or "")
            data = getattr(inline_data, "data", None)
            if data and mime_type.lower().startswith(media_prefix):
                return _as_bytes(data), mime_type
    finish_reasons = [
        str(getattr(candidate, "finish_reason", None))
        for candidate in getattr(response, "candidates", None) or []
    ]
    raise RuntimeError(
        f"Vertex model returned no {media_prefix.rstrip('/')} bytes"
        + (f"; finish_reasons={finish_reasons}" if finish_reasons else "")
    )


def _extract_generated_video(operation: Any) -> Any:
    result = getattr(operation, "result", None) or getattr(operation, "response", None)
    generated_videos = getattr(result, "generated_videos", None) if result is not None else None
    if not generated_videos:
        filtered_reasons = getattr(result, "rai_media_filtered_reasons", None)
        suffix = f"; filtered_reasons={filtered_reasons}" if filtered_reasons else ""
        raise RuntimeError(
            f"Veo operation {getattr(operation, 'name', None) or 'unknown'} "
            f"returned no generated video{suffix}"
        )
    video = getattr(generated_videos[0], "video", None)
    if video is None:
        raise RuntimeError(
            f"Veo operation {getattr(operation, 'name', None) or 'unknown'} "
            "returned an empty video result"
        )
    return video


def _as_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, str):
        return base64.b64decode(value)
    raise TypeError(f"Expected media bytes or base64 text, got {type(value).__name__}")


def _parse_gcs_uri(uri: str) -> tuple[str, str]:
    parsed = urlparse(uri)
    bucket_name = parsed.netloc
    object_name = parsed.path.lstrip("/")
    if parsed.scheme != "gs" or not bucket_name:
        raise ValueError(f"Expected a gs:// URI, got {uri!r}")
    return bucket_name, object_name


def _validate_retry_settings(
    max_retries: int,
    timeout_seconds: float,
    retry_backoff_seconds: float,
) -> None:
    if max_retries < 0:
        raise ValueError("max_retries cannot be negative")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    if retry_backoff_seconds < 0:
        raise ValueError("retry_backoff_seconds cannot be negative")


def _strip_google_prefix(model: str) -> str:
    return model.removeprefix("google-cloud:").removeprefix("google:")


def _is_retryable(exc: Exception) -> bool:
    if isinstance(exc, genai_errors.ClientError):
        code = getattr(exc, "code", None)
        return code in {408, 409, 429}
    code = getattr(exc, "code", None)
    if isinstance(code, int) and 400 <= code < 500:
        return code in {408, 409, 429}
    return True


def _validate_artifact(path: Path, kind: str, scene_number: int) -> None:
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError(f"Vertex returned an empty {kind} for scene {scene_number}")


def _operation_progress(metadata: Any) -> Any:
    if not isinstance(metadata, dict):
        return metadata or "unavailable"
    interesting = {
        key: value
        for key, value in metadata.items()
        if key.lower() in {"progresspercent", "progress_percent", "state", "status"}
    }
    return interesting or "pending"


def _write_pcm_wave(
    output_path: Path,
    pcm: bytes,
    channels: int,
    sample_rate: int,
    sample_width: int,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output_path), "wb") as output:
        output.setnchannels(channels)
        output.setsampwidth(sample_width)
        output.setframerate(sample_rate)
        output.writeframes(pcm)
