from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from pytest_mock import MockerFixture

from agentnb.errors import InvalidInputError, ProvisioningError
from agentnb.kernel.provisioner import DoctorCheck, InterpreterSelection, KernelProvisioner


def _touch(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")


@pytest.mark.parametrize(
    ("create_project_venv", "create_active_venv", "expected_source"),
    [
        (True, True, "project_venv"),
        (False, True, "active_venv"),
        (False, False, "current_python"),
    ],
)
def test_select_interpreter_precedence(
    project_dir: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mocker: MockerFixture,
    create_project_venv: bool,
    create_active_venv: bool,
    expected_source: str,
) -> None:
    if create_project_venv:
        _touch(project_dir / ".venv" / "bin" / "python")

    if create_active_venv:
        active_venv = tmp_path / "active-venv"
        _touch(active_venv / "bin" / "python")
        monkeypatch.setenv("VIRTUAL_ENV", str(active_venv))
    else:
        monkeypatch.delenv("VIRTUAL_ENV", raising=False)

    supports = mocker.patch("agentnb.kernel.provisioner._python_supports_module", return_value=True)

    selected = KernelProvisioner(project_dir).select_interpreter()

    assert selected.source == expected_source
    assert selected.ipykernel_available is True
    if expected_source == "current_python":
        assert selected.executable == str(Path(sys.executable).absolute())

    supports.assert_called_once()


def test_select_interpreter_honors_explicit_python(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    explicit = Path(sys.executable).absolute()
    _touch(project_dir / ".venv" / "bin" / "python")
    supports = mocker.patch(
        "agentnb.kernel.provisioner._python_supports_module",
        side_effect=[True, True],
    )

    selected = KernelProvisioner(project_dir).select_interpreter(preferred_python=explicit)

    assert selected.source == "explicit"
    assert selected.executable == str(explicit.absolute())
    assert selected.ipykernel_available is True
    supports.assert_any_call(explicit, "sys")


def test_select_interpreter_rejects_non_executable_explicit_python(project_dir: Path) -> None:
    with pytest.raises(InvalidInputError, match="not executable"):
        KernelProvisioner(project_dir).select_interpreter(preferred_python=project_dir)


def test_ensure_ipykernel_raises_when_auto_install_disabled(project_dir: Path) -> None:
    provisioner = KernelProvisioner(project_dir)
    selected = InterpreterSelection(
        executable=str(Path(sys.executable).absolute()),
        source="current_python",
        ipykernel_available=False,
    )

    with pytest.raises(ProvisioningError):
        provisioner.ensure_ipykernel(selected, auto_install=False)


def test_ensure_ipykernel_auto_install_flow(project_dir: Path, mocker: MockerFixture) -> None:
    provisioner = KernelProvisioner(project_dir)
    selected = InterpreterSelection(
        executable=str(Path(sys.executable).absolute()),
        source="current_python",
        ipykernel_available=False,
    )

    run_mock = mocker.patch("agentnb.kernel.provisioner.subprocess.run")
    run_mock.return_value = subprocess.CompletedProcess(args=["python"], returncode=0)
    supports_mock = mocker.patch(
        "agentnb.kernel.provisioner._python_supports_module",
        side_effect=[True, True],  # first: pip check; second: ipykernel_launcher post-install check
    )

    installed = provisioner.ensure_ipykernel(selected, auto_install=True)

    assert installed is True
    run_mock.assert_called_once()
    assert supports_mock.call_count == 2


def test_ensure_ipykernel_auto_install_surfaces_installer_failure(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    provisioner = KernelProvisioner(project_dir)
    selected = InterpreterSelection(
        executable=str(Path(sys.executable).absolute()),
        source="current_python",
        ipykernel_available=False,
    )
    run_mock = mocker.patch("agentnb.kernel.provisioner.subprocess.run")
    run_mock.return_value = subprocess.CompletedProcess(
        args=["python"],
        returncode=1,
        stderr="x" * 500,
    )

    with pytest.raises(ProvisioningError, match="Failed to auto-install ipykernel"):
        provisioner.ensure_ipykernel(selected, auto_install=True)


def test_ensure_ipykernel_auto_install_rechecks_module_availability(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    provisioner = KernelProvisioner(project_dir)
    selected = InterpreterSelection(
        executable=str(Path(sys.executable).absolute()),
        source="current_python",
        ipykernel_available=False,
    )
    mocker.patch(
        "agentnb.kernel.provisioner.subprocess.run",
        return_value=subprocess.CompletedProcess(args=["python"], returncode=0),
    )
    mocker.patch("agentnb.kernel.provisioner._python_supports_module", return_value=False)

    with pytest.raises(ProvisioningError, match="module is still unavailable"):
        provisioner.ensure_ipykernel(selected, auto_install=True)


def test_doctor_reports_warn_for_missing_ipykernel_without_fix(
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    provisioner = KernelProvisioner(project_dir)
    mocker.patch.object(
        provisioner,
        "select_interpreter",
        return_value=InterpreterSelection(
            executable="/python",
            source="explicit",
            ipykernel_available=False,
        ),
    )
    mocker.patch.object(
        provisioner,
        "_check_state_directory",
        return_value=DoctorCheck(name="state_dir", status="ok", message="ok"),
    )
    mocker.patch.object(
        provisioner,
        "_check_socket_bind",
        return_value=DoctorCheck(name="socket", status="ok", message="ok"),
    )

    report = provisioner.doctor(auto_fix=False)

    assert report.ready is False
    ipykernel_check = next(check for check in report.checks if check.name == "ipykernel")
    assert ipykernel_check.status == "warn"


def test_doctor_auto_fix_promotes_missing_ipykernel_to_ok(
    project_dir: Path, mocker: MockerFixture
) -> None:
    provisioner = KernelProvisioner(project_dir)
    mocker.patch.object(
        provisioner,
        "select_interpreter",
        return_value=InterpreterSelection(
            executable="/python",
            source="explicit",
            ipykernel_available=False,
        ),
    )
    ensure_mock = mocker.patch.object(provisioner, "ensure_ipykernel", return_value=True)
    mocker.patch.object(
        provisioner,
        "_check_state_directory",
        return_value=DoctorCheck(name="state_dir", status="ok", message="ok"),
    )
    mocker.patch.object(
        provisioner,
        "_check_socket_bind",
        return_value=DoctorCheck(name="socket", status="ok", message="ok"),
    )

    report = provisioner.doctor(auto_fix=True)

    assert report.ready is True
    ensure_mock.assert_called_once()
    ipykernel_check = next(check for check in report.checks if check.name == "ipykernel")
    assert ipykernel_check.status == "ok"


def test_ensure_ipykernel_uses_uv_when_pip_unavailable(
    project_dir: Path, mocker: MockerFixture
) -> None:
    provisioner = KernelProvisioner(project_dir)
    selected = InterpreterSelection(
        executable=str(Path(sys.executable).absolute()),
        source="current_python",
        ipykernel_available=False,
    )
    run_mock = mocker.patch("agentnb.kernel.provisioner.subprocess.run")
    run_mock.return_value = subprocess.CompletedProcess(args=["uv"], returncode=0)
    mocker.patch(
        "agentnb.kernel.provisioner._python_supports_module",
        side_effect=[False, True],  # pip unavailable, then ipykernel_launcher check passes
    )

    installed = provisioner.ensure_ipykernel(selected, auto_install=True)

    assert installed is True
    call_args = run_mock.call_args[0][0]
    assert call_args[0] == "uv"


def test_ensure_ipykernel_uses_uv_add_when_uv_lock_present(
    project_dir: Path, mocker: MockerFixture
) -> None:
    (project_dir / "uv.lock").write_text("", encoding="utf-8")
    provisioner = KernelProvisioner(project_dir)
    selected = InterpreterSelection(
        executable=str(Path(sys.executable).absolute()),
        source="current_python",
        ipykernel_available=False,
    )
    run_mock = mocker.patch("agentnb.kernel.provisioner.subprocess.run")
    run_mock.return_value = subprocess.CompletedProcess(args=["uv"], returncode=0)
    mocker.patch(
        "agentnb.kernel.provisioner._python_supports_module",
        side_effect=[False, True],
    )

    provisioner.ensure_ipykernel(selected, auto_install=True)

    call_args = run_mock.call_args[0][0]
    assert call_args == ["uv", "add", "ipykernel"]


def test_ensure_ipykernel_no_pip_error_message_suggests_uv(
    project_dir: Path, mocker: MockerFixture
) -> None:
    provisioner = KernelProvisioner(project_dir)
    selected = InterpreterSelection(
        executable=str(Path(sys.executable).absolute()),
        source="current_python",
        ipykernel_available=False,
    )
    run_mock = mocker.patch("agentnb.kernel.provisioner.subprocess.run")
    run_mock.return_value = subprocess.CompletedProcess(
        args=["python"],
        returncode=1,
        stderr="No module named pip",
    )
    mocker.patch(
        "agentnb.kernel.provisioner._python_supports_module",
        return_value=True,  # pip check passes, but pip itself reports no module named pip
    )

    with pytest.raises(ProvisioningError, match="uv"):
        provisioner.ensure_ipykernel(selected, auto_install=True)


def test_doctor_reports_python_selection_errors(project_dir: Path, mocker: MockerFixture) -> None:
    provisioner = KernelProvisioner(project_dir)
    mocker.patch.object(
        provisioner,
        "select_interpreter",
        side_effect=ProvisioningError("bad interpreter"),
    )

    report = provisioner.doctor(auto_fix=False)

    assert report.ready is False
    assert report.selected_python is None
    assert report.python_source is None
    assert report.checks == [
        DoctorCheck(
            name="python",
            status="error",
            message="bad interpreter",
            fix_hint="Provide a valid Python path with --python.",
        )
    ]
