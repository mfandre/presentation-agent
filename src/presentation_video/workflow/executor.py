from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from typing import Any

from presentation_video.workflow.models import (
    RunStatus,
    StepResult,
    StepRun,
    StepStatus,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowSnapshot,
    WorkflowStepDefinition,
)
from presentation_video.workflow.ports import WorkflowStateRepository, WorkflowStep

_REFERENCE = re.compile(r"^\$\{([^}]+)\}$")


class StepRegistry:
    def __init__(self) -> None:
        self._steps: dict[str, WorkflowStep] = {}

    def register(self, name: str, step: WorkflowStep) -> None:
        if name in self._steps:
            raise ValueError(f"Workflow step '{name}' is already registered")
        self._steps[name] = step

    def resolve(self, name: str) -> WorkflowStep:
        try:
            return self._steps[name]
        except KeyError as exc:
            raise KeyError(f"Workflow step '{name}' is not registered") from exc


@dataclass
class ExecutionContext:
    run_id: str
    workflow: WorkflowDefinition
    step: StepRun
    item: Any = None


class WorkflowExecutor:
    def __init__(
        self,
        registry: StepRegistry,
        repository: WorkflowStateRepository,
    ) -> None:
        self._registry = registry
        self._repository = repository

    async def start(
        self,
        definition: WorkflowDefinition,
        run_id: str,
        inputs: dict[str, Any],
    ) -> WorkflowSnapshot:
        normalized_inputs = self._validate_inputs(definition, inputs)
        run = WorkflowRun(
            run_id=run_id,
            workflow_id=definition.id,
            workflow_version=definition.version,
            status=RunStatus.PENDING,
            inputs=normalized_inputs,
        )
        self._repository.initialize(run, definition)
        return await self.resume(definition, run_id)

    async def resume(
        self,
        definition: WorkflowDefinition | None,
        run_id: str,
        *,
        approved_steps: set[str] | None = None,
    ) -> WorkflowSnapshot:
        approved_steps = approved_steps or set()
        snapshot = self._require_snapshot(run_id)
        stored_definition = snapshot.definition
        if stored_definition is not None:
            if definition is not None and (
                definition.id != stored_definition.id
                or definition.version != stored_definition.version
            ):
                raise ValueError(
                    "workflow definition does not match the version stored for this run"
                )
            definition = stored_definition
        if definition is None:
            raise ValueError("workflow definition is unavailable for this run")
        self._repository.set_run_status(run_id, RunStatus.RUNNING)
        while True:
            snapshot = self._require_snapshot(run_id)
            states = {step.step_id: step for step in snapshot.steps}
            progressed = False
            for definition_step in definition.steps:
                state = states[definition_step.id]
                if state.status in {
                    StepStatus.COMPLETED,
                    StepStatus.SKIPPED,
                }:
                    continue
                if state.status == StepStatus.WAITING:
                    if definition_step.id not in approved_steps:
                        self._repository.set_run_status(run_id, RunStatus.WAITING)
                        return self._require_snapshot(run_id)
                    self._repository.set_step_status(
                        run_id, definition_step.id, StepStatus.COMPLETED, outputs=state.outputs
                    )
                    progressed = True
                    continue
                dependency_states = [
                    states[dependency].status for dependency in definition_step.needs
                ]
                if any(status == StepStatus.FAILED for status in dependency_states):
                    self._repository.set_step_status(
                        run_id,
                        definition_step.id,
                        StepStatus.SKIPPED,
                        error="dependency failed",
                    )
                    progressed = True
                    continue
                if not all(
                    status in {StepStatus.COMPLETED, StepStatus.SKIPPED}
                    for status in dependency_states
                ):
                    continue
                if not self._resolve_condition(
                    definition_step.when, snapshot.run.inputs, states, None
                ):
                    self._repository.set_step_status(run_id, definition_step.id, StepStatus.SKIPPED)
                    progressed = True
                    continue
                if definition_step.checkpoint == "human":
                    self._repository.set_step_status(run_id, definition_step.id, StepStatus.WAITING)
                    self._repository.set_run_status(run_id, RunStatus.WAITING)
                    return self._require_snapshot(run_id)
                try:
                    await self._execute_step(
                        definition, definition_step, snapshot.run.inputs, states
                    )
                except Exception as exc:
                    if definition_step.continue_on_error:
                        self._repository.set_step_status(
                            run_id,
                            definition_step.id,
                            StepStatus.SKIPPED,
                            error=str(exc),
                        )
                        progressed = True
                        continue
                    self._repository.set_run_status(run_id, RunStatus.FAILED, error=str(exc))
                    return self._require_snapshot(run_id)
                progressed = True
            snapshot = self._require_snapshot(run_id)
            if all(
                step.status in {StepStatus.COMPLETED, StepStatus.SKIPPED} for step in snapshot.steps
            ):
                outputs = {step.step_id: step.outputs for step in snapshot.steps if step.outputs}
                self._repository.set_run_status(run_id, RunStatus.COMPLETED, outputs=outputs)
                return self._require_snapshot(run_id)
            if not progressed:
                self._repository.set_run_status(
                    run_id, RunStatus.FAILED, error="workflow reached a deadlock"
                )
                return self._require_snapshot(run_id)

    async def _execute_step(
        self,
        definition: WorkflowDefinition,
        step_definition: WorkflowStepDefinition,
        workflow_inputs: dict[str, Any],
        states: dict[str, StepRun],
    ) -> None:
        run_id = next(iter(states.values())).run_id
        handler = self._registry.resolve(step_definition.uses)
        items: list[Any] = [None]
        if step_definition.foreach:
            resolved = self._resolve_value(step_definition.foreach, workflow_inputs, states, None)
            if not isinstance(resolved, list):
                raise ValueError(f"step {step_definition.id} foreach must resolve to a list")
            items = resolved
        semaphore = asyncio.Semaphore(step_definition.parallelism)

        async def execute_item(item: Any) -> dict[str, Any]:
            resolved_inputs = self._resolve_value(
                step_definition.inputs, workflow_inputs, states, item
            )
            last_error: Exception | None = None
            for attempt in range(1, step_definition.retry.attempts + 1):
                self._repository.set_step_status(
                    run_id,
                    step_definition.id,
                    StepStatus.RUNNING,
                    attempt=attempt,
                    inputs=resolved_inputs,
                )
                state = self._require_step(run_id, step_definition.id)
                context = ExecutionContext(run_id, definition, state, item)
                try:
                    async with semaphore:
                        operation = handler.execute(
                            resolved_inputs, step_definition.config, context
                        )
                        raw_result = (
                            await asyncio.wait_for(
                                operation, timeout=step_definition.timeout_seconds
                            )
                            if step_definition.timeout_seconds
                            else await operation
                        )
                    result = (
                        raw_result
                        if isinstance(raw_result, StepResult)
                        else StepResult(outputs=raw_result)
                    )
                    if result.waiting:
                        raise ValueError(
                            "steps must declare checkpoint: human instead of returning waiting"
                        )
                    return result.outputs
                except Exception as exc:
                    last_error = exc
                    if attempt >= step_definition.retry.attempts:
                        break
                    delay = step_definition.retry.backoff_seconds
                    if step_definition.retry.exponential:
                        delay *= 2 ** (attempt - 1)
                    if delay:
                        await asyncio.sleep(delay)
            assert last_error is not None
            self._repository.set_step_status(
                run_id,
                step_definition.id,
                StepStatus.FAILED,
                attempt=step_definition.retry.attempts,
                error=str(last_error),
            )
            raise last_error

        outputs = await asyncio.gather(*(execute_item(item) for item in items))
        combined: dict[str, Any]
        if step_definition.foreach:
            combined = {"items": outputs}
        else:
            combined = outputs[0]
        self._repository.set_step_status(
            run_id,
            step_definition.id,
            StepStatus.COMPLETED,
            outputs=combined,
        )

    @staticmethod
    def _validate_inputs(definition: WorkflowDefinition, inputs: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(inputs)
        for name, input_definition in definition.inputs.items():
            if name not in normalized:
                if input_definition.required and input_definition.default is None:
                    raise ValueError(f"missing required workflow input '{name}'")
                normalized[name] = input_definition.default
        return normalized

    def _resolve_condition(
        self,
        condition: bool | str | dict[str, Any],
        workflow_inputs: dict[str, Any],
        states: dict[str, StepRun],
        item: Any,
    ) -> bool:
        if isinstance(condition, bool):
            return condition
        if isinstance(condition, dict):
            allowed = {"input", "reference", "equals", "not_equals", "in", "not_in"}
            unknown = set(condition) - allowed
            if unknown:
                raise ValueError(
                    f"workflow condition contains unsupported keys {sorted(unknown)}"
                )
            if ("input" in condition) == ("reference" in condition):
                raise ValueError(
                    "workflow condition must define exactly one of input or reference"
                )
            if "input" in condition:
                input_name = condition["input"]
                if not isinstance(input_name, str) or input_name not in workflow_inputs:
                    raise ValueError(f"workflow condition references unknown input {input_name!r}")
                resolved = workflow_inputs[input_name]
            else:
                resolved = self._resolve_value(
                    condition["reference"],
                    workflow_inputs,
                    states,
                    item,
                )
            operators = [
                name
                for name in ("equals", "not_equals", "in", "not_in")
                if name in condition
            ]
            if len(operators) != 1:
                raise ValueError(
                    "workflow condition must define exactly one comparison operator"
                )
            operator = operators[0]
            expected = self._resolve_value(
                condition[operator],
                workflow_inputs,
                states,
                item,
            )
            if operator == "equals":
                return resolved == expected
            if operator == "not_equals":
                return resolved != expected
            if not isinstance(expected, (list, tuple, set)):
                raise ValueError(f"workflow condition operator {operator} requires a collection")
            return resolved in expected if operator == "in" else resolved not in expected
        resolved = self._resolve_value(condition, workflow_inputs, states, item)
        if not isinstance(resolved, bool):
            raise ValueError("workflow condition must resolve to a boolean")
        return resolved

    def _resolve_value(
        self,
        value: Any,
        workflow_inputs: dict[str, Any],
        states: dict[str, StepRun],
        item: Any,
    ) -> Any:
        if isinstance(value, dict):
            return {
                key: self._resolve_value(child, workflow_inputs, states, item)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [self._resolve_value(child, workflow_inputs, states, item) for child in value]
        if not isinstance(value, str):
            return value
        match = _REFERENCE.fullmatch(value)
        if not match:
            return value
        path = match.group(1).split(".")
        if path == ["item"]:
            return item
        if path[:2] == ["workflow", "inputs"] and len(path) >= 3:
            current: Any = workflow_inputs
            for part in path[2:]:
                current = current[part]
            return current
        if len(path) >= 4 and path[0] == "steps" and path[2] == "outputs":
            current = states[path[1]].outputs
            for part in path[3:]:
                current = current[part]
            return current
        raise ValueError(f"unsupported workflow reference '{value}'")

    def _require_snapshot(self, run_id: str) -> WorkflowSnapshot:
        snapshot = self._repository.get(run_id)
        if snapshot is None:
            raise KeyError(f"Workflow run '{run_id}' not found")
        return snapshot

    def _require_step(self, run_id: str, step_id: str) -> StepRun:
        snapshot = self._require_snapshot(run_id)
        return next(step for step in snapshot.steps if step.step_id == step_id)
