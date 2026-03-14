from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest

from agentnb.contracts import ExecutionEvent, ExecutionResult, ExecutionSink
from agentnb.errors import AgentNBException
from agentnb.execution import (
    ExecutionRecord,
    ExecutionRun,
    ExecutionService,
    ExecutionStore,
    _ExecutionProgressSink,
)
from agentnb.execution_output import OutputItem
from agentnb.runs import ManagedExecution, RunSpec
from agentnb.runtime import KernelRuntime


def _event_sink(kwargs: dict[str, object]) -> ExecutionSink:
    sink = kwargs["event_sink"]
    assert sink is not None
    return cast(ExecutionSink, sink)


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
        outputs=[OutputItem.result(text="2", mime={"text/plain": "2"})],
        events=[ExecutionEvent(kind="result", content="2")],
    )

    store.append(record)

    entries = store.read(session_id="default")
    loaded = store.get("run-1")

    assert len(entries) == 1
    assert entries[0].execution_id == "run-1"
    assert loaded is not None
    assert loaded.result == "2"
    assert loaded.outputs == [OutputItem.result(text="2", mime={"text/plain": "2"})]
    assert loaded.events[0].kind == "result"


def test_execution_record_from_dict_accepts_legacy_records_without_outputs() -> None:
    record = ExecutionRecord.from_dict(
        {
            "execution_id": "run-1",
            "ts": "2026-03-10T00:00:00+00:00",
            "session_id": "default",
            "command_type": "exec",
            "status": "ok",
            "duration_ms": 12,
            "result": "2",
            "events": [{"kind": "result", "content": "2", "metadata": {}}],
        }
    )

    assert record.outputs == []
    assert record.events == [ExecutionEvent(kind="result", content="2")]


def test_execution_record_storage_dict_includes_outputs_but_public_dict_does_not() -> None:
    record = ExecutionRecord(
        execution_id="run-1",
        ts="2026-03-10T00:00:00+00:00",
        session_id="default",
        command_type="exec",
        status="ok",
        duration_ms=12,
        result="2",
        outputs=[OutputItem.result(text="2", mime={"text/plain": "2"})],
        events=[ExecutionEvent(kind="result", content="2")],
    )

    public_payload = record.to_dict()
    storage_payload = record.to_storage_dict()

    assert "outputs" not in public_payload
    assert storage_payload["outputs"] == [
        {
            "kind": "result",
            "text": "2",
            "mime": {"text/plain": "2"},
        }
    ]


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
        cast(Callable[[], None], before_backend)()
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


def test_execution_service_get_run_raises_when_missing(project_dir: Path) -> None:
    with pytest.raises(AgentNBException, match="Execution not found: missing"):
        ExecutionService(KernelRuntime()).get_run(project_root=project_dir, execution_id="missing")


def test_execution_service_start_background_code_delegates_to_run_manager(
    project_dir: Path,
) -> None:
    runtime = KernelRuntime(backend=Mock())
    run_manager = Mock()
    managed = ManagedExecution(
        record=ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
        )
    )
    run_manager.submit.return_value = managed

    service = ExecutionService(runtime, run_manager=run_manager)
    result = service.start_background_code(
        project_root=project_dir,
        session_id="default",
        code="1 + 1",
        ensure_started=True,
    )

    assert result is managed
    run_manager.submit.assert_called_once()
    spec = run_manager.submit.call_args.args[0]
    assert isinstance(spec, RunSpec)
    assert spec.project_root == project_dir.resolve()
    assert spec.session_id == "default"
    assert spec.command_type == "exec"
    assert spec.code == "1 + 1"
    assert spec.mode == "background"
    assert spec.ensure_started is True


def test_execution_service_wait_for_run_delegates_to_run_manager(project_dir: Path) -> None:
    runtime = KernelRuntime(backend=Mock())
    run_manager = Mock()
    run_manager.wait_for_run.return_value = {"execution_id": "run-1", "status": "ok"}

    service = ExecutionService(runtime, run_manager=run_manager)
    result = service.wait_for_run(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=2.0,
        poll_interval_s=0.25,
    )

    assert result == {"execution_id": "run-1", "status": "ok"}
    run_manager.wait_for_run.assert_called_once_with(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=2.0,
        poll_interval_s=0.25,
    )


def test_execution_service_follow_run_delegates_observer_to_run_manager(project_dir: Path) -> None:
    runtime = KernelRuntime(backend=Mock())
    run_manager = Mock()
    run_manager.follow_run.return_value = {"execution_id": "run-1", "status": "ok"}

    class Sink(ExecutionSink):
        def started(self, *, execution_id: str, session_id: str) -> None:
            del execution_id, session_id

        def accept(self, event: ExecutionEvent) -> None:
            del event

    sink = Sink()
    service = ExecutionService(runtime, run_manager=run_manager)
    result = service.follow_run(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=3.0,
        poll_interval_s=0.5,
        event_sink=sink,
    )

    assert result == {"execution_id": "run-1", "status": "ok"}
    run_manager.follow_run.assert_called_once_with(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=3.0,
        poll_interval_s=0.5,
        observer=sink,
    )


def test_execution_service_cancel_run_delegates_to_run_manager(project_dir: Path) -> None:
    runtime = KernelRuntime(backend=Mock())
    run_manager = Mock()
    run_manager.cancel_run.return_value = {
        "execution_id": "run-1",
        "session_id": "default",
        "cancel_requested": True,
        "status": "error",
        "run_status": "error",
        "session_outcome": "preserved",
    }

    service = ExecutionService(runtime, run_manager=run_manager)
    result = service.cancel_run(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=4.0,
        poll_interval_s=0.75,
    )

    assert result["cancel_requested"] is True
    run_manager.cancel_run.assert_called_once_with(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=4.0,
        poll_interval_s=0.75,
    )


def test_execution_service_complete_background_run_delegates_to_run_manager(
    project_dir: Path,
) -> None:
    runtime = KernelRuntime(backend=Mock())
    run_manager = Mock()

    service = ExecutionService(runtime, run_manager=run_manager)
    service.complete_background_run(project_root=project_dir, execution_id="run-1")

    run_manager.complete_background_run.assert_called_once_with(
        project_root=project_dir,
        execution_id="run-1",
    )


def test_execution_progress_sink_keeps_nonterminal_background_snapshot_running(
    project_dir: Path,
) -> None:
    store = ExecutionStore(project_dir)
    run = ExecutionRun(
        store=store,
        record=ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
            code="display_then_wait()",
            worker_pid=123,
        ),
        started=True,
    )
    sink = _ExecutionProgressSink(run)

    sink.accept(ExecutionEvent(kind="stdout", content="hello\n"))
    sink.accept(
        ExecutionEvent(
            kind="display",
            content="preview",
            metadata={"mime": {"text/plain": "preview", "text/html": "<p>preview</p>"}},
        )
    )

    stored = store.get("run-1")
    assert stored is not None
    assert stored.status == "running"
    assert stored.stdout == "hello\n"
    assert stored.result == "preview"
    assert stored.events == [
        ExecutionEvent(kind="stdout", content="hello\n"),
        ExecutionEvent(
            kind="display",
            content="preview",
            metadata={"mime": {"text/plain": "preview", "text/html": "<p>preview</p>"}},
        ),
    ]
