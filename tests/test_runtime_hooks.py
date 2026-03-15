from __future__ import annotations

import os
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from agentnb.contracts import ExecutionResult, KernelStatus
from agentnb.errors import ExecutionTimedOutError
from agentnb.hooks import Hooks
from agentnb.kernel.backend import BackendExecutionTimeout
from agentnb.kernel.provisioner import ProvisionResult
from agentnb.runtime import KernelRuntime
from agentnb.session import SessionInfo, SessionStore


class RecordingHooks(Hooks):
    def __init__(self) -> None:
        self.before_calls: list[tuple[Path, str, str]] = []
        self.after_calls: list[tuple[Path, str, str, ExecutionResult | None, Exception | None]] = []
        self.start_calls: list[tuple[Path, str, SessionInfo]] = []
        self.stop_calls: list[tuple[Path, str, SessionInfo]] = []

    def before_execute(self, project_root: Path, session_id: str, code: str) -> None:
        self.before_calls.append((project_root, session_id, code))

    def after_execute(
        self,
        project_root: Path,
        session_id: str,
        code: str,
        result: ExecutionResult | None,
        error: Exception | None,
    ) -> None:
        self.after_calls.append((project_root, session_id, code, result, error))

    def on_kernel_start(self, project_root: Path, session_id: str, session: SessionInfo) -> None:
        self.start_calls.append((project_root, session_id, session))

    def on_kernel_stop(self, project_root: Path, session_id: str, session: SessionInfo) -> None:
        self.stop_calls.append((project_root, session_id, session))


def _session(project_dir: Path, session_id: str = "default") -> SessionInfo:
    return SessionInfo(
        session_id=session_id,
        pid=os.getpid(),
        connection_file=str(project_dir / ".agentnb" / f"kernel-{session_id}.json"),
        python_executable="/custom/python",
        project_root=str(project_dir),
        started_at="2026-03-11T00:00:00+00:00",
    )


def _save_live_session(project_dir: Path, session_id: str = "default") -> SessionInfo:
    store = SessionStore(project_dir, session_id=session_id)
    session = _session(project_dir, session_id=session_id)
    store.save_session(session)
    store.connection_file.write_text("{}", encoding="utf-8")
    return session


def test_runtime_start_emits_on_kernel_start(project_dir: Path, mocker: MockerFixture) -> None:
    hooks = RecordingHooks()
    backend = mocker.Mock()
    session = _session(project_dir)
    backend.start.return_value = session
    backend.status.return_value = KernelStatus(
        alive=True,
        pid=session.pid,
        python=session.python_executable,
    )

    provisioner = mocker.Mock()
    provisioner.provision.return_value = ProvisionResult(
        executable=session.python_executable,
        source="explicit",
        installed_ipykernel=False,
    )

    runtime = KernelRuntime(backend=backend, hooks=hooks, provisioner_factory=lambda _: provisioner)
    runtime.start(project_root=project_dir)

    assert hooks.start_calls == [(project_dir, "default", session)]


def test_runtime_start_hooks_receive_canonicalized_session_id(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    hooks = RecordingHooks()
    backend = mocker.Mock()
    session = _session(project_dir)
    backend.start.return_value = session
    backend.status.return_value = KernelStatus(
        alive=True,
        pid=session.pid,
        python=session.python_executable,
    )

    provisioner = mocker.Mock()
    provisioner.provision.return_value = ProvisionResult(
        executable=session.python_executable,
        source="explicit",
        installed_ipykernel=False,
    )

    runtime = KernelRuntime(backend=backend, hooks=hooks, provisioner_factory=lambda _: provisioner)
    runtime.start(project_root=project_dir, session_id=" default ")

    assert hooks.start_calls == [(project_dir, "default", session)]


def test_runtime_stop_emits_on_kernel_stop(project_dir: Path, mocker: MockerFixture) -> None:
    hooks = RecordingHooks()
    backend = mocker.Mock()
    session = _save_live_session(project_dir)

    runtime = KernelRuntime(backend=backend, hooks=hooks)
    runtime.stop(project_root=project_dir)

    backend.stop.assert_called_once_with(session)
    assert hooks.stop_calls == [(project_dir, "default", session)]


def test_runtime_stop_hooks_receive_canonicalized_session_id(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    hooks = RecordingHooks()
    backend = mocker.Mock()
    session = _save_live_session(project_dir)

    runtime = KernelRuntime(backend=backend, hooks=hooks)
    runtime.stop(project_root=project_dir, session_id=" default ")

    backend.stop.assert_called_once_with(session)
    assert hooks.stop_calls == [(project_dir, "default", session)]


def test_runtime_execute_emits_before_and_after_hooks_on_success(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    hooks = RecordingHooks()
    backend = mocker.Mock()
    _save_live_session(project_dir)
    result = ExecutionResult(status="ok", result="2", duration_ms=5)
    backend.execute.return_value = result

    runtime = KernelRuntime(backend=backend, hooks=hooks)
    execution = runtime.execute(
        project_root=project_dir,
        session_id="default",
        code="1 + 1",
        timeout_s=5,
    )

    assert execution == result
    assert hooks.before_calls == [(project_dir, "default", "1 + 1")]
    assert hooks.after_calls == [(project_dir, "default", "1 + 1", result, None)]


def test_runtime_execute_hooks_receive_canonicalized_session_id(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    hooks = RecordingHooks()
    backend = mocker.Mock()
    _save_live_session(project_dir)
    result = ExecutionResult(status="ok", result="2", duration_ms=5)
    backend.execute.return_value = result

    runtime = KernelRuntime(backend=backend, hooks=hooks)
    execution = runtime.execute(
        project_root=project_dir,
        session_id=" default ",
        code="1 + 1",
        timeout_s=5,
    )

    assert execution == result
    assert hooks.before_calls == [(project_dir, "default", "1 + 1")]
    assert hooks.after_calls == [(project_dir, "default", "1 + 1", result, None)]


def test_runtime_execute_emits_after_hook_with_translated_timeout_error(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    hooks = RecordingHooks()
    backend = mocker.Mock()
    session = _save_live_session(project_dir)
    backend.execute.side_effect = BackendExecutionTimeout()

    runtime = KernelRuntime(backend=backend, hooks=hooks)

    with pytest.raises(ExecutionTimedOutError) as exc_info:
        runtime.execute(
            project_root=project_dir,
            session_id="default",
            code="sleep()",
            timeout_s=0.1,
        )

    backend.interrupt.assert_called_once_with(session)
    assert hooks.before_calls == [(project_dir, "default", "sleep()")]
    assert hooks.after_calls[0][0:3] == (project_dir, "default", "sleep()")
    assert hooks.after_calls[0][3] is None
    assert hooks.after_calls[0][4] is exc_info.value
