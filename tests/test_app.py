from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentnb.app import (
    AgentNBApp,
    ExecRequest,
    HistoryRequest,
    ResetRequest,
    RunsFollowRequest,
    RunsListRequest,
    SessionsDeleteRequest,
    SessionsListRequest,
    StatusRequest,
)
from agentnb.errors import AmbiguousSessionError
from agentnb.execution import ExecutionRecord, ExecutionService, ManagedExecution
from agentnb.runtime import KernelRuntime


class DummySink:
    def started(self, *, execution_id: str, session_id: str) -> None:
        del execution_id, session_id

    def accept(self, event: object) -> None:
        del event


class DummyStatus:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, object]:
        return dict(self._payload)


@pytest.mark.parametrize(
    ("request_kwargs", "expected_message"),
    [
        (
            {"background": True, "output_selector": "stdout"},
            "Output selectors are not supported with --background.",
        ),
        (
            {"stream": True, "background": True},
            "--stream and --background cannot be used together.",
        ),
        (
            {"stream": True, "output_selector": "result"},
            "Output selectors are not supported with --stream.",
        ),
    ],
)
def test_app_exec_rejects_invalid_flag_combinations_before_runtime_lookup(
    project_dir,
    request_kwargs: dict[str, object],
    expected_message: str,
) -> None:
    runtime = Mock(spec=KernelRuntime)
    executions = Mock(spec=ExecutionService)
    app = AgentNBApp(runtime=runtime, executions=executions)

    response = app.exec(
        ExecRequest(
            project_root=project_dir,
            code="1 + 1",
            **request_kwargs,
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
            ensure_started=True,
        )
    )

    assert response.status == "ok"
    assert response.session_id == "analysis"
    assert response.data["execution_id"] == "run-123"
    assert response.data["result"] == "2"
    assert response.data["ensured_started"] is True
    assert response.data["started_new_session"] is True
    executions.execute_code.assert_called_once_with(
        project_root=project_dir.resolve(),
        session_id="analysis",
        code="1 + 1",
        timeout_s=7,
        ensure_started=True,
        event_sink=None,
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
            stream=True,
        ),
        event_sink=sink,
    )

    assert response.status == "error"
    assert response.error is not None
    assert response.error.code == "EXECUTION_ERROR"
    assert response.data["status"] == "error"
    assert response.data["ename"] == "ZeroDivisionError"
    executions.execute_code.assert_called_once_with(
        project_root=project_dir.resolve(),
        session_id="default",
        code="1 / 0",
        timeout_s=30.0,
        ensure_started=False,
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
            ensure_started=True,
            background=True,
        )
    )

    assert response.status == "ok"
    assert response.session_id == "analysis"
    assert response.data["execution_id"] == "run-bg"
    assert response.data["status"] == "running"
    assert response.data["background"] is True
    assert response.data["ensured_started"] is True
    assert response.data["started_new_session"] is True
    executions.start_background_code.assert_called_once_with(
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
            output_selector="result",
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


def test_app_status_wait_idle_uses_resolved_session_and_wait_path(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "analysis"
    runtime.wait_for_idle.return_value = DummyStatus({"alive": True, "pid": 123, "busy": False})
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
    runtime.wait_for_idle.assert_called_once_with(
        project_root=project_dir.resolve(),
        session_id="analysis",
        timeout_s=5.0,
    )
    runtime.status.assert_not_called()


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
    runtime.history.assert_not_called()


def test_app_history_compacts_entries_and_applies_last_selection(project_dir) -> None:
    runtime = Mock(spec=KernelRuntime)
    runtime.resolve_session_id.return_value = "default"
    runtime.history.return_value = [
        {
            "kind": "user_command",
            "command_type": "exec",
            "status": "ok",
            "duration_ms": 1,
            "user_visible": True,
            "input": "alpha = 1",
            "label": "raw alpha",
        },
        {
            "kind": "user_command",
            "command_type": "exec",
            "status": "ok",
            "duration_ms": 2,
            "user_visible": True,
            "input": "beta = 2\nbeta + 1",
            "label": "raw beta",
        },
        {
            "kind": "user_command",
            "command_type": "vars",
            "status": "ok",
            "duration_ms": 1,
            "user_visible": True,
            "label": "vars",
        },
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
    runtime.history.assert_called_once_with(
        project_root=project_dir.resolve(),
        session_id="default",
        errors_only=False,
        include_internal=False,
    )


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
    executions.reset_session.assert_called_once_with(
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
            "stdout_preview": "nope",
            "error_type": "ValueError",
        }
    ]
    executions.list_runs.assert_called_once_with(
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
            execution_id="run-1",
            timeout_s=4.0,
        ),
        event_sink=sink,
    )

    assert response.status == "ok"
    assert response.session_id == "analysis"
    assert response.data["run"]["execution_id"] == "run-1"
    executions.follow_run.assert_called_once_with(
        project_root=project_dir.resolve(),
        execution_id="run-1",
        timeout_s=4.0,
        event_sink=sink,
    )


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
        require_live_session=False,
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
        require_live_session=False,
    )
    runtime.delete_session.assert_called_once_with(
        project_root=project_dir.resolve(),
        session_id="analysis",
    )
