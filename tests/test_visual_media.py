from pathlib import Path

from presentation_video.domain.models import MediaMode, SlideContent, VisualScenePlan
from presentation_video.infrastructure.visual_media import (
    _local_motion_filter,
    _is_sensitive_generation_error,
    _sanitize_video_inputs,
    _visual_prompt,
)


def test_visual_prompt_is_didactic_and_grounded_in_slide_content() -> None:
    plan = VisualScenePlan(
        scene_number=1,
        source_slide_numbers=[1],
        prompt="Show how governed data moves from ingestion to analytics",
        media_mode=MediaMode.VIDEO,
    )
    slide = SlideContent(
        number=1,
        title="Governança de dados",
        body_text="Catálogo central, políticas de acesso e linhagem ponta a ponta.",
        image_path=Path("slide.png"),
    )

    prompt = _visual_prompt(plan, [slide])

    assert "grounded, plausible real-world image" in prompt
    assert "Governança de dados" in prompt
    assert "Catálogo central" in prompt
    assert "Never create an isometric view, 3D diorama, miniature" in prompt
    assert "show no words, letters, numbers" in prompt
    assert "fake interface copy" in prompt
    assert "clean non-readable software workflow is allowed" in prompt


def test_visual_prompt_requires_agentic_ai_instead_of_generic_industry() -> None:
    plan = VisualScenePlan(
        scene_number=1,
        source_slide_numbers=[1],
        prompt="Show an Agentic AI operating model over governed data",
        media_mode=MediaMode.VIDEO,
        must_show_concepts=["Agentic AI", "human governance"],
        concept_visualization=(
            "Show distinct software agents handing tasks to tools and a human approval checkpoint"
        ),
    )

    prompt = _visual_prompt(plan)

    assert "Required concepts that must be visually unmistakable: Agentic AI, human governance" in prompt
    assert "distinct software agents handing tasks to tools" in prompt
    assert "Do not substitute these concepts with generic teamwork" in prompt
    assert "Never depict AI as a robot" in prompt
    assert "exactly one location" in prompt
    assert "strict whitelist" in prompt
    assert "No public figures, recognizable real people" in prompt


def test_local_motion_presets_compile_to_distinct_zoompan_choreography() -> None:
    slow_push = _local_motion_filter("slow_push", 180)
    pan_right = _local_motion_filter("pan_right", 180)
    pull_back = _local_motion_filter("pull_back", 180)

    assert slow_push.startswith("zoompan=")
    assert pan_right.startswith("zoompan=")
    assert pull_back.startswith("zoompan=")
    assert len({slow_push, pan_right, pull_back}) == 3
    assert "on/179" in pan_right


def test_veo_lite_removes_inputs_from_other_model_schemas() -> None:
    inputs = _sanitize_video_inputs(
        "google/veo-3.1-lite",
        {
            "aspect_ratio": "16:9",
            "duration": 8,
            "resolution": "1080p",
            "generate_audio": False,
            "draft": True,
        },
    )

    assert inputs == {
        "aspect_ratio": "16:9",
        "duration": 8,
        "resolution": "1080p",
    }


def test_sensitive_video_failure_is_identified_for_local_fallback() -> None:
    error = RuntimeError(
        "Prediction failed: The output was flagged as sensitive. Please try again. (E005)"
    )

    assert _is_sensitive_generation_error(error)
    assert not _is_sensitive_generation_error(RuntimeError("upstream service unavailable"))
