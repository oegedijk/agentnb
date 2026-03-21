from __future__ import annotations

from agentnb.compact import (
    compact_collection_preview,
    compact_execution_payload,
    compact_history_entry,
    compact_run_entry,
    compact_traceback,
)
from agentnb.journal import JournalEntry
from agentnb.payloads import CompactExecPayloadInput, SequencePreview


def test_compact_traceback_strips_ansi_and_middle_lines() -> None:
    traceback = [
        "\x1b[31mTraceback (most recent call last):\x1b[0m",
        '  File "main.py", line 1, in <module>',
        "line 3",
        "line 4",
        "line 5",
        "ValueError: bad value",
    ]

    compacted = compact_traceback(traceback)

    assert compacted == [
        "Traceback (most recent call last):",
        '  File "main.py", line 1, in <module>',
        "...",
        "line 4",
        "line 5",
        "ValueError: bad value",
    ]


def test_compact_execution_payload_truncates_large_fields_and_preserves_selected_output() -> None:
    payload: CompactExecPayloadInput = {
        "status": "ok",
        "duration_ms": 12,
        "execution_id": "run-1",
        "stdout": "stdout " * 80,
        "stderr": "stderr " * 80,
        "result": "result " * 80,
        "selected_output": "stdout",
        "selected_text": "exact output\n",
    }

    compacted = compact_execution_payload(payload)

    assert compacted["execution_id"] == "run-1"
    assert compacted["selected_output"] == "stdout"
    assert compacted["selected_text"] == "exact output\n"
    assert "..." in compacted["stdout"] and "chars truncated" in compacted["stdout"]
    assert "..." in compacted["stderr"] and "chars truncated" in compacted["stderr"]
    assert compacted["result"].endswith("...")


def test_compact_execution_payload_truncation_notice_includes_char_count() -> None:
    # stdout is 300 chars raw; _STDOUT_LIMIT is 200, so notice should say 100 chars truncated
    stdout = "x" * 300
    payload: CompactExecPayloadInput = {
        "status": "ok",
        "duration_ms": 5,
        "stdout": stdout,
    }

    compacted = compact_execution_payload(payload)

    assert "stdout" in compacted
    assert "[100 chars truncated]" in compacted["stdout"]


def test_compact_execution_payload_no_truncation_notice_for_short_stdout() -> None:
    stdout = "hello world"
    payload: CompactExecPayloadInput = {
        "status": "ok",
        "duration_ms": 5,
        "stdout": stdout,
    }

    compacted = compact_execution_payload(payload)

    assert compacted.get("stdout") == "hello world"


def test_compact_execution_payload_preserves_structured_result_preview() -> None:
    payload: CompactExecPayloadInput = {
        "status": "ok",
        "duration_ms": 5,
        "result": "large dataframe repr",
        "result_preview": {
            "kind": "dataframe-like",
            "shape": [200, 2],
            "columns": ["i", "text"],
            "dtypes": {"i": "int64", "text": "object"},
            "null_counts": {"i": 0, "text": 0},
            "head": [
                {"index": 0, "i": 0, "text": "x"},
                {"index": 1, "i": 1, "text": "y"},
                {"index": 2, "i": 2, "text": "z"},
                {"index": 3, "i": 3, "text": "ignored"},
            ],
        },
    }

    compacted = compact_execution_payload(payload)

    assert compacted["result"] == "large dataframe repr"
    assert compacted["result_preview"]["kind"] == "dataframe-like"
    assert compacted["result_preview"]["shape"] == [200, 2]
    head = compacted["result_preview"]["head"]
    assert head is not None
    assert len(head) == 3


def test_compact_collection_preview_limits_nested_values() -> None:
    preview: SequencePreview = {
        "kind": "sequence-like",
        "length": 5,
        "item_type": "dict",
        "sample_keys": ["id", "title", "body", "author", "meta", "ignored"],
        "sample": [
            {
                "id": 1,
                "title": "a" * 100,
                "body": "b" * 100,
                "author": "c" * 100,
                "meta": "d" * 100,
                "ignored": "e" * 100,
            }
            for _ in range(5)
        ],
    }

    compacted = compact_collection_preview(preview)

    assert compacted["kind"] == "sequence-like"
    assert compacted["length"] == 5
    assert compacted["item_type"] == "dict"
    assert compacted["sample_keys"] == ["id", "title", "body", "author", "meta"]
    assert len(compacted["sample"]) == 3
    first = compacted["sample"][0]
    assert isinstance(first, dict)
    assert set(first) == {"id", "title", "body", "author", "meta"}


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
            provenance_detail="history_record",
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
            provenance_detail="history_record",
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
            provenance_detail="history_record",
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
            provenance_detail="history_record",
            error_type="ZeroDivisionError",
        )
    )

    ok_label = ok_entry["label"]
    assert isinstance(ok_label, str)
    assert ok_label.startswith("exec url = 'https://example.com")
    assert "gamma=3" not in ok_label
    assert error_entry["label"] == "exec error ZeroDivisionError"
    assert internal_ok_entry["label"] == "exec kernel execution value = 42 value"
    assert internal_error_entry["label"] == "exec kernel error ZeroDivisionError"


def test_compact_run_entry_exposes_previews_and_error_type() -> None:
    entry = compact_run_entry(
        {
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

    assert entry == {
        "execution_id": "run-1",
        "ts": "2026-03-11T00:00:00+00:00",
        "session_id": "default",
        "command_type": "exec",
        "status": "error",
        "duration_ms": 9,
        "result_preview": "value",
        "stdout_preview": "line one line two",
        "error_type": "RuntimeError",
    }
