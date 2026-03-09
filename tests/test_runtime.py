from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from agentnb.contracts import KernelStatus
from agentnb.errors import ExecutionTimedOutError, KernelNotReadyError
from agentnb.history import HistoryStore
from agentnb.runtime import KernelRuntime
from agentnb.session import SessionInfo, SessionStore
from tests.conftest import TestLocalIPythonBackend
from tests.helpers import cleanup_integration_project, create_project_dir

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
) -> tuple[KernelRuntime, Path]:
    integration_runtime.start(integration_project_dir)
    try:
        yield integration_runtime, integration_project_dir
    finally:
        if integration_runtime.status(integration_project_dir).alive:
            integration_runtime.stop(integration_project_dir)


@pytest.fixture
def started_runtime(
    started_runtime_module: tuple[KernelRuntime, Path],
) -> tuple[KernelRuntime, Path]:
    runtime, project_dir = started_runtime_module
    cleanup_integration_project(runtime, project_dir)
    yield started_runtime_module
    cleanup_integration_project(runtime, project_dir)


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


def test_runtime_execute_reports_kernel_not_ready_when_session_exists_but_status_is_not_alive(
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

    with pytest.raises(KernelNotReadyError):
        runtime.execute(project_root=project_dir, code="1 + 1", timeout_s=5)
