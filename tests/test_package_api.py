from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

import pytest

from agentnb import (
    DoctorStatus,
    __all__,
    doctor_environment,
    execute_code,
    start_kernel,
    status_kernel,
    stop_kernel,
)
from agentnb.contracts import ExecutionResult, KernelStatus


class FakeRuntime:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def start(self, **kwargs: object) -> tuple[KernelStatus, bool]:
        self.calls.append(("start", kwargs))
        return KernelStatus(alive=True, pid=123), True

    def status(self, **kwargs: object) -> KernelStatus:
        self.calls.append(("status", kwargs))
        return KernelStatus(alive=True, pid=123)

    def execute(self, **kwargs: object) -> ExecutionResult:
        self.calls.append(("execute", kwargs))
        return ExecutionResult(status="ok", result="2", duration_ms=5)

    def stop(self, **kwargs: object) -> None:
        self.calls.append(("stop", kwargs))

    def doctor_status(self, **kwargs: object) -> DoctorStatus:
        self.calls.append(("doctor_status", kwargs))
        return DoctorStatus(
            ready=True,
            selected_python=None,
            python_source=None,
            checks=[],
            stale_session_cleaned=False,
            session_exists=False,
            kernel_alive=False,
            kernel_pid=None,
        )


def test_package_api_exports_expected_symbols() -> None:
    assert "start_kernel" in __all__
    assert "status_kernel" in __all__
    assert "execute_code" in __all__
    assert "stop_kernel" in __all__
    assert "doctor_environment" in __all__
    assert "DoctorStatus" in __all__


def test_package_api_wrapper_functions_delegate(monkeypatch, project_dir: Path) -> None:
    runtime = FakeRuntime()
    monkeypatch.setattr("agentnb.KernelRuntime", lambda: runtime)
    monkeypatch.setattr("agentnb.resolve_project_root", lambda override: project_dir)

    status, started = start_kernel(project=project_dir, session_id="analysis")
    current = status_kernel(project=project_dir, session_id="analysis")
    execution = execute_code("1 + 1", project=project_dir, timeout_s=7, session_id="analysis")
    stop_kernel(project=project_dir, session_id="analysis")
    doctor = doctor_environment(
        project=project_dir,
        python_executable=project_dir / ".venv" / "bin" / "python",
        session_id="analysis",
    )

    assert status.alive is True
    assert started is True
    assert current.alive is True
    assert execution.result == "2"
    assert isinstance(doctor, DoctorStatus)
    assert doctor.ready is True
    assert runtime.calls == [
        (
            "start",
            {
                "project_root": project_dir,
                "session_id": "analysis",
            },
        ),
        (
            "status",
            {
                "project_root": project_dir,
                "session_id": "analysis",
            },
        ),
        (
            "execute",
            {
                "project_root": project_dir,
                "session_id": "analysis",
                "code": "1 + 1",
                "timeout_s": 7,
            },
        ),
        (
            "stop",
            {
                "project_root": project_dir,
                "session_id": "analysis",
            },
        ),
        (
            "doctor_status",
            {
                "project_root": project_dir,
                "session_id": "analysis",
                "python_executable": project_dir / ".venv" / "bin" / "python",
            },
        ),
    ]


def test_start_kernel_rejects_removed_auto_install_kwarg(project_dir: Path) -> None:
    with pytest.raises(TypeError):
        start_kernel(project=project_dir, auto_install=True)  # type: ignore[call-arg]


def test_doctor_environment_rejects_removed_auto_fix_kwarg(project_dir: Path) -> None:
    with pytest.raises(TypeError):
        doctor_environment(project=project_dir, auto_fix=True)  # type: ignore[call-arg]


def test_package_main_module_invokes_cli(project_dir: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentnb",
            "status",
            "--project",
            str(project_dir),
            "--json",
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0
    assert '"command": "status"' in completed.stdout


def test_package_dunder_main_calls_cli_main(monkeypatch) -> None:
    called: list[str] = []

    monkeypatch.setattr("agentnb.cli.main", lambda: called.append("called"))

    runpy.run_module("agentnb", run_name="__main__")

    assert called == ["called"]
