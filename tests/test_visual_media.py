from pathlib import Path

import pytest
from PIL import Image

from presentation_video.domain.models import (
    MediaMode,
    SlideContent,
    VisualArtifact,
    VisualScenePlan,
)
from presentation_video.infrastructure.visual_media import (
    ReplicateVideoAssetGenerator,
    _local_motion_filter,
    _is_sensitive_generation_error,
    _sanitize_video_inputs,
    _visual_prompt,
)
from presentation_video.infrastructure.video_capabilities import video_model_capabilities


class ReplicateVideoClient:
    def __init__(self) -> None:
        self.model = ""
        self.inputs: dict[str, object] = {}

    async def run(self, model: str, inputs: dict[str, object]) -> str:
        self.model = model
        self.inputs = inputs
        return "https://example.test/storyboard.mp4"

    @staticmethod
    def output_url(_output: object) -> str:
        return "https://example.test/storyboard.mp4"

    @staticmethod
    async def download(_url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"video")


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

    assert (
        "Required concepts that must be visually unmistakable: Agentic AI, human governance"
        in prompt
    )
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


@pytest.mark.parametrize(
    "model",
    [
        "bytedance/seedance-2.0",
        "bytedance/seedance-2.0-fast",
        "bytedance/seedance-2.0-mini",
        "bytedance/seedance-2.0:version-id",
    ],
)
def test_storyboard_capability_is_selected_by_model(model: str) -> None:
    capabilities = video_model_capabilities(model)

    assert capabilities.supports_storyboard_reference
    assert capabilities.supports_multishot
    assert capabilities.minimum_output_seconds == (
        4 if model == "bytedance/seedance-2.0-fast" else 2
    )
    assert capabilities.maximum_output_seconds == 15
    assert capabilities.maximum_reference_images == 9


def test_replicate_provider_does_not_imply_storyboard_capability() -> None:
    capabilities = video_model_capabilities("google/veo-3.1-lite")

    assert not capabilities.supports_storyboard_reference
    assert not capabilities.supports_multishot
    assert capabilities.minimum_output_seconds == 4


@pytest.mark.asyncio
async def test_seedance_on_replicate_receives_only_the_storyboard(
    tmp_path: Path,
) -> None:
    storyboard_path = tmp_path / "storyboard.jpg"
    Image.new("RGB", (1280, 720), "white").save(storyboard_path)
    client = ReplicateVideoClient()
    generator = ReplicateVideoAssetGenerator(
        client,  # type: ignore[arg-type]
        "bytedance/seedance-2.0",
        input_defaults={
            "aspect_ratio": "16:9",
            "image": "must-be-removed",
            "last_frame_image": "must-be-removed",
            "generate_audio": True,
        },
    )
    plans = [
        VisualScenePlan(
            scene_number=1,
            shot_number=1,
            prompt="Lina enters the workshop",
            focal_action="Lina enters the workshop",
            media_mode=MediaMode.VIDEO,
        ),
        VisualScenePlan(
            scene_number=1,
            shot_number=2,
            prompt="Lina starts the autonomous machine",
            focal_action="Lina starts the autonomous machine",
            media_mode=MediaMode.VIDEO,
        ),
    ]

    result = await generator.animate_storyboard(
        plans,
        VisualArtifact(
            scene_number=1,
            shot_number=1,
            path=storyboard_path,
            kind="image",
        ),
        tmp_path / "clips",
        duration_seconds=12.2,
        segment_number=1,
    )

    assert client.model == "bytedance/seedance-2.0"
    assert client.inputs["duration"] == 13
    assert client.inputs["generate_audio"] is False
    assert "image" not in client.inputs
    assert "last_frame_image" not in client.inputs
    references = client.inputs["reference_images"]
    assert isinstance(references, list)
    assert len(references) == 1
    assert all(str(reference).startswith("data:image/") for reference in references)
    assert "[Image1] is a clean storyboard grid" in str(client.inputs["prompt"])
    assert "canonical sheet" not in str(client.inputs["prompt"])
    assert result.path.name == "scene-001-storyboard-segment-001.mp4"


@pytest.mark.asyncio
async def test_veo_on_replicate_rejects_direct_storyboard_input(tmp_path: Path) -> None:
    storyboard_path = tmp_path / "storyboard.jpg"
    Image.new("RGB", (1280, 720), "white").save(storyboard_path)
    generator = ReplicateVideoAssetGenerator(
        ReplicateVideoClient(),  # type: ignore[arg-type]
        "google/veo-3.1-lite",
    )

    with pytest.raises(ValueError, match="does not support storyboard multi-shot"):
        await generator.animate_storyboard(
            [
                VisualScenePlan(
                    scene_number=1,
                    prompt="A single shot",
                    media_mode=MediaMode.VIDEO,
                )
            ],
            VisualArtifact(
                scene_number=1,
                path=storyboard_path,
                kind="image",
            ),
            tmp_path / "clips",
            duration_seconds=8,
            segment_number=1,
        )


@pytest.mark.asyncio
async def test_veo_on_replicate_receives_first_and_last_storyboard_panels(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.jpg"
    last_path = tmp_path / "last.jpg"
    Image.new("RGB", (1280, 720), "white").save(first_path)
    Image.new("RGB", (1280, 720), "black").save(last_path)
    client = ReplicateVideoClient()
    generator = ReplicateVideoAssetGenerator(
        client,  # type: ignore[arg-type]
        "google/veo-3.1-lite",
        input_defaults={"aspect_ratio": "16:9", "duration": 8},
    )

    await generator.animate(
        VisualScenePlan(
            scene_number=1,
            shot_number=1,
            prompt="Lina crosses the workshop",
            focal_action="Lina crosses the workshop",
            media_mode=MediaMode.VIDEO,
        ),
        VisualArtifact(
            scene_number=1,
            shot_number=1,
            start_path=first_path,
            path=last_path,
            kind="image",
        ),
        tmp_path / "clips",
        duration_seconds=2,
    )

    assert client.inputs["duration"] == 4
    assert str(client.inputs["image"]).startswith("data:image/jpeg;base64,")
    assert str(client.inputs["last_frame"]).startswith("data:image/jpeg;base64,")
    assert client.inputs["image"] != client.inputs["last_frame"]
    assert "exact opening frame" in str(client.inputs["prompt"])
    assert "exact ending frame" in str(client.inputs["prompt"])


@pytest.mark.asyncio
async def test_seedance_fast_animation_uses_480p_without_native_audio(
    tmp_path: Path,
) -> None:
    first_path = tmp_path / "first.jpg"
    last_path = tmp_path / "last.jpg"
    Image.new("RGB", (864, 496), "white").save(first_path)
    Image.new("RGB", (864, 496), "black").save(last_path)
    client = ReplicateVideoClient()
    generator = ReplicateVideoAssetGenerator(
        client,  # type: ignore[arg-type]
        "bytedance/seedance-2.0-fast",
        input_defaults={
            "aspect_ratio": "16:9",
            "duration": 8,
            "resolution": "480p",
            "generate_audio": False,
        },
    )

    await generator.animate(
        VisualScenePlan(
            scene_number=1,
            shot_number=1,
            prompt="Lina crosses the workshop",
            focal_action="Lina crosses the workshop",
            media_mode=MediaMode.VIDEO,
        ),
        VisualArtifact(
            scene_number=1,
            shot_number=1,
            start_path=first_path,
            path=last_path,
            kind="image",
        ),
        tmp_path / "clips",
        duration_seconds=2,
    )

    assert client.model == "bytedance/seedance-2.0-fast"
    assert client.inputs["duration"] == 4
    assert client.inputs["resolution"] == "480p"
    assert client.inputs["generate_audio"] is False
    assert str(client.inputs["image"]).startswith("data:image/jpeg;base64,")
    assert str(client.inputs["last_frame_image"]).startswith("data:image/jpeg;base64,")


@pytest.mark.asyncio
async def test_seedance_fast_short_storyboard_is_clamped_to_four_seconds(
    tmp_path: Path,
) -> None:
    storyboard_path = tmp_path / "storyboard.jpg"
    Image.new("RGB", (864, 496), "white").save(storyboard_path)
    client = ReplicateVideoClient()
    generator = ReplicateVideoAssetGenerator(
        client,  # type: ignore[arg-type]
        "bytedance/seedance-2.0-fast",
        input_defaults={
            "resolution": "480p",
            "generate_audio": False,
        },
    )

    await generator.animate_storyboard(
        [
            VisualScenePlan(
                scene_number=1,
                shot_number=1,
                prompt="A short establishing shot",
                focal_action="Lina enters the workshop",
                media_mode=MediaMode.VIDEO,
            )
        ],
        VisualArtifact(
            scene_number=1,
            shot_number=1,
            path=storyboard_path,
            kind="image",
        ),
        tmp_path / "clips",
        duration_seconds=2,
        segment_number=1,
    )

    assert client.inputs["duration"] == 4
    assert client.inputs["resolution"] == "480p"
    assert client.inputs["generate_audio"] is False
