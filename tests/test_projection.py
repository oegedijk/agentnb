from __future__ import annotations

from agentnb.projection import ResponseProjector
from tests.helpers import build_error_response, build_success_response


def test_response_projector_full_json_matches_stable_contract() -> None:
    response = build_success_response(
        command="status",
        data={"alive": True, "pid": 123},
        suggestions=["Run `agentnb exec --json`."],
    )

    projected = ResponseProjector().project(response, profile="full-json")

    assert projected == response.to_dict()


def test_response_projector_agent_uses_compact_success_envelope() -> None:
    response = build_success_response(
        command="status",
        data={"alive": True, "pid": 123, "busy": False},
        suggestions=["This should not appear in the agent payload."],
    )

    projected = ResponseProjector().project(response, profile="agent")

    assert projected == {
        "ok": True,
        "command": "status",
        "session_id": "default",
        "data": {"alive": True, "pid": 123, "busy": False},
    }


def test_response_projector_agent_compacts_wait_like_status() -> None:
    response = build_success_response(
        command="wait",
        project="/tmp/project",
        session_id="default",
        data={
            "alive": True,
            "pid": 123,
            "busy": False,
            "waited": True,
            "waited_for": "idle",
            "waited_ms": 25,
            "initial_runtime_state": "busy",
        },
    )

    projected = ResponseProjector().project(response, profile="agent")

    assert projected == {
        "ok": True,
        "command": "wait",
        "session_id": "default",
        "data": {
            "alive": True,
            "pid": 123,
            "busy": False,
            "waited": True,
            "waited_for": "idle",
            "waited_ms": 25,
            "initial_runtime_state": "busy",
        },
    }


def test_response_projector_agent_compacts_error_shape() -> None:
    response = build_error_response(
        command="exec",
        code="EXECUTION_ERROR",
        message="Execution failed.",
        ename="ZeroDivisionError",
        evalue="division by zero",
        traceback=["Traceback...", "ZeroDivisionError: division by zero"],
        data={"status": "error", "execution_id": "run-1"},
        suggestions=["This should not appear in the agent payload."],
    )

    projected = ResponseProjector().project(response, profile="agent")

    assert projected == {
        "ok": False,
        "command": "exec",
        "session_id": "default",
        "data": {"status": "error", "execution_id": "run-1"},
        "error": {
            "code": "EXECUTION_ERROR",
            "message": "Execution failed.",
            "ename": "ZeroDivisionError",
            "evalue": "division by zero",
            "traceback": ["Traceback...", "ZeroDivisionError: division by zero"],
        },
    }


def test_response_projector_uses_normalized_error_shape_for_full_json_and_agent() -> None:
    response = build_error_response(
        command="exec",
        code="EXECUTION_ERROR",
        message="Execution failed.",
        ename="ZeroDivisionError",
        evalue="division by zero",
        traceback=[
            "\x1b[31mTraceback (most recent call last):\x1b[0m",
            '  File "main.py", line 1, in <module>',
            "line 3",
            "line 4",
            "line 5",
            "ZeroDivisionError: division by zero",
        ],
        data={"status": "error"},
    )

    full_payload = ResponseProjector().project(response, profile="full-json")
    agent_payload = ResponseProjector().project(response, profile="agent")

    expected_traceback = [
        "Traceback (most recent call last):",
        '  File "main.py", line 1, in <module>',
        "...",
        "line 4",
        "line 5",
        "ZeroDivisionError: division by zero",
    ]
    assert full_payload["error"]["traceback"] == expected_traceback
    assert agent_payload["error"]["traceback"] == expected_traceback


def test_response_projector_agent_keeps_suggestion_actions() -> None:
    response = build_error_response(
        command="exec",
        code="AMBIGUOUS_SESSION",
        message="Multiple live sessions exist.",
        suggestion_actions=[
            {
                "kind": "command",
                "label": "List sessions",
                "command": "agentnb",
                "args": ["sessions", "list", "--json"],
            }
        ],
    )

    projected = ResponseProjector().project(response, profile="agent")

    assert projected["suggestion_actions"] == [
        {
            "kind": "command",
            "label": "List sessions",
            "command": "agentnb",
            "args": ["sessions", "list", "--json"],
        }
    ]


def test_response_projector_agent_keeps_busy_metadata_for_exec_errors() -> None:
    response = build_error_response(
        command="exec",
        code="SESSION_BUSY",
        message="Session is busy.",
        data={
            "wait_behavior": "immediate",
            "waited_ms": 0,
            "lock_pid": 321,
            "lock_acquired_at": "2026-03-19T12:00:00+00:00",
            "busy_for_ms": 1500,
            "active_execution_id": "run-7",
        },
    )

    projected = ResponseProjector().project(response, profile="agent")

    assert projected == {
        "ok": False,
        "command": "exec",
        "session_id": "default",
        "data": {
            "wait_behavior": "immediate",
            "waited_ms": 0,
            "lock_pid": 321,
            "lock_acquired_at": "2026-03-19T12:00:00+00:00",
            "busy_for_ms": 1500,
            "active_execution_id": "run-7",
        },
        "error": {
            "code": "SESSION_BUSY",
            "message": "Session is busy.",
        },
    }


def test_response_projector_agent_compacts_exec_success_to_next_step_fields() -> None:
    response = build_success_response(
        command="exec",
        project="/tmp/project",
        session_id="default",
        data={
            "status": "ok",
            "execution_id": "run-1",
            "result": "42",
            "stdout": "",
            "stderr": "",
            "duration_ms": 12,
            "ensured_started": True,
            "started_new_session": False,
            "events": ["ignored"],
        },
    )

    projected = ResponseProjector().project(response, profile="agent")

    assert projected == {
        "ok": True,
        "command": "exec",
        "session_id": "default",
        "data": {
            "status": "ok",
            "execution_id": "run-1",
            "duration_ms": 12,
            "result": "42",
            "result_json": 42,
            "ensured_started": True,
            "started_new_session": False,
        },
    }


def test_response_projector_agent_includes_run_status_alias() -> None:
    response = build_success_response(
        command="runs-show",
        project="/tmp/project",
        session_id="default",
        data={
            "status": "running",
            "run": {
                "execution_id": "run-1",
                "session_id": "default",
                "command_type": "exec",
                "status": "running",
                "duration_ms": 12,
            },
        },
    )

    projected = ResponseProjector().project(response, profile="agent")

    assert projected["data"]["status"] == "running"
    assert projected["data"]["run"]["status"] == "running"


def test_response_projector_agent_keeps_structured_exec_result_preview() -> None:
    response = build_success_response(
        command="exec",
        project="/tmp/project",
        session_id="default",
        data={
            "status": "ok",
            "execution_id": "run-1",
            "duration_ms": 12,
            "result": "large dataframe repr",
            "result_preview": {
                "kind": "dataframe-like",
                "shape": [200, 1],
                "columns": ["i"],
            },
        },
    )

    projected = ResponseProjector().project(response, profile="agent")

    assert projected == {
        "ok": True,
        "command": "exec",
        "session_id": "default",
        "data": {
            "status": "ok",
            "execution_id": "run-1",
            "duration_ms": 12,
            "result": "large dataframe repr",
            "result_preview": {
                "kind": "dataframe-like",
                "shape": [200, 1],
                "columns": ["i"],
                "column_count": 1,
                "head": [{"i": 0}],
            },
        },
    }


def test_response_projector_agent_preserves_precomputed_exec_result_preview() -> None:
    response = build_success_response(
        command="exec",
        data={
            "status": "ok",
            "execution_id": "run-1",
            "duration_ms": 12,
            "result": "[{'id': 1, 'name': 'alpha'}, {'id': 2, 'name': 'beta'}]",
            "result_preview": {
                "kind": "sequence-like",
                "length": 2,
                "item_type": "dict",
                "sample_keys": ["id", "name"],
                "sample": [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}],
            },
        },
    )

    projected = ResponseProjector().project(response, profile="agent")

    assert projected["data"]["result_preview"] == {
        "kind": "sequence-like",
        "length": 2,
        "item_type": "dict",
        "sample_keys": ["id", "name"],
        "sample": [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}],
    }


def test_response_projector_agent_keeps_exec_truncation_flags() -> None:
    response = build_success_response(
        command="exec",
        project="/tmp/project",
        session_id="default",
        data={
            "status": "ok",
            "execution_id": "run-1",
            "stdout_truncated": True,
            "stderr_truncated": False,
            "result_truncated": True,
        },
    )

    projected = ResponseProjector().project(response, profile="agent")

    assert projected["data"]["stdout_truncated"] is True
    assert "stderr_truncated" not in projected["data"]
    assert projected["data"]["result_truncated"] is True


def test_response_projector_agent_keeps_runs_follow_observation_metadata() -> None:
    response = build_success_response(
        command="runs-follow",
        project="/tmp/project",
        session_id="default",
        data={
            "status": "running",
            "completion_reason": "window_elapsed",
            "replayed_event_count": 1,
            "emitted_event_count": 2,
            "run": {
                "execution_id": "run-1",
                "session_id": "default",
                "command_type": "exec",
                "status": "running",
                "duration_ms": 12,
            },
        },
    )

    projected = ResponseProjector().project(response, profile="agent")

    assert projected["data"] == {
        "status": "running",
        "completion_reason": "window_elapsed",
        "replayed_event_count": 1,
        "emitted_event_count": 2,
        "run": {
            "execution_id": "run-1",
            "ts": None,
            "session_id": "default",
            "command_type": "exec",
            "status": "running",
            "duration_ms": 12,
        },
    }


def test_response_projector_agent_compacts_runs_cancel_response() -> None:
    response = build_success_response(
        command="runs-cancel",
        project="/tmp/project",
        session_id="default",
        data={
            "execution_id": "run-1",
            "session_id": "analysis",
            "cancel_requested": True,
            "status": "error",
            "run_status": "running",
            "session_outcome": "preserved",
            "extra": "ignored",
        },
    )

    projected = ResponseProjector().project(response, profile="agent")

    assert projected == {
        "ok": True,
        "command": "runs-cancel",
        "session_id": "default",
        "data": {
            "execution_id": "run-1",
            "session_id": "analysis",
            "cancel_requested": True,
            "status": "error",
            "run_status": "running",
            "session_outcome": "preserved",
        },
    }
