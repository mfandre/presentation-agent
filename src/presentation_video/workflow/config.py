from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from presentation_video.workflow.models import WorkflowDefinition


class StepRuntimeConfig(BaseModel):
    """Validated, provider-neutral configuration owned by one workflow step."""

    model_config = ConfigDict(extra="allow")

    provider: str
    model: str | None = None
    model_input: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    poll_interval_seconds: float = Field(default=2.0, ge=0.2, le=60)
    timeout_seconds: int = Field(default=900, ge=30, le=3600)


class WorkflowRuntimeConfig:
    def __init__(self, definition: WorkflowDefinition) -> None:
        self.definition = definition
        self._steps = {step.id: step for step in definition.steps}

    def raw(self, step_id: str) -> dict[str, Any]:
        try:
            return self._steps[step_id].config
        except KeyError as error:
            raise ValueError(f"workflow is missing required step {step_id!r}") from error

    def step(self, step_id: str) -> StepRuntimeConfig:
        config = self.raw(step_id)
        if not config:
            raise ValueError(f"workflow step {step_id!r} must define config")
        return StepRuntimeConfig.model_validate(config)

    def parallelism(self, *step_ids: str) -> int:
        return max(self._steps[step_id].parallelism for step_id in step_ids)
