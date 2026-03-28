from __future__ import annotations

from typing import cast
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
from agentnb.command_data import RunLookupCommandData, RunsListCommandData
from agentnb.contracts import HelperAccessMetadata, KernelStatus
from agentnb.errors import AmbiguousSessionError
from agentnb.execution import (
    ExecutionCommandRequest,
    ExecutionRecord,
    ExecutionService,
    ManagedExecution,
    RunListRequest,
    RunRetrievalOutcome,
    RunRetrievalRequest,
    SessionAccessOutcome,
    StartOutcome,
)
from agentnb.execution_invocation import ExecInvocationPolicy, OutputSelector
from agentnb.introspection import KernelHelperResult, KernelIntrospection
from agentnb.journal import JournalEntry
from agentnb.runtime import KernelRuntime, RuntimeState
from agentnb.selectors import parse_history_reference, parse_run_reference
from agentnb.state import CommandLockInfo
from tests.helpers import build_execution_record, build_run_snapshot


class DummySink:
    def started(self, *, execution_id: str, session_id: str) -> None:
        del execution_id, session_id

    def accept(self, event: object) -> None:
        del event


def _assert_called_with_subset(mock_obj, **expected: object) -> None:
    kwargs = mock_obj.call_args.kwargs
    request = kwargs.get("request")
    for key, value in expected.items():
        if key in kwargs:
            assert kwargs[key] == value
            continue
        assert getattr(request, key) == value


def _single_request(mock_obj):
    assert mock_obj.call_count == 1
    if mock_obj.call_args.args:
        return mock_obj.call_args.args[0]
    if "request" in mock_obj.call_args.kwargs:
        return mock_obj.call_args.kwargs["request"]
    return next(iter(mock_obj.call_args.kwargs.values()))


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


def test_app_exec_success_routes_through_resolved_session(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.execute.return_value = ManagedExecution(
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
        start_outcome=StartOutcome(started_new_session=True, initial_runtime_state="missing"),
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
    request = _single_request(executions.execute)
    assert isinstance(request, ExecutionCommandRequest)
    assert request.project_root == project_dir.resolve()
    assert request.session_id == "analysis"
    assert request.command_type == "exec"
    assert request.code == "1 + 1"
    assert request.timeout_s == 7
    assert request.ensure_started is True
    assert request.mode == "foreground"


def test_app_exec_streaming_failure_returns_top_level_execution_error(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    executions = Mock(spec=ExecutionService)
    sink = DummySink()
    executions.execute.return_value = ManagedExecution(
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
    request = _single_request(executions.execute)
    assert isinstance(request, ExecutionCommandRequest)
    assert request.project_root == project_dir.resolve()
    assert request.session_id == "default"
    assert request.code == "1 / 0"
    assert request.timeout_s == 30.0
    assert request.ensure_started is True
    assert request.event_sink is sink


def test_app_exec_background_success_uses_background_service(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.execute.return_value = ManagedExecution(
        record=ExecutionRecord(
            execution_id="run-bg",
            ts="2026-03-12T00:00:00+00:00",
            session_id="analysis",
            command_type="exec",
            status="running",
            duration_ms=0,
            code="long_running()",
        ),
        start_outcome=StartOutcome(started_new_session=True, initial_runtime_state="missing"),
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
    request = _single_request(executions.execute)
    assert isinstance(request, ExecutionCommandRequest)
    assert request.project_root == project_dir.resolve()
    assert request.session_id == "analysis"
    assert request.code == "long_running()"
    assert request.ensure_started is True
    assert request.mode == "background"


def test_app_exec_output_selector_adds_selected_text(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    executions = Mock(spec=ExecutionService)
    executions.execute.return_value = ManagedExecution(
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


def test_app_exec_uses_strict_implicit_session_resolution(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.execute.return_value = ManagedExecution(
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
    _assert_called_with_subset(
        runtime.resolve_session_id,
        project_root=project_dir.resolve(),
        requested_session_id=None,
        require_live_session=True,
    )


def test_app_status_wait_idle_uses_resolved_session_and_wait_path(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.wait_for_session_access.return_value = SessionAccessOutcome(
        status=KernelStatus(alive=True, pid=123, busy=False),
        waited=True,
        waited_for="idle",
        runtime_state="ready",
        waited_ms=12,
        initial_runtime_state="busy",
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

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
    _assert_called_with_subset(
        executions.wait_for_session_access,
        project_root=project_dir.resolve(),
        session_id="analysis",
        target="idle",
    )
    wait_kwargs = executions.wait_for_session_access.call_args.kwargs
    assert wait_kwargs["project_root"] == project_dir.resolve()
    assert wait_kwargs["session_id"] == "analysis"
    assert 0 < wait_kwargs["timeout_s"] <= 5.0


def test_app_status_wait_idle_waits_for_active_run_before_returning(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.wait_for_session_access.return_value = SessionAccessOutcome(
        status=KernelStatus(alive=True, pid=123, busy=False),
        waited=True,
        waited_for="idle",
        runtime_state="ready",
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.status(
        StatusRequest(
            project_root=project_dir,
            wait_for="idle",
            timeout_s=5.0,
        )
    )

    assert response.status == "ok"
    assert response.data["waited"] is True
    assert response.data["waited_for"] == "idle"
    _assert_called_with_subset(
        executions.wait_for_session_access,
        project_root=project_dir.resolve(),
        session_id="analysis",
        target="idle",
    )


def test_app_status_wait_idle_marks_waited_when_only_run_completion_blocked(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.wait_for_session_access.return_value = SessionAccessOutcome(
        status=KernelStatus(alive=True, pid=123, busy=False),
        waited=True,
        waited_for="idle",
        runtime_state="ready",
        initial_runtime_state="ready",
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.status(
        StatusRequest(
            project_root=project_dir,
            wait_for="idle",
            timeout_s=5.0,
        )
    )

    assert response.status == "ok"
    assert response.data["waited"] is True
    assert response.data["waited_for"] == "idle"
    assert response.data["initial_runtime_state"] == "ready"


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
    ("method_name", "result_method_name", "request_factory"),
    [
        ("vars", "list_vars", lambda project_dir: VarsRequest(project_root=project_dir)),
        (
            "inspect",
            "inspect_var",
            lambda project_dir: InspectRequest(project_root=project_dir, name="value"),
        ),
        (
            "reload",
            "reload_module",
            lambda project_dir: ReloadRequest(project_root=project_dir),
        ),
    ],
)
def test_app_read_commands_project_starting_state_without_running_helpers(
    project_dir,
    method_name: str,
    result_method_name: str,
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
    introspection = Mock(spec=KernelIntrospection)
    app = AgentNBApp(
        runtime=runtime,
        executions=Mock(spec=ExecutionService),
        introspection=introspection,
    )

    response = getattr(app, method_name)(request_factory(project_dir))

    assert response.status == "error"
    assert response.session_id == "analysis"
    assert response.error is not None
    assert response.error.code == "KERNEL_NOT_READY"
    assert response.data["runtime_state"] == "starting"
    assert response.data["session_exists"] is False
    _assert_called_with_subset(
        runtime.runtime_state,
        project_root=project_dir.resolve(),
        session_id="analysis",
    )
    getattr(introspection, result_method_name).assert_not_called()


def test_app_wait_uses_execution_service_wait_for_usable(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.wait_for_session_access.return_value = SessionAccessOutcome(
        status=KernelStatus(alive=True, pid=123, busy=False),
        waited=True,
        waited_for="idle",
        runtime_state="ready",
        waited_ms=0,
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

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
    _assert_called_with_subset(
        executions.wait_for_session_access,
        project_root=project_dir.resolve(),
        session_id="analysis",
        timeout_s=5.0,
        target="usable",
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
            provenance_detail="user_command",
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
            provenance_detail="user_command",
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
    assert response.data["entries"][0]["label"] == "exec beta = 2 | beta + 1"
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
    executions.execute.return_value = ManagedExecution(
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
    request = _single_request(executions.execute)
    assert isinstance(request, ExecutionCommandRequest)
    assert request.project_root == project_dir.resolve()
    assert request.session_id == "analysis"
    assert request.command_type == "reset"
    assert request.timeout_s == 9.0


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
    assert isinstance(response.command_data, RunsListCommandData)
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
    executions.retrieve_run.return_value = RunRetrievalOutcome(
        run=build_execution_record(
            execution_id="run-1",
            session_id="analysis",
            status="ok",
            result="2",
        ),
        completion_reason="terminal",
    )
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
    assert "result" not in response.data["run"]
    request = _single_request(executions.retrieve_run)
    assert isinstance(request, RunRetrievalRequest)
    assert request.project_root == project_dir.resolve()
    assert request.execution_id == "run-1"
    assert request.mode == "follow"
    assert request.timeout_s == 4.0
    assert request.event_sink is sink


def test_app_run_lookup_sanitizes_tracebacks_and_hides_follow_output_fields(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    executions = Mock(spec=ExecutionService)
    sink = DummySink()
    raw_run = build_run_snapshot(
        session_id="analysis",
        status="error",
        traceback=["\x1b[31mTraceback line\x1b[0m"],
        recorded_traceback=["\x1b[31mKeyboardInterrupt\x1b[0m"],
        stdout="tick\n",
        stderr="warn\n",
        result="2",
        events=[
            {
                "kind": "error",
                "content": "boom",
                "metadata": {"traceback": ["\x1b[31mframe 1\x1b[0m"]},
            }
        ],
        outputs=[{"kind": "result", "text": "2", "mime": {"text/plain": "2"}}],
    )
    record_run = build_execution_record(
        **{key: value for key, value in dict(raw_run).items() if key != "outputs"}
    )
    executions.retrieve_run.side_effect = [
        RunRetrievalOutcome(run=cast(ExecutionRecord, dict(raw_run))),
        RunRetrievalOutcome(
            run=record_run,
            completion_reason="window_elapsed",
        ),
    ]
    app = AgentNBApp(runtime=runtime, executions=executions)

    show_response = app.runs_show(
        RunLookupRequest(project_root=project_dir, run_reference=parse_run_reference("run-1"))
    )
    follow_response = app.runs_follow(
        RunsFollowRequest(
            project_root=project_dir,
            run_reference=parse_run_reference("run-1"),
            timeout_s=4.0,
        ),
        event_sink=sink,
    )

    assert isinstance(show_response.command_data, RunLookupCommandData)
    assert isinstance(follow_response.command_data, RunLookupCommandData)
    show_run = show_response.data["run"]
    assert show_run["traceback"] == ["Traceback line"]
    assert show_run["recorded_traceback"] == ["KeyboardInterrupt"]
    assert show_run["events"][0]["metadata"]["traceback"] == ["frame 1"]
    assert "outputs" not in show_run

    follow_run = follow_response.data["run"]
    assert follow_run["traceback"] == ["frame 1"]
    assert follow_run["recorded_traceback"] == ["KeyboardInterrupt"]
    for hidden_key in ("stdout", "stderr", "result", "events", "outputs"):
        assert hidden_key not in follow_run


def test_app_runs_show_resolves_latest_selector_before_lookup(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    executions = Mock(spec=ExecutionService)
    executions.list_runs.return_value = [
        {"execution_id": "run-1", "ts": "2026-03-10T00:00:00+00:00"},
        {"execution_id": "run-2", "ts": "2026-03-11T00:00:00+00:00"},
    ]
    executions.retrieve_run.return_value = RunRetrievalOutcome(
        run=build_execution_record(
            execution_id="run-2",
            session_id="default",
            status="ok",
            result="2",
        )
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.runs_show(
        RunLookupRequest(
            project_root=project_dir,
            run_reference=parse_run_reference("@latest"),
        )
    )

    assert response.status == "ok"
    assert response.data["run"]["execution_id"] == "run-2"
    list_request = _single_request(executions.list_runs)
    assert isinstance(list_request, RunListRequest)
    assert list_request.project_root == project_dir.resolve()
    assert list_request.session_id is None
    lookup_request = _single_request(executions.retrieve_run)
    assert isinstance(lookup_request, RunRetrievalRequest)
    assert lookup_request.project_root == project_dir.resolve()
    assert lookup_request.execution_id == "run-2"
    assert lookup_request.mode == "get"


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
    observed_run = build_execution_record(
        execution_id="run-1",
        session_id="analysis",
        status="error",
        result="2",
        terminal_reason="cancelled",
        cancel_requested=True,
        recorded_ename="KeyboardInterrupt",
    )
    executions.retrieve_run.side_effect = [
        RunRetrievalOutcome(run=build_execution_record(**dict(run_payload))),
        RunRetrievalOutcome(run=build_execution_record(**dict(run_payload))),
        RunRetrievalOutcome(run=observed_run, completion_reason="terminal"),
    ]
    app = AgentNBApp(runtime=runtime, executions=executions)

    if command_name == "show":
        response = app.runs_show(request_factory(project_dir))
        request = _single_request(executions.retrieve_run)
        assert isinstance(request, RunRetrievalRequest)
        assert request.project_root == project_dir.resolve()
        assert request.execution_id == "run-1"
        assert request.mode == "get"
    elif command_name == "wait":
        response = app.runs_wait(request_factory(project_dir))
        request = _single_request(executions.retrieve_run)
        assert isinstance(request, RunRetrievalRequest)
        assert request.project_root == project_dir.resolve()
        assert request.execution_id == "run-1"
        assert request.mode == "wait"
        assert request.timeout_s == 4.0
    else:
        response = app.runs_follow(request_factory(project_dir), event_sink=sink)
        request = _single_request(executions.retrieve_run)
        assert isinstance(request, RunRetrievalRequest)
        assert request.project_root == project_dir.resolve()
        assert request.execution_id == "run-1"
        assert request.mode == "follow"
        assert request.timeout_s == 4.0
        assert request.event_sink is sink
        assert request.skip_history is True

    assert response.status == "ok"
    expected_session_id = "analysis" if command_name == "follow" else "default"
    assert response.session_id == expected_session_id
    assert response.data["run"]["execution_id"] == "run-1"
    assert "outputs" not in response.data["run"]
    assert response.data["run"]["terminal_reason"] == "cancelled"
    assert response.data["run"]["cancel_requested"] is True
    assert response.data["run"]["recorded_ename"] == "KeyboardInterrupt"


def test_app_runs_follow_reports_elapsed_window(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    executions = Mock(spec=ExecutionService)
    sink = DummySink()
    executions.retrieve_run.return_value = RunRetrievalOutcome(
        run=build_execution_record(
            execution_id="run-1",
            session_id="analysis",
            status="running",
        ),
        completion_reason="window_elapsed",
    )
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
    assert response.data["completion_reason"] == "window_elapsed"
    assert response.data["status"] == "running"
    assert response.data["run"]["status"] == "running"


def test_app_sessions_list_routes_through_handle_command(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    runtime.list_sessions.return_value = [{"session_id": "default"}]
    runtime.hidden_non_live_session_count.return_value = 2
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.sessions_list(SessionsListRequest(project_root=project_dir))

    assert response.status == "ok"
    assert response.data["sessions"][0]["session_id"] == "default"
    assert response.data["hidden_non_live_count"] == 2
    _assert_called_with_subset(
        runtime.resolve_session_id,
        project_root=project_dir.resolve(),
        requested_session_id=None,
        require_live_session=False,
    )
    _assert_called_with_subset(
        runtime.list_sessions,
        project_root=project_dir.resolve(),
    )
    _assert_called_with_subset(
        runtime.hidden_non_live_session_count,
        project_root=project_dir.resolve(),
    )


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
    _assert_called_with_subset(
        runtime.resolve_session_id,
        project_root=project_dir.resolve(),
        requested_session_id="analysis",
        require_live_session=False,
    )
    _assert_called_with_subset(
        runtime.delete_session,
        project_root=project_dir.resolve(),
        session_id="analysis",
    )


def test_app_status_remembers_explicit_session_selection(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    runtime.current_session_id.return_value = "default"
    runtime.is_live_session.return_value = True
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
    _assert_called_with_subset(
        runtime.remember_current_session,
        project_root=project_dir.resolve(),
        session_id="analysis",
    )
    assert response.data["switched_session"] == "analysis"


def test_app_vars_surfaces_helper_access_metadata(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    runtime.current_session_id.return_value = "analysis"
    introspection = Mock(spec=KernelIntrospection)
    introspection.list_vars.return_value = KernelHelperResult(
        execution=Mock(),
        payload=[{"name": "value", "type": "int", "repr": "1"}],
        access_metadata=HelperAccessMetadata(
            started_new_session=True,
            waited=True,
            waited_for="idle",
            waited_ms=25,
            initial_runtime_state="busy",
        ),
    )
    app = AgentNBApp(
        runtime=runtime,
        executions=Mock(spec=ExecutionService),
        introspection=introspection,
    )

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
    runtime.is_live_session.return_value = False
    runtime.runtime_state.return_value = RuntimeState(
        kind="ready",
        session_id="analysis",
        kernel_status=KernelStatus(alive=True, pid=123, busy=False),
    )
    app = AgentNBApp(runtime=runtime, executions=Mock(spec=ExecutionService))

    response = app.status(StatusRequest(project_root=project_dir))

    assert response.status == "ok"
    runtime.remember_current_session.assert_not_called()
    assert "switched_session" not in response.data


def test_app_runs_show_does_not_remember_session_preference(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    runtime.current_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.retrieve_run.return_value = RunRetrievalOutcome(
        run=build_execution_record(
            execution_id="run-1",
            session_id="other",
            status="ok",
        )
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.runs_show(
        RunLookupRequest(project_root=project_dir, run_reference=parse_run_reference("run-1"))
    )

    assert response.status == "ok"
    runtime.remember_current_session.assert_not_called()
    assert "switched_session" not in response.data


def test_app_runs_show_exposes_top_level_status_alias(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    runtime.current_session_id.return_value = "analysis"
    executions = Mock(spec=ExecutionService)
    executions.retrieve_run.return_value = RunRetrievalOutcome(
        run=build_execution_record(
            execution_id="run-1",
            session_id="other",
            status="running",
        )
    )
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.runs_show(
        RunLookupRequest(project_root=project_dir, run_reference=parse_run_reference("run-1"))
    )

    assert response.status == "ok"
    assert response.data["status"] == "running"
    assert response.data["run"]["status"] == "running"


def test_app_file_exec_without_visible_output_surfaces_namespace_delta(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    runtime.current_session_id.return_value = "default"
    runtime.runtime_state.side_effect = [
        RuntimeState(
            kind="ready",
            session_id="default",
            kernel_status=KernelStatus(alive=True, pid=123, python="/python", busy=False),
        ),
        RuntimeState(
            kind="ready",
            session_id="default",
            kernel_status=KernelStatus(alive=True, pid=123, python="/python", busy=False),
        ),
    ]
    executions = Mock(spec=ExecutionService)
    executions.execute.return_value = ManagedExecution(
        record=ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:00+00:00",
            session_id="default",
            command_type="exec",
            status="ok",
            duration_ms=5,
            code="value = 2",
        ),
        start_outcome=StartOutcome(),
    )
    introspection = Mock(spec=KernelIntrospection)
    introspection.list_vars.side_effect = [
        KernelHelperResult(
            execution=Mock(),
            payload=[{"name": "value", "type": "int", "repr": "1"}],
        ),
        KernelHelperResult(
            execution=Mock(),
            payload=[
                {"name": "value", "type": "int", "repr": "2"},
                {"name": "payload", "type": "dict", "repr": "dict len=1 keys=id"},
            ],
        ),
    ]
    app = AgentNBApp(
        runtime=runtime,
        executions=executions,
        introspection=introspection,
    )

    response = app.exec(
        ExecRequest(
            project_root=project_dir,
            code="value = 2",
            source_kind="file",
            source_path=project_dir / "analysis.py",
        )
    )

    assert response.status == "ok"
    assert response.data["source_kind"] == "file"
    assert response.data["source_path"] == str(project_dir / "analysis.py")
    assert response.data["namespace_delta"] == {
        "entries": [
            {"change": "updated", "name": "value", "type": "int", "repr": "2"},
            {"change": "new", "name": "payload", "type": "dict", "repr": "dict len=1 keys=id"},
        ],
        "new_count": 1,
        "updated_count": 1,
        "truncated": False,
    }
