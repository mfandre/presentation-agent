from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class RetryPolicy(BaseModel):
    attempts: int = Field(default=1, ge=1, le=20)
    backoff_seconds: float = Field(default=0, ge=0, le=300)
    exponential: bool = True


class WorkflowInputDefinition(BaseModel):
    type: str = "string"
    required: bool = False
    default: Any = None


class WorkflowStepDefinition(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    uses: str = Field(pattern=r"^[a-z][a-z0-9_.-]*$")
    needs: list[str] = Field(default_factory=list)
    inputs: dict[str, Any] = Field(default_factory=dict)
    config: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, str] = Field(default_factory=dict)
    when: bool | str | dict[str, Any] = True
    foreach: str | None = None
    parallelism: int = Field(default=1, ge=1, le=100)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    checkpoint: str | None = Field(default=None, pattern=r"^(human)?$")
    timeout_seconds: float | None = Field(default=None, gt=0, le=86400)
    continue_on_error: bool = False


class WorkflowDefinition(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    description: str = ""
    inputs: dict[str, WorkflowInputDefinition] = Field(default_factory=dict)
    settings: dict[str, Any] = Field(default_factory=dict)
    steps: list[WorkflowStepDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorkflowDefinition":
        ids = [step.id for step in self.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("workflow step ids must be unique")
        known: set[str] = set()
        for step in self.steps:
            missing = set(step.needs) - set(ids)
            if missing:
                raise ValueError(f"step {step.id} depends on unknown steps {sorted(missing)}")
            if step.id in step.needs:
                raise ValueError(f"step {step.id} cannot depend on itself")
            known.add(step.id)
        visiting: set[str] = set()
        visited: set[str] = set()
        dependencies = {step.id: step.needs for step in self.steps}

        def visit(step_id: str) -> None:
            if step_id in visiting:
                raise ValueError("workflow dependency graph contains a cycle")
            if step_id in visited:
                return
            visiting.add(step_id)
            for dependency in dependencies[step_id]:
                visit(dependency)
            visiting.remove(step_id)
            visited.add(step_id)

        for step_id in ids:
            visit(step_id)
        return self


class WorkflowRun(BaseModel):
    run_id: str
    workflow_id: str
    workflow_version: str
    status: RunStatus
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    start_datetime: datetime = Field(default_factory=lambda: datetime.now(UTC))
    end_datetime: datetime | None = None


class StepRun(BaseModel):
    run_id: str
    step_id: str
    uses: str
    status: StepStatus = StepStatus.PENDING
    attempt: int = 0
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class WorkflowSnapshot(BaseModel):
    run: WorkflowRun
    steps: list[StepRun]
    definition: WorkflowDefinition | None = None


class StepResult(BaseModel):
    outputs: dict[str, Any] = Field(default_factory=dict)
    waiting: bool = False
