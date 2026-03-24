from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import suppress
from pathlib import Path
from unittest.mock import Mock

import pytest

from agentnb.contracts import KernelStatus
from agentnb.errors import (
    AmbiguousSessionError,
    ExecutionTimedOutError,
    KernelDiedError,
    KernelNotReadyError,
    KernelWaitTimedOutError,
    SessionBusyError,
    SessionNotFoundError,
)
from agentnb.execution import ExecutionRecord, ExecutionStore
from agentnb.history import HistoryStore
from agentnb.kernel.backend import BackendExecutionTimeout
from agentnb.runtime import KernelRuntime, KernelWaitResult, RuntimeState, SessionResolutionPolicy
from agentnb.session import SessionInfo, SessionStore
from tests.conftest import TestLocalIPythonBackend
from tests.helpers import create_project_dir, install_fake_clock, reset_integration_kernel

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")


@pytest.fixture(scope="module")
def integration_project_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return create_project_dir(tmp_path_factory.mktemp("runtime-integration"))


@pytest.fixture(scope="module")
def integration_runtime() -> KernelRuntime:
    return KernelRuntime(backend=TestLocalIPythonBackend())


@pytest.fixture(scope="module")
def started_runtime_module(
    integration_runtime: KernelRuntime,
    integration_project_dir: Path,
) -> Iterator[tuple[KernelRuntime, Path]]:
    integration_runtime.start(integration_project_dir)
    try:
        yield integration_runtime, integration_project_dir
    finally:
        with suppress(Exception):
            integration_runtime.stop(integration_project_dir)


@pytest.fixture
def started_runtime(
    started_runtime_module: tuple[KernelRuntime, Path],
) -> Iterator[tuple[KernelRuntime, Path]]:
    runtime, project_dir = started_runtime_module
    reset_integration_kernel(runtime, project_dir)
    yield started_runtime_module


def test_runtime_start_status_stop(runtime: KernelRuntime, project_dir: Path) -> None:
    status, started_new = runtime.start(project_dir)
    assert status.alive is True
    assert started_new is True

    current_status = runtime.status(project_dir)
    assert current_status.alive is True

    runtime.stop(project_dir)
    stopped_status = runtime.status(project_dir)
    assert stopped_status.alive is False


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("x = 41\nx + 1", "42"),
        ("sum([1, 2, 3])", "6"),
    ],
)
def test_runtime_execute_in_running_kernel(
    started_runtime: tuple[KernelRuntime, Path],
    code: str,
    expected: str,
) -> None:
    runtime, project_dir = started_runtime
    execution = runtime.execute(project_root=project_dir, code=code, timeout_s=10)
    assert execution.status == "ok"
    assert execution.result == expected


def test_runtime_timeout_interrupt_leaves_kernel_usable(
    started_runtime: tuple[KernelRuntime, Path],
) -> None:
    runtime, project_dir = started_runtime

    with pytest.raises(ExecutionTimedOutError):
        runtime.execute(project_root=project_dir, code="import time\ntime.sleep(3)", timeout_s=0.2)

    follow_up = runtime.execute(project_root=project_dir, code="1 + 1", timeout_s=5)
    assert follow_up.status == "ok"
    assert follow_up.result == "2"


def test_runtime_reset_clears_namespace(started_runtime: tuple[KernelRuntime, Path]) -> None:
    runtime, project_dir = started_runtime
    runtime.execute(project_root=project_dir, code="x = 123", timeout_s=5)

    runtime.reset(project_root=project_dir, timeout_s=10)
    after_reset = runtime.execute(project_root=project_dir, code="x", timeout_s=5)

    assert after_reset.status == "error"
    assert after_reset.ename in {"NameError", "KeyError"}


def test_runtime_execute_does_not_append_history_by_itself(
    started_runtime: tuple[KernelRuntime, Path],
) -> None:
    runtime, project_dir = started_runtime

    runtime.execute(project_root=project_dir, code="1 + 1", timeout_s=5)
    runtime.reset(project_root=project_dir, timeout_s=5)

    history = HistoryStore(project_dir).read(include_internal=True)
    assert history == []


def test_runtime_execute_reports_kernel_not_ready_when_connection_exists_without_session(
    project_dir: Path,
) -> None:
    runtime = KernelRuntime()
    store = SessionStore(project_dir)
    store.ensure_state_dir()
    store.connection_file.write_text("{}", encoding="utf-8")

    with pytest.raises(KernelNotReadyError):
        runtime.execute(project_root=project_dir, code="1 + 1", timeout_s=5)


def test_runtime_execute_reports_dead_kernel_when_session_exists_but_status_is_not_alive(
    project_dir: Path,
) -> None:
    session = SessionInfo(
        session_id="default",
        pid=os.getpid(),
        connection_file=str(project_dir / ".agentnb" / "kernel-default.json"),
        python_executable="python",
        project_root=str(project_dir),
        started_at="2026-03-09T00:00:00+00:00",
    )
    store = SessionStore(project_dir)
    store.save_session(session)
    store.connection_file.write_text("{}", encoding="utf-8")

    backend = Mock()
    backend.status.return_value = KernelStatus(alive=False)
    runtime = KernelRuntime(backend=backend)

    with pytest.raises(KernelDiedError):
        runtime.execute(project_root=project_dir, code="1 + 1", timeout_s=5)


def test_runtime_list_sessions_reports_alive_entries(project_dir: Path) -> None:
    default_store = SessionStore(project_dir, session_id="default")
    analysis_store = SessionStore(project_dir, session_id="analysis")
    default_store.ensure_state_dir()

    default_store.save_session(
        SessionInfo(
            session_id="default",
            pid=os.getpid(),
            connection_file=str(default_store.connection_file),
            python_executable="python-default",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )
    analysis_store.save_session(
        SessionInfo(
            session_id="analysis",
            pid=os.getpid(),
            connection_file=str(analysis_store.connection_file),
            python_executable="python-analysis",
            project_root=str(project_dir),
            started_at="2026-03-09T00:01:00+00:00",
        )
    )
    default_store.connection_file.write_text("{}", encoding="utf-8")
    analysis_store.connection_file.write_text("{}", encoding="utf-8")

    backend = Mock()
    backend.status.side_effect = [
        KernelStatus(alive=True, pid=111, python="python-default"),
        KernelStatus(alive=True, pid=222, python="python-analysis"),
    ]
    runtime = KernelRuntime(backend=backend)
    runtime.remember_current_session(project_root=project_dir, session_id="analysis")

    sessions = runtime.list_sessions(project_root=project_dir)

    assert [session["session_id"] for session in sessions] == ["default", "analysis"]
    assert sessions[0]["is_default"] is True
    assert sessions[1]["is_default"] is False
    assert sessions[0]["is_current"] is False
    assert sessions[1]["is_current"] is True


def test_runtime_list_sessions_uses_execution_history_for_last_activity(project_dir: Path) -> None:
    store = SessionStore(project_dir, session_id="analysis")
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="analysis",
            pid=os.getpid(),
            connection_file=str(store.connection_file),
            python_executable="python-analysis",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )
    store.connection_file.write_text("{}", encoding="utf-8")
    ExecutionStore(project_dir).append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="analysis",
            command_type="exec",
            status="ok",
            duration_ms=12,
            result="2",
        )
    )

    backend = Mock()
    backend.status.return_value = KernelStatus(alive=True, pid=222, python="python-analysis")
    runtime = KernelRuntime(backend=backend)

    sessions = runtime.list_sessions(project_root=project_dir)

    assert sessions[0]["session_id"] == "analysis"
    assert sessions[0]["last_activity"] == "2026-03-10T00:00:00+00:00"


def test_runtime_delete_session_stops_alive_kernel(project_dir: Path) -> None:
    store = SessionStore(project_dir, session_id="analysis")
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="analysis",
            pid=os.getpid(),
            connection_file=str(store.connection_file),
            python_executable="python",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )
    store.connection_file.write_text("{}", encoding="utf-8")
    store.log_file.write_text("log", encoding="utf-8")

    backend = Mock()
    backend.status.return_value = KernelStatus(alive=True)
    runtime = KernelRuntime(backend=backend)

    payload = runtime.delete_session(project_root=project_dir, session_id="analysis")

    assert payload["deleted"] is True
    assert payload["stopped_running_kernel"] is True
    backend.stop.assert_called_once()
    assert store.load_session() is None
    assert not store.connection_file.exists()
    assert not store.log_file.exists()


def test_runtime_list_sessions_filters_stale_without_deleting_record(project_dir: Path) -> None:
    store = SessionStore(project_dir, session_id="analysis")
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="analysis",
            pid=999_999,
            connection_file=str(store.connection_file),
            python_executable="python",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )

    sessions = KernelRuntime(backend=Mock()).list_sessions(project_root=project_dir)

    assert sessions == []
    assert store.load_session() is not None


def test_runtime_cleanup_stale_sessions_removes_stale_records(project_dir: Path) -> None:
    store = SessionStore(project_dir, session_id="analysis")
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="analysis",
            pid=999_999,
            connection_file=str(store.connection_file),
            python_executable="python",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )
    store.log_file.write_text("stale", encoding="utf-8")

    deleted = KernelRuntime(backend=Mock()).cleanup_stale_sessions(project_root=project_dir)

    assert deleted == ["analysis"]
    assert store.load_session() is None
    assert not store.log_file.exists()


def test_runtime_delete_session_raises_for_missing_session(project_dir: Path) -> None:
    runtime = KernelRuntime(backend=Mock())

    with pytest.raises(SessionNotFoundError):
        runtime.delete_session(project_root=project_dir, session_id="missing")


def test_runtime_resolve_session_id_uses_only_live_session(project_dir: Path) -> None:
    store = SessionStore(project_dir, session_id="analysis")
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="analysis",
            pid=os.getpid(),
            connection_file=str(store.connection_file),
            python_executable="python",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )
    store.connection_file.write_text("{}", encoding="utf-8")

    backend = Mock()
    backend.status.return_value = KernelStatus(alive=True, pid=123)
    runtime = KernelRuntime(backend=backend)

    resolved = runtime.resolve_session_id(
        project_root=project_dir,
        requested_session_id=None,
        policy=SessionResolutionPolicy(require_live_session=True),
    )

    assert resolved == "analysis"


def test_runtime_resolve_session_id_ignores_current_session_preference_with_multiple_live_sessions(
    project_dir: Path,
) -> None:
    default_store = SessionStore(project_dir, session_id="default")
    analysis_store = SessionStore(project_dir, session_id="analysis")
    default_store.ensure_state_dir()
    for store in (default_store, analysis_store):
        store.save_session(
            SessionInfo(
                session_id=store.session_id,
                pid=os.getpid(),
                connection_file=str(store.connection_file),
                python_executable="python",
                project_root=str(project_dir),
                started_at="2026-03-09T00:00:00+00:00",
            )
        )
        store.connection_file.write_text("{}", encoding="utf-8")

    backend = Mock()
    backend.status.side_effect = [
        KernelStatus(alive=True, pid=111),
        KernelStatus(alive=True, pid=222),
    ]
    runtime = KernelRuntime(backend=backend)
    runtime.remember_current_session(project_root=project_dir, session_id="analysis")

    resolved = runtime.resolve_session_id(
        project_root=project_dir,
        requested_session_id=None,
        policy=SessionResolutionPolicy(require_live_session=True),
    )

    assert resolved == "analysis"


def test_runtime_resolve_session_id_does_not_probe_backend_status(project_dir: Path) -> None:
    store = SessionStore(project_dir, session_id="analysis")
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="analysis",
            pid=os.getpid(),
            connection_file=str(store.connection_file),
            python_executable="python",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )
    store.connection_file.write_text("{}", encoding="utf-8")

    backend = Mock()
    backend.status.side_effect = AssertionError("resolve_session_id should not probe backend")
    runtime = KernelRuntime(backend=backend)

    resolved = runtime.resolve_session_id(
        project_root=project_dir,
        requested_session_id=None,
        policy=SessionResolutionPolicy(require_live_session=True),
    )

    assert resolved == "analysis"
    backend.status.assert_not_called()


def test_runtime_resolve_session_id_raises_when_multiple_live_sessions_exist(
    project_dir: Path,
) -> None:
    default_store = SessionStore(project_dir, session_id="default")
    analysis_store = SessionStore(project_dir, session_id="analysis")
    default_store.ensure_state_dir()
    for store in (default_store, analysis_store):
        store.save_session(
            SessionInfo(
                session_id=store.session_id,
                pid=os.getpid(),
                connection_file=str(store.connection_file),
                python_executable="python",
                project_root=str(project_dir),
                started_at="2026-03-09T00:00:00+00:00",
            )
        )
        store.connection_file.write_text("{}", encoding="utf-8")

    backend = Mock()
    backend.status.side_effect = [
        KernelStatus(alive=True, pid=111),
        KernelStatus(alive=True, pid=222),
    ]
    runtime = KernelRuntime(backend=backend)

    with pytest.raises(AmbiguousSessionError):
        runtime.resolve_session_id(
            project_root=project_dir,
            requested_session_id=None,
            policy=SessionResolutionPolicy(require_live_session=True),
        )


def test_runtime_resolve_session_id_can_ignore_current_preference_on_ambiguity(
    project_dir: Path,
) -> None:
    default_store = SessionStore(project_dir, session_id="default")
    analysis_store = SessionStore(project_dir, session_id="analysis")
    default_store.ensure_state_dir()
    for store in (default_store, analysis_store):
        store.save_session(
            SessionInfo(
                session_id=store.session_id,
                pid=os.getpid(),
                connection_file=str(store.connection_file),
                python_executable="python",
                project_root=str(project_dir),
                started_at="2026-03-09T00:00:00+00:00",
            )
        )
        store.connection_file.write_text("{}", encoding="utf-8")

    runtime = KernelRuntime(backend=Mock())
    runtime.remember_current_session(project_root=project_dir, session_id="analysis")

    with pytest.raises(AmbiguousSessionError):
        runtime.resolve_session_id(
            project_root=project_dir,
            requested_session_id=None,
            policy=SessionResolutionPolicy(
                require_live_session=True,
                error_on_multiple_live_sessions=True,
            ),
        )


def test_runtime_resolve_session_id_uses_current_session_preference_when_no_live_sessions(
    project_dir: Path,
) -> None:
    runtime = KernelRuntime(backend=Mock())
    runtime.remember_current_session(project_root=project_dir, session_id="analysis")

    resolved = runtime.resolve_session_id(
        project_root=project_dir,
        requested_session_id=None,
        policy=SessionResolutionPolicy(require_live_session=True),
    )

    assert resolved == "analysis"


def test_runtime_resolve_session_id_uses_current_session_preference_for_non_live_lookup(
    project_dir: Path,
) -> None:
    runtime = KernelRuntime(backend=Mock())
    runtime.remember_current_session(project_root=project_dir, session_id="analysis")

    resolved = runtime.resolve_session_id(
        project_root=project_dir,
        requested_session_id=None,
        policy=SessionResolutionPolicy(require_live_session=False),
    )

    assert resolved == "analysis"


def test_runtime_delete_session_clears_current_session_preference(project_dir: Path) -> None:
    store = SessionStore(project_dir, session_id="analysis")
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="analysis",
            pid=os.getpid(),
            connection_file=str(store.connection_file),
            python_executable="python",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )
    store.connection_file.write_text("{}", encoding="utf-8")

    backend = Mock()
    backend.status.return_value = KernelStatus(alive=False)
    runtime = KernelRuntime(backend=backend)
    runtime.remember_current_session(project_root=project_dir, session_id="analysis")

    runtime.delete_session(project_root=project_dir, session_id="analysis")

    assert runtime.current_session_id(project_root=project_dir) is None


def test_runtime_stop_clears_current_session_preference(project_dir: Path) -> None:
    store = SessionStore(project_dir, session_id="analysis")
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="analysis",
            pid=os.getpid(),
            connection_file=str(store.connection_file),
            python_executable="python",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )
    store.connection_file.write_text("{}", encoding="utf-8")

    backend = Mock()
    runtime = KernelRuntime(backend=backend)
    runtime.remember_current_session(project_root=project_dir, session_id="analysis")

    runtime.stop(project_root=project_dir, session_id="analysis")

    assert runtime.current_session_id(project_root=project_dir) is None


def test_runtime_wait_for_ready_returns_when_status_becomes_alive(
    project_dir: Path, mocker
) -> None:
    backend = Mock()
    runtime = KernelRuntime(backend=backend)

    state_calls = [
        RuntimeState(
            kind="missing",
            session_id="default",
            kernel_status=KernelStatus(alive=False),
        ),
        RuntimeState(
            kind="starting",
            session_id="default",
            kernel_status=KernelStatus(alive=False),
            has_connection_file=True,
        ),
        RuntimeState(
            kind="ready",
            session_id="default",
            kernel_status=KernelStatus(alive=True, pid=123, busy=False),
        ),
    ]
    mocker.patch.object(runtime, "runtime_state", side_effect=state_calls)

    ready = runtime.wait_for_ready(
        project_root=project_dir,
        session_id="default",
        timeout_s=1.0,
        poll_interval_s=0.1,
    )

    assert ready.alive is True
    assert ready.pid == 123


def test_runtime_wait_for_ready_times_out(project_dir: Path, mocker) -> None:
    backend = Mock()
    runtime = KernelRuntime(backend=backend)
    install_fake_clock(mocker, "agentnb.runtime")
    mocker.patch.object(
        runtime,
        "runtime_state",
        return_value=RuntimeState(
            kind="missing",
            session_id="default",
            kernel_status=KernelStatus(alive=False),
        ),
    )

    with pytest.raises(KernelWaitTimedOutError):
        runtime.wait_for_ready(
            project_root=project_dir,
            session_id="default",
            timeout_s=0.5,
            poll_interval_s=0.25,
        )


def test_runtime_status_reports_busy_when_command_lock_exists(project_dir: Path) -> None:
    store = SessionStore(project_dir, session_id="default")
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="default",
            pid=os.getpid(),
            connection_file=str(store.connection_file),
            python_executable="python",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )
    store.connection_file.write_text("{}", encoding="utf-8")
    store.command_lock_file.write_text(str(os.getpid()), encoding="utf-8")

    backend = Mock()
    backend.status.return_value = KernelStatus(alive=True, pid=111, python="python")
    runtime = KernelRuntime(backend=backend)

    status = runtime.status(project_root=project_dir)

    assert status.alive is True
    assert status.busy is True


def test_runtime_execute_session_busy_reports_lock_metadata(project_dir: Path) -> None:
    store = SessionStore(project_dir, session_id="default")
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="default",
            pid=os.getpid(),
            connection_file=str(store.connection_file),
            python_executable="python",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )
    store.connection_file.write_text("{}", encoding="utf-8")
    acquired_at = "2026-03-19T12:00:00+00:00"
    store.command_lock_file.write_text(
        (f'{{"pid": {os.getpid()}, "acquired_at": "{acquired_at}"}}'),
        encoding="utf-8",
    )

    backend = Mock()
    backend.status.return_value = KernelStatus(alive=True, pid=os.getpid(), python="python")
    runtime = KernelRuntime(backend=backend)

    with pytest.raises(SessionBusyError) as exc_info:
        runtime.execute(project_root=project_dir, code="1 + 1", timeout_s=5)

    error_data = exc_info.value.data
    assert error_data["wait_behavior"] == "immediate"
    assert error_data["waited_ms"] == 0
    assert error_data["lock_pid"] == os.getpid()
    assert error_data["lock_acquired_at"] == acquired_at
    assert isinstance(error_data["busy_for_ms"], int)


def test_runtime_execute_timeout_records_recovery_facts(project_dir: Path) -> None:
    store = SessionStore(project_dir, session_id="default")
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="default",
            pid=os.getpid(),
            connection_file=str(store.connection_file),
            python_executable="python",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )
    store.connection_file.write_text("{}", encoding="utf-8")

    backend = Mock()
    backend.execute.side_effect = BackendExecutionTimeout()
    backend.status.return_value = KernelStatus(alive=True, pid=os.getpid(), python="python")
    runtime = KernelRuntime(backend=backend)

    with pytest.raises(ExecutionTimedOutError) as exc_info:
        runtime.execute(project_root=project_dir, code="1 + 1", timeout_s=0.1)

    assert exc_info.value.data == {
        "current_runtime_state": "ready",
        "session_busy": False,
        "interrupt_recommended": False,
        "active_execution_id": None,
    }
    backend.interrupt.assert_called_once()


def test_runtime_state_reports_starting_when_connection_exists_without_session(
    project_dir: Path,
) -> None:
    store = SessionStore(project_dir, session_id="default")
    store.ensure_state_dir()
    store.connection_file.write_text("{}", encoding="utf-8")

    state = KernelRuntime(backend=Mock()).runtime_state(project_root=project_dir)

    assert state.kind == "starting"
    assert state.alive is False
    assert state.session_exists is False


def test_runtime_state_reports_dead_kernel_when_backend_is_not_alive(project_dir: Path) -> None:
    store = SessionStore(project_dir, session_id="default")
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="default",
            pid=os.getpid(),
            connection_file=str(store.connection_file),
            python_executable="python",
            project_root=str(project_dir),
            started_at="2026-03-09T00:00:00+00:00",
        )
    )
    store.connection_file.write_text("{}", encoding="utf-8")

    backend = Mock()
    backend.status.return_value = KernelStatus(
        alive=False,
        pid=123,
        connection_file=str(store.connection_file),
        python="python",
    )

    state = KernelRuntime(backend=backend).runtime_state(project_root=project_dir)

    assert state.kind == "dead"
    assert state.alive is False
    assert state.session_exists is True
    assert state.to_kernel_status().pid == 123


def test_runtime_wait_for_idle_returns_when_status_becomes_not_busy(
    project_dir: Path, mocker
) -> None:
    runtime = KernelRuntime(backend=Mock())
    mocker.patch.object(
        runtime,
        "runtime_state",
        side_effect=[
            RuntimeState(
                kind="busy",
                session_id="default",
                kernel_status=KernelStatus(alive=True, busy=True),
                has_command_lock=True,
            ),
            RuntimeState(
                kind="ready",
                session_id="default",
                kernel_status=KernelStatus(alive=True, busy=False),
            ),
        ],
    )

    idle = runtime.wait_for_idle(
        project_root=project_dir,
        session_id="default",
        timeout_s=1.0,
        poll_interval_s=0.1,
    )

    assert idle.alive is True
    assert idle.busy is False


def test_runtime_wait_for_idle_times_out(project_dir: Path, mocker) -> None:
    runtime = KernelRuntime(backend=Mock())
    install_fake_clock(mocker, "agentnb.runtime")
    mocker.patch.object(
        runtime,
        "runtime_state",
        return_value=RuntimeState(
            kind="busy",
            session_id="default",
            kernel_status=KernelStatus(alive=True, busy=True),
            has_command_lock=True,
        ),
    )

    with pytest.raises(KernelWaitTimedOutError):
        runtime.wait_for_idle(
            project_root=project_dir,
            session_id="default",
            timeout_s=0.5,
            poll_interval_s=0.25,
        )


def test_runtime_wait_for_usable_returns_immediately_when_idle(project_dir: Path, mocker) -> None:
    runtime = KernelRuntime(backend=Mock())
    mocker.patch.object(
        runtime,
        "runtime_state",
        return_value=RuntimeState(
            kind="ready",
            session_id="default",
            kernel_status=KernelStatus(alive=True, pid=123, busy=False),
        ),
    )

    result = runtime.wait_for_usable(
        project_root=project_dir,
        session_id="default",
        timeout_s=1.0,
        poll_interval_s=0.1,
    )

    assert result.status.alive is True
    assert result.status.busy is False
    assert result.waited is False
    assert result.waited_for is None
    assert result.waited_ms == 0
    assert result.initial_runtime_state == "ready"


def test_runtime_wait_for_usable_waits_for_ready_when_not_alive(project_dir: Path, mocker) -> None:
    runtime = KernelRuntime(backend=Mock())
    mocker.patch.object(
        runtime,
        "runtime_state",
        return_value=RuntimeState(
            kind="missing",
            session_id="default",
            kernel_status=KernelStatus(alive=False),
        ),
    )
    wait_until_ready = mocker.patch.object(
        runtime,
        "wait_until_ready",
        return_value=KernelWaitResult(
            status=KernelStatus(alive=True, pid=123, busy=False),
            waited=True,
            waited_for="ready",
            runtime_state="ready",
            waited_ms=50,
            initial_runtime_state="missing",
        ),
    )

    result = runtime.wait_for_usable(
        project_root=project_dir,
        session_id="default",
        timeout_s=1.0,
        poll_interval_s=0.1,
    )

    assert result.status.alive is True
    assert result.waited is True
    assert result.waited_for == "ready"
    assert result.waited_ms == 50
    assert result.initial_runtime_state == "missing"
    wait_until_ready.assert_called_once_with(
        project_root=project_dir,
        session_id="default",
        timeout_s=1.0,
        poll_interval_s=0.1,
    )


def test_runtime_wait_for_usable_waits_for_idle_when_busy(project_dir: Path, mocker) -> None:
    runtime = KernelRuntime(backend=Mock())
    mocker.patch.object(
        runtime,
        "runtime_state",
        return_value=RuntimeState(
            kind="busy",
            session_id="default",
            kernel_status=KernelStatus(alive=True, pid=123, busy=True),
            has_command_lock=True,
        ),
    )
    wait_until_idle = mocker.patch.object(
        runtime,
        "wait_until_idle",
        return_value=KernelWaitResult(
            status=KernelStatus(alive=True, pid=123, busy=False),
            waited=True,
            waited_for="idle",
            runtime_state="ready",
            waited_ms=25,
            initial_runtime_state="busy",
        ),
    )

    result = runtime.wait_for_usable(
        project_root=project_dir,
        session_id="default",
        timeout_s=1.0,
        poll_interval_s=0.1,
    )

    assert result.status.alive is True
    assert result.waited is True
    assert result.waited_for == "idle"
    assert result.runtime_state == "ready"
    assert result.waited_ms == 25
    assert result.initial_runtime_state == "busy"
    wait_until_idle.assert_called_once_with(
        project_root=project_dir,
        session_id="default",
        timeout_s=1.0,
        poll_interval_s=0.1,
    )


def test_runtime_wait_for_ready_raises_when_session_is_dead(project_dir: Path, mocker) -> None:
    runtime = KernelRuntime(backend=Mock())
    mocker.patch.object(
        runtime,
        "runtime_state",
        return_value=RuntimeState(
            kind="dead",
            session_id="default",
            kernel_status=KernelStatus(alive=False, pid=123),
        ),
    )

    with pytest.raises(KernelDiedError):
        runtime.wait_for_ready(
            project_root=project_dir,
            session_id="default",
            timeout_s=1.0,
            poll_interval_s=0.1,
        )


def test_runtime_wait_for_usable_raises_when_session_is_dead(project_dir: Path, mocker) -> None:
    runtime = KernelRuntime(backend=Mock())
    mocker.patch.object(
        runtime,
        "runtime_state",
        return_value=RuntimeState(
            kind="dead",
            session_id="default",
            kernel_status=KernelStatus(alive=False, pid=123),
        ),
    )

    with pytest.raises(KernelDiedError):
        runtime.wait_for_usable(
            project_root=project_dir,
            session_id="default",
            timeout_s=1.0,
            poll_interval_s=0.1,
        )
