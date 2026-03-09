from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from pytest_mock import MockerFixture

from agentnb.cli import main

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")


def _payload(output: str) -> dict[str, object]:
    return json.loads(output)


def test_cli_json_envelope_for_exec_roundtrip(cli_runner: CliRunner, project_dir: Path) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    exec_res = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1 + 1"])
    assert exec_res.exit_code == 0

    payload = _payload(exec_res.output)
    assert payload["schema_version"] == "1.0"
    assert payload["status"] == "ok"
    assert payload["command"] == "exec"
    assert payload["session_id"] == "default"
    assert payload["data"]["result"] == "2"

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


@pytest.mark.parametrize(
    ("args", "stdin", "expected_result", "expected_stdout"),
    [
        (["exec", "--json", "1 + 1"], None, "2", None),
        (["exec", "--json"], "print('hello from stdin')", None, "hello from stdin"),
    ],
)
def test_cli_exec_input_modes(
    cli_runner: CliRunner,
    project_dir: Path,
    args: list[str],
    stdin: str | None,
    expected_result: str | None,
    expected_stdout: str | None,
) -> None:
    start_res = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])
    assert start_res.exit_code == 0

    full_args = [*args[:1], "--project", str(project_dir), *args[1:]]
    exec_res = cli_runner.invoke(main, full_args, input=stdin)
    assert exec_res.exit_code == 0

    payload = _payload(exec_res.output)
    if expected_result is not None:
        assert payload["data"]["result"] == expected_result
    if expected_stdout is not None:
        assert payload["data"]["stdout"].strip() == expected_stdout

    stop_res = cli_runner.invoke(main, ["stop", "--project", str(project_dir), "--json"])
    assert stop_res.exit_code == 0


def test_cli_returns_no_kernel_error(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1+1"])
    assert result.exit_code == 1

    payload = _payload(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "NO_KERNEL"


def test_cli_doctor_returns_diagnostics(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(main, ["doctor", "--project", str(project_dir), "--json"])
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["status"] == "ok"
    assert payload["command"] == "doctor"
    assert "checks" in payload["data"]


def test_cli_start_auto_install_is_opt_in(
    cli_runner: CliRunner,
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    start_mock = mocker.patch("agentnb.cli.runtime.start")
    start_mock.return_value = (
        mocker.Mock(
            to_dict=lambda: {
                "alive": True,
                "pid": 1234,
                "connection_file": str(project_dir / ".agentnb" / "kernel-default.json"),
                "started_at": "2026-03-09T00:00:00+00:00",
                "uptime_s": 0.0,
                "python": "python",
            }
        ),
        True,
    )

    result = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    assert start_mock.call_args.kwargs["auto_install"] is False


def test_cli_start_auto_install_flag_enables_installs(
    cli_runner: CliRunner,
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    start_mock = mocker.patch("agentnb.cli.runtime.start")
    start_mock.return_value = (
        mocker.Mock(
            to_dict=lambda: {
                "alive": True,
                "pid": 1234,
                "connection_file": str(project_dir / ".agentnb" / "kernel-default.json"),
                "started_at": "2026-03-09T00:00:00+00:00",
                "uptime_s": 0.0,
                "python": "python",
            }
        ),
        True,
    )

    result = cli_runner.invoke(
        main, ["start", "--project", str(project_dir), "--auto-install", "--json"]
    )

    assert result.exit_code == 0
    assert start_mock.call_args.kwargs["auto_install"] is True


def test_cli_root_help_is_shown_without_arguments(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(main, [])
    assert result.exit_code == 0
    assert "Run `agentnb --help`" in result.output
    assert "Recommended loop:" in result.output
    assert "Prefer --json for agent integrations" in result.output


def test_cli_help_is_comprehensive(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Persistent project-scoped Python REPL for agent workflows." in result.output
    assert "append-only notebook" in result.output
    assert "agentnb start --json" in result.output
    assert "--auto-install" in result.output
    assert "doctor --fix" in result.output


def test_cli_json_response_includes_suggestions(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(main, ["status", "--project", str(project_dir), "--json"])
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["status"] == "ok"
    assert payload["command"] == "status"
    assert payload["suggestions"]


def test_cli_human_output_shows_suggestions(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(main, ["status", "--project", str(project_dir)])
    assert result.exit_code == 0
    assert "Kernel is not running." in result.output
    assert "Next:" in result.output
