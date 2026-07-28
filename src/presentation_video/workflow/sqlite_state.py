from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from presentation_video.workflow.models import (
    RunStatus,
    StepRun,
    StepStatus,
    WorkflowDefinition,
    WorkflowRun,
    WorkflowSnapshot,
)


def sqlite_path_from_url(database_url: str) -> Path:
    prefix = "sqlite:///"
    if not database_url.startswith(prefix):
        raise ValueError(
            "This runtime provides the SQLite workflow-state adapter. "
            "Configure sqlite:///... or install a PostgreSQL repository adapter."
        )
    raw_path = database_url.removeprefix(prefix)
    if not raw_path:
        raise ValueError("SQLite workflow database path cannot be empty")
    return Path(raw_path)


class SQLiteWorkflowStateRepository:
    """SQLite state only; media and document artifacts remain in external storage."""

    def __init__(self, database_path: Path) -> None:
        self._path = database_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize_schema()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflow_runs (
                    run_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL,
                    workflow_version TEXT NOT NULL,
                    definition_json TEXT,
                    status TEXT NOT NULL,
                    inputs_json TEXT NOT NULL,
                    outputs_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    start_datetime TEXT,
                    end_datetime TEXT
                );
                CREATE TABLE IF NOT EXISTS workflow_step_runs (
                    run_id TEXT NOT NULL,
                    step_id TEXT NOT NULL,
                    uses_name TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    inputs_json TEXT NOT NULL DEFAULT '{}',
                    outputs_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT,
                    started_at TEXT,
                    finished_at TEXT,
                    PRIMARY KEY (run_id, step_id),
                    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS workflow_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES workflow_runs(run_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_workflow_steps_status
                    ON workflow_step_runs(run_id, status);
                CREATE INDEX IF NOT EXISTS idx_workflow_events_run
                    ON workflow_events(run_id, event_id);
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(workflow_runs)").fetchall()
            }
            if "definition_json" not in columns:
                connection.execute("ALTER TABLE workflow_runs ADD COLUMN definition_json TEXT")
            if "start_datetime" not in columns:
                connection.execute("ALTER TABLE workflow_runs ADD COLUMN start_datetime TEXT")
            if "end_datetime" not in columns:
                connection.execute("ALTER TABLE workflow_runs ADD COLUMN end_datetime TEXT")
            connection.execute(
                "UPDATE workflow_runs SET start_datetime = COALESCE(start_datetime, created_at)"
            )
            connection.execute(
                "UPDATE workflow_runs SET end_datetime = COALESCE(end_datetime, updated_at) "
                "WHERE status IN ('completed', 'failed', 'cancelled')"
            )

    def initialize(self, run: WorkflowRun, definition: WorkflowDefinition) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO workflow_runs (
                    run_id, workflow_id, workflow_version, definition_json, status, inputs_json,
                    outputs_json, error, created_at, updated_at
                    , start_datetime, end_datetime
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.workflow_id,
                    run.workflow_version,
                    definition.model_dump_json(),
                    run.status.value,
                    json.dumps(run.inputs, ensure_ascii=False),
                    json.dumps(run.outputs, ensure_ascii=False),
                    run.error,
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                    run.start_datetime.isoformat(),
                    run.end_datetime.isoformat() if run.end_datetime else None,
                ),
            )
            for step in definition.steps:
                connection.execute(
                    """
                    INSERT OR IGNORE INTO workflow_step_runs (
                        run_id, step_id, uses_name, status
                    ) VALUES (?, ?, ?, ?)
                    """,
                    (run.run_id, step.id, step.uses, StepStatus.PENDING.value),
                )
            self._insert_event(
                connection,
                run.run_id,
                "run.initialized",
                {"workflow_id": run.workflow_id, "version": run.workflow_version},
            )

    def get(self, run_id: str) -> WorkflowSnapshot | None:
        with self._lock, self._connect() as connection:
            run_row = connection.execute(
                "SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run_row is None:
                return None
            step_rows = connection.execute(
                "SELECT * FROM workflow_step_runs WHERE run_id = ? ORDER BY rowid", (run_id,)
            ).fetchall()
        run = WorkflowRun(
            run_id=run_row["run_id"],
            workflow_id=run_row["workflow_id"],
            workflow_version=run_row["workflow_version"],
            status=RunStatus(run_row["status"]),
            inputs=json.loads(run_row["inputs_json"]),
            outputs=json.loads(run_row["outputs_json"]),
            error=run_row["error"],
            created_at=datetime.fromisoformat(run_row["created_at"]),
            updated_at=datetime.fromisoformat(run_row["updated_at"]),
            start_datetime=datetime.fromisoformat(
                run_row["start_datetime"] or run_row["created_at"]
            ),
            end_datetime=(
                datetime.fromisoformat(run_row["end_datetime"])
                if run_row["end_datetime"]
                else None
            ),
        )
        steps = [
            StepRun(
                run_id=row["run_id"],
                step_id=row["step_id"],
                uses=row["uses_name"],
                status=StepStatus(row["status"]),
                attempt=row["attempt"],
                inputs=json.loads(row["inputs_json"]),
                outputs=json.loads(row["outputs_json"]),
                error=row["error"],
                started_at=(
                    datetime.fromisoformat(row["started_at"]) if row["started_at"] else None
                ),
                finished_at=(
                    datetime.fromisoformat(row["finished_at"]) if row["finished_at"] else None
                ),
            )
            for row in step_rows
        ]
        definition = (
            WorkflowDefinition.model_validate_json(run_row["definition_json"])
            if run_row["definition_json"]
            else None
        )
        return WorkflowSnapshot(run=run, steps=steps, definition=definition)

    def set_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        outputs: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        terminal = status in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }
        clear_end = status == RunStatus.RUNNING
        timing_assignment = (
            ", end_datetime = ?"
            if terminal
            else (", end_datetime = NULL" if clear_end else "")
        )
        with self._lock, self._connect() as connection:
            if outputs is None:
                connection.execute(
                    "UPDATE workflow_runs SET status = ?, error = ?, updated_at = ?"
                    f"{timing_assignment} "
                    "WHERE run_id = ?",
                    (
                        (status.value, error, now, now, run_id)
                        if terminal
                        else (status.value, error, now, run_id)
                    ),
                )
            else:
                connection.execute(
                    "UPDATE workflow_runs SET status = ?, outputs_json = ?, error = ?, "
                    f"updated_at = ?{timing_assignment} WHERE run_id = ?",
                    (
                        (
                            status.value,
                            json.dumps(outputs, ensure_ascii=False),
                            error,
                            now,
                            now,
                            run_id,
                        )
                        if terminal
                        else (
                            status.value,
                            json.dumps(outputs, ensure_ascii=False),
                            error,
                            now,
                            run_id,
                        )
                    ),
                )
            self._insert_event(
                connection, run_id, "run.status_changed", {"status": status.value, "error": error}
            )

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
    ) -> None:
        now = datetime.now(UTC).isoformat()
        assignments = ["status = ?", "error = ?"]
        values: list[Any] = [status.value, error]
        if attempt is not None:
            assignments.append("attempt = ?")
            values.append(attempt)
        if inputs is not None:
            assignments.append("inputs_json = ?")
            values.append(json.dumps(inputs, ensure_ascii=False))
        if outputs is not None:
            assignments.append("outputs_json = ?")
            values.append(json.dumps(outputs, ensure_ascii=False))
        if status == StepStatus.RUNNING:
            assignments.append("started_at = COALESCE(started_at, ?)")
            values.append(now)
            assignments.append("finished_at = NULL")
        if status in {
            StepStatus.COMPLETED,
            StepStatus.FAILED,
            StepStatus.SKIPPED,
        }:
            assignments.append("finished_at = ?")
            values.append(now)
        values.extend([run_id, step_id])
        with self._lock, self._connect() as connection:
            connection.execute(
                f"UPDATE workflow_step_runs SET {', '.join(assignments)} "
                "WHERE run_id = ? AND step_id = ?",
                values,
            )
            self._insert_event(
                connection,
                run_id,
                "step.status_changed",
                {"step_id": step_id, "status": status.value, "attempt": attempt, "error": error},
            )

    def add_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            self._insert_event(connection, run_id, event_type, payload)

    @staticmethod
    def _insert_event(
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        connection.execute(
            """
            INSERT INTO workflow_events (run_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (
                run_id,
                event_type,
                json.dumps(payload, ensure_ascii=False),
                datetime.now(UTC).isoformat(),
            ),
        )
