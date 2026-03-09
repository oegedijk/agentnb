from __future__ import annotations

from pathlib import Path

from pytest_mock import MockerFixture

from agentnb.contracts import KernelStatus
from agentnb.provisioner import DoctorCheck, DoctorReport, ProvisionResult
from agentnb.runtime import KernelRuntime
from agentnb.session import SessionInfo


def test_runtime_start_uses_provisioner_and_passes_python(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    backend = mocker.Mock()
    session = SessionInfo(
        session_id="default",
        pid=12345,
        connection_file=str(project_dir / ".agentnb" / "kernel-default.json"),
        python_executable="/custom/python",
        project_root=str(project_dir),
        started_at="2026-03-08T00:00:00+00:00",
    )
    backend.start.return_value = session
    backend.status.return_value = KernelStatus(alive=True, pid=12345, python="/custom/python")

    provisioner = mocker.Mock()
    provisioner.provision.return_value = ProvisionResult(
        executable="/custom/python",
        source="explicit",
        installed_ipykernel=True,
    )

    runtime = KernelRuntime(backend=backend, provisioner_factory=lambda _: provisioner)
    status, started_new = runtime.start(
        project_root=project_dir,
        python_executable=Path("/custom/python"),
        auto_install=False,
    )

    assert started_new is True
    assert status.alive is True
    provisioner.provision.assert_called_once_with(
        preferred_python=Path("/custom/python"),
        auto_install=False,
    )
    backend.start.assert_called_once()
    assert backend.start.call_args.kwargs["python_executable"] == "/custom/python"


def test_runtime_start_defaults_auto_install_to_false(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    backend = mocker.Mock()
    session = SessionInfo(
        session_id="default",
        pid=12345,
        connection_file=str(project_dir / ".agentnb" / "kernel-default.json"),
        python_executable="/custom/python",
        project_root=str(project_dir),
        started_at="2026-03-09T00:00:00+00:00",
    )
    backend.start.return_value = session
    backend.status.return_value = KernelStatus(alive=True, pid=12345, python="/custom/python")

    provisioner = mocker.Mock()
    provisioner.provision.return_value = ProvisionResult(
        executable="/custom/python",
        source="explicit",
        installed_ipykernel=False,
    )

    runtime = KernelRuntime(backend=backend, provisioner_factory=lambda _: provisioner)
    runtime.start(project_root=project_dir)

    provisioner.provision.assert_called_once_with(
        preferred_python=None,
        auto_install=False,
    )


def test_runtime_doctor_merges_store_metadata(project_dir: Path, mocker: MockerFixture) -> None:
    backend = mocker.Mock()
    provisioner = mocker.Mock()
    provisioner.doctor.return_value = DoctorReport(
        ready=True,
        selected_python="/custom/python",
        python_source="explicit",
        checks=[DoctorCheck(name="python", status="ok", message="ok")],
    )

    runtime = KernelRuntime(backend=backend, provisioner_factory=lambda _: provisioner)

    payload = runtime.doctor(project_root=project_dir)

    assert payload["ready"] is True
    assert payload["session_exists"] is False
    assert payload["stale_session_cleaned"] is False
    assert isinstance(payload["checks"], list)
