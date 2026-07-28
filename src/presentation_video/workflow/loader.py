from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from presentation_video.workflow.models import WorkflowDefinition


class WorkflowLoader:
    def __init__(self, root: Path) -> None:
        self._root = root.resolve()

    def load(self, workflow_id: str) -> WorkflowDefinition:
        if not workflow_id or any(part in workflow_id for part in ("/", "\\", "..")):
            raise ValueError("invalid workflow id")
        path = (self._root / f"{workflow_id}.yaml").resolve()
        if path.parent != self._root:
            raise ValueError("workflow path escapes configured root")
        if not path.is_file():
            raise FileNotFoundError(f"Workflow '{workflow_id}' not found at {path}")
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Workflow '{workflow_id}' must contain a YAML object")
        return WorkflowDefinition.model_validate(payload)

    def list(self) -> list[WorkflowDefinition]:
        definitions: list[WorkflowDefinition] = []
        if not self._root.is_dir():
            return definitions
        for path in sorted(self._root.glob("*.yaml")):
            payload: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
            definitions.append(WorkflowDefinition.model_validate(payload))
        return definitions
