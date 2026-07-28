from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest

from presentation_video.workflow.executor import (
    ExecutionContext,
    StepRegistry,
    WorkflowExecutor,
)
from presentation_video.workflow.loader import WorkflowLoader
from presentation_video.workflow.models import (
    RunStatus,
    StepStatus,
    WorkflowDefinition,
)
from presentation_video.workflow.sqlite_state import SQLiteWorkflowStateRepository
from presentation_video.workflow.tracker import WorkflowJobTracker
from presentation_video.domain.models import JobStatus


class InputStep:
    async def execute(
        self,
        inputs: dict[str, Any],
        config: dict[str, Any],
        context: ExecutionContext,
    ) -> dict[str, Any]:
        return {"numbers": inputs["numbers"]}


class FlakyStep:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(
        self,
        inputs: dict[str, Any],
        config: dict[str, Any],
        context: ExecutionContext,
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("temporary")
        return {"ready": True}


class DoubleStep:
    async def execute(
        self,
        inputs: dict[str, Any],
        config: dict[str, Any],
        context: ExecutionContext,
    ) -> dict[str, Any]:
        return {"value": int(inputs["value"]) * 2}


def _definition() -> WorkflowDefinition:
    return WorkflowDefinition.model_validate(
        {
            "id": "test-flow",
            "version": "1.0.0",
            "inputs": {"numbers": {"type": "array", "required": True}},
            "steps": [
                {
                    "id": "seed",
                    "uses": "test.input",
                    "inputs": {"numbers": "${workflow.inputs.numbers}"},
                },
                {
                    "id": "flaky",
                    "uses": "test.flaky",
                    "needs": ["seed"],
                    "retry": {"attempts": 2},
                },
                {
                    "id": "review",
                    "uses": "human.approval",
                    "needs": ["flaky"],
                    "checkpoint": "human",
                },
                {
                    "id": "double",
                    "uses": "test.double",
                    "needs": ["review"],
                    "foreach": "${steps.seed.outputs.numbers}",
                    "inputs": {"value": "${item}"},
                    "parallelism": 2,
                },
            ],
        }
    )


def test_default_workflow_yaml_is_valid() -> None:
    definition = WorkflowLoader(Path("workflows")).load("presentation-video")

    assert definition.version == "2.4.0"
    assert [step.id for step in definition.steps] == [
        "ingest",
        "narrative",
        "duration_validate",
        "duration_review",
        "speech",
        "scene_plan",
        "visual_plan",
        "prompt_compile",
        "rule_validate",
        "generate_images",
        "visual_review",
        "animate",
        "visual_qa",
        "render",
        "assemble",
        "captions",
    ]
    assert (
        next(step for step in definition.steps if step.id == "visual_review").checkpoint == "human"
    )
    configs = {step.id: step.config for step in definition.steps}
    assert configs["narrative"]["provider"] == "replicate"
    assert configs["narrative"]["model"] == "openai/gpt-5.6-terra"
    assert configs["narrative"]["secrets"]["api_token"] == "REPLICATE_API_TOKEN"
    assert configs["generate_images"]["model_input"]["aspect_ratio"] == "1536x1024"
    assert configs["speech"]["voice"] == "Kore"
    assert configs["scene_plan"]["informational_scenes"]["preserve_as_static"] is True
    assert configs["duration_validate"]["tolerance_percent"] == 5
    assert configs["captions"]["formats"] == ["webvtt", "srt"]
    assert configs["captions"]["burn_in"] is False


def test_pydantic_ai_workflow_yaml_is_valid() -> None:
    definition = WorkflowLoader(Path("workflows")).load("presentation-video-pydantic-ai")
    configs = {step.id: step.config for step in definition.steps}

    assert definition.id == "presentation-video-pydantic-ai"
    assert configs["narrative"]["provider"] == "pydantic_ai"
    assert configs["narrative"]["model"] == "google-cloud:gemini-3.5-flash"
    assert configs["visual_plan"]["provider"] == "pydantic_ai"
    assert configs["generate_images"]["provider"] == "vertex_ai"
    assert configs["animate"]["provider"] == "vertex_ai"
    assert configs["speech"]["provider"] == "vertex_ai"


def test_sqlite_contains_state_only_and_survives_repository_recreation(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.db"
    definition = _definition()
    repository = SQLiteWorkflowStateRepository(database)
    from presentation_video.workflow.models import WorkflowRun

    repository.initialize(
        WorkflowRun(
            run_id="run-1",
            workflow_id=definition.id,
            workflow_version=definition.version,
            status=RunStatus.PENDING,
            inputs={"numbers": [1, 2]},
        ),
        definition,
    )
    repository.set_step_status("run-1", "seed", StepStatus.RUNNING, attempt=1)

    restored = SQLiteWorkflowStateRepository(database).get("run-1")
    assert restored is not None
    assert restored.steps[0].attempt == 1
    assert restored.run.start_datetime is not None
    assert restored.run.end_datetime is None
    SQLiteWorkflowStateRepository(database).set_run_status("run-1", RunStatus.COMPLETED)
    completed = SQLiteWorkflowStateRepository(database).get("run-1")
    assert completed is not None
    assert completed.run.end_datetime is not None
    with sqlite3.connect(database) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    assert "workflow_runs" in tables
    assert "workflow_step_runs" in tables
    assert "artifacts" not in tables
    assert "blobs" not in tables


@pytest.mark.asyncio
async def test_executor_retries_pauses_and_resumes_foreach(tmp_path: Path) -> None:
    definition = _definition()
    repository = SQLiteWorkflowStateRepository(tmp_path / "state.db")
    registry = StepRegistry()
    flaky = FlakyStep()
    registry.register("test.input", InputStep())
    registry.register("test.flaky", flaky)
    registry.register("test.double", DoubleStep())
    executor = WorkflowExecutor(registry, repository)

    waiting = await executor.start(definition, "run-2", {"numbers": [2, 5]})

    assert waiting.run.status == RunStatus.WAITING
    assert flaky.calls == 2
    assert (
        next(step for step in waiting.steps if step.step_id == "review").status
        == StepStatus.WAITING
    )

    completed = await executor.resume(definition, "run-2", approved_steps={"review"})

    assert completed.run.status == RunStatus.COMPLETED
    doubled = next(step for step in completed.steps if step.step_id == "double")
    assert doubled.outputs == {"items": [{"value": 4}, {"value": 10}]}


def test_workflow_rejects_cycles() -> None:
    with pytest.raises(ValueError, match="cycle"):
        WorkflowDefinition.model_validate(
            {
                "id": "cycle",
                "version": "1.0.0",
                "steps": [
                    {"id": "first", "uses": "test.first", "needs": ["second"]},
                    {"id": "second", "uses": "test.second", "needs": ["first"]},
                ],
            }
        )


@pytest.mark.asyncio
async def test_job_tracker_persists_pipeline_checkpoint(tmp_path: Path) -> None:
    definition = WorkflowLoader(Path("workflows")).load("presentation-video")
    repository = SQLiteWorkflowStateRepository(tmp_path / "state.db")
    tracker = WorkflowJobTracker(repository, definition)
    tracker.initialize("job-1", {"source_path": "document.pdf", "target_seconds": 180})

    await tracker.update("job-1", JobStatus.INGESTING, "Reading")
    await tracker.update("job-1", JobStatus.SCRIPTING, "Writing")
    await tracker.update("job-1", JobStatus.AWAITING_VISUAL_APPROVAL, "Review visuals")

    snapshot = tracker.snapshot("job-1")
    assert snapshot is not None
    assert snapshot.run.status == RunStatus.WAITING
    statuses = {step.step_id: step.status for step in snapshot.steps}
    assert statuses["ingest"] == StepStatus.COMPLETED
    assert statuses["narrative"] == StepStatus.COMPLETED
    assert statuses["visual_plan"] == StepStatus.COMPLETED
    assert statuses["generate_images"] == StepStatus.COMPLETED
    assert statuses["visual_review"] == StepStatus.WAITING

    tracker.approve("job-1")
    approved = tracker.snapshot("job-1")
    assert approved is not None
    assert approved.run.status == RunStatus.RUNNING
    assert (
        next(step for step in approved.steps if step.step_id == "visual_review").status
        == StepStatus.COMPLETED
    )
