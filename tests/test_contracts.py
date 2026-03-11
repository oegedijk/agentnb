from __future__ import annotations

from agentnb.contracts import SCHEMA_VERSION, error_response, success_response


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
