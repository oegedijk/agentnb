from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner
from pytest_mock import MockerFixture

from agentnb.cli import main
from agentnb.contracts import ExecutionEvent, ExecutionResult
from agentnb.errors import SessionBusyError
from agentnb.execution import ExecutionRecord, ManagedExecution


def _payload(output: str) -> dict[str, object]:
    return json.loads(output)


def _write_module(project_dir: Path, name: str, body: str) -> None:
    (project_dir / f"{name}.py").write_text(body, encoding="utf-8")


def _ok_execution(
    *,
    result: str | None = None,
    stdout: str = "",
    stderr: str = "",
) -> ExecutionResult:
    return ExecutionResult(status="ok", result=result, stdout=stdout, stderr=stderr, duration_ms=5)


def _error_execution(
    *,
    ename: str,
    evalue: str,
    traceback: list[str] | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        status="error",
        ename=ename,
        evalue=evalue,
        traceback=traceback or [f"{ename}: {evalue}"],
        duration_ms=5,
    )


def test_cli_json_envelope_for_exec_roundtrip(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.runtime.execute = lambda **_: _ok_execution(result="2")  # type: ignore[method-assign]
    exec_res = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1 + 1"])
    assert exec_res.exit_code == 0

    payload = _payload(exec_res.output)
    assert payload["schema_version"] == "1.0"
    assert payload["status"] == "ok"
    assert payload["command"] == "exec"
    assert payload["session_id"] == "default"
    assert payload["data"]["result"] == "2"
    assert isinstance(payload["data"]["execution_id"], str)
    assert "events" not in payload["data"]


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
    import agentnb.cli as cli

    cli.runtime.execute = lambda **_: _ok_execution(  # type: ignore[method-assign]
        result=expected_result,
        stdout=f"{expected_stdout}\n" if expected_stdout is not None else "",
    )

    full_args = [*args[:1], "--project", str(project_dir), *args[1:]]
    exec_res = cli_runner.invoke(main, full_args, input=stdin)
    assert exec_res.exit_code == 0

    payload = _payload(exec_res.output)
    if expected_result is not None:
        assert payload["data"]["result"] == expected_result
    if expected_stdout is not None:
        assert payload["data"]["stdout"].strip() == expected_stdout


def test_cli_exec_returns_top_level_error_when_execution_fails(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.execute = lambda **_: _error_execution(  # type: ignore[method-assign]
        ename="ZeroDivisionError",
        evalue="division by zero",
        traceback=["ZeroDivisionError: division by zero"],
    )

    exec_res = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1 / 0"])
    assert exec_res.exit_code == 1

    payload = _payload(exec_res.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "EXECUTION_ERROR"
    assert payload["data"]["status"] == "error"
    assert payload["data"]["ename"] == "ZeroDivisionError"
    assert "traceback" not in payload["data"]
    assert "events" not in payload["data"]
    assert len(payload["error"]["traceback"]) <= 6


def test_cli_exec_stream_json_emits_start_events_and_final(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.resolve_session_id = lambda **_: "default"  # type: ignore[method-assign]

    def execute_code_stub(**kwargs: object) -> ManagedExecution:
        sink = kwargs["event_sink"]
        assert sink is not None
        sink.started(execution_id="run-123", session_id="default")
        sink.accept(ExecutionEvent(kind="stdout", content="hello\n"))
        sink.accept(ExecutionEvent(kind="display", content="2"))
        return ManagedExecution(
            record=ExecutionRecord(
                execution_id="run-123",
                ts="2026-03-11T00:00:00+00:00",
                session_id="default",
                command_type="exec",
                status="ok",
                duration_ms=5,
                code="print('hello')\n1 + 1",
                stdout="hello\n",
                result="2",
                events=[
                    ExecutionEvent(kind="stdout", content="hello\n"),
                    ExecutionEvent(kind="display", content="2"),
                ],
            )
        )

    cli.executions.execute_code = execute_code_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--stream", "--json", "print('hello')\n1 + 1"],
    )

    assert result.exit_code == 0
    frames = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert frames[0] == {"type": "start", "execution_id": "run-123", "session_id": "default"}
    assert frames[1]["type"] == "event"
    assert frames[1]["event"]["kind"] == "stdout"
    assert frames[2]["event"]["kind"] == "display"
    assert frames[-1]["type"] == "final"
    assert frames[-1]["response"]["status"] == "ok"
    assert frames[-1]["response"]["data"]["execution_id"] == "run-123"


def test_cli_exec_stream_human_prints_live_output(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.resolve_session_id = lambda **_: "default"  # type: ignore[method-assign]

    def execute_code_stub(**kwargs: object) -> ManagedExecution:
        sink = kwargs["event_sink"]
        assert sink is not None
        sink.started(execution_id="run-456", session_id="default")
        sink.accept(ExecutionEvent(kind="stdout", content="hello\n"))
        sink.accept(ExecutionEvent(kind="result", content="2"))
        return ManagedExecution(
            record=ExecutionRecord(
                execution_id="run-456",
                ts="2026-03-11T00:00:00+00:00",
                session_id="default",
                command_type="exec",
                status="ok",
                duration_ms=5,
                code="print('hello')\n1 + 1",
                stdout="hello\n",
                result="2",
                events=[
                    ExecutionEvent(kind="stdout", content="hello\n"),
                    ExecutionEvent(kind="result", content="2"),
                ],
            )
        )

    cli.executions.execute_code = execute_code_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        [
            "--no-suggestions",
            "exec",
            "--project",
            str(project_dir),
            "--stream",
            "print('hello')\n1 + 1",
        ],
    )

    assert result.exit_code == 0
    assert result.output == "hello\n2\n"


def test_cli_exec_stream_json_returns_error_final_frame_on_execution_failure(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.resolve_session_id = lambda **_: "default"  # type: ignore[method-assign]

    def execute_code_stub(**kwargs: object) -> ManagedExecution:
        sink = kwargs["event_sink"]
        assert sink is not None
        sink.started(execution_id="run-789", session_id="default")
        sink.accept(
            ExecutionEvent(
                kind="error",
                content="division by zero",
                metadata={"ename": "ZeroDivisionError", "traceback": ["tb"]},
            )
        )
        return ManagedExecution(
            record=ExecutionRecord(
                execution_id="run-789",
                ts="2026-03-11T00:00:00+00:00",
                session_id="default",
                command_type="exec",
                status="error",
                duration_ms=5,
                code="1 / 0",
                ename="ZeroDivisionError",
                evalue="division by zero",
                traceback=["tb"],
                events=[
                    ExecutionEvent(
                        kind="error",
                        content="division by zero",
                        metadata={"ename": "ZeroDivisionError", "traceback": ["tb"]},
                    )
                ],
            )
        )

    cli.executions.execute_code = execute_code_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--stream", "--json", "1 / 0"],
    )

    assert result.exit_code == 1
    frames = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert frames[-1]["type"] == "final"
    assert frames[-1]["response"]["status"] == "error"
    assert frames[-1]["response"]["error"]["code"] == "EXECUTION_ERROR"
    assert frames[-1]["response"]["data"]["status"] == "error"


def test_cli_returns_no_kernel_error(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1+1"])
    assert result.exit_code == 1

    payload = _payload(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "NO_KERNEL"


def test_cli_returns_kernel_not_ready_error_when_connection_exists_without_session(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    state_dir = project_dir / ".agentnb"
    state_dir.mkdir()
    (state_dir / "kernel-default.json").write_text("{}", encoding="utf-8")

    result = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1+1"])
    assert result.exit_code == 1

    payload = _payload(result.output)
    assert payload["status"] == "error"
    assert payload["error"]["code"] == "KERNEL_NOT_READY"


def test_cli_returns_ambiguous_session_error_when_multiple_live_sessions_exist(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.runtime.list_sessions = lambda **_: [  # type: ignore[method-assign]
        {"session_id": "default"},
        {"session_id": "analysis"},
    ]

    result = cli_runner.invoke(main, ["status", "--project", str(project_dir), "--json"])

    assert result.exit_code == 1
    payload = _payload(result.output)
    assert payload["error"]["code"] == "AMBIGUOUS_SESSION"
    assert payload["error"]["message"].startswith("Multiple live sessions exist")
    assert payload["data"]["available_sessions"] == ["default", "analysis"]
    assert payload["suggestions"] == [
        "Run `agentnb sessions list --json` to see the live session names.",
        "Retry with `agentnb status --session NAME --json` to target one explicitly.",
    ]


def test_cli_status_uses_only_live_session_when_implicit(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    status_calls: list[dict[str, object]] = []

    cli.runtime.list_sessions = lambda **_: [  # type: ignore[method-assign]
        {"session_id": "analysis"}
    ]

    def status_stub(**kwargs: object) -> object:
        status_calls.append(dict(kwargs))
        return type("Status", (), {"to_dict": lambda self: {"alive": True, "pid": 123}})()

    cli.runtime.status = status_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(main, ["status", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["session_id"] == "analysis"
    assert status_calls[0]["session_id"] == "analysis"


def test_cli_status_wait_uses_runtime_wait_for_ready(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    wait_calls: list[dict[str, object]] = []

    def wait_stub(**kwargs: object) -> object:
        wait_calls.append(dict(kwargs))
        return type("Status", (), {"to_dict": lambda self: {"alive": True, "pid": 321}})()

    cli.runtime.wait_for_ready = wait_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["status", "--project", str(project_dir), "--wait", "--timeout", "5", "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["alive"] is True
    assert payload["data"]["waited"] is True
    assert wait_calls[0]["timeout_s"] == 5.0


def test_cli_status_wait_idle_uses_runtime_wait_for_idle(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    wait_calls: list[dict[str, object]] = []

    def wait_stub(**kwargs: object) -> object:
        wait_calls.append(dict(kwargs))
        return type(
            "Status",
            (),
            {"to_dict": lambda self: {"alive": True, "pid": 321, "busy": False}},
        )()

    cli.runtime.wait_for_idle = wait_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["status", "--project", str(project_dir), "--wait-idle", "--timeout", "5", "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["alive"] is True
    assert payload["data"]["waited"] is True
    assert payload["data"]["waited_for"] == "idle"
    assert wait_calls[0]["timeout_s"] == 5.0


def test_cli_quiet_suppresses_status_body_and_suggestions(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.status = lambda **_: type(  # type: ignore[method-assign]
        "Status",
        (),
        {"to_dict": lambda self: {"alive": True, "pid": 123}},
    )()

    result = cli_runner.invoke(
        main,
        ["status", "--project", str(project_dir), "--quiet"],
    )

    assert result.exit_code == 0
    assert result.output == ""


def test_cli_doctor_returns_diagnostics(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.runtime.doctor = lambda **_: {  # type: ignore[method-assign]
        "ready": True,
        "checks": [{"name": "python", "status": "ok", "message": "ok"}],
        "selected_python": "python",
        "python_source": "current",
        "session_exists": False,
        "stale_session_cleaned": False,
    }

    result = cli_runner.invoke(main, ["doctor", "--project", str(project_dir), "--json"])
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["status"] == "ok"
    assert payload["command"] == "doctor"
    assert payload["data"] == {
        "ready": True,
        "checks": [{"name": "python", "status": "ok", "message": "ok"}],
        "selected_python": "python",
        "python_source": "current",
        "session_exists": False,
        "stale_session_cleaned": False,
    }


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


def test_cli_start_passes_named_session(
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
                "connection_file": str(project_dir / ".agentnb" / "kernel-analysis.json"),
                "started_at": "2026-03-09T00:00:00+00:00",
                "uptime_s": 0.0,
                "python": "python",
            }
        ),
        True,
    )

    result = cli_runner.invoke(
        main,
        ["start", "--project", str(project_dir), "--session", "analysis", "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["session_id"] == "analysis"
    assert start_mock.call_args.kwargs["session_id"] == "analysis"


def test_cli_root_help_is_shown_without_arguments(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(main, [])
    assert result.exit_code == 0
    assert "Run `agentnb --help`" in result.output
    assert "Recommended loop:" in result.output
    assert "Prefer --json for agent integrations" in result.output
    assert "One project session should be driven serially." in result.output


def test_cli_help_is_comprehensive(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "Persistent project-scoped Python REPL for agent workflows." in result.output
    assert "append-only notebook" in result.output
    assert "agentnb exec --ensure-started" in result.output
    assert "Use `--session NAME`" in result.output
    assert "runs wait" in result.output
    assert "--auto-install" in result.output
    assert "doctor --fix" in result.output
    assert "--recent" in result.output
    assert "sessions" in result.output


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


def test_cli_agent_preset_enables_json_and_suppresses_suggestions(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    result = cli_runner.invoke(main, ["--agent", "status", "--project", str(project_dir)])
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["status"] == "ok"
    assert payload["command"] == "status"
    assert payload["suggestions"] == []


def test_cli_root_flags_work_after_subcommand(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(main, ["status", "--agent", "--project", str(project_dir)])
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["command"] == "status"
    assert payload["suggestions"] == []


def test_cli_no_suggestions_strips_suggestions_from_json(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    result = cli_runner.invoke(
        main, ["--no-suggestions", "status", "--project", str(project_dir), "--json"]
    )
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["suggestions"] == []


def test_cli_env_format_json_applies_without_per_command_flag(
    cli_runner: CliRunner, project_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENTNB_FORMAT", "json")

    result = cli_runner.invoke(main, ["status", "--project", str(project_dir)])
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["command"] == "status"


def test_cli_exec_result_only_returns_selected_text(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.runtime.execute = lambda **_: _ok_execution(result="2")  # type: ignore[method-assign]

    exec_res = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--result-only", "1 + 1"],
    )
    assert exec_res.exit_code == 0
    assert exec_res.output.strip() == "2"


def test_cli_exec_passes_named_session(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    execute_calls: list[dict[str, object]] = []

    def execute_stub(**kwargs: object) -> ExecutionResult:
        execute_calls.append(dict(kwargs))
        return _ok_execution(result="2")

    cli.runtime.execute = execute_stub  # type: ignore[method-assign]

    exec_res = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--session", "analysis", "--json", "1 + 1"],
    )

    assert exec_res.exit_code == 0
    payload = _payload(exec_res.output)
    assert payload["session_id"] == "analysis"
    assert execute_calls[0]["session_id"] == "analysis"


def test_cli_exec_ensure_started_starts_missing_session(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    ensure_calls: list[dict[str, object]] = []

    def ensure_stub(**kwargs: object) -> tuple[object, bool]:
        ensure_calls.append(dict(kwargs))
        return object(), True

    cli.runtime.ensure_started = ensure_stub  # type: ignore[method-assign]
    cli.runtime.execute = lambda **_: _ok_execution(result="2")  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--ensure-started", "--json", "1 + 1"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["ensured_started"] is True
    assert payload["data"]["started_new_session"] is True
    assert ensure_calls[0]["session_id"] == "default"


def test_cli_exec_background_returns_run_id(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.executions.start_background_code = lambda **_: ManagedExecution(  # type: ignore[method-assign]
        record=ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-11T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="running",
            duration_ms=0,
            code="1 + 1",
            worker_pid=123,
        ),
        started_new_session=True,
    )

    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--background", "--json", "1 + 1"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["execution_id"] == "run-1"
    assert payload["data"]["background"] is True
    assert (
        payload["suggestions"][0]
        == "Run `agentnb runs wait EXECUTION_ID --json` to wait for the final result."
    )


def test_cli_vars_includes_types_by_default(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.ops.list_vars = lambda **_: [{"name": "value", "type": "int", "repr": "42"}]  # type: ignore[method-assign]

    vars_res = cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])
    assert vars_res.exit_code == 0

    payload = _payload(vars_res.output)
    assert payload["data"]["vars"][0]["name"] == "value"
    assert payload["data"]["vars"][0]["type"] == "int"


def test_cli_vars_hides_routines_and_compacts_container_values(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.ops.list_vars = lambda **_: [  # type: ignore[method-assign]
        {"name": "posts", "type": "list", "repr": "list len=1 item_keys=id, title, body"},
        {"name": "query", "type": "dict", "repr": "dict len=2 keys=postId, _limit"},
    ]

    vars_res = cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])
    assert vars_res.exit_code == 0

    payload = _payload(vars_res.output)
    names = {item["name"] for item in payload["data"]["vars"]}
    assert "urlopen" not in names
    assert "urlencode" not in names
    assert payload["data"]["vars"][0]["repr"] == "list len=1 item_keys=id, title, body"
    assert payload["data"]["vars"][1]["repr"] == "dict len=2 keys=postId, _limit"


def test_cli_vars_no_types_hides_types(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.ops.list_vars = lambda **_: [{"name": "value", "type": "int", "repr": "42"}]  # type: ignore[method-assign]

    vars_res = cli_runner.invoke(
        main,
        ["vars", "--project", str(project_dir), "--no-types", "--json"],
    )
    assert vars_res.exit_code == 0

    payload = _payload(vars_res.output)
    assert payload["data"]["vars"][0] == {"name": "value", "repr": "42"}


def test_cli_vars_match_and_recent_filter_namespace(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.ops.list_vars = lambda **_: [  # type: ignore[method-assign]
        {"name": "alpha_value", "type": "int", "repr": "1"},
        {"name": "beta_value", "type": "int", "repr": "2"},
        {"name": "gamma_value", "type": "int", "repr": "3"},
    ]

    recent_res = cli_runner.invoke(
        main,
        ["vars", "--project", str(project_dir), "--recent", "2", "--json"],
    )
    assert recent_res.exit_code == 0
    recent_payload = _payload(recent_res.output)
    assert [item["name"] for item in recent_payload["data"]["vars"]] == [
        "beta_value",
        "gamma_value",
    ]

    match_res = cli_runner.invoke(
        main,
        ["vars", "--project", str(project_dir), "--match", "beta", "--json"],
    )
    assert match_res.exit_code == 0
    match_payload = _payload(match_res.output)
    assert [item["name"] for item in match_payload["data"]["vars"]] == ["beta_value"]


def test_cli_history_latest_returns_only_most_recent_entry(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.runtime.history = lambda **_: [  # type: ignore[method-assign]
        {
            "command_type": "exec",
            "label": "exec 1 + 1",
            "kind": "user_command",
            "user_visible": True,
            "input": "1 + 1",
        },
        {
            "command_type": "exec",
            "label": "exec x = 2 x + 2",
            "kind": "user_command",
            "user_visible": True,
            "input": "x = 2\nx + 2",
        },
    ]

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--latest", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    assert len(payload["data"]["entries"]) == 1
    assert payload["data"]["entries"][0]["command_type"] == "exec"
    assert payload["data"]["entries"][0]["label"] == "exec x = 2 x + 2"
    assert payload["data"]["entries"][0]["kind"] == "user_command"


def test_cli_history_hides_helper_code_by_default(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.runtime.history = lambda **_: [  # type: ignore[method-assign]
        {
            "command_type": "exec",
            "label": "exec value = 42 import localmod",
            "kind": "user_command",
            "user_visible": True,
        },
        {"command_type": "vars", "label": "vars", "kind": "user_command", "user_visible": True},
        {
            "command_type": "inspect",
            "label": "inspect value",
            "kind": "user_command",
            "user_visible": True,
        },
        {
            "command_type": "reload",
            "label": "reload localmod",
            "kind": "user_command",
            "user_visible": True,
        },
    ]

    history_res = cli_runner.invoke(main, ["history", "--project", str(project_dir), "--json"])
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    entries = payload["data"]["entries"]
    assert [entry["command_type"] for entry in entries] == ["exec", "vars", "inspect", "reload"]
    assert all(entry["kind"] == "user_command" for entry in entries)
    assert [entry["label"] for entry in entries[1:]] == ["vars", "inspect value", "reload localmod"]
    assert not any("get_ipython" in str(entry.get("code")) for entry in entries)


def test_cli_history_all_includes_internal_helper_entries(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    history_calls: list[dict[str, object]] = []

    def history_stub(**kwargs: object) -> list[dict[str, object]]:
        history_calls.append(kwargs)
        return [
            {
                "kind": "kernel_execution",
                "command_type": "exec",
                "label": "exec kernel execution",
                "user_visible": False,
                "code": "value = 42",
            },
            {
                "kind": "user_command",
                "command_type": "exec",
                "label": "exec value = 42",
                "user_visible": True,
            },
            {
                "kind": "kernel_execution",
                "command_type": "vars",
                "label": "vars kernel execution",
                "user_visible": False,
                "code": "get_ipython()",
            },
            {
                "kind": "user_command",
                "command_type": "vars",
                "label": "vars",
                "user_visible": True,
            },
        ]

    cli.runtime.history = history_stub  # type: ignore[method-assign]

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--all", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    entries = payload["data"]["entries"]
    assert len(entries) == 4
    assert [entry["kind"] for entry in entries] == [
        "kernel_execution",
        "user_command",
        "kernel_execution",
        "user_command",
    ]
    assert entries[0]["label"] == "exec kernel execution value = 42"
    assert entries[1]["label"] == "exec"
    assert entries[-2]["command_type"] == "vars"
    assert entries[-2]["user_visible"] is False
    assert "code" not in entries[-2]
    assert history_calls[0]["include_internal"] is True


def test_cli_history_errors_filters_semantic_failures(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    history_calls: list[dict[str, object]] = []

    def history_stub(**kwargs: object) -> list[dict[str, object]]:
        history_calls.append(kwargs)
        return [
            {
                "label": "inspect missing_name",
                "kind": "user_command",
                "status": "error",
                "user_visible": True,
            }
        ]

    cli.runtime.history = history_stub  # type: ignore[method-assign]

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--errors", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    entries = payload["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["label"] == "inspect missing_name"
    assert entries[0]["kind"] == "user_command"
    assert entries[0]["status"] == "error"
    assert history_calls[0]["errors_only"] is True


def test_cli_history_last_limits_visible_entries(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.runtime.history = lambda **_: [  # type: ignore[method-assign]
        {"command_type": "exec", "label": "exec", "kind": "user_command", "user_visible": True},
        {"command_type": "vars", "label": "vars", "kind": "user_command", "user_visible": True},
        {
            "command_type": "reload",
            "label": "reload localmod",
            "kind": "user_command",
            "user_visible": True,
        },
    ]

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--last", "2", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    assert [entry["command_type"] for entry in payload["data"]["entries"]] == ["vars", "reload"]


def test_cli_history_error_exec_label_is_semantic(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.runtime.history = lambda **_: [  # type: ignore[method-assign]
        {
            "label": "exec error ZeroDivisionError",
            "kind": "user_command",
            "status": "error",
            "user_visible": True,
        }
    ]

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--errors", "--latest", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    assert payload["data"]["entries"][0]["label"] == "exec error ZeroDivisionError"


def test_cli_sessions_list_returns_runtime_entries(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.runtime.list_sessions = lambda **_: [  # type: ignore[method-assign]
        {
            "session_id": "default",
            "alive": True,
            "pid": 111,
            "python": "python",
            "is_default": True,
            "last_activity": "2026-03-09T00:00:00+00:00",
        },
        {
            "session_id": "analysis",
            "alive": True,
            "pid": 222,
            "python": "python",
            "is_default": False,
            "last_activity": None,
        },
    ]

    result = cli_runner.invoke(main, ["sessions", "list", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "sessions-list"
    assert [session["session_id"] for session in payload["data"]["sessions"]] == [
        "default",
        "analysis",
    ]


def test_cli_sessions_list_empty_has_actionable_suggestions(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.runtime.list_sessions = lambda **_: []  # type: ignore[method-assign]

    result = cli_runner.invoke(main, ["sessions", "list", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["sessions"] == []
    assert payload["suggestions"] == [
        "Run `agentnb start --json` to start the default session.",
        'Run `agentnb exec --ensure-started --json "..."` to start and execute in one step.',
    ]


def test_cli_sessions_delete_calls_runtime_delete(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    delete_calls: list[dict[str, object]] = []

    def delete_stub(**kwargs: object) -> dict[str, object]:
        delete_calls.append(dict(kwargs))
        return {
            "deleted": True,
            "session_id": "analysis",
            "stopped_running_kernel": True,
        }

    cli.runtime.delete_session = delete_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["sessions", "delete", "analysis", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "sessions-delete"
    assert payload["session_id"] == "analysis"
    assert delete_calls[0]["session_id"] == "analysis"


def test_cli_runs_list_returns_compacted_runs(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.executions.list_runs = lambda **_: [  # type: ignore[method-assign]
        {
            "execution_id": "run-1",
            "ts": "2026-03-10T00:00:00+00:00",
            "session_id": "default",
            "command_type": "exec",
            "status": "ok",
            "duration_ms": 12,
            "stdout": "",
            "stderr": "",
            "result": "42",
            "ename": None,
        }
    ]

    result = cli_runner.invoke(main, ["runs", "list", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "runs-list"
    assert payload["data"]["runs"][0]["execution_id"] == "run-1"
    assert payload["data"]["runs"][0]["result_preview"] == "42"


def test_cli_runs_show_returns_run_details(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.executions.get_run = lambda **_: {  # type: ignore[method-assign]
        "execution_id": "run-1",
        "ts": "2026-03-10T00:00:00+00:00",
        "session_id": "default",
        "command_type": "exec",
        "status": "ok",
        "duration_ms": 12,
        "code": "1 + 1",
        "stdout": "",
        "stderr": "",
        "result": "2",
        "execution_count": 1,
        "ename": None,
        "evalue": None,
        "traceback": None,
        "outputs": [
            {
                "kind": "result",
                "text": "2",
                "mime": {"text/plain": "2"},
            }
        ],
        "events": [],
    }

    result = cli_runner.invoke(
        main,
        ["runs", "show", "run-1", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "runs-show"
    assert payload["data"]["run"]["execution_id"] == "run-1"
    assert "outputs" not in payload["data"]["run"]


def test_cli_runs_show_human_clarifies_snapshot_for_running_run(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.executions.get_run = lambda **_: {  # type: ignore[method-assign]
        "execution_id": "run-1",
        "ts": "2026-03-10T00:00:00+00:00",
        "session_id": "default",
        "command_type": "exec",
        "status": "running",
        "duration_ms": 12,
        "stdout": "tick 1\ntick 2\n",
        "stderr": "",
        "result": None,
        "events": [{"kind": "stdout", "content": "tick 1\n", "metadata": {}}],
    }

    result = cli_runner.invoke(
        main,
        ["runs", "show", "run-1", "--project", str(project_dir)],
    )

    assert result.exit_code == 0
    assert "Run run-1 [running] exec on session default." in result.output
    assert (
        "snapshot: persisted state only; use `agentnb runs follow` for live events" in result.output
    )


def test_cli_runs_wait_returns_completed_run(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.executions.wait_for_run = lambda **_: {  # type: ignore[method-assign]
        "execution_id": "run-1",
        "status": "ok",
        "result": "2",
    }

    result = cli_runner.invoke(
        main,
        ["runs", "wait", "run-1", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "runs-wait"
    assert payload["data"]["run"]["execution_id"] == "run-1"


@pytest.mark.parametrize(
    ("subcommand", "setup"),
    [
        (
            "wait",
            lambda cli: setattr(  # type: ignore[method-assign]
                cli.executions,
                "wait_for_run",
                lambda **_: {
                    "execution_id": "run-1",
                    "session_id": "default",
                    "status": "ok",
                    "result": "2",
                    "outputs": [
                        {
                            "kind": "result",
                            "text": "2",
                            "mime": {"text/plain": "2"},
                        }
                    ],
                },
            ),
        ),
        (
            "follow",
            lambda cli: setattr(  # type: ignore[method-assign]
                cli.executions,
                "follow_run",
                lambda **kwargs: (
                    kwargs["event_sink"].started(
                        execution_id="run-1",
                        session_id="default",
                    ),
                    {
                        "execution_id": "run-1",
                        "session_id": "default",
                        "status": "ok",
                        "result": "2",
                        "outputs": [
                            {
                                "kind": "result",
                                "text": "2",
                                "mime": {"text/plain": "2"},
                            }
                        ],
                    },
                )[-1],
            ),
        ),
    ],
)
def test_cli_run_lookup_json_hides_internal_outputs(
    cli_runner: CliRunner,
    project_dir: Path,
    subcommand: str,
    setup,
) -> None:
    import agentnb.cli as cli

    setup(cli)

    result = cli_runner.invoke(
        main,
        ["runs", subcommand, "run-1", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    if subcommand == "follow":
        frames = [json.loads(line) for line in result.output.splitlines() if line.strip()]
        run_payload = frames[-1]["response"]["data"]["run"]
    else:
        run_payload = _payload(result.output)["data"]["run"]
    assert run_payload["execution_id"] == "run-1"
    assert "outputs" not in run_payload


def test_cli_runs_follow_stream_json_emits_events_and_final(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    def follow_stub(**kwargs: object) -> dict[str, object]:
        sink = kwargs["event_sink"]
        assert sink is not None
        sink.started(execution_id="run-1", session_id="default")
        sink.accept(ExecutionEvent(kind="stdout", content="hello\n"))
        sink.accept(ExecutionEvent(kind="result", content="2"))
        return {
            "execution_id": "run-1",
            "session_id": "default",
            "status": "ok",
            "result": "2",
            "events": [
                {"kind": "stdout", "content": "hello\n", "metadata": {}},
                {"kind": "result", "content": "2", "metadata": {}},
            ],
        }

    cli.executions.follow_run = follow_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["runs", "follow", "run-1", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    frames = [json.loads(line) for line in result.output.splitlines() if line.strip()]
    assert frames[0] == {"type": "start", "execution_id": "run-1", "session_id": "default"}
    assert frames[1]["event"]["kind"] == "stdout"
    assert frames[2]["event"]["kind"] == "result"
    assert frames[-1]["type"] == "final"
    assert frames[-1]["response"]["command"] == "runs-follow"
    assert frames[-1]["response"]["data"]["run"]["execution_id"] == "run-1"


def test_cli_runs_cancel_requests_interrupt(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.executions.cancel_run = lambda **_: {  # type: ignore[method-assign]
        "execution_id": "run-1",
        "session_id": "analysis",
        "cancel_requested": True,
        "status": "error",
        "run_status": "error",
        "session_outcome": "preserved",
    }

    result = cli_runner.invoke(
        main,
        ["runs", "cancel", "run-1", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "runs-cancel"
    assert payload["data"]["cancel_requested"] is True
    assert payload["data"]["session_outcome"] == "preserved"


def test_cli_module_entrypoint_invokes_main(project_dir: Path) -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "agentnb.cli",
            "sessions",
            "list",
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
    payload = _payload(completed.stdout)
    assert payload["command"] == "sessions-list"


def test_cli_invalid_session_name_is_rejected(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(
        main,
        ["status", "--project", str(project_dir), "--session", "../bad", "--json"],
    )

    assert result.exit_code == 2
    assert "Invalid session name" in result.output


def test_cli_reset_is_recorded_as_visible_history_entry(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.runtime.reset = lambda **_: _ok_execution()  # type: ignore[method-assign]

    cli_runner.invoke(main, ["reset", "--project", str(project_dir), "--json"])

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--latest", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    entry = payload["data"]["entries"][0]
    assert entry["command_type"] == "reset"
    assert entry["label"] == "reset"
    assert entry["kind"] == "user_command"


def test_cli_reload_without_module_reloads_project_local_imports(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.ops.reload_module = lambda **_: {  # type: ignore[method-assign]
        "mode": "project",
        "requested_module": None,
        "reloaded_modules": ["localmod"],
        "failed_modules": [],
        "skipped_modules": [],
        "rebound_names": ["greet"],
        "stale_names": [],
        "excluded_module_count": 3,
        "notes": [],
    }

    reload_res = cli_runner.invoke(main, ["reload", "--project", str(project_dir), "--json"])
    assert reload_res.exit_code == 0

    reload_payload = _payload(reload_res.output)
    assert reload_payload["data"]["mode"] == "project"
    assert reload_payload["data"]["requested_module"] is None
    assert reload_payload["data"]["reloaded_modules"] == ["localmod"]
    assert "greet" in reload_payload["data"]["rebound_names"]
    assert reload_payload["data"]["excluded_module_count"] > 0
    assert reload_payload["data"]["skipped_modules"] == []


def test_cli_exec_returns_session_busy_when_lock_exists(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    def raise_busy(**_: object) -> ExecutionResult:
        raise SessionBusyError()

    cli.runtime.execute = raise_busy  # type: ignore[method-assign]
    exec_res = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1 + 1"])
    assert exec_res.exit_code == 1

    payload = _payload(exec_res.output)
    assert payload["error"]["code"] == "SESSION_BUSY"
    assert "Wait for the prior command to finish" in payload["error"]["message"]


def test_cli_vars_compacts_dataframe_repr(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.ops.list_vars = lambda **_: [  # type: ignore[method-assign]
        {"name": "frame", "type": "FakeFrame", "repr": "DataFrame shape=(10, 3) columns=a, b, c"}
    ]

    vars_res = cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])
    payload = _payload(vars_res.output)
    assert payload["data"]["vars"][0]["repr"] == "DataFrame shape=(10, 3) columns=a, b, c"


def test_cli_sqlite_rows_get_structural_previews(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.ops.list_vars = lambda **_: [  # type: ignore[method-assign]
        {"name": "rows", "type": "list", "repr": "list len=2 item_keys=id, title"}
    ]
    cli.ops.inspect_var = lambda **_: {  # type: ignore[method-assign]
        "name": "rows",
        "type": "list",
        "preview": {
            "kind": "sequence-like",
            "length": 2,
            "sample_keys": ["id", "title"],
            "sample": [{"id": 1, "title": "a"}, {"id": 2, "title": "b"}],
            "item_type": "Row",
        },
        "members": [],
        "doc": "",
        "repr": "[...]",
    }

    vars_res = cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])
    assert vars_res.exit_code == 0
    vars_payload = _payload(vars_res.output)
    row_entry = next(item for item in vars_payload["data"]["vars"] if item["name"] == "rows")
    assert row_entry["repr"] == "list len=2 item_keys=id, title"

    inspect_res = cli_runner.invoke(
        main, ["inspect", "--project", str(project_dir), "--json", "rows"]
    )
    assert inspect_res.exit_code == 0
    inspect_payload = _payload(inspect_res.output)["data"]["inspect"]["preview"]
    assert inspect_payload["sample_keys"] == ["id", "title"]
    assert inspect_payload["sample"][0]["title"] == "a"


def test_cli_inspect_compacts_dataframe_payload(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.ops.inspect_var = lambda **_: {  # type: ignore[method-assign]
        "name": "df",
        "type": "DataFrameLike",
        "preview": {
            "kind": "dataframe-like",
            "shape": [4, 2],
            "columns": ["a", "b"],
            "head": [{"a": 1, "b": 5}, {"a": 2, "b": 6}, {"a": 3, "b": 7}],
        },
        "members": [],
        "doc": "",
        "repr": "DataFrameLike(...)",
    }

    inspect_res = cli_runner.invoke(
        main,
        ["inspect", "--project", str(project_dir), "--json", "df"],
    )
    assert inspect_res.exit_code == 0
    payload = _payload(inspect_res.output)
    inspect_payload = payload["data"]["inspect"]
    assert "repr" not in inspect_payload
    assert "members" not in inspect_payload
    assert len(inspect_payload["preview"]["head"]) == 3


def test_cli_inspect_compacts_sequence_payload(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.ops.inspect_var = lambda **_: {  # type: ignore[method-assign]
        "name": "posts",
        "type": "list",
        "preview": {
            "kind": "sequence-like",
            "length": 3,
            "sample": [
                {"id": 1, "title": "a", "body": "alpha"},
                {"id": 2, "title": "b", "body": "beta"},
                {"id": 3, "title": "c", "body": "gamma"},
            ],
            "item_type": "dict",
            "sample_keys": ["id", "title", "body"],
        },
        "members": [],
        "doc": "",
        "repr": "[...]",
    }

    inspect_res = cli_runner.invoke(
        main,
        ["inspect", "--project", str(project_dir), "--json", "posts"],
    )
    assert inspect_res.exit_code == 0

    payload = _payload(inspect_res.output)
    inspect_payload = payload["data"]["inspect"]
    assert inspect_payload["preview"]["kind"] == "sequence-like"
    assert inspect_payload["preview"]["length"] == 3
    assert inspect_payload["preview"]["item_type"] == "dict"
    assert inspect_payload["preview"]["sample_keys"] == ["id", "title", "body"]
    assert len(inspect_payload["preview"]["sample"]) == 3
    assert "repr" not in inspect_payload
    assert "members" not in inspect_payload


def test_cli_history_exec_label_shortens_urls(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.runtime.history = lambda **_: [  # type: ignore[method-assign]
        {
            "label": (
                "exec url = "
                "'https://jsonplaceholder.typicode.com/comments?"
                "postId=1&_limit=2&expand=author&include=metadata' url"
            ),
            "command_type": "exec",
            "kind": "user_command",
            "user_visible": True,
            "input": (
                "url = 'https://jsonplaceholder.typicode.com/comments?"
                "postId=1&_limit=2&expand=author&include=metadata'\n"
                "url"
            ),
        }
    ]

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--latest", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    label = payload["data"]["entries"][0]["label"]
    assert "jsonplaceholder.typicode.com" in label
    assert "metadata" not in label
    assert len(label) <= 69


def test_cli_history_last_rejects_latest_combination(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    result = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--latest", "--last", "2"],
    )
    assert result.exit_code != 0
    assert "Use either --latest or --last" in result.output
