from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal, cast
from unittest.mock import Mock

import pytest

from agentnb.contracts import ExecutionEvent, ExecutionResult, ExecutionSink, KernelStatus
from agentnb.errors import (
    AgentNBException,
    KernelNotReadyError,
    NoKernelRunningError,
    RunWaitTimedOutError,
    SessionBusyError,
)
from agentnb.execution import ExecutionRecord, ExecutionStore
from agentnb.kernel.backend import BackendCapabilities
from agentnb.runs import LocalRunManager, RunSpec
from agentnb.runtime import KernelRuntime, KernelWaitResult, RuntimeState


def _runtime(*, supports_interrupt: bool = True) -> KernelRuntime:
    backend = Mock()
    backend.capabilities = BackendCapabilities(
        supports_stream=True,
        supports_interrupt=supports_interrupt,
        supports_background=False,
        supports_artifacts=False,
    )
    return KernelRuntime(backend=backend)


def _active_record(
    *,
    status: Literal["starting", "running"] = "running",
    code: str | None = "1 + 1",
    worker_pid: int | None = 123,
) -> ExecutionRecord:
    return ExecutionRecord(
        execution_id="run-1",
        ts="2026-03-10T00:00:00+00:00",
        session_id="default",
        command_type="exec",
        status=status,
        duration_ms=0,
        code=code,
        worker_pid=worker_pid,
    )


@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    [
        ("get_run", {}),
        ("wait_for_run", {"timeout_s": 0.0, "poll_interval_s": 0.0}),
        ("follow_run", {"timeout_s": 0.0, "poll_interval_s": 0.0}),
        ("cancel_run", {"timeout_s": 0.0, "poll_interval_s": 0.0}),
    ],
)
def test_local_run_manager_raises_when_run_is_missing(
    project_dir: Path,
    method_name: str,
    kwargs: dict[str, float],
) -> None:
    manager = LocalRunManager(_runtime())
    method = getattr(manager, method_name)

    with pytest.raises(AgentNBException, match="Execution not found: missing"):
        method(project_root=project_dir, execution_id="missing", **kwargs)


def test_local_run_manager_submit_rejects_unsupported_command_type(project_dir: Path) -> None:
    manager = LocalRunManager(_runtime())

    with pytest.raises(ValueError, match="Unsupported run command type: reload"):
        manager.submit(
            RunSpec(
                project_root=project_dir,
                session_id="default",
                command_type=cast(Any, "reload"),
                code="reload()",
                mode="foreground",
            )
        )


def test_local_run_manager_submit_rejects_background_reset(project_dir: Path) -> None:
    manager = LocalRunManager(_runtime())

    with pytest.raises(ValueError, match="Unsupported run mode for reset: background"):
        manager.submit(
            RunSpec(
                project_root=project_dir,
                session_id="default",
                command_type="reset",
                code=None,
                mode="background",
            )
        )


def test_local_run_manager_cancel_run_requires_interrupt_capability(project_dir: Path) -> None:
    manager = LocalRunManager(_runtime(supports_interrupt=False))

    with pytest.raises(AgentNBException, match="does not support interrupting runs"):
        manager.cancel_run(project_root=project_dir, execution_id="run-1")


def test_local_run_manager_submit_background_persists_starting_record(
    project_dir: Path, mocker
) -> None:
    runtime = _runtime()
    manager = LocalRunManager(runtime)
    popen = mocker.patch("agentnb.runs.executor.subprocess.Popen")
    popen.return_value.pid = 456

    managed = manager.submit(
        RunSpec(
            project_root=project_dir,
            session_id="default",
            command_type="exec",
            code="1 + 1",
            mode="background",
        )
    )

    stored = ExecutionStore(project_dir).get(managed.record.execution_id)
    assert stored is not None
    assert stored.status == "starting"
    assert stored.worker_pid is None
    assert managed.record.status == "running"
    assert managed.record.worker_pid == 456


def test_local_run_manager_get_run_preserves_starting_background_record(project_dir: Path) -> None:
    ExecutionStore(project_dir).append(_active_record(status="starting", worker_pid=None))

    run = LocalRunManager(_runtime()).get_run(project_root=project_dir, execution_id="run-1")

    assert run["status"] == "starting"
    assert run["worker_pid"] is None
    assert run["snapshot_stale"] is True


def test_local_run_manager_submit_background_persists_spawn_failure(
    project_dir: Path, mocker
) -> None:
    runtime = _runtime()
    manager = LocalRunManager(runtime)
    mocker.patch("agentnb.runs.executor.subprocess.Popen", side_effect=OSError("spawn failed"))

    with pytest.raises(OSError, match="spawn failed"):
        manager.submit(
            RunSpec(
                project_root=project_dir,
                session_id="default",
                command_type="exec",
                code="1 + 1",
                mode="background",
            )
        )

    runs = ExecutionStore(project_dir).read(session_id="default")
    assert len(runs) == 1
    assert runs[0].status == "error"
    assert runs[0].ename == "OSError"
    assert runs[0].evalue == "spawn failed"


def test_local_run_manager_submit_background_uses_ensure_started_result(
    project_dir: Path, mocker
) -> None:
    runtime = _runtime()
    ensure_started = mocker.patch.object(runtime, "ensure_started", return_value=(object(), True))
    popen = mocker.patch("agentnb.runs.executor.subprocess.Popen")
    popen.return_value.pid = 456
    manager = LocalRunManager(runtime)

    managed = manager.submit(
        RunSpec(
            project_root=project_dir,
            session_id="default",
            command_type="exec",
            code="1 + 1",
            mode="background",
            ensure_started=True,
        )
    )

    assert managed.start_outcome.started_new_session is True
    assert managed.start_outcome.initial_runtime_state == "missing"
    ensure_started.assert_called_once_with(project_root=project_dir, session_id="default")


def test_local_run_manager_submit_reset_persists_completed_record(project_dir: Path) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.reset.return_value = ExecutionResult(status="ok", duration_ms=6)
    manager = LocalRunManager(runtime)

    managed = manager.submit(
        RunSpec(
            project_root=project_dir,
            session_id="default",
            command_type="reset",
            code=None,
            mode="foreground",
            timeout_s=6.0,
        )
    )

    stored = ExecutionStore(project_dir).get(managed.record.execution_id)
    assert stored is not None
    assert stored.command_type == "reset"
    assert stored.status == "ok"
    runtime.reset.assert_called_once_with(
        project_root=project_dir,
        session_id="default",
        timeout_s=6.0,
    )


def test_local_run_manager_submit_reset_persists_non_kernel_errors(project_dir: Path) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.reset.side_effect = AgentNBException(
        code="EXECUTION_ERROR",
        message="Reset failed",
        ename="RuntimeError",
        evalue="reset failed",
        traceback=["tb"],
    )
    manager = LocalRunManager(runtime)

    with pytest.raises(AgentNBException) as exc_info:
        manager.submit(
            RunSpec(
                project_root=project_dir,
                session_id="default",
                command_type="reset",
                code=None,
                mode="foreground",
                timeout_s=6.0,
            )
        )

    stored = ExecutionStore(project_dir).read(session_id="default")
    assert len(stored) == 1
    assert stored[0].command_type == "reset"
    assert stored[0].status == "error"
    assert stored[0].ename == "RuntimeError"
    assert exc_info.value.code == "EXECUTION_ERROR"
    assert exc_info.value.data["execution_id"] == stored[0].execution_id


def test_local_run_manager_submit_exec_preserves_agent_error_metadata(project_dir: Path) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.execute.side_effect = SessionBusyError(
        wait_behavior="immediate",
        waited_ms=0,
        lock_pid=321,
        lock_acquired_at="2026-03-19T12:00:00+00:00",
        busy_for_ms=1500,
    )
    manager = LocalRunManager(runtime)

    with pytest.raises(AgentNBException) as exc_info:
        manager.submit(
            RunSpec(
                project_root=project_dir,
                session_id="default",
                command_type="exec",
                code="1 + 1",
                mode="foreground",
                timeout_s=6.0,
            )
        )

    stored = ExecutionStore(project_dir).read(session_id="default")
    assert len(stored) == 1
    assert stored[0].status == "error"
    assert stored[0].error_data == {
        "wait_behavior": "immediate",
        "waited_ms": 0,
        "lock_pid": 321,
        "lock_acquired_at": "2026-03-19T12:00:00+00:00",
        "busy_for_ms": 1500,
    }
    assert exc_info.value.code == "SESSION_BUSY"
    assert exc_info.value.data["execution_id"] == stored[0].execution_id
    assert exc_info.value.data["wait_behavior"] == "immediate"
    assert exc_info.value.data["waited_ms"] == 0
    assert exc_info.value.data["lock_pid"] == 321
    assert exc_info.value.data["busy_for_ms"] == 1500


@pytest.mark.parametrize("mode", ["foreground", "background"])
def test_local_run_manager_submit_rejects_active_same_session_run(
    project_dir: Path,
    mocker,
    mode: Literal["foreground", "background"],
) -> None:
    mocker.patch("agentnb.runs.local_manager.pid_exists", return_value=True)
    runtime = _runtime()
    ensure_started = mocker.patch.object(runtime, "ensure_started", return_value=(object(), False))
    execute = mocker.patch.object(runtime, "execute")
    popen = mocker.patch("agentnb.runs.executor.subprocess.Popen")
    manager = LocalRunManager(runtime)
    active = _active_record(status="starting", worker_pid=456)
    ExecutionStore(project_dir).append(active)

    with pytest.raises(AgentNBException) as exc_info:
        manager.submit(
            RunSpec(
                project_root=project_dir,
                session_id="default",
                command_type="exec",
                code="2 + 2",
                mode=mode,
                ensure_started=True,
            )
        )

    runs = ExecutionStore(project_dir).read(session_id="default")
    assert len(runs) == 2
    rejected = next(record for record in runs if record.execution_id != active.execution_id)
    assert rejected.status == "error"
    assert rejected.failure_origin == "control"
    assert rejected.error_data == {
        "wait_behavior": "immediate",
        "waited_ms": 0,
        "lock_pid": 456,
        "active_execution_id": active.execution_id,
    }
    assert exc_info.value.code == "SESSION_BUSY"
    assert exc_info.value.data["execution_id"] == rejected.execution_id
    assert exc_info.value.data["active_execution_id"] == active.execution_id
    ensure_started.assert_not_called()
    execute.assert_not_called()
    popen.assert_not_called()


@pytest.mark.parametrize("error_type", [NoKernelRunningError, KernelNotReadyError])
def test_local_run_manager_submit_reset_does_not_persist_kernel_state_errors(
    project_dir: Path,
    error_type: type[Exception],
) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.reset.side_effect = error_type()
    manager = LocalRunManager(runtime)

    with pytest.raises(error_type):
        manager.submit(
            RunSpec(
                project_root=project_dir,
                session_id="default",
                command_type="reset",
                code=None,
                mode="foreground",
                timeout_s=6.0,
            )
        )

    runtime.reset.assert_called_once_with(
        project_root=project_dir,
        session_id="default",
        timeout_s=6.0,
    )
    assert ExecutionStore(project_dir).read(session_id="default") == []


def test_local_run_manager_wait_for_run_returns_completed_record(project_dir: Path) -> None:
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

    run = LocalRunManager(_runtime()).wait_for_run(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=0.0,
        poll_interval_s=0.0,
    )

    assert run["execution_id"] == "run-1"
    assert run["status"] == "ok"


def test_local_run_manager_helper_access_passes_through_runtime_wait(project_dir: Path) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.runtime_state.return_value = RuntimeState(
        kind="starting",
        session_id="default",
        kernel_status=KernelStatus(alive=False),
        has_connection_file=True,
    )
    runtime.wait_for_usable.return_value = KernelWaitResult(
        status=KernelStatus(alive=True, pid=123, busy=False),
        waited=True,
        waited_for="ready",
        runtime_state="ready",
        waited_ms=20,
        initial_runtime_state="starting",
    )

    access = LocalRunManager(runtime).wait_for_helper_session_access(
        project_root=project_dir,
        session_id="default",
        timeout_s=1.0,
        poll_interval_s=0.0,
    )

    assert access.waited is True
    assert access.waited_for == "ready"
    assert access.waited_ms == 20
    assert access.initial_runtime_state == "starting"
    assert access.blocking_execution_id is None
    runtime.wait_for_usable.assert_called_once()
    kwargs = runtime.wait_for_usable.call_args.kwargs
    assert kwargs["project_root"] == project_dir
    assert kwargs["session_id"] == "default"
    assert kwargs["poll_interval_s"] == 0.0
    assert 0.0 <= kwargs["timeout_s"] <= 1.0


def test_local_run_manager_helper_access_waits_for_active_run(project_dir: Path, mocker) -> None:
    mocker.patch("agentnb.runs.local_manager.pid_exists", return_value=True)
    runtime = Mock(spec=KernelRuntime)
    runtime.runtime_state.return_value = RuntimeState(
        kind="ready",
        session_id="default",
        kernel_status=KernelStatus(alive=True, pid=321, busy=False),
    )
    runtime.wait_for_usable.return_value = KernelWaitResult(
        status=KernelStatus(alive=True, pid=321, busy=False),
        waited=False,
        runtime_state="ready",
        waited_ms=0,
        initial_runtime_state="ready",
    )
    store = ExecutionStore(project_dir)
    store.append(_active_record())

    def sleep_stub(_: float) -> None:
        store.append(
            ExecutionRecord(
                execution_id="run-1",
                ts="2026-03-10T00:00:01+00:00",
                session_id="default",
                command_type="exec",
                status="ok",
                duration_ms=10,
                worker_pid=123,
                result="2",
            )
        )

    mocker.patch("agentnb.runs.local_manager.time.sleep", side_effect=sleep_stub)

    access = LocalRunManager(runtime).wait_for_helper_session_access(
        project_root=project_dir,
        session_id="default",
        timeout_s=1.0,
        poll_interval_s=0.0,
    )

    assert access.waited is True
    assert access.waited_for == "idle"
    assert access.waited_ms >= 0
    assert access.initial_runtime_state == "busy"
    assert access.blocking_execution_id == "run-1"
    runtime.wait_for_usable.assert_called_once()


def test_local_run_manager_helper_access_times_out_behind_active_run(
    project_dir: Path,
    mocker,
) -> None:
    mocker.patch("agentnb.runs.local_manager.pid_exists", return_value=True)
    runtime = Mock(spec=KernelRuntime)
    runtime.runtime_state.return_value = RuntimeState(
        kind="ready",
        session_id="default",
        kernel_status=KernelStatus(alive=True, pid=321, busy=False),
    )
    ExecutionStore(project_dir).append(_active_record())

    with pytest.raises(SessionBusyError) as exc_info:
        LocalRunManager(runtime).wait_for_helper_session_access(
            project_root=project_dir,
            session_id="default",
            timeout_s=0.0,
            poll_interval_s=0.0,
        )

    assert exc_info.value.data["wait_behavior"] == "after_wait"
    assert exc_info.value.data["active_execution_id"] == "run-1"
    assert exc_info.value.data["lock_pid"] == 123
    runtime.wait_for_usable.assert_not_called()


def test_local_run_manager_wait_for_run_times_out(project_dir: Path, mocker) -> None:
    mocker.patch("agentnb.runs.local_manager.pid_exists", return_value=True)
    ExecutionStore(project_dir).append(_active_record())

    with pytest.raises(RunWaitTimedOutError):
        LocalRunManager(_runtime()).wait_for_run(
            project_root=project_dir,
            execution_id="run-1",
            timeout_s=0.0,
            poll_interval_s=0.0,
        )


def test_local_run_manager_follow_run_replays_incremental_events(project_dir: Path, mocker) -> None:
    class Sink(ExecutionSink):
        def __init__(self) -> None:
            self.started_calls: list[tuple[str, str]] = []
            self.events: list[ExecutionEvent] = []

        def started(self, *, execution_id: str, session_id: str) -> None:
            self.started_calls.append((execution_id, session_id))

        def accept(self, event: ExecutionEvent) -> None:
            self.events.append(event)

    mocker.patch("agentnb.runs.local_manager.pid_exists", return_value=True)
    store = ExecutionStore(project_dir)
    store.append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
            worker_pid=123,
            events=[ExecutionEvent(kind="stdout", content="hello\n")],
        )
    )

    def sleep_stub(_: float) -> None:
        store.append(
            ExecutionRecord(
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
        )

    mocker.patch("agentnb.runs.local_manager.time.sleep", side_effect=sleep_stub)

    sink = Sink()
    observation = LocalRunManager(_runtime()).follow_run(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=1.0,
        poll_interval_s=0.1,
        observer=sink,
    )

    assert observation.completion_reason == "terminal"
    assert observation.replayed_event_count == 1
    assert observation.emitted_event_count == 1
    assert observation.run["status"] == "ok"
    assert sink.started_calls == [("run-1", "default")]
    assert sink.events == [
        ExecutionEvent(kind="stdout", content="hello\n"),
        ExecutionEvent(kind="result", content="2"),
    ]


def test_local_run_manager_follow_run_reports_elapsed_window(project_dir: Path, mocker) -> None:
    mocker.patch("agentnb.runs.local_manager.pid_exists", return_value=True)
    ExecutionStore(project_dir).append(_active_record())

    observation = LocalRunManager(_runtime()).follow_run(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=0.0,
        poll_interval_s=0.0,
    )

    assert observation.completion_reason == "window_elapsed"
    assert observation.run["status"] == "running"
    assert observation.run["snapshot_stale"] is True


def test_local_run_manager_cancel_run_interrupts_session(project_dir: Path, mocker) -> None:
    mocker.patch("agentnb.runs.local_manager.pid_exists", return_value=True)
    kill = mocker.patch("agentnb.runs.local_manager.os.kill")
    runtime = _runtime()
    interrupt = mocker.patch.object(runtime, "interrupt")
    ExecutionStore(project_dir).append(_active_record())

    payload = LocalRunManager(runtime).cancel_run(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=0.0,
        poll_interval_s=0.0,
    )
    stored = ExecutionStore(project_dir).get("run-1")

    assert payload["cancel_requested"] is True
    assert payload["status"] == "error"
    assert payload["session_outcome"] == "preserved"
    interrupt.assert_called_once_with(project_root=project_dir, session_id="default")
    kill.assert_called_once()
    assert stored is not None
    assert stored.status == "error"
    assert stored.ename == "CancelledError"
    assert stored.cancel_requested is True
    assert stored.terminal_reason == "cancelled"


def test_local_run_manager_cancel_run_returns_finished_state_when_run_completes_after_interrupt(
    project_dir: Path,
    mocker,
) -> None:
    mocker.patch("agentnb.runs.local_manager.pid_exists", return_value=True)
    kill = mocker.patch("agentnb.runs.local_manager.os.kill")
    runtime = _runtime()
    store = ExecutionStore(project_dir)
    store.append(_active_record())

    def interrupt_stub(**kwargs: object) -> None:
        del kwargs
        store.append(
            ExecutionRecord(
                execution_id="run-1",
                ts="2026-03-10T00:00:01+00:00",
                session_id="default",
                command_type="exec",
                status="ok",
                duration_ms=7,
                worker_pid=123,
                result="2",
            )
        )

    interrupt = mocker.patch.object(runtime, "interrupt", side_effect=interrupt_stub)

    payload = LocalRunManager(runtime).cancel_run(
        project_root=project_dir,
        execution_id="run-1",
        timeout_s=0.2,
        poll_interval_s=0.0,
    )

    assert payload == {
        "execution_id": "run-1",
        "session_id": "default",
        "cancel_requested": True,
        "status": "ok",
        "run_status": "ok",
        "session_outcome": "preserved",
    }
    interrupt.assert_called_once_with(project_root=project_dir, session_id="default")
    kill.assert_not_called()
    stored = store.get("run-1")
    assert stored is not None
    assert stored.cancel_requested is True
    assert stored.terminal_reason == "completed"


def test_local_run_manager_cancel_run_stops_starting_session(project_dir: Path, mocker) -> None:
    kill = mocker.patch("agentnb.runs.local_manager.os.kill")
    runtime = _runtime()
    mocker.patch.object(runtime, "interrupt", side_effect=KernelNotReadyError())
    stop_starting = mocker.patch.object(runtime, "stop_starting")
    ExecutionStore(project_dir).append(_active_record(status="starting", worker_pid=None))

    payload = LocalRunManager(runtime).cancel_run(
        project_root=project_dir,
        execution_id="run-1",
    )
    stored = ExecutionStore(project_dir).get("run-1")

    assert payload["cancel_requested"] is True
    assert payload["status"] == "error"
    assert payload["session_outcome"] == "stopped"
    stop_starting.assert_called_once_with(project_root=project_dir, session_id="default")
    kill.assert_not_called()
    assert stored is not None
    assert stored.status == "error"
    assert stored.ename == "CancelledError"
    assert stored.cancel_requested is True
    assert stored.terminal_reason == "cancelled"


def test_local_run_manager_cancel_run_returns_unchanged_for_finished_run(project_dir: Path) -> None:
    ExecutionStore(project_dir).append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="ok",
            duration_ms=7,
            result="2",
        )
    )

    payload = LocalRunManager(_runtime()).cancel_run(
        project_root=project_dir,
        execution_id="run-1",
    )

    assert payload == {
        "execution_id": "run-1",
        "session_id": "default",
        "cancel_requested": False,
        "status": "ok",
        "run_status": "ok",
        "session_outcome": "unchanged",
    }


def test_local_run_manager_marks_exited_background_worker_as_error(
    project_dir: Path, mocker
) -> None:
    mocker.patch("agentnb.runs.local_manager.pid_exists", return_value=False)
    ExecutionStore(project_dir).append(_active_record())

    run = LocalRunManager(_runtime()).get_run(project_root=project_dir, execution_id="run-1")

    assert run["status"] == "error"
    assert run["ename"] == "WorkerExitedError"
    assert run["evalue"] == "Background worker exited before recording a result."


def test_local_run_manager_marks_exited_background_worker_as_cancelled_after_cancel_request(
    project_dir: Path,
    mocker,
) -> None:
    mocker.patch("agentnb.runs.local_manager.pid_exists", return_value=False)
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
            worker_pid=123,
            cancel_requested=True,
            cancel_requested_at="2026-03-10T00:00:01+00:00",
            cancel_request_source="user",
        )
    )

    run = LocalRunManager(_runtime()).get_run(project_root=project_dir, execution_id="run-1")
    stored = store.get("run-1")

    assert run["status"] == "error"
    assert run["ename"] == "CancelledError"
    assert run["evalue"] == "Run was cancelled by user."
    assert stored is not None
    assert stored.terminal_reason == "cancelled"
    assert stored.recorded_ename == "WorkerExitedError"


def test_local_run_manager_complete_background_run_persists_streamed_progress(
    project_dir: Path,
    mocker,
) -> None:
    runtime = _runtime()
    manager = LocalRunManager(runtime)
    mocker.patch("agentnb.runs.local_manager.pid_exists", return_value=True)
    ExecutionStore(project_dir).append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="starting",
            duration_ms=0,
            code="print('hello')",
            worker_pid=None,
        )
    )

    def execute_stub(**kwargs: object) -> ExecutionResult:
        sink = cast(ExecutionSink, kwargs["event_sink"])
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
        assert in_progress.worker_pid == os.getpid()
        assert in_progress.stdout == "hello\n"
        assert in_progress.stderr == "warn\n"
        assert in_progress.result == "2\n42"
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

    mocker.patch.object(runtime, "execute", side_effect=execute_stub)

    manager.complete_background_run(project_root=project_dir, execution_id="run-1")

    stored = ExecutionStore(project_dir).get("run-1")
    assert stored is not None
    assert stored.status == "error"
    assert stored.stdout == "hello\n"
    assert stored.stderr == "warn\n"
    assert stored.result == "2\n42"
    assert stored.ename == "RuntimeError"
    assert stored.evalue == "boom"
    assert stored.traceback == ["tb"]


@pytest.mark.parametrize(
    "record",
    [
        None,
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="ok",
            duration_ms=1,
            code="1 + 1",
            worker_pid=123,
        ),
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
            code=None,
            worker_pid=123,
        ),
    ],
)
def test_local_run_manager_complete_background_run_noops_for_non_runnable_state(
    project_dir: Path,
    record: ExecutionRecord | None,
    mocker,
) -> None:
    runtime = _runtime()
    execute = mocker.patch.object(runtime, "execute")
    manager = LocalRunManager(runtime)

    if record is not None:
        ExecutionStore(project_dir).append(record)

    manager.complete_background_run(project_root=project_dir, execution_id="run-1")

    execute.assert_not_called()


def test_local_run_manager_complete_background_run_preserves_external_terminal_write(
    project_dir: Path,
    mocker,
) -> None:
    mocker.patch("agentnb.runs.local_manager.pid_exists", return_value=True)
    runtime = _runtime()
    manager = LocalRunManager(runtime)
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
            worker_pid=123,
        )
    )

    def execute_stub(**kwargs: object) -> ExecutionResult:
        del kwargs
        store.append(
            ExecutionRecord(
                execution_id="run-1",
                ts="2026-03-10T00:00:01+00:00",
                session_id="default",
                command_type="exec",
                status="ok",
                duration_ms=4,
                result="external",
            )
        )
        return ExecutionResult(
            status="ok",
            result="internal",
            duration_ms=5,
            events=[ExecutionEvent(kind="result", content="internal")],
        )

    mocker.patch.object(runtime, "execute", side_effect=execute_stub)

    manager.complete_background_run(project_root=project_dir, execution_id="run-1")

    stored = store.get("run-1")
    assert stored is not None
    assert stored.status == "ok"
    assert stored.result == "external"
