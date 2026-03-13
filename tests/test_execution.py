from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import pytest

from agentnb.contracts import ExecutionEvent, ExecutionResult, ExecutionSink
from agentnb.errors import AgentNBException, KernelNotReadyError, RunWaitTimedOutError
from agentnb.execution import (
    ExecutionRecord,
    ExecutionRun,
    ExecutionService,
    ExecutionStore,
    _ExecutionProgressSink,
)
from agentnb.execution_output import OutputItem
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


def test_execution_service_start_background_code_persists_spawn_failure(
    project_dir: Path, mocker
) -> None:
    runtime = KernelRuntime(backend=Mock())
    service = ExecutionService(runtime)
    mocker.patch("agentnb.execution.subprocess.Popen", side_effect=OSError("spawn failed"))

    with pytest.raises(OSError, match="spawn failed"):
        service.start_background_code(
            project_root=project_dir,
            session_id="default",
            code="1 + 1",
        )

    runs = ExecutionStore(project_dir).read(session_id="default")
    assert len(runs) == 1
    assert runs[0].status == "error"
    assert runs[0].ename == "OSError"
    assert runs[0].evalue == "spawn failed"


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


def test_execution_service_get_run_raises_when_missing(project_dir: Path) -> None:
    with pytest.raises(AgentNBException, match="Execution not found: missing"):
        ExecutionService(KernelRuntime()).get_run(project_root=project_dir, execution_id="missing")


def test_execution_service_wait_for_run_raises_when_missing(project_dir: Path) -> None:
    with pytest.raises(AgentNBException, match="Execution not found: missing"):
        ExecutionService(KernelRuntime()).wait_for_run(
            project_root=project_dir,
            execution_id="missing",
            timeout_s=0.0,
            poll_interval_s=0.0,
        )


def test_execution_service_follow_run_replays_incremental_events(project_dir: Path, mocker) -> None:
    class Sink(ExecutionSink):
        def __init__(self) -> None:
            self.started_calls: list[tuple[str, str]] = []
            self.events: list[ExecutionEvent] = []

        def started(self, *, execution_id: str, session_id: str) -> None:
            self.started_calls.append((execution_id, session_id))

        def accept(self, event: ExecutionEvent) -> None:
            self.events.append(event)

    service = ExecutionService(KernelRuntime())
    sink = Sink()
    running = ExecutionRecord(
        execution_id="run-1",
        ts="2026-03-10T00:00:00+00:00",
        session_id="default",
        command_type="exec",
        status="running",
        duration_ms=0,
        events=[ExecutionEvent(kind="stdout", content="hello\n")],
    )
    finished = ExecutionRecord(
        execution_id="run-1",
        ts="2026-03-10T00:00:01+00:00",
        session_id="default",
        command_type="exec",
        status="ok",
        duration_ms=10,
        result="2",
        events=[
            ExecutionEvent(kind="stdout", content="hello\n"),
            ExecutionEvent(kind="result", content="2"),
        ],
    )
    mocker.patch.object(service, "_load_run", side_effect=[running, finished])

    run = service.follow_run(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=1.0,
        poll_interval_s=0.0,
        event_sink=sink,
    )

    assert run["status"] == "ok"
    assert sink.started_calls == [("run-1", "default")]
    assert sink.events == [
        ExecutionEvent(kind="stdout", content="hello\n"),
        ExecutionEvent(kind="result", content="2"),
    ]


def test_execution_service_cancel_run_interrupts_session(project_dir: Path, mocker) -> None:
    mocker.patch("agentnb.execution.pid_exists", return_value=True)
    kill = mocker.patch("agentnb.execution.os.kill")
    runtime = KernelRuntime(backend=Mock())
    mocker.patch.object(runtime, "interrupt")
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
        timeout_s=0.0,
        poll_interval_s=0.0,
    )
    stored = ExecutionStore(project_dir).get("run-1")

    assert payload["cancel_requested"] is True
    assert payload["status"] == "error"
    assert payload["session_id"] == "analysis"
    assert payload["session_outcome"] == "preserved"
    runtime.interrupt.assert_called_once_with(project_root=project_dir, session_id="analysis")
    kill.assert_called_once()
    assert stored is not None
    assert stored.status == "error"
    assert stored.ename == "CancelledError"


def test_execution_service_cancel_run_returns_finished_state_when_run_completes_after_interrupt(
    project_dir: Path,
    mocker,
) -> None:
    mocker.patch("agentnb.execution.pid_exists", return_value=True)
    kill = mocker.patch("agentnb.execution.os.kill")
    runtime = KernelRuntime(backend=Mock())
    store = ExecutionStore(project_dir)
    store.append(
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

    def interrupt_stub(**kwargs: object) -> None:
        del kwargs
        store.append(
            ExecutionRecord(
                execution_id="run-1",
                ts="2026-03-10T00:00:01+00:00",
                session_id="analysis",
                command_type="exec",
                status="ok",
                duration_ms=7,
                worker_pid=123,
                result="2",
            )
        )

    interrupt = mocker.patch.object(runtime, "interrupt", side_effect=interrupt_stub)

    payload = ExecutionService(runtime).cancel_run(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=0.2,
        poll_interval_s=0.0,
    )
    stored = store.get("run-1")

    assert payload == {
        "execution_id": "run-1",
        "session_id": "analysis",
        "cancel_requested": True,
        "status": "ok",
        "run_status": "ok",
        "session_outcome": "preserved",
    }
    interrupt.assert_called_once_with(project_root=project_dir, session_id="analysis")
    kill.assert_not_called()
    assert stored is not None
    assert stored.status == "ok"
    assert stored.result == "2"


def test_execution_service_cancel_run_stops_starting_session(project_dir: Path, mocker) -> None:
    mocker.patch("agentnb.execution.pid_exists", return_value=True)
    kill = mocker.patch("agentnb.execution.os.kill")
    runtime = KernelRuntime(backend=Mock())
    mocker.patch.object(runtime, "interrupt", side_effect=KernelNotReadyError())
    stop_starting = mocker.patch.object(runtime, "stop_starting")
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
    assert payload["session_id"] == "analysis"
    assert payload["session_outcome"] == "stopped"
    stop_starting.assert_called_once_with(project_root=project_dir, session_id="analysis")
    kill.assert_called_once()
    assert stored is not None
    assert stored.status == "error"
    assert stored.ename == "CancelledError"


def test_execution_service_cancel_run_returns_unchanged_for_finished_run(project_dir: Path) -> None:
    ExecutionStore(project_dir).append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="analysis",
            command_type="exec",
            status="ok",
            duration_ms=7,
            result="2",
        )
    )

    payload = ExecutionService(KernelRuntime()).cancel_run(
        project_root=project_dir,
        execution_id="run-1",
    )

    assert payload == {
        "execution_id": "run-1",
        "session_id": "analysis",
        "cancel_requested": False,
        "status": "ok",
        "run_status": "ok",
        "session_outcome": "unchanged",
    }


def test_execution_service_marks_exited_background_worker_as_error(
    project_dir: Path, mocker
) -> None:
    mocker.patch("agentnb.execution.pid_exists", return_value=False)
    ExecutionStore(project_dir).append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
            worker_pid=123,
            code="1 + 1",
        )
    )

    run = ExecutionService(KernelRuntime()).get_run(project_root=project_dir, execution_id="run-1")

    assert run["status"] == "error"
    assert run["ename"] == "WorkerExitedError"
    assert run["evalue"] == "Background worker exited before recording a result."


def test_execution_service_complete_background_run_persists_streamed_progress(
    project_dir: Path,
    mocker,
) -> None:
    runtime = KernelRuntime(backend=Mock())
    service = ExecutionService(runtime)
    mocker.patch("agentnb.execution.pid_exists", return_value=True)
    ExecutionStore(project_dir).append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
            code="print('hello')",
            worker_pid=123,
        )
    )

    def execute_stub(**kwargs: object) -> ExecutionResult:
        sink = kwargs["event_sink"]
        assert sink is not None
        sink.accept(ExecutionEvent(kind="stdout", content="hello\n"))
        sink.accept(ExecutionEvent(kind="stderr", content="warn\n"))
        sink.accept(ExecutionEvent(kind="result", content="2"))
        sink.accept(
            ExecutionEvent(
                kind="display",
                content="42",
                metadata={"mime": {"text/plain": "42", "text/html": "<b>42</b>"}},
            )
        )
        sink.accept(
            ExecutionEvent(
                kind="error",
                content="boom",
                metadata={"ename": "RuntimeError", "traceback": ["tb"]},
            )
        )
        in_progress = ExecutionStore(project_dir).get("run-1")
        assert in_progress is not None
        assert in_progress.status == "error"
        assert in_progress.stdout == "hello\n"
        assert in_progress.stderr == "warn\n"
        assert in_progress.result == "2\n42"
        assert in_progress.ename == "RuntimeError"
        assert in_progress.evalue == "boom"
        assert in_progress.traceback == ["tb"]
        assert in_progress.events == [
            ExecutionEvent(kind="stdout", content="hello\n"),
            ExecutionEvent(kind="stderr", content="warn\n"),
            ExecutionEvent(kind="result", content="2"),
            ExecutionEvent(
                kind="display",
                content="42",
                metadata={"mime": {"text/plain": "42", "text/html": "<b>42</b>"}},
            ),
            ExecutionEvent(
                kind="error",
                content="boom",
                metadata={"ename": "RuntimeError", "traceback": ["tb"]},
            ),
        ]
        return ExecutionResult(
            status="error",
            stdout="hello\n",
            stderr="warn\n",
            result="2\n42",
            duration_ms=8,
            ename="RuntimeError",
            evalue="boom",
            traceback=["tb"],
            events=[
                ExecutionEvent(kind="stdout", content="hello\n"),
                ExecutionEvent(kind="stderr", content="warn\n"),
                ExecutionEvent(kind="result", content="2"),
                ExecutionEvent(
                    kind="display",
                    content="42",
                    metadata={"mime": {"text/plain": "42", "text/html": "<b>42</b>"}},
                ),
                ExecutionEvent(
                    kind="error",
                    content="boom",
                    metadata={"ename": "RuntimeError", "traceback": ["tb"]},
                ),
            ],
        )

    runtime.execute = Mock(side_effect=execute_stub)  # type: ignore[method-assign]

    service.complete_background_run(project_root=project_dir, execution_id="run-1")

    stored = ExecutionStore(project_dir).get("run-1")
    assert stored is not None
    assert stored.status == "error"
    assert stored.stdout == "hello\n"
    assert stored.stderr == "warn\n"
    assert stored.result == "2\n42"
    assert stored.ename == "RuntimeError"
    assert stored.evalue == "boom"
    assert stored.traceback == ["tb"]
    assert stored.events == [
        ExecutionEvent(kind="stdout", content="hello\n"),
        ExecutionEvent(kind="stderr", content="warn\n"),
        ExecutionEvent(kind="result", content="2"),
        ExecutionEvent(
            kind="display",
            content="42",
            metadata={"mime": {"text/plain": "42", "text/html": "<b>42</b>"}},
        ),
        ExecutionEvent(
            kind="error",
            content="boom",
            metadata={"ename": "RuntimeError", "traceback": ["tb"]},
        ),
    ]


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
