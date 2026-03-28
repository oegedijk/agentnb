from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypedDict, cast
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from pytest_mock import MockerFixture

from agentnb import __version__
from agentnb.cli import main
from agentnb.contracts import (
    ExecutionEvent,
    ExecutionResult,
    ExecutionSink,
    HelperAccessMetadata,
    KernelStatus,
    success_response,
)
from agentnb.errors import SessionBusyError
from agentnb.execution import (
    ExecutionRecord,
    ManagedExecution,
    RunRetrievalOutcome,
    SessionAccessOutcome,
    StartOutcome,
)
from agentnb.execution_output import OutputItem
from agentnb.introspection import KernelHelperResult
from agentnb.journal import JournalEntry, JournalQuery
from agentnb.kernel.provisioner import DoctorCheck, DoctorReport
from tests.helpers import build_execution_record

pytestmark = pytest.mark.usefixtures("patch_cli_runtime")


JSONDict = dict[str, Any]


class ErrorEnvelope(TypedDict):
    code: str
    message: str
    ename: str | None
    evalue: str | None
    traceback: list[str] | None


class CommandEnvelope(TypedDict):
    schema_version: str
    status: str
    command: str
    project: str
    session_id: str
    timestamp: str
    data: JSONDict
    suggestions: list[str]
    suggestion_actions: list[JSONDict]
    error: ErrorEnvelope | None


def _payload(output: str) -> CommandEnvelope:
    return cast(CommandEnvelope, json.loads(output))


def _frame(line: str) -> JSONDict:
    return cast(JSONDict, json.loads(line))


def _error(payload: CommandEnvelope) -> ErrorEnvelope:
    error = payload["error"]
    assert error is not None
    return error


def _event_sink(kwargs: dict[str, object]) -> ExecutionSink:
    sink = kwargs.get("event_sink")
    if sink is None:
        request = cast(Any, kwargs["request"])
        sink = request.event_sink
    assert sink is not None
    return cast(ExecutionSink, sink)


def _history_selection(entries: list[JournalEntry]) -> SimpleNamespace:
    return SimpleNamespace(entries=entries)


def _write_module(project_dir: Path, name: str, body: str) -> None:
    (project_dir / f"{name}.py").write_text(body, encoding="utf-8")


def test_cli_exec_rejects_legacy_state_without_manifest(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    state_dir = project_dir / ".agentnb"
    state_dir.mkdir()
    (state_dir / "executions.jsonl").write_text(
        json.dumps(
            {
                "execution_id": "run-1",
                "ts": "2026-03-10T00:00:00+00:00",
                "session_id": "default",
                "command_type": "exec",
                "status": "ok",
                "duration_ms": 1,
                "result": "2",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--json", "1 + 1"],
    )

    assert result.exit_code == 1
    payload = _payload(result.output)
    assert _error(payload)["code"] == "STATE_SCHEMA_INCOMPATIBLE"
    assert _error(payload)["message"] == "State manifest is missing for existing state."


def _ok_execution(
    *,
    result: str | None = None,
    stdout: str = "",
    stderr: str = "",
) -> ExecutionResult:
    return ExecutionResult(status="ok", result=result, stdout=stdout, stderr=stderr, duration_ms=5)


def _managed_execution(
    *,
    status: str = "ok",
    result: str | None = None,
    stdout: str = "",
    stderr: str = "",
    ename: str | None = None,
    evalue: str | None = None,
    traceback: list[str] | None = None,
    session_id: str = "default",
    code: str = "1 + 1",
    started_new_session: bool = False,
    initial_runtime_state: str | None = None,
) -> ManagedExecution:
    return ManagedExecution(
        record=ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id=session_id,
            command_type="exec",
            status=cast(Any, status),
            duration_ms=5,
            code=code,
            result=result,
            stdout=stdout,
            stderr=stderr,
            ename=ename,
            evalue=evalue,
            traceback=traceback,
        ),
        start_outcome=StartOutcome(
            started_new_session=started_new_session,
            initial_runtime_state=cast(Any, initial_runtime_state),
        ),
    )


def _helper_result(
    payload: object,
    *,
    access_metadata: HelperAccessMetadata | None = None,
) -> KernelHelperResult[object]:
    return KernelHelperResult(
        execution=ExecutionResult(status="ok"),
        payload=payload,
        access_metadata=access_metadata or HelperAccessMetadata(),
    )


def _journal_entry(
    *,
    kind: str = "user_command",
    command_type: str = "exec",
    label: str,
    status: str = "ok",
    user_visible: bool = True,
    input_text: str | None = None,
    code: str | None = None,
    error_type: str | None = None,
) -> JournalEntry:
    return JournalEntry(
        kind=kind,
        ts="2026-03-10T00:00:00+00:00",
        session_id="default",
        execution_id=None,
        status=status,
        duration_ms=1,
        command_type=command_type,
        label=label,
        user_visible=user_visible,
        classification="internal"
        if not user_visible
        else ("replayable" if command_type in {"exec", "reset"} else "inspection"),
        provenance_source="history_store",
        provenance_detail="kernel_execution" if kind == "kernel_execution" else "user_command",
        input=input_text,
        code=code,
        error_type=error_type,
    )


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

    cli.executions.execute = lambda **_: _managed_execution(result="2")  # type: ignore[method-assign]
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


def test_cli_root_version_flag_reports_package_version(cli_runner: CliRunner) -> None:
    result = cli_runner.invoke(main, ["--version"])

    assert result.exit_code == 0
    assert result.output.strip() == f"agentnb, version {__version__}"


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

    cli.executions.execute = lambda **_: _managed_execution(  # type: ignore[method-assign]
        result=expected_result,
        stdout=f"{expected_stdout}\n" if expected_stdout is not None else "",
        code="print('hello from stdin')\n" if stdin is not None else "1 + 1",
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

    cli.executions.execute = lambda **_: _managed_execution(  # type: ignore[method-assign]
        status="error",
        ename="ZeroDivisionError",
        evalue="division by zero",
        traceback=["ZeroDivisionError: division by zero"],
    )

    exec_res = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1 / 0"])
    assert exec_res.exit_code == 1

    payload = _payload(exec_res.output)
    assert payload["status"] == "error"
    error = _error(payload)
    assert error["code"] == "EXECUTION_ERROR"
    assert payload["data"]["status"] == "error"
    assert payload["data"]["ename"] == "ZeroDivisionError"
    assert "traceback" not in payload["data"]
    assert "events" not in payload["data"]
    traceback = error["traceback"]
    assert traceback is not None
    assert len(traceback) <= 6


def test_cli_implicit_exec_argument_uses_hot_path(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    ensure_calls: list[dict[str, object]] = []

    def ensure_stub(**kwargs: object) -> tuple[object, bool]:
        ensure_calls.append(dict(kwargs))
        return object(), True

    cli.runtime.ensure_started = ensure_stub  # type: ignore[method-assign]
    cli.runtime.execute = lambda **_: _ok_execution(result="2")  # type: ignore[method-assign]

    result = cli_runner.invoke(main, ["--project", str(project_dir), "--json", "1 + 1"])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "exec"
    assert payload["data"]["result"] == "2"
    assert payload["data"]["ensured_started"] is True
    assert ensure_calls[0]["session_id"] == "default"


def test_cli_implicit_exec_file_uses_hot_path(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    script = project_dir / "analysis.py"
    script.write_text("value = 1\nvalue + 1\n", encoding="utf-8")
    executed_code: list[str] = []

    cli.runtime.ensure_started = lambda **_: (object(), True)  # type: ignore[method-assign]

    def execute_stub(**kwargs: object) -> ExecutionResult:
        executed_code.append(str(kwargs["code"]))
        return _ok_execution(result="2")

    cli.runtime.execute = execute_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(main, ["--project", str(project_dir), "--json", str(script)])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "exec"
    assert payload["data"]["result"] == "2"
    assert payload["data"]["ensured_started"] is True
    assert executed_code == ["value = 1\nvalue + 1\n"]


def test_cli_implicit_exec_stdin_uses_hot_path(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    ensure_calls: list[dict[str, object]] = []

    def ensure_stub(**kwargs: object) -> tuple[object, bool]:
        ensure_calls.append(dict(kwargs))
        return object(), True

    cli.runtime.ensure_started = ensure_stub  # type: ignore[method-assign]
    cli.runtime.execute = lambda **_: _ok_execution(result="2")  # type: ignore[method-assign]

    result = cli_runner.invoke(main, ["--project", str(project_dir), "--json"], input="1 + 1")

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "exec"
    assert payload["data"]["result"] == "2"
    assert payload["data"]["ensured_started"] is True
    assert ensure_calls[0]["session_id"] == "default"


def test_cli_exec_stream_json_emits_start_events_and_final(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.resolve_session_id = lambda **_: "default"  # type: ignore[method-assign]

    def execute_code_stub(**kwargs: object) -> ManagedExecution:
        sink = _event_sink(kwargs)
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

    cli.executions.execute = execute_code_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--stream", "--json", "print('hello')\n1 + 1"],
    )

    assert result.exit_code == 0
    frames = [_frame(line) for line in result.output.splitlines() if line.strip()]
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
        sink = _event_sink(kwargs)
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

    cli.executions.execute = execute_code_stub  # type: ignore[method-assign]

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


def test_cli_exec_stream_human_reports_restart_notice_after_recovery(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.resolve_session_id = lambda **_: "default"  # type: ignore[method-assign]

    def execute_code_stub(**kwargs: object) -> ManagedExecution:
        sink = _event_sink(kwargs)
        sink.started(execution_id="run-457", session_id="default")
        sink.accept(ExecutionEvent(kind="result", content="2"))
        return ManagedExecution(
            record=ExecutionRecord(
                execution_id="run-457",
                ts="2026-03-11T00:00:00+00:00",
                session_id="default",
                command_type="exec",
                status="ok",
                duration_ms=5,
                code="1 + 1",
                result="2",
                events=[ExecutionEvent(kind="result", content="2")],
            ),
            start_outcome=StartOutcome(started_new_session=True, initial_runtime_state="dead"),
        )

    cli.executions.execute = execute_code_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--stream", "1 + 1"],
    )

    assert result.exit_code == 0
    assert result.output == (
        "2\n"
        "Notice: session was restarted after the previous kernel died; "
        "prior in-memory state was lost.\n"
    )


def test_cli_exec_stream_human_reports_file_namespace_summary_without_duplicate_completion(
    cli_runner: CliRunner,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agentnb.cli as cli
    from agentnb.app import ExecRequest

    script = project_dir / "analysis.py"
    script.write_text("value = 2\n", encoding="utf-8")

    def exec_stub(request: ExecRequest, event_sink=None):
        del event_sink
        return success_response(
            command="exec",
            project=str(project_dir),
            session_id="default",
            data={
                "source_kind": request.source_kind,
                "source_path": str(request.source_path),
                "namespace_delta": {
                    "entries": [
                        {"change": "new", "name": "value", "type": "int", "repr": "2"},
                    ],
                    "new_count": 1,
                    "updated_count": 0,
                    "truncated": False,
                },
            },
        )

    monkeypatch.setattr(cli.application, "exec", exec_stub)

    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--stream", "--file", str(script)],
    )

    assert result.exit_code == 0
    assert result.output == "File executed. Namespace changes:\n- new: value: 2 (int)\n"


def test_cli_exec_stream_json_returns_error_final_frame_on_execution_failure(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.resolve_session_id = lambda **_: "default"  # type: ignore[method-assign]

    def execute_code_stub(**kwargs: object) -> ManagedExecution:
        sink = _event_sink(kwargs)
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

    cli.executions.execute = execute_code_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--stream", "--json", "1 / 0"],
    )

    assert result.exit_code == 1
    frames = [_frame(line) for line in result.output.splitlines() if line.strip()]
    assert frames[-1]["type"] == "final"
    assert frames[-1]["response"]["status"] == "error"
    assert frames[-1]["response"]["error"]["code"] == "EXECUTION_ERROR"
    assert frames[-1]["response"]["data"]["status"] == "error"


def test_cli_returns_no_kernel_error(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--no-ensure-started", "--json", "1+1"],
    )
    assert result.exit_code == 1

    payload = _payload(result.output)
    assert payload["status"] == "error"
    assert _error(payload)["code"] == "NO_KERNEL"


def test_cli_returns_kernel_not_ready_error_when_connection_exists_without_session(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    state_dir = project_dir / ".agentnb"
    state_dir.mkdir()
    (state_dir / "kernel-default.json").write_text("{}", encoding="utf-8")

    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--no-ensure-started", "--json", "1+1"],
    )
    assert result.exit_code == 1

    payload = _payload(result.output)
    assert payload["status"] == "error"
    assert _error(payload)["code"] == "KERNEL_NOT_READY"


def test_cli_vars_projects_starting_state_when_connection_exists_without_session(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    state_dir = project_dir / ".agentnb"
    state_dir.mkdir()
    (state_dir / "kernel-default.json").write_text("{}", encoding="utf-8")

    result = cli_runner.invoke(
        main,
        ["vars", "--project", str(project_dir), "--json"],
    )
    assert result.exit_code == 1

    payload = _payload(result.output)
    assert payload["status"] == "error"
    assert _error(payload)["code"] == "KERNEL_NOT_READY"
    assert payload["data"]["runtime_state"] == "starting"
    assert payload["data"]["session_exists"] is False
    assert len(payload["suggestions"]) == 2
    assert any("agentnb wait" in suggestion for suggestion in payload["suggestions"])
    assert any("agentnb status" in suggestion for suggestion in payload["suggestions"])
    assert all("--session" not in suggestion for suggestion in payload["suggestions"])
    assert all(
        "--session" not in " ".join(action["args"]) for action in payload["suggestion_actions"]
    )


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
    error = _error(payload)
    assert error["code"] == "AMBIGUOUS_SESSION"
    assert error["message"].startswith("Multiple live sessions exist")
    assert payload["data"]["available_sessions"] == ["default", "analysis"]
    assert len(payload["suggestions"]) == 2
    assert any("sessions list" in suggestion for suggestion in payload["suggestions"])
    assert any("--session NAME" in suggestion for suggestion in payload["suggestions"])


def test_cli_exec_implicit_target_is_ambiguous_even_with_current_session_preference(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.runtime.list_sessions = lambda **_: [  # type: ignore[method-assign]
        {"session_id": "default"},
        {"session_id": "analysis"},
    ]
    cli.runtime.current_session_id = lambda **_: "analysis"  # type: ignore[method-assign]

    result = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1 + 1"])

    assert result.exit_code == 1
    payload = _payload(result.output)
    assert _error(payload)["code"] == "AMBIGUOUS_SESSION"
    assert payload["suggestion_actions"] == [
        {
            "kind": "command",
            "label": "List sessions",
            "command": "agentnb",
            "args": ["sessions", "list", "--project", str(project_dir), "--json"],
        },
        {
            "kind": "command",
            "label": "Retry with --session",
            "command": "agentnb",
            "args": ["exec", "--session", "NAME", "--project", str(project_dir), "--json"],
        },
    ]


def test_cli_agent_exec_ambiguity_keeps_suggestion_actions(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.runtime.list_sessions = lambda **_: [  # type: ignore[method-assign]
        {"session_id": "default"},
        {"session_id": "analysis"},
    ]

    cli.runtime.current_session_id = lambda **_: "analysis"  # type: ignore[method-assign]

    result = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--agent", "1 + 1"])

    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["error"]["code"] == "AMBIGUOUS_SESSION"
    assert payload["suggestion_actions"] == [
        {
            "kind": "command",
            "label": "List sessions",
            "command": "agentnb",
            "args": ["sessions", "list", "--project", str(project_dir), "--json"],
        },
        {
            "kind": "command",
            "label": "Retry with --session",
            "command": "agentnb",
            "args": ["exec", "--session", "NAME", "--project", str(project_dir), "--json"],
        },
    ]


def test_cli_omitted_session_ignores_remembered_preference_when_multiple_live_sessions_exist(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.current_session_id = lambda **_: "analysis"  # type: ignore[method-assign]
    cli.runtime.list_sessions = lambda **_: [  # type: ignore[method-assign]
        {"session_id": "default"},
        {"session_id": "analysis"},
    ]

    result = cli_runner.invoke(main, ["status", "--project", str(project_dir), "--json"])

    assert result.exit_code == 1
    payload = _payload(result.output)
    assert _error(payload)["code"] == "AMBIGUOUS_SESSION"
    assert payload["data"]["available_sessions"] == ["default", "analysis"]


def test_cli_status_uses_only_live_session_when_implicit(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    status_calls: list[dict[str, object]] = []

    cli.runtime.list_sessions = lambda **_: [  # type: ignore[method-assign]
        {"session_id": "analysis"}
    ]

    def state_stub(**kwargs: object) -> object:
        status_calls.append(dict(kwargs))
        from agentnb.runtime import RuntimeState

        return RuntimeState(
            kind="ready",
            session_id="analysis",
            kernel_status=KernelStatus(alive=True, pid=123, busy=False),
        )

    cli.runtime.runtime_state = state_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(main, ["status", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["session_id"] == "analysis"
    assert status_calls[0]["session_id"] == "analysis"


def test_cli_status_wait_uses_execution_service_wait_for_ready(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    wait_stub = Mock(
        return_value=SessionAccessOutcome(
            status=KernelStatus(alive=True, pid=321),
            waited=True,
            waited_for="ready",
            runtime_state="ready",
            waited_ms=10,
            initial_runtime_state="starting",
        )
    )

    cli.executions.wait_for_session_access = wait_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["status", "--project", str(project_dir), "--wait", "--timeout", "5", "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["alive"] is True
    assert payload["data"]["waited"] is True
    wait_stub.assert_called_once_with(
        project_root=project_dir,
        session_id="default",
        timeout_s=5.0,
        target="ready",
    )


def test_cli_status_wait_idle_uses_execution_service_wait_for_idle(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    wait_stub = Mock(
        return_value=SessionAccessOutcome(
            status=KernelStatus(alive=True, pid=321, busy=False),
            waited=True,
            waited_for="idle",
            runtime_state="ready",
            waited_ms=10,
            initial_runtime_state="busy",
        )
    )

    cli.executions.wait_for_session_access = wait_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["status", "--project", str(project_dir), "--wait-idle", "--timeout", "5", "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["alive"] is True
    assert payload["data"]["waited"] is True
    assert payload["data"]["waited_for"] == "idle"
    assert payload["data"]["waited_ms"] == 10
    assert payload["data"]["initial_runtime_state"] == "busy"
    wait_stub.assert_called_once_with(
        project_root=project_dir,
        session_id="default",
        timeout_s=5.0,
        target="idle",
    )


def test_cli_status_wait_idle_waits_for_active_run_before_returning(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    wait_stub = Mock(
        return_value=SessionAccessOutcome(
            status=KernelStatus(alive=True, pid=321, busy=False),
            waited=True,
            waited_for="idle",
            runtime_state="ready",
        )
    )

    cli.executions.wait_for_session_access = wait_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["status", "--project", str(project_dir), "--wait-idle", "--timeout", "5", "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["waited_for"] == "idle"
    wait_stub.assert_called_once_with(
        project_root=project_dir,
        session_id="default",
        timeout_s=5.0,
        target="idle",
    )


def test_cli_wait_uses_execution_service_wait_for_usable(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    wait_stub = Mock(
        return_value=SessionAccessOutcome(
            status=KernelStatus(alive=True, pid=321, busy=False),
            waited=False,
            runtime_state="ready",
        )
    )

    cli.executions.wait_for_session_access = wait_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["wait", "--project", str(project_dir), "--timeout", "5", "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "wait"
    assert payload["data"]["alive"] is True
    assert payload["data"]["waited"] is False
    assert "waited_for" not in payload["data"]
    wait_stub.assert_called_once_with(
        project_root=project_dir,
        session_id="default",
        timeout_s=5.0,
        target="usable",
    )


def test_cli_quiet_suppresses_status_body_and_suggestions(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.status = lambda **_: KernelStatus(alive=True, pid=123)  # type: ignore[method-assign]

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
    assert payload["data"]["ready"] is True
    assert payload["data"]["selected_python"] == "python"
    assert payload["data"]["python_source"] == "current"
    assert payload["data"]["session_exists"] is False
    assert payload["data"]["stale_session_cleaned"] is False
    assert payload["data"]["checks"][0]["name"] == "python"
    assert payload["data"]["checks"][0]["status"] == "ok"
    assert payload["data"]["checks"][0]["message"] == "ok"


def test_cli_start_uses_manual_recovery_contract(
    cli_runner: CliRunner,
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    start_mock = mocker.patch("agentnb.cli.runtime.start")
    start_mock.return_value = (
        KernelStatus(
            alive=True,
            pid=1234,
            connection_file=str(project_dir / ".agentnb" / "kernel-default.json"),
            started_at="2026-03-09T00:00:00+00:00",
            uptime_s=0.0,
            python="python",
        ),
        True,
    )

    result = cli_runner.invoke(main, ["start", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    assert start_mock.call_args.kwargs["project_root"] == project_dir.resolve()
    assert start_mock.call_args.kwargs["python_executable"] is None


def test_cli_start_rejects_removed_auto_install_flag(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    result = cli_runner.invoke(
        main, ["start", "--project", str(project_dir), "--auto-install", "--json"]
    )

    assert result.exit_code != 0
    assert "No such option: --auto-install" in result.output


def test_cli_start_passes_named_session(
    cli_runner: CliRunner,
    project_dir: Path,
    mocker: MockerFixture,
) -> None:
    start_mock = mocker.patch("agentnb.cli.runtime.start")
    start_mock.return_value = (
        KernelStatus(
            alive=True,
            pid=1234,
            connection_file=str(project_dir / ".agentnb" / "kernel-analysis.json"),
            started_at="2026-03-09T00:00:00+00:00",
            uptime_s=0.0,
            python="python",
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
    assert 'agentnb "import json"' in result.output
    assert "`--agent` returns compact JSON." in result.output
    assert "agentnb wait" in result.output


@pytest.mark.parametrize(
    ("argv", "expected_phrases"),
    [
        (
            ["--help"],
            [
                "Persistent project-scoped Python REPL for agent workflows.",
                "runs show @latest",
                "Cleanup primitives:",
            ],
        ),
        (
            ["vars", "--help"],
            [
                "contain this substring",
                "require explicit selection",
            ],
        ),
        (
            ["history", "--help"],
            [
                "@latest",
                "helper/provenance entries",
            ],
        ),
        (
            ["exec", "--help"],
            [
                "Show only stdout from the execution.",
                "exec --fresh: stop and restart the session, then execute code.",
            ],
        ),
        (
            ["runs", "follow", "--help"],
            [
                "--timeout FLOAT",
                "observation window",
            ],
        ),
    ],
)
def test_cli_help_mentions_key_guidance(
    cli_runner: CliRunner,
    argv: list[str],
    expected_phrases: list[str],
) -> None:
    result = cli_runner.invoke(main, argv)

    assert result.exit_code == 0
    for phrase in expected_phrases:
        assert phrase in result.output


@pytest.mark.parametrize(
    ("argv", "expected_phrases"),
    [
        (
            ["list"],
            ["Unknown command 'list'.", "sessions list", "runs list"],
        ),
        (
            ["log"],
            ["Unknown command 'log'.", "agentnb history"],
        ),
    ],
)
def test_cli_unknown_command_reports_deterministic_guidance(
    cli_runner: CliRunner,
    argv: list[str],
    expected_phrases: list[str],
) -> None:
    result = cli_runner.invoke(main, argv)

    assert result.exit_code == 2
    for phrase in expected_phrases:
        assert phrase in result.output
    assert "NameError" not in result.output


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

    payload = json.loads(result.output)
    assert payload == {
        "ok": True,
        "command": "status",
        "session_id": "default",
        "data": {"alive": False, "runtime_state": "missing"},
    }


def test_cli_root_flags_work_after_subcommand(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(main, ["status", "--agent", "--project", str(project_dir)])
    assert result.exit_code == 0

    payload = json.loads(result.output)
    assert payload == {
        "ok": True,
        "command": "status",
        "session_id": "default",
        "data": {"alive": False, "runtime_state": "missing"},
    }


def test_cli_no_suggestions_strips_suggestions_from_json(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    result = cli_runner.invoke(
        main, ["--no-suggestions", "status", "--project", str(project_dir), "--json"]
    )
    assert result.exit_code == 0

    payload = _payload(result.output)
    assert payload["suggestions"] == []


def test_cli_quiet_root_flag_works_after_subcommand(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    result = cli_runner.invoke(main, ["status", "--quiet", "--project", str(project_dir)])
    assert result.exit_code == 0
    assert result.output == ""


def test_cli_quiet_keeps_error_recovery_guidance(cli_runner: CliRunner, project_dir: Path) -> None:
    result = cli_runner.invoke(
        main,
        ["exec", "--quiet", "--project", str(project_dir), "missing_name"],
    )

    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Next:" in result.output


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

    cli.executions.execute = lambda **_: _managed_execution(result="2")  # type: ignore[method-assign]

    exec_res = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--result-only", "1 + 1"],
    )
    assert exec_res.exit_code == 0
    assert exec_res.output.strip() == "2"


def test_cli_exec_result_only_prefers_preview_summary_for_large_values(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.executions.execute = lambda **_: ManagedExecution(  # type: ignore[method-assign]
        record=ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="ok",
            duration_ms=5,
            code="big",
            outputs=[
                OutputItem.result(
                    text=(
                        "     i text\n"
                        "0    0    xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
                        "\n[200 rows x 2 columns]"
                    ),
                    mime={
                        "text/plain": (
                            "     i text\n"
                            "0    0    xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx\n"
                            "\n[200 rows x 2 columns]"
                        ),
                        "text/html": (
                            '<table border="1" class="dataframe">'
                            "<thead><tr><th></th><th>i</th><th>text</th></tr></thead>"
                            "<tbody><tr><th>0</th><td>0</td><td>x</td></tr></tbody>"
                            "</table>"
                        ),
                    },
                )
            ],
        )
    )

    exec_res = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--result-only", "big"],
    )

    assert exec_res.exit_code == 0
    assert exec_res.output.strip() == "DataFrame shape=(200, 2) columns=i, text"


def test_cli_exec_passes_named_session(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    execute_calls: list[dict[str, object]] = []

    def execute_stub(**kwargs: object) -> ManagedExecution:
        execute_calls.append(dict(kwargs))
        return _managed_execution(result="2", session_id="analysis")

    cli.executions.execute = execute_stub  # type: ignore[method-assign]

    exec_res = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--session", "analysis", "--json", "1 + 1"],
    )

    assert exec_res.exit_code == 0
    payload = _payload(exec_res.output)
    assert payload["session_id"] == "analysis"
    assert cast(Any, execute_calls[0]["request"]).session_id == "analysis"


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


def test_cli_exec_human_reports_restart_notice_after_dead_recovery(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.executions.execute = lambda **_: ManagedExecution(  # type: ignore[method-assign]
        record=ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="error",
            duration_ms=5,
            code="x",
            ename="NameError",
            evalue="name 'x' is not defined",
            traceback=["tb"],
        ),
        start_outcome=StartOutcome(started_new_session=True, initial_runtime_state="dead"),
    )

    result = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "x"])

    assert result.exit_code == 1
    assert "Notice: session was restarted after the previous kernel died" in result.output
    assert "Error: Execution failed" in result.output


def test_cli_exec_file_request_preserves_source_metadata(
    cli_runner: CliRunner,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agentnb.cli as cli
    from agentnb.app import ExecRequest

    script = project_dir / "analysis.py"
    script.write_text("value = 2\npayload = {'id': 1}\n", encoding="utf-8")
    captured: dict[str, ExecRequest] = {}

    def exec_stub(request: ExecRequest):
        captured["request"] = request
        return success_response(
            command="exec",
            project=str(project_dir),
            session_id="default",
            data={
                "source_kind": request.source_kind,
                "source_path": str(request.source_path),
                "namespace_delta": {
                    "entries": [
                        {"change": "new", "name": "value", "type": "int", "repr": "2"},
                    ],
                    "new_count": 1,
                    "updated_count": 0,
                    "truncated": False,
                },
            },
        )

    monkeypatch.setattr(cli.application, "exec", exec_stub)

    result = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--file", str(script)])

    assert result.exit_code == 0
    request = captured["request"]
    assert request.source_kind == "file"
    assert request.source_path == script
    assert "File executed. Namespace changes:" in result.output


def test_cli_exec_background_returns_run_id(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.executions.execute = lambda **_: ManagedExecution(  # type: ignore[method-assign]
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
        start_outcome=StartOutcome(started_new_session=True, initial_runtime_state="missing"),
    )

    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--background", "--json", "1 + 1"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["execution_id"] == "run-1"
    assert payload["data"]["background"] is True
    assert "runs wait run-1" in payload["suggestions"][0]


def test_cli_exec_existing_python_path_suggests_file_execution(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    script = project_dir / "analysis.py"
    script.write_text("answer = 42\n", encoding="utf-8")

    result = cli_runner.invoke(
        main,
        ["exec", "--project", str(project_dir), "--json", str(script)],
    )

    assert result.exit_code == 1
    payload = _payload(result.output)
    assert _error(payload)["code"] == "INVALID_INPUT"
    assert payload["data"] == {
        "input_shape": "exec_file_path",
        "source_path": str(script),
    }
    assert len(payload["suggestions"]) == 2
    assert any("exec --file" in suggestion for suggestion in payload["suggestions"])
    assert any(str(script) in suggestion for suggestion in payload["suggestions"])
    assert payload["suggestion_actions"] == [
        {
            "kind": "command",
            "label": "Use exec --file",
            "command": "agentnb",
            "args": ["exec", "--file", str(script), "--project", str(project_dir), "--json"],
        },
        {
            "kind": "command",
            "label": "Use top-level file exec",
            "command": "agentnb",
            "args": [str(script), "--project", str(project_dir), "--json"],
        },
    ]


def test_cli_vars_includes_types_by_default(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.introspection.list_vars = lambda **_: _helper_result(  # type: ignore[method-assign]
        [{"name": "value", "type": "int", "repr": "42"}]
    )

    vars_res = cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])
    assert vars_res.exit_code == 0

    payload = _payload(vars_res.output)
    assert payload["data"]["vars"][0]["name"] == "value"
    assert payload["data"]["vars"][0]["type"] == "int"


def test_cli_vars_human_output_includes_session_identity(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.list_sessions = lambda **_: [{"session_id": "analysis"}]  # type: ignore[method-assign]
    cli.introspection.list_vars = lambda **_: _helper_result(  # type: ignore[method-assign]
        [{"name": "value", "type": "int", "repr": "42"}]
    )

    vars_res = cli_runner.invoke(main, ["vars", "--project", str(project_dir)])

    assert vars_res.exit_code == 0
    assert "session: analysis" in vars_res.output
    assert "value: 42 (int)" in vars_res.output


def test_cli_vars_reports_helper_wait_metadata(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.introspection.list_vars = lambda **_: KernelHelperResult(  # type: ignore[method-assign]
        execution=ExecutionResult(status="ok"),
        payload=[{"name": "value", "type": "int", "repr": "42"}],
        access_metadata=HelperAccessMetadata(
            started_new_session=True,
            waited=True,
            waited_for="idle",
            waited_ms=30,
            initial_runtime_state="busy",
        ),
    )

    vars_res = cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])

    assert vars_res.exit_code == 0
    payload = _payload(vars_res.output)
    assert payload["data"]["started_new_session"] is True
    assert payload["data"]["waited"] is True
    assert payload["data"]["waited_for"] == "idle"
    assert payload["data"]["waited_ms"] == 30
    assert payload["data"]["initial_runtime_state"] == "busy"


def test_cli_vars_hides_routines_and_compacts_container_values(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.introspection.list_vars = lambda **_: _helper_result(  # type: ignore[method-assign]
        [
            {"name": "posts", "type": "list", "repr": "list len=1 item_keys=id, title, body"},
            {"name": "query", "type": "dict", "repr": "dict len=2 keys=postId, _limit"},
        ]
    )

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

    cli.introspection.list_vars = lambda **_: _helper_result(  # type: ignore[method-assign]
        [{"name": "value", "type": "int", "repr": "42"}]
    )

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

    cli.introspection.list_vars = lambda **_: _helper_result(  # type: ignore[method-assign]
        [
            {"name": "alpha_value", "type": "int", "repr": "1"},
            {"name": "beta_value", "type": "int", "repr": "2"},
            {"name": "gamma_value", "type": "int", "repr": "3"},
        ]
    )

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

    queries: list[JournalQuery] = []

    def select_history_stub(**kwargs: object) -> SimpleNamespace:
        queries.append(cast(JournalQuery, kwargs["query"]))
        return _history_selection(
            [_journal_entry(label="exec x = 2 x + 2", input_text="x = 2\nx + 2")]
        )

    cli.runtime.select_history = select_history_stub  # type: ignore[method-assign]

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--latest", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    assert len(payload["data"]["entries"]) == 1
    assert payload["data"]["entries"][0]["command_type"] == "exec"
    assert payload["data"]["entries"][0]["label"] == "exec x = 2 | x + 2"
    assert payload["data"]["entries"][0]["kind"] == "user_command"
    assert queries[0].session_id == "default"
    assert queries[0].errors_only is False
    assert queries[0].include_internal is False
    assert queries[0].latest is True
    assert queries[0].last is None


def test_cli_history_hides_helper_code_by_default(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.runtime.select_history = lambda **_: _history_selection(  # type: ignore[method-assign]
        [
            _journal_entry(command_type="exec", label="exec value = 42 import localmod"),
            _journal_entry(command_type="vars", label="vars"),
            _journal_entry(command_type="inspect", label="inspect value"),
            _journal_entry(command_type="reload", label="reload localmod"),
        ]
    )

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

    queries: list[JournalQuery] = []

    def select_history_stub(**kwargs: object) -> SimpleNamespace:
        queries.append(cast(JournalQuery, kwargs["query"]))
        return _history_selection(
            [
                _journal_entry(
                    kind="kernel_execution",
                    command_type="exec",
                    label="exec kernel execution",
                    user_visible=False,
                    code="value = 42",
                ),
                _journal_entry(command_type="exec", label="exec value = 42"),
                _journal_entry(
                    kind="kernel_execution",
                    command_type="vars",
                    label="vars kernel execution",
                    user_visible=False,
                    code="get_ipython()",
                ),
                _journal_entry(command_type="vars", label="vars"),
            ]
        )

    cli.runtime.select_history = select_history_stub  # type: ignore[method-assign]

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
    assert queries[0].include_internal is True


def test_cli_history_errors_filters_semantic_failures(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    queries: list[JournalQuery] = []

    def select_history_stub(**kwargs: object) -> SimpleNamespace:
        queries.append(cast(JournalQuery, kwargs["query"]))
        return _history_selection(
            [
                _journal_entry(
                    command_type="inspect",
                    label="inspect missing_name",
                    status="error",
                )
            ]
        )

    cli.runtime.select_history = select_history_stub  # type: ignore[method-assign]

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
    assert queries[0].errors_only is True


def test_cli_history_last_limits_visible_entries(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    queries: list[JournalQuery] = []

    def select_history_stub(**kwargs: object) -> SimpleNamespace:
        queries.append(cast(JournalQuery, kwargs["query"]))
        return _history_selection(
            [
                _journal_entry(command_type="vars", label="vars"),
                _journal_entry(command_type="reload", label="reload localmod"),
            ]
        )

    cli.runtime.select_history = select_history_stub  # type: ignore[method-assign]

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--last", "2", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    assert [entry["command_type"] for entry in payload["data"]["entries"]] == ["vars", "reload"]
    assert queries[0].session_id == "default"
    assert queries[0].errors_only is False
    assert queries[0].include_internal is False
    assert queries[0].latest is False
    assert queries[0].last == 2


def test_cli_history_error_exec_label_is_semantic(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.runtime.select_history = lambda **_: _history_selection(  # type: ignore[method-assign]
        [
            _journal_entry(
                label="exec error ZeroDivisionError",
                status="error",
                error_type="ZeroDivisionError",
            )
        ]
    )

    history_res = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--errors", "--latest", "--json"],
    )
    assert history_res.exit_code == 0

    payload = _payload(history_res.output)
    assert payload["data"]["entries"][0]["label"] == "exec error ZeroDivisionError"


def test_cli_history_latest_selector_uses_history_reference(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    queries: list[JournalQuery] = []

    def select_history_stub(**kwargs: object) -> SimpleNamespace:
        queries.append(cast(JournalQuery, kwargs["query"]))
        return _history_selection([_journal_entry(command_type="vars", label="vars")])

    cli.runtime.select_history = select_history_stub  # type: ignore[method-assign]

    history_res = cli_runner.invoke(
        main,
        ["history", "@latest", "--project", str(project_dir), "--json"],
    )

    assert history_res.exit_code == 0
    payload = _payload(history_res.output)
    assert payload["data"]["entries"][0]["label"] == "vars"
    assert queries[0].latest is True
    assert queries[0].errors_only is False


def test_cli_history_last_error_selector_uses_history_reference(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    queries: list[JournalQuery] = []

    def select_history_stub(**kwargs: object) -> SimpleNamespace:
        queries.append(cast(JournalQuery, kwargs["query"]))
        return _history_selection(
            [
                _journal_entry(
                    command_type="exec",
                    label="exec error ZeroDivisionError",
                    status="error",
                    error_type="ZeroDivisionError",
                )
            ]
        )

    cli.runtime.select_history = select_history_stub  # type: ignore[method-assign]

    history_res = cli_runner.invoke(
        main,
        ["history", "@last-error", "--project", str(project_dir), "--json"],
    )

    assert history_res.exit_code == 0
    payload = _payload(history_res.output)
    assert payload["data"]["entries"][0]["label"] == "exec error ZeroDivisionError"
    assert queries[0].latest is True
    assert queries[0].errors_only is True
    assert queries[0].prefer_execution_errors is True


def test_cli_history_successes_flag_filters_successes(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    queries: list[JournalQuery] = []

    def select_history_stub(**kwargs: object) -> SimpleNamespace:
        queries.append(cast(JournalQuery, kwargs["query"]))
        return _history_selection([_journal_entry(command_type="exec", label="exec")])

    cli.runtime.select_history = select_history_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["history", "--project", str(project_dir), "--successes", "--latest", "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["entries"][0]["label"] == "exec"
    assert queries[0].success_only is True
    assert queries[0].latest is True


def test_cli_history_accepts_equivalent_selector_flag_combinations(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    queries: list[JournalQuery] = []

    def select_history_stub(**kwargs: object) -> SimpleNamespace:
        queries.append(cast(JournalQuery, kwargs["query"]))
        return _history_selection([_journal_entry(command_type="exec", label="exec")])

    cli.runtime.select_history = select_history_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        [
            "history",
            "@last-success",
            "--successes",
            "--latest",
            "--project",
            str(project_dir),
            "--json",
        ],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["entries"][0]["label"] == "exec"
    assert queries[0].success_only is True
    assert queries[0].latest is True


def test_cli_history_rejects_contradictory_selector_flag_combinations(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    result = cli_runner.invoke(
        main,
        [
            "history",
            "@last-error",
            "--successes",
            "--project",
            str(project_dir),
            "--json",
        ],
    )

    assert result.exit_code == 1
    payload = _payload(result.output)
    assert _error(payload)["code"] == "INVALID_INPUT"
    assert "equivalent --errors/--successes/--latest filters" in _error(payload)["message"]


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


def test_cli_bare_sessions_shortcut_accepts_group_options(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.runtime.list_sessions = lambda **_: [{"session_id": "default"}]  # type: ignore[method-assign]

    result = cli_runner.invoke(main, ["sessions", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "sessions-list"
    assert payload["data"]["sessions"][0]["session_id"] == "default"


def test_cli_sessions_list_empty_has_actionable_suggestions(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.runtime.list_sessions = lambda **_: []  # type: ignore[method-assign]

    result = cli_runner.invoke(main, ["sessions", "list", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["sessions"] == []
    assert len(payload["suggestions"]) == 2
    assert any("agentnb start" in suggestion for suggestion in payload["suggestions"])
    assert any('agentnb "..."' in suggestion for suggestion in payload["suggestions"])


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
            "status": "error",
            "duration_ms": 12,
            "terminal_reason": "cancelled",
            "cancel_requested": True,
            "stdout": "",
            "stderr": "",
            "result": "42",
            "ename": "CancelledError",
        }
    ]

    result = cli_runner.invoke(main, ["runs", "list", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "runs-list"
    assert payload["data"]["runs"][0]["execution_id"] == "run-1"
    assert payload["data"]["runs"][0]["terminal_reason"] == "cancelled"
    assert payload["data"]["runs"][0]["cancel_requested"] is True
    assert payload["data"]["runs"][0]["result_preview"] == "42"


def test_cli_runs_show_returns_run_details(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.executions.retrieve_run = lambda **_: RunRetrievalOutcome(  # type: ignore[method-assign]
        run=cast(
            ExecutionRecord,
            {
                "execution_id": "run-1",
                "ts": "2026-03-10T00:00:00+00:00",
                "session_id": "default",
                "command_type": "exec",
                "status": "error",
                "duration_ms": 12,
                "code": "1 + 1",
                "stdout": "",
                "stderr": "",
                "result": "2",
                "execution_count": 1,
                "ename": "CancelledError",
                "evalue": "Run was cancelled by user.",
                "traceback": None,
                "terminal_reason": "cancelled",
                "cancel_requested": True,
                "recorded_ename": "KeyboardInterrupt",
                "outputs": [
                    {
                        "kind": "result",
                        "text": "2",
                        "mime": {"text/plain": "2"},
                    }
                ],
                "events": [],
            },
        )
    )

    result = cli_runner.invoke(
        main,
        ["runs", "show", "run-1", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["command"] == "runs-show"
    assert payload["data"]["run"]["execution_id"] == "run-1"
    assert "outputs" not in payload["data"]["run"]
    assert payload["data"]["run"]["terminal_reason"] == "cancelled"
    assert payload["data"]["run"]["cancel_requested"] is True
    assert payload["data"]["run"]["recorded_ename"] == "KeyboardInterrupt"


def test_cli_runs_show_json_strips_ansi_tracebacks(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.executions.retrieve_run = lambda **_: RunRetrievalOutcome(  # type: ignore[method-assign]
        run=cast(
            ExecutionRecord,
            {
                "execution_id": "run-1",
                "session_id": "default",
                "status": "error",
                "traceback": ["\u001b[31mTraceback...\u001b[0m", "ZeroDivisionError: boom"],
                "recorded_traceback": ["\u001b[31mKeyboardInterrupt\u001b[0m"],
                "events": [
                    {
                        "kind": "error",
                        "content": "boom",
                        "metadata": {
                            "traceback": [
                                "\u001b[31mTraceback...\u001b[0m",
                                "ZeroDivisionError: boom",
                            ]
                        },
                    }
                ],
            },
        )
    )

    result = cli_runner.invoke(
        main,
        ["runs", "show", "run-1", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["run"]["traceback"] == ["Traceback...", "ZeroDivisionError: boom"]
    assert payload["data"]["run"]["recorded_traceback"] == ["KeyboardInterrupt"]
    event_tb = payload["data"]["run"]["events"][0]["metadata"]["traceback"]
    assert event_tb == ["Traceback...", "ZeroDivisionError: boom"]


def test_cli_runs_show_accepts_latest_selector(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.executions.list_runs = lambda **_: [  # type: ignore[method-assign]
        {"execution_id": "run-1", "ts": "2026-03-10T00:00:00+00:00"},
        {"execution_id": "run-2", "ts": "2026-03-11T00:00:00+00:00"},
    ]
    cli.executions.retrieve_run = lambda **_: RunRetrievalOutcome(  # type: ignore[method-assign]
        run=build_execution_record(
            execution_id="run-2",
            ts="2026-03-11T00:00:00+00:00",
            session_id="default",
            status="ok",
            duration_ms=10,
            result="2",
            events=[],
        )
    )

    result = cli_runner.invoke(
        main,
        ["runs", "show", "@latest", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["run"]["execution_id"] == "run-2"


def test_cli_runs_show_defaults_to_latest_relevant_run(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.current_session_id = lambda **_: "analysis"  # type: ignore[method-assign]
    cli.executions.list_runs = lambda **kwargs: (  # type: ignore[method-assign]
        [{"execution_id": "run-analysis", "ts": "2026-03-11T00:00:00+00:00"}]
        if kwargs["request"].session_id == "analysis"
        else [{"execution_id": "run-other", "ts": "2026-03-12T00:00:00+00:00"}]
    )
    cli.executions.retrieve_run = lambda **_: RunRetrievalOutcome(  # type: ignore[method-assign]
        run=build_execution_record(
            execution_id="run-analysis",
            session_id="analysis",
            status="ok",
        )
    )

    result = cli_runner.invoke(main, ["runs", "show", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["run"]["execution_id"] == "run-analysis"


def test_cli_runs_wait_defaults_to_active_run(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.executions.list_runs = lambda **_: [  # type: ignore[method-assign]
        {"execution_id": "run-1", "ts": "2026-03-11T00:00:00+00:00", "status": "running"},
    ]
    cli.executions.retrieve_run = lambda **_: RunRetrievalOutcome(  # type: ignore[method-assign]
        run=build_execution_record(
            execution_id="run-1",
            session_id="default",
            status="ok",
            result="2",
        )
    )

    result = cli_runner.invoke(main, ["runs", "wait", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["run"]["execution_id"] == "run-1"


def test_cli_runs_show_human_clarifies_snapshot_for_running_run(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.executions.retrieve_run = lambda **_: RunRetrievalOutcome(  # type: ignore[method-assign]
        run=build_execution_record(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            status="running",
            duration_ms=12,
            stdout="tick 1\ntick 2\n",
            stderr="",
            result=None,
            events=[{"kind": "stdout", "content": "tick 1\n", "metadata": {}}],
        )
    )

    result = cli_runner.invoke(
        main,
        ["runs", "show", "run-1", "--project", str(project_dir)],
    )

    assert result.exit_code == 0
    assert "Run run-1 [running] exec on session default." in result.output
    assert (
        "snapshot: persisted state only; use `agentnb runs follow` for live events" in result.output
    )


def test_cli_runs_show_json_marks_running_snapshot_as_stale(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.executions.retrieve_run = lambda **_: RunRetrievalOutcome(  # type: ignore[method-assign]
        run=build_execution_record(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            status="running",
            snapshot_stale=True,
            duration_ms=12,
            stdout="",
            stderr="",
            result=None,
            events=[],
        )
    )

    result = cli_runner.invoke(
        main,
        ["runs", "show", "run-1", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["run"]["snapshot_stale"] is True


def test_cli_runs_show_json_exposes_top_level_status_alias(
    cli_runner: CliRunner, project_dir: Path
) -> None:
    import agentnb.cli as cli

    cli.executions.retrieve_run = lambda **_: RunRetrievalOutcome(  # type: ignore[method-assign]
        run=build_execution_record(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            status="running",
            duration_ms=12,
            stdout="",
            stderr="",
            result=None,
            events=[],
        )
    )

    result = cli_runner.invoke(
        main,
        ["runs", "show", "run-1", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["status"] == "running"
    assert payload["data"]["run"]["status"] == "running"


def test_cli_runs_wait_returns_completed_run(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.executions.retrieve_run = lambda **_: RunRetrievalOutcome(  # type: ignore[method-assign]
        run=build_execution_record(
            execution_id="run-1",
            status="ok",
            result="2",
        )
    )

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
            lambda cli: setattr(
                cli.executions,
                "retrieve_run",
                lambda **_: RunRetrievalOutcome(
                    run=cast(
                        ExecutionRecord,
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
                    )
                ),
            ),
        ),
        (
            "follow",
            lambda cli: setattr(
                cli.executions,
                "retrieve_run",
                lambda **kwargs: (
                    _event_sink(kwargs).started(
                        execution_id="run-1",
                        session_id="default",
                    ),
                    RunRetrievalOutcome(
                        run=build_execution_record(
                            execution_id="run-1",
                            session_id="default",
                            status="ok",
                            result="2",
                        ),
                        completion_reason="terminal",
                    ),
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
        frames = [_frame(line) for line in result.output.splitlines() if line.strip()]
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

    def follow_stub(**kwargs: object) -> RunRetrievalOutcome:
        sink = _event_sink(kwargs)
        sink.started(execution_id="run-1", session_id="default")
        sink.accept(ExecutionEvent(kind="stdout", content="hello\n"))
        sink.accept(ExecutionEvent(kind="result", content="2"))
        return RunRetrievalOutcome(
            run=build_execution_record(
                execution_id="run-1",
                session_id="default",
                status="ok",
                result="2",
                events=[
                    ExecutionEvent(kind="stdout", content="hello\n"),
                    ExecutionEvent(kind="result", content="2"),
                ],
            ),
            completion_reason="terminal",
        )

    cli.executions.retrieve_run = follow_stub  # type: ignore[method-assign]

    result = cli_runner.invoke(
        main,
        ["runs", "follow", "run-1", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    frames = [_frame(line) for line in result.output.splitlines() if line.strip()]
    assert frames[0] == {"type": "start", "execution_id": "run-1", "session_id": "default"}
    assert frames[1]["event"]["kind"] == "stdout"
    assert frames[2]["event"]["kind"] == "result"
    assert frames[-1]["type"] == "final"
    assert frames[-1]["response"]["command"] == "runs-follow"
    assert frames[-1]["response"]["data"]["run"]["execution_id"] == "run-1"


def test_cli_runs_follow_window_elapsed_returns_ok_final_frame(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.executions.retrieve_run = lambda **_: RunRetrievalOutcome(  # type: ignore[method-assign]
        run=build_execution_record(status="running", duration_ms=12, events=[]),
        completion_reason="window_elapsed",
        replayed_event_count=1,
        emitted_event_count=0,
    )

    result = cli_runner.invoke(
        main,
        ["runs", "follow", "run-1", "--project", str(project_dir), "--json"],
    )

    assert result.exit_code == 0
    frames = [_frame(line) for line in result.output.splitlines() if line.strip()]
    assert frames[-1]["type"] == "final"
    assert frames[-1]["response"]["status"] == "ok"
    assert frames[-1]["response"]["data"]["completion_reason"] == "window_elapsed"
    assert frames[-1]["response"]["data"]["replayed_event_count"] == 1
    assert frames[-1]["response"]["data"]["emitted_event_count"] == 0


def test_cli_runs_follow_human_window_elapsed_reuses_snapshot_renderer(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.executions.retrieve_run = lambda **_: RunRetrievalOutcome(  # type: ignore[method-assign]
        run=build_execution_record(
            status="running",
            duration_ms=12,
            stdout="tick\n",
            events=[],
        ),
        completion_reason="window_elapsed",
    )

    result = cli_runner.invoke(
        main,
        ["--no-suggestions", "runs", "follow", "run-1", "--project", str(project_dir)],
    )

    assert result.exit_code == 0
    assert result.output == (
        "Run run-1 [running] exec on session default.\n"
        "duration: 12ms\n"
        "Observation window elapsed; the run is still active.\n"
    )


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

    cli.introspection.reload_module = lambda **_: _helper_result(  # type: ignore[method-assign]
        {
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
    )

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
        raise SessionBusyError(
            wait_behavior="immediate",
            waited_ms=0,
            lock_pid=321,
            lock_acquired_at="2026-03-19T12:00:00+00:00",
            busy_for_ms=1500,
        )

    cli.executions.execute = raise_busy  # type: ignore[method-assign]
    exec_res = cli_runner.invoke(main, ["exec", "--project", str(project_dir), "--json", "1 + 1"])
    assert exec_res.exit_code == 1

    payload = _payload(exec_res.output)
    error = _error(payload)
    assert error["code"] == "SESSION_BUSY"
    assert "Wait for the prior command to finish" in error["message"]
    assert payload["data"]["wait_behavior"] == "immediate"
    assert payload["data"]["waited_ms"] == 0
    assert payload["data"]["lock_pid"] == 321
    assert payload["data"]["lock_acquired_at"] == "2026-03-19T12:00:00+00:00"
    assert payload["data"]["busy_for_ms"] == 1500


def test_cli_background_overlap_fails_fast_with_blocking_run_id(
    cli_runner: CliRunner,
    started_runtime: tuple[object, Path],
) -> None:
    _, project_dir = started_runtime

    background = cli_runner.invoke(
        main,
        [
            "--project",
            str(project_dir),
            "--json",
            "--background",
            "import time; time.sleep(1.5); 'done'",
        ],
    )
    assert background.exit_code == 0
    background_payload = _payload(background.output)
    blocking_execution_id = background_payload["data"]["execution_id"]

    result = cli_runner.invoke(main, ["--project", str(project_dir), "--json", "99"])

    assert result.exit_code == 1
    payload = _payload(result.output)
    assert _error(payload)["code"] == "SESSION_BUSY"
    assert payload["data"]["active_execution_id"] == blocking_execution_id
    assert payload["suggestions"] == [
        (
            f"Run `agentnb runs wait {blocking_execution_id} --project {project_dir} --json` "
            "to wait for the blocking run."
        ),
        (
            f"Run `agentnb runs show {blocking_execution_id} --project {project_dir} --json` "
            "to inspect the blocking run."
        ),
    ]

    wait_result = cli_runner.invoke(
        main,
        ["runs", "wait", blocking_execution_id, "--project", str(project_dir), "--json"],
    )
    assert wait_result.exit_code == 0


def test_cli_status_human_suppresses_implicit_session_switch_for_non_live_preference(
    cli_runner: CliRunner,
    runtime,
    project_dir: Path,
) -> None:
    runtime.start(project_dir, session_id="analysis")
    runtime.remember_current_session(project_root=project_dir, session_id="default")

    try:
        result = cli_runner.invoke(main, ["status", "--project", str(project_dir)])
    finally:
        runtime.stop(project_dir, session_id="analysis")

    assert result.exit_code == 0
    assert "session: analysis" in result.output
    assert "(now targeting session: analysis)" not in result.output


def test_cli_doctor_uses_target_project_root(
    cli_runner: CliRunner,
    project_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agentnb.cli as cli

    calls: list[dict[str, object]] = []

    class FakeProvisioner:
        def __init__(self, project_root: Path) -> None:
            self.project_root = project_root

        def doctor(
            self,
            preferred_python: Path | None = None,
        ) -> DoctorReport:
            calls.append(
                {
                    "project_root": self.project_root,
                    "preferred_python": preferred_python,
                }
            )
            return DoctorReport(
                ready=True,
                selected_python="/custom/python",
                python_source="explicit",
                checks=[DoctorCheck(name="python", status="ok", message="ok")],
            )

    monkeypatch.setattr(
        cli.runtime,
        "_provisioner_factory",
        lambda project_root: FakeProvisioner(project_root),
    )

    result = cli_runner.invoke(main, ["doctor", "--project", str(project_dir), "--json"])

    assert result.exit_code == 0
    payload = _payload(result.output)
    assert payload["data"]["ready"] is True
    assert calls == [
        {
            "project_root": project_dir.resolve(),
            "preferred_python": None,
        }
    ]


def test_cli_doctor_rejects_removed_fix_flag(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    result = cli_runner.invoke(main, ["doctor", "--project", str(project_dir), "--fix", "--json"])

    assert result.exit_code != 0
    assert "No such option: --fix" in result.output


def test_cli_vars_compacts_dataframe_repr(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.introspection.list_vars = lambda **_: _helper_result(  # type: ignore[method-assign]
        [{"name": "frame", "type": "FakeFrame", "repr": "DataFrame shape=(10, 3) columns=a, b, c"}]
    )

    vars_res = cli_runner.invoke(main, ["vars", "--project", str(project_dir), "--json"])
    payload = _payload(vars_res.output)
    assert payload["data"]["vars"][0]["repr"] == "DataFrame shape=(10, 3) columns=a, b, c"


def test_cli_sqlite_rows_get_structural_previews(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.introspection.list_vars = lambda **_: _helper_result(  # type: ignore[method-assign]
        [{"name": "rows", "type": "list", "repr": "list len=2 item_keys=id, title"}]
    )
    cli.introspection.inspect_var = lambda **_: _helper_result(  # type: ignore[method-assign]
        {
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
    )

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

    cli.introspection.inspect_var = lambda **_: _helper_result(  # type: ignore[method-assign]
        {
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
    )

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


def test_cli_inspect_human_output_includes_session_identity(
    cli_runner: CliRunner,
    project_dir: Path,
) -> None:
    import agentnb.cli as cli

    cli.runtime.list_sessions = lambda **_: [{"session_id": "analysis"}]  # type: ignore[method-assign]
    cli.introspection.inspect_var = lambda **_: _helper_result(  # type: ignore[method-assign]
        {
            "name": "thing",
            "type": "Thing",
            "repr": "Thing(value=1)",
            "members": ["alpha", "beta"],
        }
    )

    inspect_res = cli_runner.invoke(main, ["inspect", "--project", str(project_dir), "thing"])

    assert inspect_res.exit_code == 0
    assert "session: analysis" in inspect_res.output
    assert "name: thing" in inspect_res.output


def test_cli_inspect_compacts_sequence_payload(cli_runner: CliRunner, project_dir: Path) -> None:
    import agentnb.cli as cli

    cli.introspection.inspect_var = lambda **_: _helper_result(  # type: ignore[method-assign]
        {
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
    )

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

    cli.runtime.select_history = lambda **_: _history_selection(  # type: ignore[method-assign]
        [
            _journal_entry(
                label=(
                    "exec url = "
                    "'https://jsonplaceholder.typicode.com/comments?"
                    "postId=1&_limit=2&expand=author&include=metadata' url"
                ),
                input_text=(
                    "url = 'https://jsonplaceholder.typicode.com/comments?"
                    "postId=1&_limit=2&expand=author&include=metadata'\n"
                    "url"
                ),
            )
        ]
    )

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
