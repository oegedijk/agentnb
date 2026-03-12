from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agentnb.journal import CommandJournal


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
