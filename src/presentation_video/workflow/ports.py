from __future__ import annotations

from typing import Any, Protocol

from presentation_video.workflow.models import (
    RunStatus,
    StepResult,
    StepRun,
    StepStatus,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowSnapshot,
)


class WorkflowStateRepository(Protocol):
    def initialize(self, run: WorkflowRun, definition: WorkflowDefinition) -> None: ...
    def get(self, run_id: str) -> WorkflowSnapshot | None: ...
    def set_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None: ...
    def set_step_status(
        self,
        run_id: str,
        step_id: str,
        status: StepStatus,
        *,
        attempt: int | None = None,
        inputs: dict[str, Any] | None = None,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None: ...
    def add_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None: ...


class WorkflowStep(Protocol):
    async def execute(
        self,
        inputs: dict[str, Any],
        config: dict[str, Any],
        context: "StepContext",
    ) -> StepResult | dict[str, Any]: ...


class StepContext(Protocol):
    run_id: str
    workflow: WorkflowDefinition
    step: StepRun
    item: Any
