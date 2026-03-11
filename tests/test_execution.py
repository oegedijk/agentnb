from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

from agentnb.contracts import ExecutionEvent, ExecutionResult
from agentnb.execution import ExecutionRecord, ExecutionService, ExecutionStore
from agentnb.runtime import KernelRuntime


def test_execution_store_roundtrip_and_get(project_dir: Path) -> None:
    store = ExecutionStore(project_dir)
    record = ExecutionRecord(
        execution_id="run-1",
        ts="2026-03-10T00:00:00+00:00",
        session_id="default",
        command_type="exec",
        status="ok",
        duration_ms=12,
        code="1 + 1",
        result="2",
        events=[ExecutionEvent(kind="result", content="2")],
    )

    store.append(record)

    entries = store.read(session_id="default")
    loaded = store.get("run-1")

    assert len(entries) == 1
    assert entries[0].execution_id == "run-1"
    assert loaded is not None
    assert loaded.result == "2"
    assert loaded.events[0].kind == "result"


def test_execution_service_persists_exec_runs(project_dir: Path) -> None:
    runtime = KernelRuntime(backend=Mock())
    runtime.execute = Mock(  # type: ignore[method-assign]
        return_value=ExecutionResult(
            status="ok",
            result="2",
            duration_ms=5,
            events=[ExecutionEvent(kind="result", content="2")],
        )
    )
    service = ExecutionService(runtime)

    managed = service.execute_code(
        project_root=project_dir,
        session_id="default",
        code="1 + 1",
        timeout_s=5,
    )

    stored = ExecutionStore(project_dir).read(session_id="default")
    assert managed.record.execution_id
    assert stored[0].execution_id == managed.record.execution_id
    assert stored[0].command_type == "exec"
    assert stored[0].result == "2"


def test_execution_service_history_projection_uses_execution_ids(project_dir: Path) -> None:
    store = ExecutionStore(project_dir)
    store.append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="reset",
            status="ok",
            duration_ms=9,
        )
    )

    entries = ExecutionService(KernelRuntime()).history_entries(
        project_root=project_dir,
        session_id="default",
        include_internal=True,
        errors_only=False,
    )

    assert len(entries) == 2
    assert all(entry["execution_id"] == "run-1" for entry in entries)
    assert [entry["kind"] for entry in entries] == ["kernel_execution", "user_command"]
    assert all(entry["ts"] == "2026-03-10T00:00:00+00:00" for entry in entries)
