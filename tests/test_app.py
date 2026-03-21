from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentnb.app import (
    AgentNBApp,
    ExecRequest,
    HistoryRequest,
    InspectRequest,
    ReloadRequest,
    ResetRequest,
    RunLookupRequest,
    RunsFollowRequest,
    RunsListRequest,
    RunsWaitRequest,
    SessionsDeleteRequest,
    SessionsListRequest,
    StatusRequest,
    VarsRequest,
    WaitRequest,
)
from agentnb.contracts import KernelStatus
from agentnb.errors import AmbiguousSessionError
from agentnb.execution import ExecutionRecord, ExecutionService, ManagedExecution
from agentnb.execution_invocation import ExecInvocationPolicy, OutputSelector
from agentnb.introspection import KernelHelperResult
from agentnb.journal import JournalEntry
from agentnb.runtime import KernelRuntime, KernelWaitResult, RuntimeState, SessionResolutionPolicy
from agentnb.selectors import parse_history_reference, parse_run_reference
from agentnb.state import CommandLockInfo


class DummySink:
    def started(self, *, execution_id: str, session_id: str) -> None:
        del execution_id, session_id

    def accept(self, event: object) -> None:
        del event


def _assert_called_with_subset(mock_obj, **expected: object) -> None:
    kwargs = mock_obj.call_args.kwargs
    for key, value in expected.items():
        assert kwargs[key] == value


@pytest.mark.parametrize(
    ("background", "stream", "output_selector", "expected_message"),
    [
        (
            True,
            False,
            "stdout",
            "Output selectors are not supported with --background.",
        ),
        (
            True,
            True,
            None,
            "--stream and --background cannot be used together.",
        ),
        (
            False,
            True,
            "result",
            "Output selectors are not supported with --stream.",
        ),
    ],
)
def test_app_exec_rejects_invalid_flag_combinations_before_runtime_lookup(
    project_dir,
    background: bool,
    stream: bool,
    output_selector: OutputSelector | None,
    expected_message: str,
) -> None:
    runtime = Mock(spec=KernelRuntime)
    executions = Mock(spec=ExecutionService)
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.exec(
        ExecRequest(
            project_root=project_dir,
            code="1 + 1",
            invocation=ExecInvocationPolicy(
                background=background,
                stream=stream,
                output_selector=output_selector,
            ),
        )
    )

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "INVALID_INPUT"
    assert response.error.message == expected_message
    runtime.resolve_session_id.assert_not_called()
    executions.execute_code.assert_not_called()
    executions.start_background_code.assert_not_called()


def test_app_exec_success_routes_through_resolved_session(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.execute_code.return_value = ManagedExecution(
        record=ExecutionRecord(
            execution_id="run-123",
            ts="2026-03-12T00:00:00+00:00",
            session_id="analysis",
            command_type="exec",
            status="ok",
            duration_ms=5,
            code="1 + 1",
            result="2",
        ),
        started_new_session=True,
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.exec(
        ExecRequest(
            project_root=project_dir,
            code="1 + 1",
            timeout_s=7,
            invocation=ExecInvocationPolicy(startup_policy="always"),
        )
    )

    assert response.status == "ok"
    assert response.session_id == "analysis"
    assert response.data["execution_id"] == "run-123"
    assert response.data["result"] == "2"
    assert response.data["ensured_started"] is True
    assert response.data["started_new_session"] is True
    executions.execute_code.assert_called_once()
    _assert_called_with_subset(
        executions.execute_code,
        project_root=project_dir.resolve(),
        session_id="analysis",
        code="1 + 1",
        timeout_s=7,
        ensure_started=True,
    )


def test_app_exec_streaming_failure_returns_top_level_execution_error(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    executions = Mock(spec=ExecutionService)
    sink = DummySink()
    executions.execute_code.return_value = ManagedExecution(
        record=ExecutionRecord(
            execution_id="run-456",
            ts="2026-03-12T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="error",
            duration_ms=5,
            code="1 / 0",
            ename="ZeroDivisionError",
            evalue="division by zero",
            traceback=["tb"],
        )
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.exec(
        ExecRequest(
            project_root=project_dir,
            code="1 / 0",
            invocation=ExecInvocationPolicy(stream=True),
        ),
        event_sink=sink,
    )

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "EXECUTION_ERROR"
    assert response.data["status"] == "error"
    assert response.data["ename"] == "ZeroDivisionError"
    executions.execute_code.assert_called_once()
    _assert_called_with_subset(
        executions.execute_code,
        project_root=project_dir.resolve(),
        session_id="default",
        code="1 / 0",
        timeout_s=30.0,
        ensure_started=True,
        event_sink=sink,
    )


def test_app_exec_background_success_uses_background_service(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.start_background_code.return_value = ManagedExecution(
        record=ExecutionRecord(
            execution_id="run-bg",
            ts="2026-03-12T00:00:00+00:00",
            session_id="analysis",
            command_type="exec",
            status="running",
            duration_ms=0,
            code="long_running()",
        ),
        started_new_session=True,
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.exec(
        ExecRequest(
            project_root=project_dir,
            code="long_running()",
            session_id="analysis",
            invocation=ExecInvocationPolicy(startup_policy="always", background=True),
        )
    )

    assert response.status == "ok"
    assert response.session_id == "analysis"
    assert response.data["execution_id"] == "run-bg"
    assert response.data["status"] == "running"
    assert response.data["background"] is True
    assert response.data["ensured_started"] is True
    assert response.data["started_new_session"] is True
    executions.start_background_code.assert_called_once()
    _assert_called_with_subset(
        executions.start_background_code,
        project_root=project_dir.resolve(),
        session_id="analysis",
        code="long_running()",
        ensure_started=True,
    )
    executions.execute_code.assert_not_called()


def test_app_exec_output_selector_adds_selected_text(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    executions = Mock(spec=ExecutionService)
    executions.execute_code.return_value = ManagedExecution(
        record=ExecutionRecord(
            execution_id="run-select",
            ts="2026-03-12T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="ok",
            duration_ms=5,
            code="print('hello')\n1 + 1",
            stdout="hello\n",
            result="2",
        )
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.exec(
        ExecRequest(
            project_root=project_dir,
            code="print('hello')\n1 + 1",
            invocation=ExecInvocationPolicy(output_selector="result"),
        )
    )

    assert response.status == "ok"
    assert response.data["selected_output"] == "result"
    assert response.data["selected_text"] == "2"
    assert response.data["result"] == "2"


def test_app_exec_maps_ambiguous_session_errors_to_response(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.side_effect = AmbiguousSessionError(["default", "analysis"])
    executions = Mock(spec=ExecutionService)
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.exec(ExecRequest(project_root=project_dir, code="1 + 1"))

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "AMBIGUOUS_SESSION"
    assert response.data["available_sessions"] == ["default", "analysis"]
    assert any("--session" in suggestion for suggestion in response.suggestions)
    executions.execute_code.assert_not_called()
    executions.start_background_code.assert_not_called()


def test_app_exec_uses_strict_implicit_session_resolution(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.execute_code.return_value = ManagedExecution(
        record=ExecutionRecord(
            execution_id="run-123",
            ts="2026-03-12T00:00:00+00:00",
            session_id="analysis",
            command_type="exec",
            status="ok",
            duration_ms=5,
            code="1 + 1",
            result="2",
        )
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.exec(ExecRequest(project_root=project_dir, code="1 + 1"))

    assert response.status == "ok"
    runtime.resolve_session_id.assert_called_once_with(
        project_root=project_dir.resolve(),
        requested_session_id=None,
        policy=SessionResolutionPolicy(
            require_live_session=True,
            error_on_multiple_live_sessions=True,
        ),
    )


def test_app_status_wait_idle_uses_resolved_session_and_wait_path(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    runtime.wait_until_idle.return_value = KernelWaitResult(
        status=KernelStatus(alive=True, pid=123, busy=False),
        waited=True,
        waited_for="idle",
        runtime_state="ready",
        waited_ms=12,
        initial_runtime_state="busy",
    )
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.status(
        StatusRequest(
            project_root=project_dir,
            wait_for="idle",
            timeout_s=5.0,
        )
    )

    assert response.status == "ok"
    assert response.session_id == "analysis"
    assert response.data["alive"] is True
    assert response.data["waited"] is True
    assert response.data["waited_for"] == "idle"
    assert response.data["waited_ms"] == 12
    assert response.data["initial_runtime_state"] == "busy"
    runtime.wait_until_idle.assert_called_once()
    _assert_called_with_subset(
        runtime.wait_until_idle,
        project_root=project_dir.resolve(),
        session_id="analysis",
        timeout_s=5.0,
    )
    runtime.status.assert_not_called()


def test_app_status_projects_runtime_state_for_starting_session(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    runtime.runtime_state.return_value = RuntimeState(
        kind="starting",
        session_id="analysis",
        kernel_status=KernelStatus(alive=False),
        has_connection_file=True,
    )
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.status(StatusRequest(project_root=project_dir))

    assert response.status == "ok"
    assert response.session_id == "analysis"
    assert response.data["alive"] is False
    assert response.data["runtime_state"] == "starting"
    assert response.data["session_exists"] is False
    runtime.runtime_state.assert_called_once()
    runtime.status.assert_not_called()


def test_app_status_projects_busy_lock_metadata(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    runtime.runtime_state.return_value = RuntimeState(
        kind="busy",
        session_id="analysis",
        kernel_status=KernelStatus(alive=True, pid=123, busy=True),
        command_lock=CommandLockInfo(
            pid=987,
            acquired_at="2026-03-19T12:00:00+00:00",
        ),
        has_command_lock=True,
    )
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.status(StatusRequest(project_root=project_dir))

    assert response.status == "ok"
    assert response.data["busy"] is True
    assert response.data["lock_pid"] == 987
    assert response.data["lock_acquired_at"] == "2026-03-19T12:00:00+00:00"
    assert isinstance(response.data["busy_for_ms"], int)


@pytest.mark.parametrize(
    ("method_name", "request_factory"),
    [
        ("vars", lambda project_dir: VarsRequest(project_root=project_dir)),
        ("inspect", lambda project_dir: InspectRequest(project_root=project_dir, name="value")),
        ("reload", lambda project_dir: ReloadRequest(project_root=project_dir)),
    ],
)
def test_app_read_commands_project_starting_state_without_running_helpers(
    project_dir,
    method_name: str,
    request_factory,
) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    runtime.runtime_state.return_value = RuntimeState(
        kind="starting",
        session_id="analysis",
        kernel_status=KernelStatus(alive=False),
        has_connection_file=True,
    )
    ops = Mock()
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService), ops=ops)

    response = getattr(app, method_name)(request_factory(project_dir))

    assert response.status == "error"
    assert response.session_id == "analysis"
    assert response.error is not None
    assert response.error.code == "KERNEL_NOT_READY"
    assert response.data["runtime_state"] == "starting"
    assert response.data["session_exists"] is False
    runtime.runtime_state.assert_called_once_with(
        project_root=project_dir.resolve(),
        session_id="analysis",
    )
    ops.list_vars.assert_not_called()
    ops.inspect_var.assert_not_called()
    ops.reload_module.assert_not_called()


def test_app_wait_uses_runtime_wait_for_usable(project_dir, mocker) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    runtime.wait_for_usable.return_value = mocker.Mock(
        status=KernelStatus(alive=True, pid=123, busy=False),
        waited=True,
        waited_for="idle",
        runtime_state="ready",
    )
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.wait(
        WaitRequest(
            project_root=project_dir,
            timeout_s=5.0,
        )
    )

    assert response.status == "ok"
    assert response.command == "wait"
    assert response.session_id == "analysis"
    assert response.data["alive"] is True
    assert response.data["waited"] is True
    assert response.data["waited_for"] == "idle"
    assert response.data["runtime_state"] == "ready"
    assert response.data["waited_ms"] == 0
    runtime.wait_for_usable.assert_called_once()
    _assert_called_with_subset(
        runtime.wait_for_usable,
        project_root=project_dir.resolve(),
        session_id="analysis",
        timeout_s=5.0,
    )


def test_app_history_rejects_latest_and_last_combination_before_runtime_lookup(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.history(
        HistoryRequest(
            project_root=project_dir,
            latest=True,
            last=2,
        )
    )

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "INVALID_INPUT"
    assert response.error.message == "Use either --latest or --last, not both."
    runtime.resolve_session_id.assert_not_called()
    runtime.select_history.assert_not_called()


def test_app_history_compacts_entries_and_applies_last_selection(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    runtime.select_history.return_value.entries = [
        JournalEntry(
            kind="user_command",
            ts="2026-03-12T00:00:00+00:00",
            session_id="default",
            execution_id=None,
            status="ok",
            duration_ms=2,
            command_type="exec",
            label="raw beta",
            user_visible=True,
            classification="replayable",
            provenance_source="history_store",
            provenance_detail="history_record",
            input="beta = 2\nbeta + 1",
        ),
        JournalEntry(
            kind="user_command",
            ts="2026-03-12T00:00:01+00:00",
            session_id="default",
            execution_id=None,
            status="ok",
            duration_ms=1,
            command_type="vars",
            label="vars",
            user_visible=True,
            classification="inspection",
            provenance_source="history_store",
            provenance_detail="history_record",
        ),
    ]
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.history(
        HistoryRequest(
            project_root=project_dir,
            last=2,
        )
    )

    assert response.status == "ok"
    assert [entry["command_type"] for entry in response.data["entries"]] == ["exec", "vars"]
    assert response.data["entries"][0]["label"] == "exec beta = 2 beta + 1"
    runtime.select_history.assert_called_once()
    query = runtime.select_history.call_args.kwargs["query"]
    assert query.session_id == "default"
    assert query.errors_only is False
    assert query.include_internal is False
    assert query.latest is False
    assert query.last == 2


def test_app_history_reference_uses_selector_query(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    runtime.select_history.return_value.entries = []
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.history(
        HistoryRequest(
            project_root=project_dir,
            reference=parse_history_reference("@latest"),
        )
    )

    assert response.status == "ok"
    query = runtime.select_history.call_args.kwargs["query"]
    assert query.latest is True
    assert query.errors_only is False


def test_app_reset_failure_returns_top_level_execution_error(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.reset_session.return_value = ManagedExecution(
        record=ExecutionRecord(
            execution_id="run-reset",
            ts="2026-03-12T00:00:00+00:00",
            session_id="analysis",
            command_type="reset",
            status="error",
            duration_ms=7,
            ename="RuntimeError",
            evalue="reset failed",
            traceback=["tb"],
        )
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.reset(
        ResetRequest(
            project_root=project_dir,
            timeout_s=9.0,
        )
    )

    assert response.status == "error"
    assert response.session_id == "analysis"
    assert response.error is not None
    assert response.error.code == "EXECUTION_ERROR"
    assert response.data["status"] == "error"
    assert response.data["execution_id"] == "run-reset"
    executions.reset_session.assert_called_once()
    _assert_called_with_subset(
        executions.reset_session,
        project_root=project_dir.resolve(),
        session_id="analysis",
        timeout_s=9.0,
    )


def test_app_runs_list_compacts_runs_and_applies_last_selection(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    executions = Mock(spec=ExecutionService)
    executions.list_runs.return_value = [
        {
            "execution_id": "run-1",
            "ts": "2026-03-12T00:00:00+00:00",
            "session_id": "default",
            "command_type": "exec",
            "status": "ok",
            "duration_ms": 5,
            "result": "41",
            "stdout": "",
            "ename": None,
        },
        {
            "execution_id": "run-2",
            "ts": "2026-03-12T00:01:00+00:00",
            "session_id": "analysis",
            "command_type": "exec",
            "status": "error",
            "duration_ms": 6,
            "terminal_reason": "cancelled",
            "cancel_requested": True,
            "result": "",
            "stdout": "nope",
            "ename": "ValueError",
        },
    ]
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.runs_list(
        RunsListRequest(
            project_root=project_dir,
            session_id="analysis",
            errors=True,
            last=1,
        )
    )

    assert response.status == "ok"
    assert response.data["runs"] == [
        {
            "execution_id": "run-2",
            "ts": "2026-03-12T00:01:00+00:00",
            "session_id": "analysis",
            "command_type": "exec",
            "status": "error",
            "duration_ms": 6,
            "terminal_reason": "cancelled",
            "cancel_requested": True,
            "stdout_preview": "nope",
            "error_type": "ValueError",
        }
    ]
    executions.list_runs.assert_called_once()
    _assert_called_with_subset(
        executions.list_runs,
        project_root=project_dir.resolve(),
        session_id="analysis",
        errors_only=True,
    )


def test_app_runs_follow_uses_run_session_id_in_response(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    executions = Mock(spec=ExecutionService)
    sink = DummySink()
    executions.follow_run.return_value = {
        "execution_id": "run-1",
        "session_id": "analysis",
        "status": "ok",
        "result": "2",
    }
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.runs_follow(
        RunsFollowRequest(
            project_root=project_dir,
            run_reference=parse_run_reference("run-1"),
            timeout_s=4.0,
        ),
        event_sink=sink,
    )

    assert response.status == "ok"
    assert response.session_id == "analysis"
    assert response.data["run"]["execution_id"] == "run-1"
    executions.follow_run.assert_called_once()
    _assert_called_with_subset(
        executions.follow_run,
        project_root=project_dir.resolve(),
        execution_id="run-1",
        timeout_s=4.0,
        event_sink=sink,
    )


def test_app_runs_show_resolves_latest_selector_before_lookup(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    executions = Mock(spec=ExecutionService)
    executions.list_runs.return_value = [
        {"execution_id": "run-1", "ts": "2026-03-10T00:00:00+00:00"},
        {"execution_id": "run-2", "ts": "2026-03-11T00:00:00+00:00"},
    ]
    executions.get_run.return_value = {
        "execution_id": "run-2",
        "session_id": "default",
        "status": "ok",
        "result": "2",
    }
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.runs_show(
        RunLookupRequest(
            project_root=project_dir,
            run_reference=parse_run_reference("@latest"),
        )
    )

    assert response.status == "ok"
    assert response.data["run"]["execution_id"] == "run-2"
    executions.list_runs.assert_called_once_with(
        project_root=project_dir.resolve(),
        session_id=None,
    )
    executions.get_run.assert_called_once_with(
        project_root=project_dir.resolve(),
        execution_id="run-2",
    )


@pytest.mark.parametrize(
    ("command_name", "request_factory"),
    [
        (
            "show",
            lambda project_dir: RunLookupRequest(
                project_root=project_dir,
                run_reference=parse_run_reference("run-1"),
            ),
        ),
        (
            "wait",
            lambda project_dir: RunsWaitRequest(
                project_root=project_dir,
                run_reference=parse_run_reference("run-1"),
                timeout_s=4.0,
            ),
        ),
        (
            "follow",
            lambda project_dir: RunsFollowRequest(
                project_root=project_dir,
                run_reference=parse_run_reference("run-1"),
                timeout_s=4.0,
            ),
        ),
    ],
)
def test_app_run_lookup_commands_hide_internal_outputs_from_response(
    project_dir,
    command_name: str,
    request_factory,
) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    executions = Mock(spec=ExecutionService)
    sink = DummySink()
    run_payload = {
        "execution_id": "run-1",
        "session_id": "analysis",
        "status": "error",
        "result": "2",
        "terminal_reason": "cancelled",
        "cancel_requested": True,
        "recorded_ename": "KeyboardInterrupt",
        "outputs": [{"kind": "result", "text": "2", "mime": {"text/plain": "2"}}],
    }
    executions.get_run.return_value = dict(run_payload)
    executions.wait_for_run.return_value = dict(run_payload)
    executions.follow_run.return_value = dict(run_payload)
    app = AgentNBApp(runtime=runtime, executions=executions)

    if command_name == "show":
        response = app.runs_show(request_factory(project_dir))
        executions.get_run.assert_called_once_with(
            project_root=project_dir.resolve(),
            execution_id="run-1",
        )
    elif command_name == "wait":
        response = app.runs_wait(request_factory(project_dir))
        executions.wait_for_run.assert_called_once_with(
            project_root=project_dir.resolve(),
            execution_id="run-1",
            timeout_s=4.0,
        )
    else:
        response = app.runs_follow(request_factory(project_dir), event_sink=sink)
        executions.follow_run.assert_called_once_with(
            project_root=project_dir.resolve(),
            execution_id="run-1",
            timeout_s=4.0,
            event_sink=sink,
            skip_history=False,
        )

    assert response.status == "ok"
    expected_session_id = "analysis" if command_name == "follow" else "default"
    assert response.session_id == expected_session_id
    assert response.data["run"]["execution_id"] == "run-1"
    assert "outputs" not in response.data["run"]
    assert response.data["run"]["terminal_reason"] == "cancelled"
    assert response.data["run"]["cancel_requested"] is True
    assert response.data["run"]["recorded_ename"] == "KeyboardInterrupt"


def test_app_sessions_list_routes_through_handle_command(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    runtime.list_sessions.return_value = [{"session_id": "default"}]
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.sessions_list(SessionsListRequest(project_root=project_dir))

    assert response.status == "ok"
    assert response.data["sessions"] == [{"session_id": "default"}]
    runtime.resolve_session_id.assert_called_once_with(
        project_root=project_dir.resolve(),
        requested_session_id=None,
        policy=SessionResolutionPolicy(require_live_session=False),
    )
    runtime.list_sessions.assert_called_once_with(project_root=project_dir.resolve())


def test_app_sessions_delete_routes_named_session_through_handle_command(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    runtime.delete_session.return_value = {
        "deleted": True,
        "session_id": "analysis",
        "stopped_running_kernel": False,
    }
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.sessions_delete(
        SessionsDeleteRequest(
            project_root=project_dir,
            session_name="analysis",
        )
    )

    assert response.status == "ok"
    assert response.session_id == "analysis"
    assert response.data["deleted"] is True
    runtime.resolve_session_id.assert_called_once_with(
        project_root=project_dir.resolve(),
        requested_session_id="analysis",
        policy=SessionResolutionPolicy(require_live_session=False),
    )
    runtime.delete_session.assert_called_once_with(
        project_root=project_dir.resolve(),
        session_id="analysis",
    )


def test_app_status_remembers_explicit_session_selection(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    runtime.current_session_id.return_value = "default"
    runtime.runtime_state.return_value = RuntimeState(
        kind="ready",
        session_id="analysis",
        kernel_status=KernelStatus(alive=True, pid=123, busy=False),
    )
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.status(
        StatusRequest(
            project_root=project_dir,
            session_id="analysis",
        )
    )

    assert response.status == "ok"
    runtime.remember_current_session.assert_called_once_with(
        project_root=project_dir.resolve(),
        session_id="analysis",
    )
    assert response.data["switched_session"] == "analysis"


def test_app_vars_surfaces_helper_access_metadata(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    runtime.current_session_id.return_value = "analysis"
    ops = Mock()
    ops.list_vars_result.return_value = KernelHelperResult(
        execution=Mock(),
        payload=[{"name": "value", "type": "int", "repr": "1"}],
        wait_result=KernelWaitResult(
            status=KernelStatus(alive=True, pid=123, busy=False),
            waited=True,
            waited_for="idle",
            runtime_state="ready",
            waited_ms=25,
            initial_runtime_state="busy",
        ),
        started_new_session=True,
    )
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService), ops=ops)

    response = app.vars(VarsRequest(project_root=project_dir))

    assert response.status == "ok"
    assert response.data["started_new_session"] is True
    assert response.data["waited"] is True
    assert response.data["waited_for"] == "idle"
    assert response.data["waited_ms"] == 25
    assert response.data["initial_runtime_state"] == "busy"


def test_app_status_remembers_implicit_session_selection(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    runtime.current_session_id.return_value = "default"
    runtime.runtime_state.return_value = RuntimeState(
        kind="ready",
        session_id="analysis",
        kernel_status=KernelStatus(alive=True, pid=123, busy=False),
    )
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.status(StatusRequest(project_root=project_dir))

    assert response.status == "ok"
    runtime.remember_current_session.assert_called_once_with(
        project_root=project_dir.resolve(),
        session_id="analysis",
    )
    assert response.data["switched_session"] == "analysis"


def test_app_runs_show_does_not_remember_session_preference(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    runtime.current_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.get_run.return_value = {
        "execution_id": "run-1",
        "session_id": "other",
        "status": "ok",
    }
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.runs_show(
        RunLookupRequest(project_root=project_dir, run_reference=parse_run_reference("run-1"))
    )

    assert response.status == "ok"
    runtime.remember_current_session.assert_not_called()
    assert "switched_session" not in response.data
