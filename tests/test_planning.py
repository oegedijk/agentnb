from __future__ import annotations

from pathlib import Path

import pytest

from agentnb.journal import CommandJournal, JournalQuery
from agentnb.planning import ReplayPlanner, SnapshotPlanner
from agentnb.state import StateRepository


@pytest.fixture
def populated_journal(project_dir: Path, journal_builder) -> Path:
    journal_builder["history"](
        ts="2026-03-10T00:00:00+00:00",
        session_id="default",
        command_type="vars",
        label="vars",
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
    return project_dir


def test_replay_planner_builds_steps_only_for_replayable_entries(
    populated_journal,
) -> None:
    selection = CommandJournal().select(
        project_root=populated_journal,
        query=JournalQuery(session_id="default", include_internal=True),
    )

    plan = ReplayPlanner().build(selection)

    assert [step.command_type for step in plan.steps] == ["exec", "reset"]
    assert [step.execution_id for step in plan.steps] == ["run-ok", "run-err"]
    assert all(step.provenance_source == "execution_store" for step in plan.steps)


def test_snapshot_planner_builds_registered_future_state_resources(project_dir) -> None:
    repository = StateRepository(project_dir)

    plan = SnapshotPlanner().build(repository)

    assert {resource.name for resource in plan.resources} == {
        "snapshots",
        "artifacts",
        "exports",
        "metadata",
    }
    assert set(plan.resource_paths(repository)) == {
        project_dir / ".agentnb" / "snapshots",
        project_dir / ".agentnb" / "artifacts",
        project_dir / ".agentnb" / "exports",
        project_dir / ".agentnb" / "metadata",
    }
