import pytest
import httpx

from presentation_video.domain.models import PresentationVisualPlan, VisualScenePlan
from presentation_video.infrastructure.replicate import ReplicatePredictionClient


def test_visual_plan_requires_at_least_one_scene() -> None:
    with pytest.raises(ValueError):
        PresentationVisualPlan(scenes=[])


def test_visual_scene_has_safe_negative_prompt() -> None:
    scene = VisualScenePlan(
        scene_number=1,
        source_slide_numbers=[1],
        prompt="A scientist examining a sample",
    )
    assert "watermarks" in scene.negative_prompt


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("hello", "hello"),
        (["hel", "lo"], "hello"),
        ({"text": "hello"}, "hello"),
    ],
)
def test_replicate_output_text(output: object, expected: str) -> None:
    assert ReplicatePredictionClient.output_text(output) == expected


def test_replicate_output_url_accepts_common_shapes() -> None:
    assert ReplicatePredictionClient.output_url({"video": "https://example.test/a.mp4"}).endswith(
        ".mp4"
    )


def test_replicate_http_error_includes_api_response_body() -> None:
    request = httpx.Request("POST", "https://api.replicate.com/v1/predictions")
    response = httpx.Response(422, request=request, json={"detail": "invalid aspect_ratio"})

    with pytest.raises(RuntimeError, match="invalid aspect_ratio"):
        ReplicatePredictionClient._raise_for_status(response)
