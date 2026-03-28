from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Literal, cast
from unittest.mock import Mock

import pytest

from agentnb.contracts import (
    ExecutionEvent,
    ExecutionResult,
    ExecutionSink,
    HelperAccessMetadata,
    KernelStatus,
)
from agentnb.errors import AgentNBException, StateCompatibilityError
from agentnb.execution import (
    ExecutionCommandRequest,
    ExecutionRecord,
    ExecutionRun,
    ExecutionService,
    ExecutionStore,
    RunRetrievalOutcome,
    RunRetrievalRequest,
    RunSelectionRequest,
    RunSelectorCandidate,
    SessionAccessOutcome,
    _ExecutionProgressSink,
)
from agentnb.execution_output import OutputItem
from agentnb.history import HistoryRecord
from agentnb.recording import CommandRecorder
from agentnb.runs import ManagedExecution, RunHandle, RunSpec
from agentnb.runs.models import RunObservationResult
from agentnb.runtime import KernelRuntime, KernelWaitResult, RuntimeState


def _event_sink(kwargs: dict[str, object]) -> ExecutionSink:
    sink = kwargs["event_sink"]
    assert sink is not None
    return cast(ExecutionSink, sink)


def _journal_entries(
    *,
    execution_id: str = "run-1",
    session_id: str = "default",
    command_type: str = "exec",
    code: str | None = None,
    status: str = "ok",
    duration_ms: int = 12,
    error_type: str | None = None,
    failure_origin: Literal["kernel", "control"] | None = None,
    result: str | None = None,
) -> list[HistoryRecord]:
    return (
        CommandRecorder()
        .for_execution(
            command_type=command_type,
            code=code,
        )
        .build_records(
            ts="2026-03-10T00:00:00+00:00",
            session_id=session_id,
            execution_id=execution_id,
            status=status,
            duration_ms=duration_ms,
            error_type=error_type,
            failure_origin=failure_origin,
            result=result,
        )
    )


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
        journal_entries=_journal_entries(code="1 + 1", result="2"),
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


def test_execution_record_from_dict_synthesizes_outputs_for_legacy_records() -> None:
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

    assert record.outputs == [OutputItem.result(text="2", mime={})]
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


def test_execution_store_rejects_terminal_exec_without_persisted_journal_entries(
    project_dir: Path,
) -> None:
    store = ExecutionStore(project_dir)
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

    with pytest.raises(StateCompatibilityError):
        store.read(session_id="default")


def test_execution_store_preserves_terminal_error_without_synthesizing_error_event(
    project_dir: Path,
) -> None:
    store = ExecutionStore(project_dir)
    record = ExecutionRecord(
        execution_id="run-1",
        ts="2026-03-10T00:00:00+00:00",
        session_id="default",
        command_type="exec",
        status="error",
        duration_ms=12,
        stdout="hello\n",
        ename="ValueError",
        evalue="boom",
        traceback=["tb"],
        outputs=[OutputItem.stdout("hello\n")],
        journal_entries=_journal_entries(status="error", error_type="ValueError"),
    )

    store.append(record)
    stored = store.get("run-1")

    assert stored is not None
    assert stored.status == "error"
    assert stored.stdout == "hello\n"
    assert stored.ename == "ValueError"
    assert stored.evalue == "boom"
    assert stored.traceback == ["tb"]
    assert stored.outputs == [OutputItem.stdout("hello\n")]
    assert stored.events == [ExecutionEvent(kind="stdout", content="hello\n")]


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
            journal_entries=_journal_entries(code="1 + 1", result="2"),
        )
    )

    entries = store.read(session_id="default")

    assert len(entries) == 1
    assert entries[0].status == "ok"
    assert entries[0].result == "2"


def test_execution_store_merges_cancel_provenance_into_later_terminal_record(
    project_dir: Path,
) -> None:
    store = ExecutionStore(project_dir)
    store.append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
            code="sleep()",
            cancel_requested=True,
            cancel_requested_at="2026-03-10T00:00:01+00:00",
            cancel_request_source="user",
        )
    )
    store.append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="error",
            duration_ms=12,
            code="sleep()",
            ename="KeyboardInterrupt",
            evalue="interrupted",
            traceback=["tb"],
            journal_entries=_journal_entries(
                code="sleep()",
                status="error",
                error_type="KeyboardInterrupt",
            ),
        )
    )

    stored = store.get("run-1")

    assert stored is not None
    assert stored.cancel_requested is True
    assert stored.cancel_requested_at == "2026-03-10T00:00:01+00:00"
    assert stored.cancel_request_source == "user"
    assert stored.terminal_reason == "cancelled"
    assert stored.status == "error"
    assert stored.ename == "CancelledError"
    assert stored.evalue == "Run was cancelled by user."
    assert stored.traceback is None
    assert stored.recorded_status == "error"
    assert stored.recorded_ename == "KeyboardInterrupt"
    assert stored.recorded_evalue == "interrupted"
    assert stored.recorded_traceback == ["tb"]


def test_execution_store_errors_only_reads_preserve_merged_cancel_provenance(
    project_dir: Path,
) -> None:
    store = ExecutionStore(project_dir)
    store.append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
            code="sleep()",
            cancel_requested=True,
            cancel_requested_at="2026-03-10T00:00:01+00:00",
            cancel_request_source="user",
        )
    )
    store.append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="error",
            duration_ms=12,
            code="sleep()",
            ename="KeyboardInterrupt",
            evalue="interrupted",
            traceback=["tb"],
            journal_entries=_journal_entries(
                code="sleep()",
                status="error",
                error_type="KeyboardInterrupt",
            ),
        )
    )

    entries = store.read(session_id="default", errors_only=True)

    assert len(entries) == 1
    assert entries[0].terminal_reason == "cancelled"
    assert entries[0].cancel_requested is True
    assert entries[0].ename == "CancelledError"
    assert entries[0].recorded_ename == "KeyboardInterrupt"


def test_execution_store_projects_cancelled_journal_entries_from_merged_terminal_record(
    project_dir: Path,
) -> None:
    store = ExecutionStore(project_dir)
    store.append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
            cancel_requested=True,
            cancel_requested_at="2026-03-10T00:00:01+00:00",
            cancel_request_source="user",
        )
    )
    store.append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="error",
            duration_ms=12,
            ename="KeyboardInterrupt",
            evalue="interrupted",
            journal_entries=[
                HistoryRecord(
                    kind="kernel_execution",
                    classification="internal",
                    provenance_detail="kernel_execution",
                    ts="2026-03-10T00:00:00+00:00",
                    session_id="default",
                    execution_id="run-1",
                    status="error",
                    duration_ms=12,
                    command_type="exec",
                    label="exec kernel execution",
                    user_visible=False,
                    error_type="KeyboardInterrupt",
                )
            ],
        )
    )

    stored = store.get("run-1")

    assert stored is not None
    assert stored.journal_entries[0].error_type == "CancelledError"


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

    managed = service.execute(
        ExecutionCommandRequest(
            project_root=project_dir,
            session_id="default",
            command_type="exec",
            code="1 + 1",
            timeout_s=5,
        )
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

    managed = service.execute(
        ExecutionCommandRequest(
            project_root=project_dir,
            session_id="default",
            command_type="exec",
            code="print('hello')",
            timeout_s=5,
            event_sink=sink,
        )
    )

    stored = ExecutionStore(project_dir).get(managed.record.execution_id)
    assert sink.started_execution_id == managed.record.execution_id
    assert sink.started_session_id == "default"
    assert sink.events == [ExecutionEvent(kind="stdout", content="hello\n")]
    assert stored is not None
    assert stored.execution_id == managed.record.execution_id
    assert stored.stdout == "hello\n"


def test_execution_service_retrieve_run_raises_when_missing(project_dir: Path) -> None:
    with pytest.raises(AgentNBException, match="Execution not found: missing"):
        ExecutionService(KernelRuntime()).retrieve_run(
            RunRetrievalRequest(project_root=project_dir, execution_id="missing")
        )


def test_execution_service_execute_background_delegates_to_run_manager(
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
    result = service.execute(
        ExecutionCommandRequest(
            project_root=project_dir,
            session_id="default",
            command_type="exec",
            mode="background",
            code="1 + 1",
            ensure_started=True,
        )
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


def test_execution_service_execute_reset_delegates_to_run_manager(project_dir: Path) -> None:
    runtime = KernelRuntime(backend=Mock())
    run_manager = Mock()
    managed = ManagedExecution(
        record=ExecutionRecord(
            execution_id="run-reset",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="reset",
            status="ok",
            duration_ms=5,
        )
    )
    run_manager.submit.return_value = managed

    service = ExecutionService(runtime, run_manager=run_manager)
    result = service.execute(
        ExecutionCommandRequest(
            project_root=project_dir,
            session_id="default",
            command_type="reset",
            timeout_s=9.0,
        )
    )

    assert result is managed
    run_manager.submit.assert_called_once()
    spec = run_manager.submit.call_args.args[0]
    assert isinstance(spec, RunSpec)
    assert spec.project_root == project_dir.resolve()
    assert spec.session_id == "default"
    assert spec.command_type == "reset"
    assert spec.code is None
    assert spec.mode == "foreground"
    assert spec.timeout_s == 9.0


def test_execution_service_lists_typed_run_selector_candidates(project_dir: Path) -> None:
    runtime = KernelRuntime(backend=Mock())
    run_manager = Mock()
    run_manager.list_run_selector_candidates.return_value = [
        RunSelectorCandidate(
            execution_id="run-starting",
            ts="2026-03-10T00:00:00+00:00",
            session_id="analysis",
            status="starting",
        ),
        RunSelectorCandidate(
            execution_id="run-running",
            ts="2026-03-10T00:00:01+00:00",
            session_id="analysis",
            status="running",
        ),
        RunSelectorCandidate(
            execution_id="run-ok",
            ts="2026-03-10T00:00:02+00:00",
            session_id="analysis",
            status="ok",
        ),
        RunSelectorCandidate(
            execution_id="run-error",
            ts="2026-03-10T00:00:03+00:00",
            session_id="analysis",
            status="error",
        ),
    ]
    service = ExecutionService(runtime, run_manager=run_manager)

    candidates = service.list_run_selector_candidates(
        request=RunSelectionRequest(
            project_root=project_dir,
            session_id="analysis",
        )
    )

    assert candidates == [
        RunSelectorCandidate(
            execution_id="run-starting",
            ts="2026-03-10T00:00:00+00:00",
            session_id="analysis",
            status="starting",
        ),
        RunSelectorCandidate(
            execution_id="run-running",
            ts="2026-03-10T00:00:01+00:00",
            session_id="analysis",
            status="running",
        ),
        RunSelectorCandidate(
            execution_id="run-ok",
            ts="2026-03-10T00:00:02+00:00",
            session_id="analysis",
            status="ok",
        ),
        RunSelectorCandidate(
            execution_id="run-error",
            ts="2026-03-10T00:00:03+00:00",
            session_id="analysis",
            status="error",
        ),
    ]
    assert all(not hasattr(candidate, "result") for candidate in candidates)
    run_manager.list_run_selector_candidates.assert_called_once_with(
        project_root=project_dir,
        session_id="analysis",
    )


def test_execution_service_wait_for_session_access_uses_runtime_ready(project_dir: Path) -> None:
    runtime = KernelRuntime(backend=Mock())
    wait_until_ready = Mock(
        return_value=KernelWaitResult(
            status=KernelStatus(alive=True, pid=123, busy=False),
            waited=True,
            waited_for="ready",
            runtime_state="ready",
            waited_ms=25,
            initial_runtime_state="starting",
        )
    )
    runtime.wait_until_ready = wait_until_ready  # type: ignore[method-assign]
    service = ExecutionService(runtime)

    access = service.wait_for_session_access(
        project_root=project_dir,
        session_id="default",
        timeout_s=3.0,
        target="ready",
    )

    assert access == SessionAccessOutcome(
        status=KernelStatus(alive=True, pid=123, busy=False),
        waited=True,
        waited_for="ready",
        runtime_state="ready",
        waited_ms=25,
        initial_runtime_state="starting",
    )
    wait_until_ready.assert_called_once_with(
        project_root=project_dir.resolve(),
        session_id="default",
        timeout_s=3.0,
        poll_interval_s=0.1,
    )


def test_execution_service_wait_for_session_access_waits_for_active_run_when_idle(
    project_dir: Path,
) -> None:
    runtime = KernelRuntime(backend=Mock())
    wait_until_idle = Mock(
        side_effect=[
            KernelWaitResult(
                status=KernelStatus(alive=True, pid=123, busy=False),
                waited=False,
                waited_for="idle",
                runtime_state="ready",
                initial_runtime_state="ready",
            ),
            KernelWaitResult(
                status=KernelStatus(alive=True, pid=123, busy=False),
                waited=False,
                waited_for="idle",
                runtime_state="ready",
                initial_runtime_state="ready",
            ),
        ]
    )
    runtime.wait_until_idle = wait_until_idle  # type: ignore[method-assign]
    run_manager = Mock()
    run_manager.active_run_for_session.side_effect = [
        RunHandle(execution_id="run-1", session_id="default", command_type="exec"),
        None,
    ]
    run_manager.wait_for_run.return_value = {
        "execution_id": "run-1",
        "session_id": "default",
        "status": "ok",
    }
    service = ExecutionService(runtime, run_manager=run_manager)

    access = service.wait_for_session_access(
        project_root=project_dir,
        session_id="default",
        timeout_s=5.0,
        target="idle",
    )

    assert access.waited is True
    assert access.waited_for == "idle"
    assert access.initial_runtime_state == "ready"
    assert wait_until_idle.call_count == 2
    run_manager.wait_for_run.assert_called_once()


def test_execution_service_wait_for_session_access_wraps_helper_access(project_dir: Path) -> None:
    runtime = KernelRuntime(backend=Mock())
    runtime.runtime_state = Mock(  # type: ignore[method-assign]
        return_value=RuntimeState(
            kind="ready",
            session_id="default",
            kernel_status=KernelStatus(alive=True, pid=123, busy=False),
        )
    )
    run_manager = Mock()
    run_manager.wait_for_helper_session_access.return_value = HelperAccessMetadata(
        waited=True,
        waited_for="idle",
        waited_ms=18,
        initial_runtime_state="busy",
        blocking_execution_id="run-7",
    )
    service = ExecutionService(runtime, run_manager=run_manager)

    access = service.wait_for_session_access(
        project_root=project_dir,
        session_id="default",
        timeout_s=2.0,
        target="helper",
    )

    assert access.status == KernelStatus(alive=True, pid=123, busy=False)
    assert access.waited is True
    assert access.waited_for == "idle"
    assert access.waited_ms == 18
    assert access.initial_runtime_state == "busy"
    assert access.blocking_execution_id == "run-7"
    run_manager.wait_for_helper_session_access.assert_called_once_with(
        project_root=project_dir.resolve(),
        session_id="default",
        timeout_s=2.0,
        poll_interval_s=0.1,
    )


def test_execution_service_retrieve_run_returns_follow_outcome_when_window_elapses(
    project_dir: Path,
) -> None:
    runtime = KernelRuntime(backend=Mock())
    run_manager = Mock()
    run_manager.follow_run.return_value = RunObservationResult(
        run=ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
        ),
        completion_reason="window_elapsed",
    )
    service = ExecutionService(runtime, run_manager=run_manager)

    outcome = service.retrieve_run(
        RunRetrievalRequest(
            project_root=project_dir,
            execution_id="run-1",
            mode="follow",
            timeout_s=3.0,
        )
    )

    assert outcome == RunRetrievalOutcome(
        run=run_manager.follow_run.return_value.run,
        completion_reason="window_elapsed",
        replayed_event_count=0,
        emitted_event_count=0,
    )


def test_execution_service_retrieve_run_returns_get_outcome(project_dir: Path) -> None:
    runtime = KernelRuntime(backend=Mock())
    run_manager = Mock()
    record = ExecutionRecord(
        execution_id="run-1",
        ts="2026-03-10T00:00:00+00:00",
        session_id="default",
        command_type="exec",
        status="ok",
        duration_ms=5,
        result="2",
    )
    run_manager.get_run.return_value = record
    service = ExecutionService(runtime, run_manager=run_manager)

    outcome = service.retrieve_run(
        RunRetrievalRequest(project_root=project_dir, execution_id="run-1")
    )

    assert outcome == RunRetrievalOutcome(run=record)
    run_manager.get_run.assert_called_once_with(
        project_root=project_dir.resolve(),
        execution_id="run-1",
    )


def test_execution_service_retrieve_run_uses_follow_mode(project_dir: Path) -> None:
    runtime = KernelRuntime(backend=Mock())
    run_manager = Mock()
    run_manager.follow_run.return_value = RunObservationResult(
        run=ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="ok",
            duration_ms=5,
            result="2",
        ),
        completion_reason="terminal",
    )
    service = ExecutionService(runtime, run_manager=run_manager)

    outcome = service.retrieve_run(
        RunRetrievalRequest(
            project_root=project_dir,
            execution_id="run-1",
            mode="follow",
            timeout_s=3.0,
        )
    )

    assert outcome.run.result == "2"
    assert outcome.completion_reason == "terminal"
    run_manager.follow_run.assert_called_once_with(
        project_root=project_dir.resolve(),
        execution_id="run-1",
        timeout_s=3.0,
        poll_interval_s=0.1,
        observer=None,
        skip_history=False,
    )
    run_manager.get_run.assert_not_called()


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
