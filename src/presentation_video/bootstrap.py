from __future__ import annotations

import logging
from typing import Any

from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google_cloud import GoogleCloudProvider

from presentation_video.application.pipeline import CreatePresentationVideo
from presentation_video.domain.ports import (
    JobReporter,
    NarrativeGenerator,
    SpeechSynthesizer,
    VideoClipGenerator,
    VisualAssetGenerator,
    VisualPlanner,
)
from presentation_video.infrastructure.avatar import NoAvatarRenderer
from presentation_video.infrastructure.documents import ExtensionDocumentIngestorFactory
from presentation_video.infrastructure.narrative import (
    DebugNarrativeGenerator,
    PydanticAINarrativeGenerator,
    ReplicateNarrativeGenerator,
)
from presentation_video.infrastructure.reporting import LoggingJobReporter
from presentation_video.infrastructure.speech import (
    EspeakSpeechSynthesizer,
    PydanticAIGoogleTTSSynthesizer,
    ReplicateTTSSynthesizer,
)
from presentation_video.infrastructure.video import FfmpegSceneRenderer, FfmpegVideoAssembler
from presentation_video.infrastructure.replicate import ReplicatePredictionClient
from presentation_video.infrastructure.vertex import (
    VertexClientFactory,
    VertexImageAssetGenerator,
    VertexSpeechSynthesizer,
    VertexVideoAssetGenerator,
)
from presentation_video.infrastructure.visual_media import (
    FfmpegImageAnimator,
    ReplicateImageAssetGenerator,
    ReplicateVideoAssetGenerator,
    SlideVisualAssetGenerator,
)
from presentation_video.infrastructure.visual_planning import (
    DebugVisualPlanner,
    PydanticAIVisualPlanner,
    ReplicateVisualPlanner,
)
from presentation_video.settings import Settings

logger = logging.getLogger(__name__)


def _require_google_cloud_project(settings: Settings, context: str) -> str:
    if not settings.google_cloud_project:
        raise ValueError(f"GOOGLE_CLOUD_PROJECT is required for {context}")
    return settings.google_cloud_project


def _pydantic_ai_model(model_name: str, settings: Settings) -> Any:
    """Resolve google-cloud models explicitly so ADC never falls back to an API key."""

    prefix = "google-cloud:"
    if not model_name.startswith(prefix):
        return model_name
    vertex_model_name = model_name.removeprefix(prefix).strip()
    if not vertex_model_name:
        raise ValueError("A model name is required after the 'google-cloud:' prefix")
    provider = GoogleCloudProvider(
        project=_require_google_cloud_project(settings, "Pydantic AI with Vertex AI"),
        location=settings.vertex_text_location,
    )
    return GoogleModel(vertex_model_name, provider=provider)


def build_pipeline(
    settings: Settings | None = None,
    reporter: JobReporter | None = None,
) -> CreatePresentationVideo:
    settings = settings or Settings()
    if settings.debug_mode:
        logger.warning(
            "DEBUG_MODE enabled: all external AI providers are bypassed; "
            "using deterministic narrative/planning, source-page images, local eSpeak and FFmpeg"
        )
        return CreatePresentationVideo(
            ingestor_factory=ExtensionDocumentIngestorFactory(),
            narrative_generator=DebugNarrativeGenerator(
                max_scenes=settings.debug_max_scenes,
                words_per_minute=settings.words_per_minute,
            ),
            visual_planner=DebugVisualPlanner(),
            visual_asset_generator=SlideVisualAssetGenerator(),
            video_clip_generator=FfmpegImageAnimator(),
            speech_synthesizer=EspeakSpeechSynthesizer(
                voice=settings.debug_tts_voice,
                rate=settings.tts_rate,
            ),
            avatar_renderer=NoAvatarRenderer(),
            scene_renderer=FfmpegSceneRenderer(),
            video_assembler=FfmpegVideoAssembler(),
            reporter=reporter or LoggingJobReporter(),
            work_root=settings.work_root,
            output_root=settings.output_root,
            max_parallel_scenes=settings.max_parallel_scenes,
        )
    if settings.visual_image_provider == "slide" and settings.visual_media_provider in {
        "replicate",
        "vertex_ai",
    }:
        raise ValueError(
            "VISUAL_IMAGE_PROVIDER=slide cannot be combined with "
            f"VISUAL_MEDIA_PROVIDER={settings.visual_media_provider} because source-page text "
            "would be sent to image-to-video. Use a generative image provider or "
            "VISUAL_MEDIA_PROVIDER=ffmpeg."
        )
    replicate_client = None
    if (
        settings.narrative_provider == "replicate"
        or settings.visual_planner_provider == "replicate"
        or settings.visual_image_provider == "replicate"
        or settings.visual_media_provider == "replicate"
        or settings.tts_provider == "replicate"
    ):
        replicate_client = ReplicatePredictionClient(
            settings.replicate_api_token or "",
            settings.replicate_poll_interval_seconds,
            settings.replicate_timeout_seconds,
        )

    vertex_client_factory = None
    if (
        settings.visual_image_provider == "vertex_ai"
        or settings.visual_media_provider == "vertex_ai"
        or settings.tts_provider == "vertex_ai"
    ):
        vertex_client_factory = VertexClientFactory(
            _require_google_cloud_project(settings, "Vertex AI providers")
        )

    narrative_generator: NarrativeGenerator
    if settings.narrative_provider == "pydantic_ai":
        narrative_generator = PydanticAINarrativeGenerator(
            _pydantic_ai_model(settings.llm_model, settings),
            settings.narrative_max_revisions,
            settings.words_per_minute,
        )
    elif settings.narrative_provider == "replicate":
        if not settings.replicate_narrative_model:
            raise ValueError("REPLICATE_NARRATIVE_MODEL is required for Replicate narration")
        assert replicate_client is not None
        narrative_generator = ReplicateNarrativeGenerator(
            replicate_client,
            settings.replicate_narrative_model,
            settings.replicate_narrative_input,
            settings.narrative_max_revisions,
            settings.words_per_minute,
        )
    else:
        raise ValueError("NARRATIVE_PROVIDER must be 'pydantic_ai' or 'replicate'")

    visual_planner: VisualPlanner
    if settings.visual_planner_provider == "pydantic_ai":
        visual_planner = PydanticAIVisualPlanner(
            _pydantic_ai_model(settings.visual_planner_model, settings)
        )
    elif settings.visual_planner_provider == "replicate":
        if not settings.replicate_planner_model:
            raise ValueError("REPLICATE_PLANNER_MODEL is required for the Replicate planner")
        assert replicate_client is not None
        visual_planner = ReplicateVisualPlanner(
            replicate_client, settings.replicate_planner_model, settings.replicate_planner_input
        )
    else:
        raise ValueError("VISUAL_PLANNER_PROVIDER must be 'pydantic_ai' or 'replicate'")

    visual_asset_generator: VisualAssetGenerator
    if settings.visual_image_provider == "slide":
        visual_asset_generator = SlideVisualAssetGenerator()
    elif settings.visual_image_provider == "replicate":
        if not settings.replicate_image_model:
            raise ValueError("REPLICATE_IMAGE_MODEL is required for Replicate image generation")
        assert replicate_client is not None
        visual_asset_generator = ReplicateImageAssetGenerator(
            replicate_client, settings.replicate_image_model, settings.replicate_image_input
        )
    elif settings.visual_image_provider == "vertex_ai":
        assert vertex_client_factory is not None
        visual_asset_generator = VertexImageAssetGenerator(
            vertex_client_factory.client(settings.vertex_image_location),
            model=settings.vertex_image_model,
            aspect_ratio=settings.vertex_image_aspect_ratio,
            image_size=settings.vertex_image_size,
            timeout_seconds=settings.vertex_image_timeout_seconds,
        )
    else:
        raise ValueError("VISUAL_IMAGE_PROVIDER must be 'slide', 'replicate', or 'vertex_ai'")

    video_clip_generator: VideoClipGenerator
    if settings.visual_media_provider == "ffmpeg":
        video_clip_generator = FfmpegImageAnimator()
    elif settings.visual_media_provider == "replicate":
        if not settings.replicate_video_model:
            raise ValueError("REPLICATE_VIDEO_MODEL is required for Replicate video generation")
        assert replicate_client is not None
        video_clip_generator = ReplicateVideoAssetGenerator(
            replicate_client,
            settings.replicate_video_model,
            settings.replicate_video_image_input_key,
            settings.replicate_video_input,
        )
    elif settings.visual_media_provider == "vertex_ai":
        assert vertex_client_factory is not None
        video_clip_generator = VertexVideoAssetGenerator(
            vertex_client_factory.client(settings.vertex_video_location),
            settings.vertex_video_output_gcs_uri,
            model=settings.vertex_video_model,
            aspect_ratio=settings.vertex_video_aspect_ratio,
            resolution=settings.vertex_video_resolution,
            clip_duration_seconds=settings.vertex_video_duration_seconds,
            poll_interval_seconds=settings.vertex_video_poll_interval_seconds,
            timeout_seconds=settings.vertex_video_timeout_seconds,
        )
    else:
        raise ValueError("VISUAL_MEDIA_PROVIDER must be 'ffmpeg', 'replicate', or 'vertex_ai'")

    speech_synthesizer: SpeechSynthesizer
    if settings.tts_provider == "pydantic_ai":
        speech_synthesizer = PydanticAIGoogleTTSSynthesizer(
            model=settings.tts_model,
            voice=settings.tts_voice,
            language_code=settings.tts_language_code,
            style_prompt=settings.tts_prompt,
            max_retries=settings.tts_max_retries,
            api_key=settings.google_api_key,
        )
    elif settings.tts_provider == "replicate":
        if not settings.replicate_tts_model:
            raise ValueError("REPLICATE_TTS_MODEL is required for Replicate TTS")
        assert replicate_client is not None
        speech_synthesizer = ReplicateTTSSynthesizer(
            replicate_client,
            settings.replicate_tts_model,
            settings.replicate_tts_input,
            voice=settings.tts_voice,
            language_code=settings.tts_language_code,
            style_prompt=settings.tts_prompt,
            max_retries=settings.tts_max_retries,
        )
    elif settings.tts_provider == "espeak":
        speech_synthesizer = EspeakSpeechSynthesizer(
            voice=settings.tts_voice,
            rate=settings.tts_rate,
        )
    elif settings.tts_provider == "vertex_ai":
        assert vertex_client_factory is not None
        speech_synthesizer = VertexSpeechSynthesizer(
            vertex_client_factory.client(settings.vertex_tts_location),
            model=settings.tts_model.removeprefix("google-cloud:"),
            voice=settings.tts_voice,
            language_code=settings.tts_language_code,
            style_prompt=settings.tts_prompt,
            max_retries=settings.tts_max_retries,
            timeout_seconds=settings.vertex_tts_timeout_seconds,
        )
    else:
        raise ValueError(
            "TTS_PROVIDER must be 'pydantic_ai', 'replicate', 'vertex_ai', or 'espeak'"
        )

    return CreatePresentationVideo(
        ingestor_factory=ExtensionDocumentIngestorFactory(),
        narrative_generator=narrative_generator,
        visual_planner=visual_planner,
        visual_asset_generator=visual_asset_generator,
        video_clip_generator=video_clip_generator,
        speech_synthesizer=speech_synthesizer,
        avatar_renderer=NoAvatarRenderer(),
        scene_renderer=FfmpegSceneRenderer(),
        video_assembler=FfmpegVideoAssembler(),
        reporter=reporter or LoggingJobReporter(),
        work_root=settings.work_root,
        output_root=settings.output_root,
        max_parallel_scenes=settings.max_parallel_scenes,
    )
