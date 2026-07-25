from typing import Any, cast

import pytest
import httpx

from presentation_video.domain.models import PresentationVisualPlan, VisualScenePlan
from presentation_video.infrastructure.replicate import ReplicateAPIError, ReplicatePredictionClient


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


def test_replicate_rate_limit_error_preserves_retry_after() -> None:
    request = httpx.Request("POST", "https://api.replicate.com/v1/predictions")
    response = httpx.Response(
        429,
        request=request,
        json={"detail": "Request was throttled", "retry_after": 7},
    )

    with pytest.raises(ReplicateAPIError) as caught:
        ReplicatePredictionClient._raise_for_status(response)

    assert caught.value.status_code == 429
    assert caught.value.retry_after_seconds == 7


class FakePredictionHTTPClient:
    def __init__(self) -> None:
        self.calls = 0

    async def post(self, *args: object, **kwargs: object) -> httpx.Response:
        self.calls += 1
        request = httpx.Request("POST", "https://api.replicate.com/v1/predictions")
        if self.calls == 1:
            return httpx.Response(
                429,
                request=request,
                json={"detail": "Request was throttled", "retry_after": 10},
            )
        return httpx.Response(
            201,
            request=request,
            json={"id": "prediction-1", "status": "succeeded", "output": "ok"},
        )


@pytest.mark.asyncio
async def test_replicate_creation_retries_all_models_after_server_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("presentation_video.infrastructure.replicate.asyncio.sleep", fake_sleep)
    client = ReplicatePredictionClient("token")
    http_client = FakePredictionHTTPClient()

    response = await client._create_prediction(
        cast(Any, http_client),
        "https://api.replicate.com/v1/models/openai/gpt-image-2/predictions",
        {"input": {"prompt": "test"}},
        "openai/gpt-image-2",
    )

    assert response.status_code == 201
    assert http_client.calls == 2
    assert delays == [10.5]
