from __future__ import annotations

from agentnb.command_data import ExecCommandData
from agentnb.contracts import (
    SCHEMA_VERSION,
    ExecutionEvent,
    ExecutionResult,
    error_response,
    success_response,
)
from agentnb.execution_output import OutputItem
from agentnb.runs.store import ExecutionRecord


def test_success_response_to_dict_preserves_schema_and_payload() -> None:
    response = success_response(
        command="status",
        project="/tmp/project",
        session_id="default",
        data={"alive": False},
        suggestions=["Run `agentnb start --json`."],
    )

    payload = response.to_dict()

    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["status"] == "ok"
    assert payload["command"] == "status"
    assert payload["project"] == "/tmp/project"
    assert payload["session_id"] == "default"
    assert payload["data"] == {"alive": False}
    assert payload["suggestions"] == ["Run `agentnb start --json`."]
    assert payload["error"] is None
    assert isinstance(payload["timestamp"], str)


def test_error_response_to_dict_preserves_nested_error_fields() -> None:
    response = error_response(
        command="exec",
        project="/tmp/project",
        session_id="default",
        code="NO_KERNEL",
        message="No kernel running.",
        ename="RuntimeError",
        evalue="missing kernel",
        traceback=["line1"],
        data={"alive": False},
        suggestions=["Run `agentnb start --json`."],
    )

    payload = response.to_dict()

    assert payload["status"] == "error"
    assert payload["data"] == {"alive": False}
    assert payload["suggestions"] == ["Run `agentnb start --json`."]
    assert payload["error"] == {
        "code": "NO_KERNEL",
        "message": "No kernel running.",
        "ename": "RuntimeError",
        "evalue": "missing kernel",
        "traceback": ["line1"],
    }


def test_error_response_preserves_mapping_payload_for_sessions_delete() -> None:
    response = error_response(
        command="sessions-delete",
        project="/tmp/project",
        session_id="default",
        code="SESSION_NOT_FOUND",
        message="Session not found.",
        data={"session_id": "analysis"},
    )

    assert response.data == {"session_id": "analysis"}
    assert response.command_data is None
    assert response.to_dict()["data"] == {"session_id": "analysis"}


def test_success_response_preserves_partial_mapping_payload_for_runs_cancel() -> None:
    response = success_response(
        command="runs-cancel",
        project="/tmp/project",
        session_id="default",
        data={"execution_id": "run-1", "cancel_requested": False, "status": "ok"},
    )

    assert response.data == {
        "execution_id": "run-1",
        "cancel_requested": False,
        "status": "ok",
    }
    assert response.command_data is None
    assert response.to_dict()["data"] == {
        "execution_id": "run-1",
        "cancel_requested": False,
        "status": "ok",
    }


def test_success_response_serializes_typed_command_data_into_stable_envelope() -> None:
    response = success_response(
        command="exec",
        project="/tmp/project",
        session_id="default",
        data=ExecCommandData(
            record=ExecutionRecord(
                execution_id="run-1",
                ts="2026-03-12T00:00:00+00:00",
                session_id="default",
                command_type="exec",
                status="ok",
                duration_ms=5,
                result="42",
            ),
            source_kind="argument",
            ensured_started=True,
            started_new_session=False,
        ),
    )

    payload = response.to_dict()

    assert payload["data"] == {
        "duration_ms": 5,
        "status": "ok",
        "execution_id": "run-1",
        "result": "42",
        "source_kind": "argument",
        "ensured_started": True,
        "started_new_session": False,
    }


def test_execution_result_to_dict_keeps_legacy_surface_without_outputs() -> None:
    result = ExecutionResult(
        status="ok",
        duration_ms=5,
        outputs=[OutputItem.result(text="2", mime={"text/plain": "2"})],
    )

    payload = result.to_dict()

    assert payload == {
        "status": "ok",
        "stdout": "",
        "stderr": "",
        "result": "2",
        "execution_count": None,
        "duration_ms": 5,
        "ename": None,
        "evalue": None,
        "traceback": None,
        "events": [
            {
                "kind": "result",
                "content": "2",
                "metadata": {"mime": {"text/plain": "2"}},
            }
        ],
    }


def test_execution_result_preserves_terminal_error_without_synthesizing_events() -> None:
    result = ExecutionResult(
        status="error",
        duration_ms=5,
        outputs=[OutputItem.stdout("hello\n")],
        ename="ValueError",
        evalue="boom",
        traceback=["tb"],
    )

    assert result.status == "error"
    assert result.stdout == "hello\n"
    assert result.ename == "ValueError"
    assert result.evalue == "boom"
    assert result.traceback == ["tb"]
    assert result.events == [ExecutionEvent(kind="stdout", content="hello\n")]
