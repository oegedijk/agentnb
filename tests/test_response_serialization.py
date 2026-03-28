from __future__ import annotations

from typing import Any, cast

from agentnb.command_data import ExecCommandData, RunsListCommandData
from agentnb.execution_output import OutputItem
from agentnb.journal import JournalEntry
from agentnb.response_serialization import (
    compact_history_entry,
    serialize_command_data,
)
from agentnb.runs.store import ExecutionRecord
from tests.helpers import build_run_list_entry_data


def _record(**overrides: object) -> ExecutionRecord:
    payload: dict[str, Any] = {
        "execution_id": "run-1",
        "ts": "2026-03-11T00:00:00+00:00",
        "session_id": "default",
        "command_type": "exec",
        "status": "ok",
        "duration_ms": 5,
    }
    payload.update(cast(dict[str, Any], overrides))
    return ExecutionRecord(**cast(Any, payload))


def _serialize_exec_payload(**overrides: object) -> dict[str, object]:
    return cast(
        dict[str, object],
        serialize_command_data("exec", ExecCommandData(record=_record(**overrides))),
    )


def test_exec_serialization_truncates_large_fields_and_preserves_selected_output() -> None:
    compacted = _serialize_exec_payload(
        duration_ms=12,
        stdout="stdout " * 80,
        stderr="stderr " * 80,
        result="result " * 80,
    )

    assert compacted["execution_id"] == "run-1"
    assert "..." in cast(str, compacted["stdout"]) and "chars truncated" in cast(
        str, compacted["stdout"]
    )
    assert "..." in cast(str, compacted["stderr"]) and "chars truncated" in cast(
        str, compacted["stderr"]
    )
    assert cast(str, compacted["result"]).endswith("...")
    assert compacted["stdout_truncated"] is True
    assert compacted["stderr_truncated"] is True
    assert compacted["result_truncated"] is True


def test_exec_serialization_truncation_notice_includes_char_count() -> None:
    compacted = _serialize_exec_payload(stdout="x" * 300)

    assert "stdout" in compacted
    assert "[100 chars truncated]" in cast(str, compacted["stdout"])
    assert compacted["stdout_truncated"] is True


def test_exec_serialization_no_truncation_notice_for_short_stdout() -> None:
    compacted = _serialize_exec_payload(stdout="hello world")

    assert compacted.get("stdout") == "hello world"
    assert "stdout_truncated" not in compacted


def test_exec_serialization_preserves_structured_result_preview() -> None:
    compacted = _serialize_exec_payload(
        result="large dataframe repr",
        outputs=[
            OutputItem.result(
                text="large dataframe repr",
                mime={
                    "text/plain": "large dataframe repr",
                    "application/json": '{"alpha": 1, "beta": 2}',
                },
            )
        ],
    )

    assert compacted["result"] == "large dataframe repr"
    assert compacted["result_preview"] == {
        "kind": "mapping-like",
        "length": 2,
        "keys": ["alpha", "beta"],
        "sample": {"alpha": 1, "beta": 2},
    }


def test_exec_serialization_derives_structured_result_preview_from_result_text() -> None:
    compacted = _serialize_exec_payload(
        result="[{'id': 1, 'name': 'alpha'}, {'id': 2, 'name': 'beta'}]"
    )

    assert compacted["result"] == "[{'id': 1, 'name': 'alpha'}, {'id': 2, 'name': 'beta'}]"
    assert compacted["result_preview"] == {
        "kind": "sequence-like",
        "length": 2,
        "item_type": "dict",
        "sample_keys": ["id", "name"],
        "sample": [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}],
    }


def test_exec_serialization_does_not_invent_structured_preview_for_scalars() -> None:
    compacted = _serialize_exec_payload(result="42")

    assert compacted["result"] == "42"
    assert "result_preview" not in compacted


def test_runs_list_serialization_prefers_structured_result_preview_when_available() -> None:
    compacted = serialize_command_data(
        "runs-list",
        RunsListCommandData(
            runs=[
                build_run_list_entry_data(
                    result="large mapping repr",
                    result_preview={
                        "kind": "mapping-like",
                        "length": 2,
                        "keys": ["alpha", "beta"],
                        "sample": {"alpha": 1, "beta": 2},
                    },
                )
            ]
        ),
    )["runs"][0]

    assert compacted["result_preview"] == {
        "kind": "mapping-like",
        "length": 2,
        "keys": ["alpha", "beta"],
        "sample": {"alpha": 1, "beta": 2},
    }


def test_runs_list_serialization_preserves_projected_cancelled_error_type() -> None:
    compacted = serialize_command_data(
        "runs-list",
        RunsListCommandData(
            runs=[
                build_run_list_entry_data(
                    status="error",
                    cancel_requested=True,
                    terminal_reason="cancelled",
                    error_type="CancelledError",
                )
            ]
        ),
    )["runs"][0]

    assert compacted["terminal_reason"] == "cancelled"
    assert compacted["error_type"] == "CancelledError"


def test_compact_history_entry_formats_exec_preview_and_errors() -> None:
    ok_entry = compact_history_entry(
        JournalEntry(
            kind="user_command",
            ts="2026-03-11T00:00:00+00:00",
            session_id="default",
            execution_id=None,
            status="ok",
            duration_ms=5,
            command_type="exec",
            label="exec",
            user_visible=True,
            classification="replayable",
            provenance_source="history_store",
            provenance_detail="user_command",
            input=(
                "url = 'https://example.com/really/long/path/to/resource?"
                "alpha=1&beta=2&gamma=3'\nurl"
            ),
        )
    )
    error_entry = compact_history_entry(
        JournalEntry(
            kind="user_command",
            ts="2026-03-11T00:00:00+00:00",
            session_id="default",
            execution_id=None,
            status="error",
            duration_ms=5,
            command_type="exec",
            label="exec",
            user_visible=True,
            classification="replayable",
            provenance_source="history_store",
            provenance_detail="user_command",
            error_type="ZeroDivisionError",
        )
    )
    internal_ok_entry = compact_history_entry(
        JournalEntry(
            kind="kernel_execution",
            ts="2026-03-11T00:00:00+00:00",
            session_id="default",
            execution_id=None,
            status="ok",
            duration_ms=5,
            command_type="exec",
            label="exec kernel execution",
            user_visible=False,
            classification="internal",
            provenance_source="history_store",
            provenance_detail="kernel_execution",
            code="value = 42\nvalue",
        )
    )
    internal_error_entry = compact_history_entry(
        JournalEntry(
            kind="kernel_execution",
            ts="2026-03-11T00:00:00+00:00",
            session_id="default",
            execution_id=None,
            status="error",
            duration_ms=5,
            command_type="exec",
            label="exec kernel execution",
            user_visible=False,
            classification="internal",
            provenance_source="history_store",
            provenance_detail="kernel_execution",
            error_type="ZeroDivisionError",
        )
    )

    ok_label = ok_entry["label"]
    assert isinstance(ok_label, str)
    assert ok_label.startswith("exec url = 'https://example.com")
    assert "gamma=3" not in ok_label
    assert error_entry["label"] == "exec error ZeroDivisionError"
    assert internal_ok_entry["label"] == "exec kernel execution value = 42 | value"
    assert internal_error_entry["label"] == "exec kernel error ZeroDivisionError"


def test_compact_history_entry_preserves_multiline_code_preview() -> None:
    entry = compact_history_entry(
        JournalEntry(
            kind="user_command",
            ts="2026-03-11T00:00:00+00:00",
            session_id="default",
            execution_id=None,
            status="error",
            duration_ms=5,
            command_type="exec",
            label="exec",
            user_visible=True,
            classification="replayable",
            provenance_source="history_store",
            provenance_detail="user_command",
            code="a = 1\nb = 2\nc = a + b\nc",
            error_type="NameError",
        )
    )

    assert entry["code"] == "a = 1\nb = 2\nc = a + b\n..."


def test_runs_list_serialization_exposes_previews_and_error_type() -> None:
    entry = serialize_command_data(
        "runs-list",
        RunsListCommandData(
            runs=[
                build_run_list_entry_data(
                    ts="2026-03-11T00:00:00+00:00",
                    status="error",
                    duration_ms=9,
                    stdout="line one\nline two",
                    result="value",
                    ename="RuntimeError",
                )
            ]
        ),
    )["runs"][0]

    assert entry == {
        "execution_id": "run-1",
        "ts": "2026-03-11T00:00:00+00:00",
        "session_id": "default",
        "command_type": "exec",
        "status": "error",
        "duration_ms": 9,
        "cancel_requested": False,
        "result_preview": "value",
        "stdout_preview": "line one line two",
        "error_type": "RuntimeError",
    }
