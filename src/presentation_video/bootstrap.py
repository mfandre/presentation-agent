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
from presentation_video.workflow.config import StepRuntimeConfig, WorkflowRuntimeConfig
from presentation_video.workflow.loader import WorkflowLoader
from presentation_video.workflow.models import WorkflowDefinition

logger = logging.getLogger(__name__)


def _require_google_cloud_project(settings: Settings, context: str) -> str:
    if not settings.google_cloud_project:
        raise ValueError(f"GOOGLE_CLOUD_PROJECT is required for {context}")
    return settings.google_cloud_project


def _pydantic_ai_model(model_name: str, settings: Settings, location: str = "global") -> Any:
    """Resolve google-cloud models explicitly so ADC never falls back to an API key."""

    prefix = "google-cloud:"
    if not model_name.startswith(prefix):
        return model_name
    vertex_model_name = model_name.removeprefix(prefix).strip()
    if not vertex_model_name:
        raise ValueError("A model name is required after the 'google-cloud:' prefix")
    provider = GoogleCloudProvider(
        project=_require_google_cloud_project(settings, "Pydantic AI with Vertex AI"),
        location=location,
    )
    return GoogleModel(vertex_model_name, provider=provider)


def build_pipeline(
    settings: Settings | None = None,
    reporter: JobReporter | None = None,
    workflow: WorkflowDefinition | None = None,
) -> CreatePresentationVideo:
    settings = settings or Settings()
    workflow = workflow or WorkflowLoader(settings.workflow_root).load(settings.default_workflow)
    runtime = WorkflowRuntimeConfig(workflow)
    narrative_config = runtime.step("narrative")
    planner_config = runtime.step("visual_plan")
    image_config = runtime.step("generate_images")
    speech_config = runtime.step("speech")
    video_config = runtime.step("animate")
    scene_plan_config = runtime.raw("scene_plan")
    duration_config = runtime.raw("duration_validate")
    allow_duration_review = bool(duration_config.get("checkpoint_when_over_limit", True))
    maximum_shot_seconds = float(scene_plan_config.get("maximum_shot_seconds", 8))

    def replicate_client(config: StepRuntimeConfig) -> ReplicatePredictionClient:
        secret_name = config.secrets.get("api_token")
        if not secret_name:
            raise ValueError(
                "Replicate steps must declare config.secrets.api_token"
            )
        return ReplicatePredictionClient(
            settings.secret(secret_name) or "",
            config.poll_interval_seconds,
            config.timeout_seconds,
        )

    if settings.debug_mode:
        narrative_debug = runtime.raw("narrative").get("debug", {})
        speech_debug = runtime.raw("speech").get("debug", {})
        logger.warning(
            "DEBUG_MODE enabled: all external AI providers are bypassed; "
            "using deterministic narrative/planning, source-page images, local eSpeak and FFmpeg"
        )
        return CreatePresentationVideo(
            ingestor_factory=ExtensionDocumentIngestorFactory(),
            narrative_generator=DebugNarrativeGenerator(
                max_scenes=int(narrative_debug.get("max_scenes", 3)),
                words_per_minute=int(runtime.raw("narrative").get("words_per_minute", 155)),
            ),
            visual_planner=DebugVisualPlanner(),
            visual_asset_generator=SlideVisualAssetGenerator(),
            video_clip_generator=FfmpegImageAnimator(),
            speech_synthesizer=EspeakSpeechSynthesizer(
                voice=str(speech_debug.get("voice", "pt-br")),
                rate=int(runtime.raw("speech").get("rate", 155)),
            ),
            avatar_renderer=NoAvatarRenderer(),
            scene_renderer=FfmpegSceneRenderer(),
            video_assembler=FfmpegVideoAssembler(),
            reporter=reporter or LoggingJobReporter(),
            work_root=settings.work_root,
            output_root=settings.output_root,
            max_parallel_scenes=runtime.parallelism(
                "generate_images", "speech", "animate", "render"
            ),
            maximum_shot_seconds=maximum_shot_seconds,
            duration_tolerance_percent=float(duration_config.get("tolerance_percent", 5)),
            words_per_minute=int(duration_config.get("words_per_minute", 155)),
        )
    if image_config.provider == "slide" and video_config.provider in {
        "replicate",
        "vertex_ai",
    }:
        raise ValueError(
            "generate_images.provider=slide cannot be combined with "
            f"animate.provider={video_config.provider} because source-page text "
            "would be sent to image-to-video. Use a generative image provider or "
            "animate.provider=ffmpeg."
        )

    vertex_client_factory = None
    if (
        image_config.provider == "vertex_ai"
        or video_config.provider == "vertex_ai"
        or speech_config.provider == "vertex_ai"
    ):
        vertex_client_factory = VertexClientFactory(
            _require_google_cloud_project(settings, "Vertex AI providers")
        )

    narrative_generator: NarrativeGenerator
    if narrative_config.provider == "pydantic_ai":
        narrative_generator = PydanticAINarrativeGenerator(
            _pydantic_ai_model(
                narrative_config.model or "",
                settings,
                str(runtime.raw("narrative").get("location", "global")),
            ),
            int(runtime.raw("narrative").get("max_revisions", 2)),
            int(runtime.raw("narrative").get("words_per_minute", 155)),
            allow_duration_review,
        )
    elif narrative_config.provider == "replicate":
        if not narrative_config.model:
            raise ValueError("narrative.model is required for Replicate narration")
        narrative_generator = ReplicateNarrativeGenerator(
            replicate_client(narrative_config),
            narrative_config.model,
            narrative_config.model_input,
            int(runtime.raw("narrative").get("max_revisions", 2)),
            int(runtime.raw("narrative").get("words_per_minute", 155)),
            allow_duration_review,
        )
    else:
        raise ValueError("narrative.provider must be 'pydantic_ai' or 'replicate'")

    visual_planner: VisualPlanner
    if planner_config.provider == "pydantic_ai":
        visual_planner = PydanticAIVisualPlanner(
            _pydantic_ai_model(
                planner_config.model or "",
                settings,
                str(runtime.raw("visual_plan").get("location", "global")),
            )
        )
    elif planner_config.provider == "replicate":
        if not planner_config.model:
            raise ValueError("visual_plan.model is required for the Replicate planner")
        visual_planner = ReplicateVisualPlanner(
            replicate_client(planner_config),
            planner_config.model,
            planner_config.model_input,
        )
    else:
        raise ValueError("visual_plan.provider must be 'pydantic_ai' or 'replicate'")

    visual_asset_generator: VisualAssetGenerator
    if image_config.provider == "slide":
        visual_asset_generator = SlideVisualAssetGenerator()
    elif image_config.provider == "replicate":
        if not image_config.model:
            raise ValueError("generate_images.model is required for Replicate image generation")
        visual_asset_generator = ReplicateImageAssetGenerator(
            replicate_client(image_config),
            image_config.model,
            image_config.model_input,
        )
    elif image_config.provider == "vertex_ai":
        assert vertex_client_factory is not None
        visual_asset_generator = VertexImageAssetGenerator(
            vertex_client_factory.client(
                str(runtime.raw("generate_images").get("location", "global"))
            ),
            model=image_config.model or "gemini-3.1-flash-image",
            aspect_ratio=str(runtime.raw("generate_images").get("aspect_ratio", "16:9")),
            image_size=str(runtime.raw("generate_images").get("image_size", "2K")),
            timeout_seconds=image_config.timeout_seconds,
        )
    else:
        raise ValueError("generate_images.provider must be 'slide', 'replicate', or 'vertex_ai'")

    video_clip_generator: VideoClipGenerator
    if video_config.provider == "ffmpeg":
        video_clip_generator = FfmpegImageAnimator()
    elif video_config.provider == "replicate":
        if not video_config.model:
            raise ValueError("animate.model is required for Replicate video generation")
        video_clip_generator = ReplicateVideoAssetGenerator(
            replicate_client(video_config),
            video_config.model,
            str(runtime.raw("animate").get("image_input_key", "image")),
            video_config.model_input,
        )
    elif video_config.provider == "vertex_ai":
        assert vertex_client_factory is not None
        video_clip_generator = VertexVideoAssetGenerator(
            vertex_client_factory.client(
                str(runtime.raw("animate").get("location", "us-central1"))
            ),
            settings.vertex_video_output_gcs_uri,
            model=video_config.model or "veo-3.1-fast-generate-001",
            aspect_ratio=str(runtime.raw("animate").get("aspect_ratio", "16:9")),
            resolution=str(runtime.raw("animate").get("resolution", "720p")),
            clip_duration_seconds=int(runtime.raw("animate").get("duration_seconds", 8)),
            poll_interval_seconds=video_config.poll_interval_seconds,
            timeout_seconds=video_config.timeout_seconds,
        )
    else:
        raise ValueError("animate.provider must be 'ffmpeg', 'replicate', or 'vertex_ai'")

    speech_synthesizer: SpeechSynthesizer
    speech_raw = runtime.raw("speech")
    if speech_config.provider == "pydantic_ai":
        speech_synthesizer = PydanticAIGoogleTTSSynthesizer(
            model=speech_config.model or "",
            voice=str(speech_raw.get("voice", "Kore")),
            language_code=str(speech_raw.get("language_code", "pt-BR")),
            style_prompt=str(speech_raw.get("style_prompt", "")),
            max_retries=int(speech_raw.get("max_retries", 2)),
            api_key=settings.google_api_key,
        )
    elif speech_config.provider == "replicate":
        if not speech_config.model:
            raise ValueError("speech.model is required for Replicate TTS")
        speech_synthesizer = ReplicateTTSSynthesizer(
            replicate_client(speech_config),
            speech_config.model,
            speech_config.model_input,
            voice=str(speech_raw.get("voice", "Kore")),
            language_code=str(speech_raw.get("language_code", "pt-BR")),
            style_prompt=str(speech_raw.get("style_prompt", "")),
            max_retries=int(speech_raw.get("max_retries", 2)),
        )
    elif speech_config.provider == "espeak":
        speech_synthesizer = EspeakSpeechSynthesizer(
            voice=str(speech_raw.get("voice", "pt-br")),
            rate=int(speech_raw.get("rate", 155)),
        )
    elif speech_config.provider == "vertex_ai":
        assert vertex_client_factory is not None
        speech_synthesizer = VertexSpeechSynthesizer(
            vertex_client_factory.client(str(speech_raw.get("location", "global"))),
            model=(speech_config.model or "").removeprefix("google-cloud:"),
            voice=str(speech_raw.get("voice", "Kore")),
            language_code=str(speech_raw.get("language_code", "pt-BR")),
            style_prompt=str(speech_raw.get("style_prompt", "")),
            max_retries=int(speech_raw.get("max_retries", 2)),
            timeout_seconds=speech_config.timeout_seconds,
        )
    else:
        raise ValueError(
            "speech.provider must be 'pydantic_ai', 'replicate', 'vertex_ai', or 'espeak'"
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
        max_parallel_scenes=runtime.parallelism("generate_images", "speech", "animate", "render"),
        maximum_shot_seconds=maximum_shot_seconds,
        duration_tolerance_percent=float(duration_config.get("tolerance_percent", 5)),
        words_per_minute=int(duration_config.get("words_per_minute", 155)),
    )
