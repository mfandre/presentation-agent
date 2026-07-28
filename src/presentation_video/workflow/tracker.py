from __future__ import annotations

from typing import Any

from presentation_video.domain.models import JobStatus
from presentation_video.workflow.models import (
    RunStatus,
    StepStatus,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowSnapshot,
)
from presentation_video.workflow.ports import WorkflowStateRepository

_STATUS_TO_STEP: dict[JobStatus, str] = {
    JobStatus.INGESTING: "ingest",
    JobStatus.SCRIPTING: "narrative",
    JobStatus.DURATION_VALIDATING: "duration_validate",
    JobStatus.AWAITING_DURATION_APPROVAL: "duration_review",
    JobStatus.SYNTHESIZING: "speech",
    JobStatus.SCENE_PLANNING: "scene_plan",
    JobStatus.VISUAL_PLANNING: "visual_plan",
    JobStatus.PROMPT_COMPILING: "prompt_compile",
    JobStatus.RULE_VALIDATING: "rule_validate",
    JobStatus.GENERATING_IMAGES: "generate_images",
    JobStatus.AWAITING_VISUAL_APPROVAL: "visual_review",
    JobStatus.GENERATING_VIDEO: "animate",
    JobStatus.VISUAL_QA: "visual_qa",
    JobStatus.RENDERING: "render",
    JobStatus.ASSEMBLING: "assemble",
    JobStatus.CAPTIONING: "captions",
}


class WorkflowJobTracker:
    """Projects legacy pipeline progress into persistent workflow state."""

    def __init__(
        self,
        repository: WorkflowStateRepository,
        definition: WorkflowDefinition,
    ) -> None:
        self._repository = repository
        self._definition = definition
        self._order = [step.id for step in definition.steps]

    def _snapshot_order(self, snapshot: WorkflowSnapshot) -> list[str]:
        if snapshot.definition is not None:
            return [step.id for step in snapshot.definition.steps]
        return [step.step_id for step in snapshot.steps]

    def initialize(self, job_id: str, inputs: dict[str, Any]) -> WorkflowSnapshot:
        existing = self._repository.get(job_id)
        if existing is None:
            self._repository.initialize(
                WorkflowRun(
                    run_id=job_id,
                    workflow_id=self._definition.id,
                    workflow_version=self._definition.version,
                    status=RunStatus.PENDING,
                    inputs=inputs,
                ),
                self._definition,
            )
        snapshot = self._repository.get(job_id)
        assert snapshot is not None
        return snapshot

    async def update(self, job_id: str, status: JobStatus, detail: str = "") -> None:
        snapshot = self._repository.get(job_id)
        if snapshot is None:
            return
        if status in {JobStatus.FAILED, JobStatus.CANCELLED}:
            running = next(
                (step for step in snapshot.steps if step.status == StepStatus.RUNNING),
                None,
            )
            if running is not None:
                self._repository.set_step_status(
                    job_id, running.step_id, StepStatus.FAILED, error=detail
                )
            self._repository.set_run_status(
                job_id,
                RunStatus.FAILED if status == JobStatus.FAILED else RunStatus.CANCELLED,
                error=detail,
            )
            return
        if status == JobStatus.COMPLETED:
            for step in snapshot.steps:
                if step.status not in {StepStatus.COMPLETED, StepStatus.SKIPPED}:
                    self._repository.set_step_status(job_id, step.step_id, StepStatus.COMPLETED)
            self._repository.set_run_status(job_id, RunStatus.COMPLETED)
            return
        order = self._snapshot_order(snapshot)
        step_id = _STATUS_TO_STEP.get(status)
        if step_id is None or step_id not in order:
            return
        target_index = order.index(step_id)
        states = {step.step_id: step.status for step in snapshot.steps}
        for previous in order[:target_index]:
            if states.get(previous) not in {StepStatus.COMPLETED, StepStatus.SKIPPED}:
                self._repository.set_step_status(job_id, previous, StepStatus.COMPLETED)
        if status in {
            JobStatus.AWAITING_DURATION_APPROVAL,
            JobStatus.AWAITING_VISUAL_APPROVAL,
        }:
            self._repository.set_step_status(job_id, step_id, StepStatus.WAITING)
            self._repository.set_run_status(job_id, RunStatus.WAITING)
        else:
            current = next(
                (step for step in snapshot.steps if step.step_id == step_id),
                None,
            )
            attempt = (
                (current.attempt + 1)
                if current is not None and current.status != StepStatus.RUNNING
                else None
            )
            self._repository.set_step_status(job_id, step_id, StepStatus.RUNNING, attempt=attempt)
            self._repository.set_run_status(job_id, RunStatus.RUNNING)
        self._repository.add_event(
            job_id,
            "job.progress",
            {"job_status": status.value, "step_id": step_id, "detail": detail},
        )

    def approve(self, job_id: str) -> None:
        snapshot = self._repository.get(job_id)
        if snapshot is None:
            return
        self._repository.set_step_status(job_id, "visual_review", StepStatus.COMPLETED)
        self._repository.set_run_status(job_id, RunStatus.RUNNING)
        self._repository.add_event(job_id, "checkpoint.approved", {"step_id": "visual_review"})

    def resolve_duration(self, job_id: str, decision: str) -> None:
        snapshot = self._repository.get(job_id)
        if snapshot is None:
            return
        self._repository.set_step_status(
            job_id,
            "duration_review",
            StepStatus.COMPLETED,
            outputs={"decision": decision},
        )
        self._repository.set_run_status(job_id, RunStatus.RUNNING)
        self._repository.add_event(
            job_id,
            "checkpoint.duration_resolved",
            {"step_id": "duration_review", "decision": decision},
        )

    def snapshot(self, job_id: str) -> WorkflowSnapshot | None:
        return self._repository.get(job_id)
