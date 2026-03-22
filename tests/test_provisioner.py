from __future__ import annotations

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


def test_ensure_ipykernel_raises_with_manual_install_command(project_dir: Path) -> None:
    provisioner = KernelProvisioner(project_dir)
    selected = InterpreterSelection(
        executable=str(Path(sys.executable).absolute()),
        source="current_python",
        ipykernel_available=False,
    )

    with pytest.raises(ProvisioningError) as exc_info:
        provisioner.ensure_ipykernel(selected)

    assert "Run manually:" in exc_info.value.message
    assert exc_info.value.recovery_command is not None
    assert exc_info.value.recovery_command in exc_info.value.message
    assert 'agentnb --fresh "..."' in exc_info.value.message


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


def test_doctor_reports_manual_recovery_for_missing_ipykernel(
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
    mocker.patch.object(
        provisioner,
        "_ipykernel_install_cmd",
        return_value=["/python", "-m", "pip", "install", "ipykernel>=6.0"],
    )

    report = provisioner.doctor(auto_fix=True)

    assert report.ready is False
    ipykernel_check = next(check for check in report.checks if check.name == "ipykernel")
    assert ipykernel_check.status == "warn"
    assert ipykernel_check.fix_hint == (
        'Run: /python -m pip install ipykernel>=6.0. Then restart with `agentnb --fresh "..."`.'
    )


def test_ensure_ipykernel_uses_uv_when_pip_unavailable(
    project_dir: Path, mocker: MockerFixture
) -> None:
    provisioner = KernelProvisioner(project_dir)
    selected = InterpreterSelection(
        executable=str(Path(sys.executable).absolute()),
        source="current_python",
        ipykernel_available=False,
    )
    mocker.patch(
        "agentnb.kernel.provisioner._python_supports_module",
        return_value=False,
    )

    with pytest.raises(ProvisioningError) as exc_info:
        provisioner.ensure_ipykernel(selected)

    assert exc_info.value.recovery_command == (
        f"uv pip install --python {Path(sys.executable).absolute()} ipykernel>=6.0"
    )


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
    mocker.patch(
        "agentnb.kernel.provisioner._python_supports_module",
        return_value=False,
    )

    with pytest.raises(ProvisioningError) as exc_info:
        provisioner.ensure_ipykernel(selected)

    assert exc_info.value.recovery_command == "uv add ipykernel"


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
