from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from agentnb.contracts import ExecutionEvent, ExecutionResult, ExecutionSink
from agentnb.errors import KernelNotReadyError, RunWaitTimedOutError
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


def test_execution_store_returns_latest_version_for_same_run(project_dir: Path) -> None:
    store = ExecutionStore(project_dir)
    store.append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
            code="1 + 1",
        )
    )
    store.append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="ok",
            duration_ms=12,
            code="1 + 1",
            result="2",
        )
    )

    entries = store.read(session_id="default")

    assert len(entries) == 1
    assert entries[0].status == "ok"
    assert entries[0].result == "2"


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


def test_execution_service_stream_sink_reuses_execution_id(project_dir: Path) -> None:
    runtime = KernelRuntime(backend=Mock())

    class Sink(ExecutionSink):
        def __init__(self) -> None:
            self.started_execution_id: str | None = None
            self.started_session_id: str | None = None
            self.events: list[ExecutionEvent] = []

        def started(self, *, execution_id: str, session_id: str) -> None:
            self.started_execution_id = execution_id
            self.started_session_id = session_id

        def accept(self, event: ExecutionEvent) -> None:
            self.events.append(event)

    def execute_stub(**kwargs: object) -> ExecutionResult:
        before_backend = kwargs["before_backend"]
        sink = kwargs["event_sink"]
        assert callable(before_backend)
        assert isinstance(sink, Sink)
        before_backend()
        sink.accept(ExecutionEvent(kind="stdout", content="hello\n"))
        return ExecutionResult(
            status="ok",
            stdout="hello\n",
            duration_ms=5,
            events=[ExecutionEvent(kind="stdout", content="hello\n")],
        )

    runtime.execute = Mock(side_effect=execute_stub)  # type: ignore[method-assign]
    service = ExecutionService(runtime)
    sink = Sink()

    managed = service.execute_code(
        project_root=project_dir,
        session_id="default",
        code="print('hello')",
        timeout_s=5,
        event_sink=sink,
    )

    stored = ExecutionStore(project_dir).get(managed.record.execution_id)
    assert sink.started_execution_id == managed.record.execution_id
    assert sink.started_session_id == "default"
    assert sink.events == [ExecutionEvent(kind="stdout", content="hello\n")]
    assert stored is not None
    assert stored.execution_id == managed.record.execution_id
    assert stored.stdout == "hello\n"


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


def test_execution_service_start_background_code_persists_running_record(
    project_dir: Path, mocker
) -> None:
    runtime = KernelRuntime(backend=Mock())
    service = ExecutionService(runtime)
    popen = mocker.patch("agentnb.execution.subprocess.Popen")
    popen.return_value.pid = 456

    managed = service.start_background_code(
        project_root=project_dir,
        session_id="default",
        code="1 + 1",
    )

    stored = ExecutionStore(project_dir).get(managed.record.execution_id)
    assert stored is not None
    assert stored.status == "running"
    assert stored.worker_pid == 456


def test_execution_service_wait_for_run_returns_completed_record(project_dir: Path) -> None:
    ExecutionStore(project_dir).append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="ok",
            duration_ms=12,
            result="2",
        )
    )

    run = ExecutionService(KernelRuntime()).wait_for_run(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=0.0,
        poll_interval_s=0.0,
    )

    assert run["execution_id"] == "run-1"
    assert run["status"] == "ok"


def test_execution_service_wait_for_run_times_out(project_dir: Path, mocker) -> None:
    mocker.patch("agentnb.execution.pid_exists", return_value=True)
    ExecutionStore(project_dir).append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
            worker_pid=123,
        )
    )

    with pytest.raises(RunWaitTimedOutError):
        ExecutionService(KernelRuntime()).wait_for_run(
            project_root=project_dir,
            execution_id="run-1",
            timeout_s=0.0,
            poll_interval_s=0.0,
        )


def test_execution_service_cancel_run_interrupts_session(project_dir: Path, mocker) -> None:
    mocker.patch("agentnb.execution.pid_exists", return_value=True)
    kill = mocker.patch("agentnb.execution.os.kill")
    runtime = KernelRuntime(backend=Mock())
    runtime.interrupt = Mock()  # type: ignore[method-assign]
    ExecutionStore(project_dir).append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="analysis",
            command_type="exec",
            status="running",
            duration_ms=0,
            worker_pid=123,
        )
    )

    payload = ExecutionService(runtime).cancel_run(project_root=project_dir, execution_id="run-1")
    stored = ExecutionStore(project_dir).get("run-1")

    assert payload["cancel_requested"] is True
    assert payload["status"] == "error"
    runtime.interrupt.assert_called_once_with(project_root=project_dir, session_id="analysis")
    kill.assert_called_once()
    assert stored is not None
    assert stored.status == "error"
    assert stored.ename == "CancelledError"


def test_execution_service_cancel_run_stops_starting_session(project_dir: Path, mocker) -> None:
    mocker.patch("agentnb.execution.pid_exists", return_value=True)
    kill = mocker.patch("agentnb.execution.os.kill")
    runtime = KernelRuntime(backend=Mock())
    runtime.interrupt = Mock(side_effect=KernelNotReadyError())  # type: ignore[method-assign]
    runtime.stop_starting = Mock()  # type: ignore[method-assign]
    ExecutionStore(project_dir).append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="analysis",
            command_type="exec",
            status="running",
            duration_ms=0,
            worker_pid=123,
        )
    )

    payload = ExecutionService(runtime).cancel_run(
        project_root=project_dir,
        execution_id="run-1",
    )
    stored = ExecutionStore(project_dir).get("run-1")

    assert payload["cancel_requested"] is True
    assert payload["status"] == "error"
    runtime.stop_starting.assert_called_once_with(project_root=project_dir, session_id="analysis")
    kill.assert_called_once()
    assert stored is not None
    assert stored.status == "error"
    assert stored.ename == "CancelledError"
