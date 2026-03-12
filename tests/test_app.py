from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentnb.app import AgentNBApp, ExecRequest
from agentnb.errors import AmbiguousSessionError
from agentnb.execution import ExecutionRecord, ExecutionService, ManagedExecution
from agentnb.runtime import KernelRuntime


class DummySink:
    def started(self, *, execution_id: str, session_id: str) -> None:
        del execution_id, session_id

    def accept(self, event: object) -> None:
        del event


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
