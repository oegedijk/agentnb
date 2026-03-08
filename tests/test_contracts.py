from __future__ import annotations

import pytest

from agentnb.contracts import error_response, success_response


@pytest.mark.parametrize(
    "required_key",
    ["schema_version", "status", "command", "project", "session_id", "timestamp", "data", "error"],
)
def test_success_response_has_required_top_level_fields(required_key: str) -> None:
    response = success_response(
        command="status",
        project="/tmp/project",
        session_id="default",
        data={"alive": False},
    )

    payload = response.to_dict()
    assert required_key in payload


@pytest.mark.parametrize(
    ("field", "expected"),
    [
        ("code", "NO_KERNEL"),
        ("message", "No kernel running."),
        ("ename", "RuntimeError"),
        ("evalue", "missing kernel"),
        ("traceback", ["line1"]),
    ],
)
def test_error_response_includes_stable_error_keys(field: str, expected: str | list[str]) -> None:
    response = error_response(
        command="exec",
        project="/tmp/project",
        session_id="default",
        code="NO_KERNEL",
        message="No kernel running.",
        ename="RuntimeError",
        evalue="missing kernel",
        traceback=["line1"],
    )

    payload = response.to_dict()
    assert payload["status"] == "error"
    assert payload["error"] is not None
    assert payload["error"][field] == expected
