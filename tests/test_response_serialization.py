from __future__ import annotations

from typing import Any, cast

from agentnb.command_data import ExecCommandData, RunListEntryData
from agentnb.contracts import ExecutionEvent
from agentnb.execution_output import OutputItem
from agentnb.journal import JournalEntry
from agentnb.response_serialization import (
    compact_execution_payload,
    compact_history_entry,
    compact_run_entry,
)
from agentnb.runs.store import ExecutionRecord


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


def test_compact_execution_payload_truncates_large_fields_and_preserves_selected_output() -> None:
    compacted = compact_execution_payload(
        ExecCommandData(
            record=_record(
                duration_ms=12,
                stdout="stdout " * 80,
                stderr="stderr " * 80,
                result="result " * 80,
            )
        )
    )

    assert compacted["execution_id"] == "run-1"
    assert "..." in compacted["stdout"] and "chars truncated" in compacted["stdout"]
    assert "..." in compacted["stderr"] and "chars truncated" in compacted["stderr"]
    assert compacted["result"].endswith("...")
    assert compacted["stdout_truncated"] is True
    assert compacted["stderr_truncated"] is True
    assert compacted["result_truncated"] is True


def test_compact_execution_payload_truncation_notice_includes_char_count() -> None:
    compacted = compact_execution_payload(ExecCommandData(record=_record(stdout="x" * 300)))

    assert "stdout" in compacted
    assert "[100 chars truncated]" in compacted["stdout"]
    assert compacted["stdout_truncated"] is True


def test_compact_execution_payload_no_truncation_notice_for_short_stdout() -> None:
    compacted = compact_execution_payload(ExecCommandData(record=_record(stdout="hello world")))

    assert compacted.get("stdout") == "hello world"
    assert "stdout_truncated" not in compacted


def test_compact_execution_payload_preserves_structured_result_preview() -> None:
    compacted = compact_execution_payload(
        ExecCommandData(
            record=_record(
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
        )
    )

    assert compacted["result"] == "large dataframe repr"
    assert compacted["result_preview"] == {
        "kind": "mapping-like",
        "length": 2,
        "keys": ["alpha", "beta"],
        "sample": {"alpha": 1, "beta": 2},
    }


def test_compact_execution_payload_derives_structured_result_preview_from_result_text() -> None:
    compacted = compact_execution_payload(
        ExecCommandData(
            record=_record(result="[{'id': 1, 'name': 'alpha'}, {'id': 2, 'name': 'beta'}]")
        )
    )

    assert compacted["result"] == "[{'id': 1, 'name': 'alpha'}, {'id': 2, 'name': 'beta'}]"
    assert compacted["result_preview"] == {
        "kind": "sequence-like",
        "length": 2,
        "item_type": "dict",
        "sample_keys": ["id", "name"],
        "sample": [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}],
    }


def test_compact_execution_payload_does_not_invent_structured_preview_for_scalars() -> None:
    compacted = compact_execution_payload(ExecCommandData(record=_record(result="42")))

    assert compacted["result"] == "42"
    assert "result_preview" not in compacted


def test_compact_run_entry_prefers_structured_result_preview_when_available() -> None:
    compacted = compact_run_entry(
        RunListEntryData(
            payload={
                "execution_id": "run-1",
                "status": "ok",
                "result": "large mapping repr",
                "result_preview": {
                    "kind": "mapping-like",
                    "length": 2,
                    "keys": ["alpha", "beta"],
                    "sample": {"alpha": 1, "beta": 2},
                },
            }
        )
    )

    assert compacted["result_preview"] == {
        "kind": "mapping-like",
        "length": 2,
        "keys": ["alpha", "beta"],
        "sample": {"alpha": 1, "beta": 2},
    }


def test_compact_run_entry_preserves_projected_cancelled_error_type() -> None:
    compacted = compact_run_entry(
        RunListEntryData(
            payload=cast(
                dict[str, object],
                _record(
                    status="error",
                    cancel_requested=True,
                    terminal_reason="cancelled",
                    events=[
                        ExecutionEvent(
                            kind="error",
                            content="interrupted",
                            metadata={"ename": "KeyboardInterrupt", "traceback": ["tb"]},
                        )
                    ],
                ).to_dict(),
            )
        )
    )

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


def test_compact_run_entry_exposes_previews_and_error_type() -> None:
    entry = compact_run_entry(
        RunListEntryData(
            payload={
                "execution_id": "run-1",
                "ts": "2026-03-11T00:00:00+00:00",
                "session_id": "default",
                "command_type": "exec",
                "status": "error",
                "duration_ms": 9,
                "stdout": "line one\nline two",
                "result": "value",
                "ename": "RuntimeError",
            }
        )
    )

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
