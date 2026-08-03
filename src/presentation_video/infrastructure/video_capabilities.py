from __future__ import annotations

import math

from presentation_video.domain.models import VideoGeneratorCapabilities


_MODEL_CAPABILITIES: dict[str, VideoGeneratorCapabilities] = {
    "google/veo-3.1": VideoGeneratorCapabilities(
        minimum_output_seconds=4,
        maximum_output_seconds=8,
        supports_first_frame=True,
        supports_last_frame=True,
    ),
    "google/veo-3.1-fast": VideoGeneratorCapabilities(
        minimum_output_seconds=4,
        maximum_output_seconds=8,
        supports_first_frame=True,
        supports_last_frame=True,
    ),
    "google/veo-3.1-lite": VideoGeneratorCapabilities(
        minimum_output_seconds=4,
        maximum_output_seconds=8,
        supports_first_frame=True,
        supports_last_frame=True,
    ),
    "bytedance/seedance-2.0": VideoGeneratorCapabilities(
        supports_storyboard_reference=True,
        supports_multishot=True,
        minimum_output_seconds=2,
        maximum_output_seconds=15,
        maximum_reference_images=9,
        supports_first_frame=True,
        supports_last_frame=True,
    ),
    "bytedance/seedance-2.0-fast": VideoGeneratorCapabilities(
        supports_storyboard_reference=True,
        supports_multishot=True,
        # The public schema represents intelligent duration as -1, but the model's
        # runtime rejects explicit values from 0 through 3 with E006.
        minimum_output_seconds=4,
        maximum_output_seconds=15,
        maximum_reference_images=9,
        supports_first_frame=True,
        supports_last_frame=True,
    ),
    "bytedance/seedance-2.0-mini": VideoGeneratorCapabilities(
        supports_storyboard_reference=True,
        supports_multishot=True,
        minimum_output_seconds=2,
        maximum_output_seconds=15,
        maximum_reference_images=9,
        supports_first_frame=True,
        supports_last_frame=True,
    ),
}

_LAST_FRAME_INPUT_KEYS = {
    "google/veo-3.1": "last_frame",
    "google/veo-3.1-fast": "last_frame",
    "google/veo-3.1-lite": "last_frame",
    "bytedance/seedance-2.0": "last_frame_image",
    "bytedance/seedance-2.0-fast": "last_frame_image",
    "bytedance/seedance-2.0-mini": "last_frame_image",
}

_DISCRETE_OUTPUT_SECONDS = {
    "google/veo-3.1": (4, 6, 8),
    "google/veo-3.1-fast": (4, 6, 8),
    "google/veo-3.1-lite": (4, 6, 8),
}


def video_model_capabilities(model: str) -> VideoGeneratorCapabilities:
    """Return capabilities for a concrete model, independently of its provider."""

    normalized = model.strip().lower().partition(":")[0]
    configured = _MODEL_CAPABILITIES.get(normalized)
    if configured is not None:
        return configured.model_copy(deep=True)
    return VideoGeneratorCapabilities()


def video_model_last_frame_input_key(model: str) -> str | None:
    """Return the model-specific last-frame field expected by its API schema."""

    normalized = model.strip().lower().partition(":")[0]
    return _LAST_FRAME_INPUT_KEYS.get(normalized)


def video_model_output_duration(model: str, requested_seconds: float) -> int | None:
    """Select the shortest model-supported duration that covers one planned take."""

    normalized = model.strip().lower().partition(":")[0]
    capabilities = _MODEL_CAPABILITIES.get(normalized)
    if capabilities is None:
        return None
    requested = min(
        max(requested_seconds, capabilities.minimum_output_seconds),
        capabilities.maximum_output_seconds,
    )
    discrete = _DISCRETE_OUTPUT_SECONDS.get(normalized)
    if discrete is not None:
        return next((seconds for seconds in discrete if seconds >= requested), discrete[-1])
    return math.ceil(requested)
