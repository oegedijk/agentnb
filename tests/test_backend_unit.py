from __future__ import annotations

import signal
import subprocess
from pathlib import Path
from queue import Empty
from unittest.mock import Mock

import pytest
from pytest import MonkeyPatch
from pytest_mock import MockerFixture

from agentnb.backend import BackendOperationError, LocalIPythonBackend, _tail_log, _uptime_seconds
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


def test_start_configures_detached_project_kernel_process(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    backend = LocalIPythonBackend()
    process = mocker.Mock(pid=4321)
    popen_mock = mocker.patch("agentnb.backend.subprocess.Popen", return_value=process)
    mocker.patch.object(backend, "_wait_for_ready")
    mocker.patch.object(
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
    assert session.project_root == str(tmp_path)
    assert session.python_executable == "/usr/bin/python3"
    popen_kwargs = popen_mock.call_args.kwargs
    assert popen_kwargs["cwd"] == str(tmp_path)
    assert popen_kwargs["start_new_session"] is True
    assert popen_kwargs["env"]["AGENTNB_PROJECT_ROOT"] == str(tmp_path)


def test_status_falls_back_to_heartbeat_when_shell_probe_is_busy(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    backend = LocalIPythonBackend()
    connection_file = tmp_path / "kernel-default.json"
    connection_file.write_text("{}", encoding="utf-8")
    session = SessionInfo(
        session_id="default",
        pid=1234,
        connection_file=str(connection_file),
        python_executable="python",
        project_root=str(tmp_path),
        started_at="2026-01-01T00:00:00+00:00",
    )

    client = Mock()
    client.kernel_info.return_value = "msg-1"
    client.get_shell_msg.side_effect = Empty()
    client.is_alive.return_value = True
    mocker.patch.object(backend, "_create_client", return_value=client)
    mocker.patch("agentnb.backend.pid_exists", return_value=True)

    status = backend.status(session, timeout_s=0.1)

    assert status.alive is True
    client.start_channels.assert_called_once_with(
        shell=True,
        iopub=False,
        stdin=False,
        hb=True,
        control=False,
    )


def test_execute_raises_when_connection_file_is_missing(tmp_path: Path) -> None:
    backend = LocalIPythonBackend()
    session = SessionInfo(
        session_id="default",
        pid=1234,
        connection_file=str(tmp_path / "kernel-default.json"),
        python_executable="python",
        project_root=str(tmp_path),
        started_at="2026-01-01T00:00:00+00:00",
    )

    with pytest.raises(BackendOperationError, match="connection file is missing"):
        backend.execute(session, "1 + 1", timeout_s=1.0)


def test_wait_for_ready_includes_kernel_log_when_process_exits_early(
    tmp_path: Path,
    mocker: MockerFixture,
) -> None:
    backend = LocalIPythonBackend()
    log_file = tmp_path / "kernel-default.log"
    log_file.write_text("kernel boot failed", encoding="utf-8")
    session = SessionInfo(
        session_id="default",
        pid=1234,
        connection_file=str(tmp_path / "kernel-default.json"),
        python_executable="python",
        project_root=str(tmp_path),
        started_at="2026-01-01T00:00:00+00:00",
    )
    process = mocker.Mock()
    process.poll.return_value = 7

    with pytest.raises(BackendOperationError, match="kernel boot failed"):
        backend._wait_for_ready(session, process=process, timeout_s=0.0, log_file=log_file)


def test_reset_reapplies_startup_code_after_success(mocker: MockerFixture, tmp_path: Path) -> None:
    backend = LocalIPythonBackend(startup_code="print('bootstrapped')")
    session = SessionInfo(
        session_id="default",
        pid=1234,
        connection_file=str(tmp_path / "kernel-default.json"),
        python_executable="python",
        project_root=str(tmp_path),
        started_at="2026-01-01T00:00:00+00:00",
    )
    execute_mock = mocker.patch.object(
        backend,
        "execute",
        side_effect=[
            mocker.Mock(status="ok", duration_ms=5),
            mocker.Mock(status="ok", duration_ms=2),
        ],
    )

    result = backend.reset(session, timeout_s=1.0)

    assert result.status == "ok"
    assert execute_mock.call_count == 2
    assert execute_mock.call_args_list[1].args[1] == "print('bootstrapped')"


def test_tail_log_and_uptime_handle_edge_cases(tmp_path: Path) -> None:
    log_file = tmp_path / "kernel.log"
    log_file.write_text("x" * 500, encoding="utf-8")

    assert len(_tail_log(log_file, max_chars=40)) == 40
    assert _tail_log(tmp_path / "missing.log") == ""
    assert _uptime_seconds("not-a-timestamp") is None
