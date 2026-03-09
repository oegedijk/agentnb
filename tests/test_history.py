from __future__ import annotations

from agentnb.contracts import ExecutionResult
from agentnb.history import (
    HistoryStore,
    kernel_execution_record,
    summarize_history_text,
    user_command_record,
)


def test_history_store_filters_internal_and_errors(project_dir) -> None:
    store = HistoryStore(project_dir)
    store.append(
        user_command_record(
            session_id="default",
            command_type="exec",
            label="exec",
            input_text="1 + 1",
            code="1 + 1",
            execution=ExecutionResult(status="ok", result="2", duration_ms=12),
        )
    )
    store.append(
        kernel_execution_record(
            session_id="default",
            command_type="vars",
            label="vars helper",
            code="print('internal helper')",
            origin="ops_helper",
            execution=ExecutionResult(status="error", ename="NameError", duration_ms=4),
        )
    )

    visible_entries = store.read()
    all_entries = store.read(include_internal=True)
    error_entries = store.read(include_internal=True, errors_only=True)

    assert [entry.command_type for entry in visible_entries] == ["exec"]
    assert [entry.kind for entry in all_entries] == ["user_command", "kernel_execution"]
    assert len(error_entries) == 1
    assert error_entries[0].command_type == "vars"
    assert error_entries[0].user_visible is False


def test_history_record_compacts_output_previews(project_dir) -> None:
    store = HistoryStore(project_dir)
    store.append(
        user_command_record(
            session_id="default",
            command_type="exec",
            label="exec",
            input_text="print('x')",
            code="print('x')",
            execution=ExecutionResult(
                status="ok",
                stdout="line one\nline two\nline three",
                result="  spaced    result  ",
                duration_ms=3,
            ),
        )
    )

    entry = store.read()[0]
    assert entry.result_preview == "spaced result"
    assert entry.stdout_preview == "line one line two line three"


def test_summarize_history_text_truncates_long_values() -> None:
    text = "word " * 50
    preview = summarize_history_text(text, limit=20)

    assert preview is not None
    assert preview.endswith("...")
    assert len(preview) == 20
