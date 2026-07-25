from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ReplicateAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after_seconds = retry_after_seconds


class ReplicatePredictionClient:
    def __init__(
        self,
        api_token: str,
        poll_interval_seconds: float = 2.0,
        timeout_seconds: int = 900,
    ) -> None:
        if not api_token:
            raise ValueError("REPLICATE_API_TOKEN is required")
        self._headers = {"Authorization": f"Bearer {api_token}", "Content-Type": "application/json"}
        self._poll_interval = poll_interval_seconds
        self._timeout = timeout_seconds
        self._creation_lock = asyncio.Lock()
        self._max_creation_retries = 5

    async def run(self, model: str, inputs: dict[str, object]) -> Any:
        model_name, separator, version = model.partition(":")
        parts = model_name.split("/")
        if len(parts) != 2 or not all(parts):
            raise ValueError("Replicate model must use 'owner/model' or 'owner/model:version'")
        if separator:
            endpoint = "https://api.replicate.com/v1/predictions"
            body: dict[str, object] = {"version": version, "input": inputs}
        else:
            endpoint = f"https://api.replicate.com/v1/models/{parts[0]}/{parts[1]}/predictions"
            body = {"input": inputs}
        started_at = time.monotonic()
        logger.info(
            "replicate prediction creating model=%s input_keys=%s timeout_seconds=%s",
            model,
            sorted(inputs),
            self._timeout,
        )
        async with httpx.AsyncClient(timeout=70, follow_redirects=True) as client:
            response = await self._create_prediction(client, endpoint, body, model)
            prediction = response.json()
            prediction_id = prediction.get("id", "unknown")
            logger.info(
                "replicate prediction created id=%s model=%s status=%s",
                prediction_id,
                model,
                prediction.get("status"),
            )
            deadline = time.monotonic() + self._timeout
            last_status = prediction.get("status")
            last_progress_log = time.monotonic()
            while prediction.get("status") not in {"succeeded", "failed", "canceled"}:
                if time.monotonic() >= deadline:
                    raise TimeoutError(f"Replicate prediction {prediction.get('id')} timed out")
                await asyncio.sleep(self._poll_interval)
                get_url = prediction.get("urls", {}).get("get")
                if not get_url:
                    raise RuntimeError("Replicate response did not include urls.get")
                poll = await client.get(get_url, headers=self._headers)
                self._raise_for_status(poll)
                prediction = poll.json()
                current_status = prediction.get("status")
                now = time.monotonic()
                if current_status != last_status or now - last_progress_log >= 15:
                    logger.info(
                        "replicate prediction polling id=%s model=%s status=%s elapsed_seconds=%.1f",
                        prediction_id,
                        model,
                        current_status,
                        now - started_at,
                    )
                    last_status = current_status
                    last_progress_log = now
            if prediction.get("status") != "succeeded":
                logger.error(
                    "replicate prediction terminated id=%s model=%s status=%s error=%s",
                    prediction_id,
                    model,
                    prediction.get("status"),
                    prediction.get("error"),
                )
                raise RuntimeError(
                    f"Replicate prediction {prediction.get('id')} {prediction.get('status')}: "
                    f"{prediction.get('error') or 'unknown error'}"
                )
            logger.info(
                "replicate prediction succeeded id=%s model=%s elapsed_seconds=%.1f",
                prediction_id,
                model,
                time.monotonic() - started_at,
            )
            return prediction.get("output")

    async def _create_prediction(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        body: dict[str, object],
        model: str,
    ) -> httpx.Response:
        # Replicate applies the create-prediction limit across models. A single shared
        # lock prevents image, video and TTS scene tasks from exhausting burst=1 together.
        async with self._creation_lock:
            for attempt in range(self._max_creation_retries + 1):
                response = await client.post(
                    endpoint,
                    headers={
                        **self._headers,
                        "Prefer": "wait=60",
                        "Cancel-After": f"{self._timeout}s",
                    },
                    json=body,
                )
                try:
                    self._raise_for_status(response)
                    return response
                except ReplicateAPIError as exc:
                    if exc.status_code != 429 or attempt >= self._max_creation_retries:
                        raise
                    delay = (
                        max(exc.retry_after_seconds, 0) + 0.5
                        if exc.retry_after_seconds is not None
                        else min(2**attempt, 10)
                    )
                    logger.warning(
                        "replicate prediction throttled model=%s attempt=%s next_attempt=%s "
                        "delay_seconds=%.1f",
                        model,
                        attempt + 1,
                        attempt + 2,
                        delay,
                    )
                    await asyncio.sleep(delay)
        raise RuntimeError("Unreachable Replicate prediction creation retry state")

    @staticmethod
    def output_text(output: Any) -> str:
        if isinstance(output, str):
            return output
        if isinstance(output, list) and all(isinstance(item, str) for item in output):
            return "".join(output)
        if isinstance(output, dict):
            for key in ("text", "output", "response"):
                if isinstance(output.get(key), str):
                    return output[key]
        return json.dumps(output, ensure_ascii=False)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = response.text.strip()
            if len(detail) > 2_000:
                detail = detail[:2_000] + "..."
            retry_after: float | None = None
            header_retry_after = response.headers.get("retry-after")
            if header_retry_after:
                try:
                    retry_after = float(header_retry_after)
                except ValueError:
                    pass
            try:
                payload = response.json()
                body_retry_after = payload.get("retry_after") if isinstance(payload, dict) else None
                if body_retry_after is not None:
                    retry_after = float(body_retry_after)
            except (ValueError, TypeError):
                pass
            raise ReplicateAPIError(
                f"Replicate API returned {response.status_code} for {response.request.url}: "
                f"{detail or response.reason_phrase}",
                status_code=response.status_code,
                retry_after_seconds=retry_after,
            ) from exc

    @staticmethod
    def output_url(output: Any) -> str:
        if isinstance(output, str) and output.startswith("http"):
            return output
        if isinstance(output, list):
            for item in output:
                if isinstance(item, str) and item.startswith("http"):
                    return item
        if isinstance(output, dict):
            for key in ("url", "video", "output"):
                value = output.get(key)
                if isinstance(value, str) and value.startswith("http"):
                    return value
        raise ValueError("Could not find a media URL in the Replicate output")

    async def download(self, url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                self._raise_for_status(response)
                with destination.open("wb") as output:
                    async for chunk in response.aiter_bytes():
                        output.write(chunk)
        logger.info(
            "replicate artifact downloaded destination=%s bytes=%s",
            destination,
            destination.stat().st_size,
        )
