from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from presentation_video.workflow.models import WorkflowDefinition


class WorkflowLoader:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def load(self, workflow_id: str) -> WorkflowDefinition:
        return WorkflowDefinition.model_validate(
            self._load_payload(workflow_id, inheritance_chain=())
        )

    def _load_payload(
        self,
        workflow_id: str,
        *,
        inheritance_chain: tuple[str, ...],
    ) -> dict[str, Any]:
        if not workflow_id or any(part in workflow_id for part in ("/", "\\", "..")):
            raise ValueError("invalid workflow id")
        if workflow_id in inheritance_chain:
            chain = " -> ".join((*inheritance_chain, workflow_id))
            raise ValueError(f"workflow inheritance contains a cycle: {chain}")
        path = (self._root / f"{workflow_id}.yaml").resolve()
        if path.parent != self._root:
            raise ValueError("workflow path escapes configured root")
        if not path.is_file():
            raise FileNotFoundError(f"Workflow '{workflow_id}' not found at {path}")
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Workflow '{workflow_id}' must contain a YAML object")
        parent_id = payload.pop("extends", None)
        if parent_id is None:
            return payload
        if not isinstance(parent_id, str) or not parent_id.strip():
            raise ValueError(f"Workflow '{workflow_id}' extends must be a workflow id")
        parent = self._load_payload(
            parent_id.strip(),
            inheritance_chain=(*inheritance_chain, workflow_id),
        )
        return _deep_merge(parent, payload)

    def list(self) -> list[WorkflowDefinition]:
        definitions: list[WorkflowDefinition] = []
        if not self._root.is_dir():
            return definitions
        for path in sorted(self._root.glob("*.yaml")):
            definitions.append(self.load(path.stem))
        return definitions


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Merge a workflow variant while retaining the base graph and step order."""

    if "$replace" in override:
        replacement = override["$replace"]
        if not isinstance(replacement, dict):
            raise ValueError("workflow $replace value must be an object")
        return deepcopy(replacement)
    result = deepcopy(base)
    for key, value in override.items():
        current = result.get(key)
        if key == "steps" and isinstance(current, list) and isinstance(value, list):
            result[key] = _merge_steps(current, value)
        elif isinstance(current, dict) and isinstance(value, dict):
            result[key] = _deep_merge(current, value)
        else:
            result[key] = deepcopy(value)
    return result


def _merge_steps(base: list[Any], overrides: list[Any]) -> list[Any]:
    result = deepcopy(base)
    positions = {
        step["id"]: index
        for index, step in enumerate(result)
        if isinstance(step, dict) and isinstance(step.get("id"), str)
    }
    for override in overrides:
        step_id = override.get("id") if isinstance(override, dict) else None
        if isinstance(step_id, str) and step_id in positions:
            index = positions[step_id]
            result[index] = _deep_merge(result[index], override)
        else:
            result.append(deepcopy(override))
            if isinstance(step_id, str):
                positions[step_id] = len(result) - 1
    return result
