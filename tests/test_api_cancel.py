import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from presentation_video import api
from presentation_video.domain.models import JobStatus


@pytest.mark.asyncio
async def test_cancel_endpoint_stops_active_task_and_marks_terminal_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = "a" * 32
    started = asyncio.Event()

    async def background_work() -> None:
        started.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(background_work())
    await started.wait()
    now = datetime.now(UTC)
    record = api.JobRecord(
        view=api.JobView(
            job_id=job_id,
            status=JobStatus.GENERATING_VIDEO,
            progress_percent=70,
            detail="Gerando clipes",
            file_name="training.pdf",
            target_seconds=60,
            language="pt-BR",
            audience="training",
            tone="didactic",
            created_at=now,
            updated_at=now,
            start_datetime=now,
        ),
        source_path=tmp_path / "training.pdf",
        active_task=task,
    )
    updates: list[tuple[str, JobStatus, str]] = []

    async def update(job: str, status: JobStatus, detail: str) -> None:
        updates.append((job, status, detail))

    monkeypatch.setattr(api.workflow_tracker, "update", update)
    async with api._jobs_lock:
        api._jobs[job_id] = record
    try:
        view = await api.cancel_video(job_id)
    finally:
        async with api._jobs_lock:
            api._jobs.pop(job_id, None)

    assert task.cancelled()
    assert view.status == JobStatus.CANCELLED
    assert view.end_datetime is not None
    assert updates == [
        (job_id, JobStatus.CANCELLED, "Processamento cancelado pelo usuário")
    ]
