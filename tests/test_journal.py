from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentnb.execution import ExecutionRecord, ExecutionStore
from agentnb.history import kernel_execution_record, user_command_record
from agentnb.journal import CommandJournal, JournalQuery


@pytest.fixture
def populated_journal(project_dir: Path, journal_builder: dict[str, Any]) -> Path:
    journal_builder["history"](
        ts="2026-03-10T00:00:00+00:00",
        session_id="default",
        command_type="vars",
        label="vars",
    )
    journal_builder["history"](
        ts="2026-03-10T00:00:03+00:00",
        session_id="default",
        command_type="inspect",
        label="inspect thing",
        status="error",
        error_type="NameError",
    )
    journal_builder["history"](
        ts="2026-03-10T00:00:04+00:00",
        session_id="other",
        command_type="reload",
        label="reload mod",
    )
    journal_builder["execution"](
        execution_id="run-ok",
        ts="2026-03-10T00:00:01+00:00",
        session_id="default",
        command_type="exec",
        status="ok",
        duration_ms=12,
        code="1 + 1",
        result="2",
    )
    journal_builder["execution"](
        execution_id="run-err",
        ts="2026-03-10T00:00:02+00:00",
        session_id="default",
        command_type="reset",
        status="error",
        duration_ms=9,
        ename="RuntimeError",
    )
    journal_builder["execution"](
        execution_id="run-other",
        ts="2026-03-10T00:00:05+00:00",
        session_id="other",
        command_type="exec",
        status="ok",
        duration_ms=5,
        code="99",
        result="99",
    )
    return project_dir


def test_command_journal_filters_to_requested_session(
    populated_journal: Path,
) -> None:
    entries = CommandJournal().entries(project_root=populated_journal, session_id="default")

    assert [entry.session_id for entry in entries] == ["default", "default", "default", "default"]
    assert [entry.label for entry in entries] == [
        "vars",
        "exec",
        "reset",
        "inspect thing",
    ]


@pytest.mark.parametrize(
    ("include_internal", "errors_only", "expected_kinds", "expected_labels"),
    [
        (
            False,
            False,
            ["user_command", "user_command", "user_command", "user_command"],
            ["vars", "exec", "reset", "inspect thing"],
        ),
        (
            True,
            False,
            [
                "user_command",
                "kernel_execution",
                "user_command",
                "kernel_execution",
                "user_command",
                "user_command",
            ],
            [
                "vars",
                "exec kernel execution",
                "exec",
                "reset kernel state",
                "reset",
                "inspect thing",
            ],
        ),
        (
            False,
            True,
            ["user_command", "user_command"],
            ["reset", "inspect thing"],
        ),
        (
            True,
            True,
            ["kernel_execution", "user_command", "user_command"],
            ["reset kernel state", "reset", "inspect thing"],
        ),
    ],
)
def test_command_journal_entries_respect_selector_semantics(
    populated_journal: Path,
    include_internal: bool,
    errors_only: bool,
    expected_kinds: list[str],
    expected_labels: list[str],
) -> None:
    entries = CommandJournal().entries(
        project_root=populated_journal,
        session_id="default",
        include_internal=include_internal,
        errors_only=errors_only,
    )

    assert [entry.kind for entry in entries] == expected_kinds
    assert [entry.label for entry in entries] == expected_labels


def test_command_journal_replayable_entries_include_only_user_visible_exec_and_reset(
    populated_journal: Path,
) -> None:
    entries = CommandJournal().replayable_entries(
        project_root=populated_journal,
        session_id="default",
    )

    assert [entry.command_type for entry in entries] == ["exec", "reset"]
    assert [entry.label for entry in entries] == ["exec", "reset"]
    assert all(entry.replayable for entry in entries)


def test_command_journal_last_activity_uses_latest_entry_for_session(
    populated_journal: Path,
) -> None:
    last_activity = CommandJournal().last_activity(
        project_root=populated_journal,
        session_id="default",
    )

    assert last_activity == "2026-03-10T00:00:03+00:00"


def test_command_journal_select_applies_latest_and_last_queries(populated_journal: Path) -> None:
    latest = CommandJournal().select(
        project_root=populated_journal,
        query=JournalQuery(session_id="default", latest=True),
    )
    last_two = CommandJournal().select(
        project_root=populated_journal,
        query=JournalQuery(session_id="default", last=2),
    )

    assert [entry.label for entry in latest.entries] == ["inspect thing"]
    assert [entry.label for entry in last_two.entries] == ["reset", "inspect thing"]


def test_command_journal_latest_error_prefers_kernel_failure_over_control_error(
    project_dir: Path,
    journal_builder: dict[str, Any],
) -> None:
    journal_builder["execution"](
        execution_id="run-kernel-error",
        ts="2026-03-10T00:00:01+00:00",
        session_id="default",
        command_type="exec",
        status="error",
        duration_ms=12,
        code="1 / 0",
        ename="ZeroDivisionError",
        failure_origin="kernel",
    )
    journal_builder["execution"](
        execution_id="run-busy",
        ts="2026-03-10T00:00:02+00:00",
        session_id="default",
        command_type="exec",
        status="error",
        duration_ms=0,
        code="99",
        ename="SessionBusyError",
        failure_origin="control",
    )

    selection = CommandJournal().select(
        project_root=project_dir,
        query=JournalQuery(
            session_id="default",
            errors_only=True,
            latest=True,
            prefer_execution_errors=True,
        ),
    )

    assert [entry.label for entry in selection.entries] == ["exec"]
    assert selection.entries[0].execution_id == "run-kernel-error"


def test_command_journal_latest_error_falls_back_to_control_error_when_needed(
    project_dir: Path,
    journal_builder: dict[str, Any],
) -> None:
    journal_builder["execution"](
        execution_id="run-busy",
        ts="2026-03-10T00:00:02+00:00",
        session_id="default",
        command_type="exec",
        status="error",
        duration_ms=0,
        code="99",
        ename="SessionBusyError",
        failure_origin="control",
    )

    selection = CommandJournal().select(
        project_root=project_dir,
        query=JournalQuery(
            session_id="default",
            errors_only=True,
            latest=True,
            prefer_execution_errors=True,
        ),
    )

    assert [entry.label for entry in selection.entries] == ["exec"]
    assert selection.entries[0].execution_id == "run-busy"


def test_command_journal_select_can_filter_by_execution_id(populated_journal: Path) -> None:
    selection = CommandJournal().select(
        project_root=populated_journal,
        query=JournalQuery(
            session_id="default",
            execution_id="run-ok",
            include_internal=True,
        ),
    )

    assert [entry.label for entry in selection.entries] == ["exec kernel execution", "exec"]


def test_command_journal_prefers_persisted_journal_entries_over_fallback_projection(
    project_dir: Path,
) -> None:
    ExecutionStore(project_dir).append(
        ExecutionRecord(
            execution_id="run-1",
            ts="2026-03-10T00:00:01+00:00",
            session_id="default",
            command_type="exec",
            status="ok",
            duration_ms=12,
            code="alpha = 1\nalpha",
            result="1",
            journal_entries=[
                kernel_execution_record(
                    ts="2026-03-10T00:00:01+00:00",
                    session_id="default",
                    execution_id="run-1",
                    command_type="exec",
                    label="persisted helper",
                    code="alpha = 1\nalpha",
                    origin="execution_service",
                    status="ok",
                    duration_ms=12,
                    result="1",
                ),
                user_command_record(
                    ts="2026-03-10T00:00:01+00:00",
                    session_id="default",
                    execution_id="run-1",
                    command_type="exec",
                    label="persisted exec",
                    input_text="alpha = 1\nalpha",
                    code="alpha = 1\nalpha",
                    origin="execution_service",
                    status="ok",
                    duration_ms=12,
                    result="1",
                ),
            ],
        )
    )

    selection = CommandJournal().select(
        project_root=project_dir,
        query=JournalQuery(session_id="default", include_internal=True),
    )

    assert [entry.label for entry in selection.entries] == ["persisted helper", "persisted exec"]
    assert [entry.provenance_source for entry in selection.entries] == [
        "execution_store",
        "execution_store",
    ]
    assert [entry.provenance_detail for entry in selection.entries] == [
        "projected_kernel_execution",
        "projected_user_command",
    ]


def test_command_journal_falls_back_to_projected_entries_when_persisted_journal_is_absent(
    project_dir: Path,
    journal_builder: dict[str, Any],
) -> None:
    journal_builder["execution"](
        execution_id="run-1",
        ts="2026-03-10T00:00:01+00:00",
        session_id="default",
        command_type="exec",
        status="ok",
        duration_ms=12,
        code="alpha = 1\nalpha",
        result="1",
    )

    selection = CommandJournal().select(
        project_root=project_dir,
        query=JournalQuery(session_id="default", include_internal=True),
    )

    assert [entry.label for entry in selection.entries] == ["exec kernel execution", "exec"]
    assert [entry.provenance_source for entry in selection.entries] == [
        "execution_store",
        "execution_store",
    ]


def test_command_journal_entries_include_classification_and_provenance(
    populated_journal: Path,
) -> None:
    selection = CommandJournal().select(
        project_root=populated_journal,
        query=JournalQuery(session_id="default", include_internal=True),
    )

    by_label = {entry.label: entry for entry in selection.entries}
    assert by_label["vars"].classification == "inspection"
    assert by_label["inspect thing"].classification == "inspection"
    assert by_label["exec"].classification == "replayable"
    assert by_label["reset"].classification == "replayable"
    assert by_label["exec kernel execution"].classification == "internal"
    assert by_label["exec"].provenance_source == "execution_store"
    assert by_label["exec"].provenance_detail == "projected_user_command"
    assert by_label["vars"].provenance_source == "history_store"
    assert by_label["vars"].provenance_detail == "history_record"
