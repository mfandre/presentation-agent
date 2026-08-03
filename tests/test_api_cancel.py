import asyncio
from datetime import UTC, datetime
from pathlib import Path

import pytest

from presentation_video import api
from presentation_video.domain.models import JobStatus
from presentation_video.workflow.models import (
    RunStatus,
    StepRun,
    StepStatus,
    WorkflowRun,
    WorkflowSnapshot,
)


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


@pytest.mark.asyncio
async def test_cancel_endpoint_cancels_job_waiting_for_visual_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = "b" * 32
    now = datetime.now(UTC)
    record = api.JobRecord(
        view=api.JobView(
            job_id=job_id,
            status=JobStatus.AWAITING_VISUAL_APPROVAL,
            progress_percent=55,
            detail="Revise e aprove as cenas",
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

    assert view.status == JobStatus.CANCELLED
    assert view.end_datetime is not None
    assert record.active_task is None
    assert updates == [
        (job_id, JobStatus.CANCELLED, "Processamento cancelado pelo usuário")
    ]


def test_recover_cancelled_workflow_does_not_restore_visual_approval(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job_id = "c" * 32
    now = datetime.now(UTC)
    source_path = tmp_path / "training.pdf"
    snapshot = WorkflowSnapshot(
        run=WorkflowRun(
            run_id=job_id,
            workflow_id="presentation-video",
            workflow_version="2.9.0",
            status=RunStatus.CANCELLED,
            inputs={"source_path": str(source_path)},
            error="Processamento cancelado pelo usuário",
            created_at=now,
            updated_at=now,
            start_datetime=now,
            end_datetime=now,
        ),
        steps=[
            StepRun(
                run_id=job_id,
                step_id="visual_review",
                uses="human.approval",
                status=StepStatus.WAITING,
            )
        ],
    )
    monkeypatch.setattr(api.workflow_tracker, "snapshot", lambda _: snapshot)

    recovered = api._recover_workflow_job(job_id)

    assert recovered is not None
    assert recovered.view.status == JobStatus.CANCELLED
    assert recovered.view.detail == "Processamento cancelado pelo usuário"
    assert recovered.view.end_datetime == now
