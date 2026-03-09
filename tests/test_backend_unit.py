from __future__ import annotations

import signal
import subprocess
from pathlib import Path

from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from agentnb.backend import STARTUP_CODE, LocalIPythonBackend
from agentnb.provisioner import _python_supports_module
from agentnb.session import SessionInfo


def test_python_supports_module_uses_subprocess_probe(mocker: MockerFixture) -> None:
    run_mock = mocker.patch("agentnb.provisioner.subprocess.run")
    run_mock.return_value = subprocess.CompletedProcess(args=["python"], returncode=0)

    assert _python_supports_module(Path("/usr/bin/python3"), "ipykernel_launcher") is True
    run_mock.assert_called_once()


def test_stop_falls_back_to_sigterm_when_sigkill_is_unavailable(
    tmp_path: Path,
    mocker: MockerFixture,
    monkeypatch: MonkeyPatch,
) -> None:
    backend = LocalIPythonBackend()
    session = SessionInfo(
        session_id="default",
        pid=1234,
        connection_file=str(tmp_path / "kernel-default.json"),
        python_executable="python",
        project_root=str(tmp_path),
        started_at="2026-01-01T00:00:00+00:00",
    )

    monkeypatch.delattr("agentnb.backend.signal.SIGKILL", raising=False)
    mocker.patch("agentnb.backend.pid_exists", side_effect=[True, True, True, True, True])
    mocker.patch("agentnb.backend.time.monotonic", side_effect=[0.0, 1.0, 1.0, 4.0])
    kill_mock = mocker.patch("agentnb.backend.os.kill")

    backend.stop(session, timeout_s=0.0)

    assert kill_mock.call_count == 2
    assert kill_mock.call_args_list[0].args == (1234, signal.SIGTERM)
    assert kill_mock.call_args_list[1].args == (1234, signal.SIGTERM)


def test_start_detaches_kernel_process(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    backend = LocalIPythonBackend()
    process = mocker.Mock(pid=4321)
    popen_mock = mocker.patch("agentnb.backend.subprocess.Popen", return_value=process)
    wait_mock = mocker.patch.object(backend, "_wait_for_ready")
    execute_mock = mocker.patch.object(
        backend,
        "execute",
        return_value=mocker.Mock(status="ok", evalue=None, ename=None),
    )

    session = backend.start(
        project_root=tmp_path,
        state_dir=tmp_path / ".agentnb",
        session_id="default",
        python_executable="/usr/bin/python3",
    )

    assert session.pid == 4321
    popen_mock.assert_called_once()
    assert popen_mock.call_args.kwargs["start_new_session"] is True
    wait_mock.assert_called_once()
    execute_mock.assert_called_once()


def test_startup_code_only_bootstraps_project_path() -> None:
    assert "autoreload" not in STARTUP_CODE
    assert "AGENTNB_PROJECT_ROOT" in STARTUP_CODE
