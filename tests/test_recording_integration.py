from __future__ import annotations

from pathlib import Path

import pytest

from agentnb.errors import AgentNBException
from agentnb.execution import ExecutionCommandRequest, ExecutionService
from agentnb.introspection import KernelIntrospection
from agentnb.journal import JournalEntry, JournalQuery
from agentnb.planning import ReplayPlanner
from agentnb.runtime import KernelRuntime

pytest.importorskip("jupyter_client")
pytest.importorskip("ipykernel")


def _entry(
    entries: list[JournalEntry],
    *,
    command_type: str,
    kind: str,
    label: str | None = None,
) -> JournalEntry:
    matches = [
        entry
        for entry in entries
        if entry.command_type == command_type
        and entry.kind == kind
        and (label is None or entry.label == label)
    ]
    assert len(matches) == 1
    return matches[0]


def test_recording_contract_is_canonical_for_exec_inspect_and_reset(
    started_runtime: tuple[KernelRuntime, Path],
) -> None:
    runtime, project_dir = started_runtime
    executions = ExecutionService(runtime)
    introspection = KernelIntrospection(runtime)

    exec_run = executions.execute(
        ExecutionCommandRequest(
            project_root=project_dir,
            command_type="exec",
            code="alpha = 41\nalpha + 1",
            timeout_s=5,
        )
    )
    inspect_payload = introspection.inspect_var(project_root=project_dir, name="alpha").payload
    reset_run = executions.execute(
        ExecutionCommandRequest(
            project_root=project_dir,
            command_type="reset",
            timeout_s=5,
        )
    )

    assert inspect_payload["name"] == "alpha"
    assert inspect_payload["repr"] == "41"

    visible_history = runtime.history(project_root=project_dir)
    assert [entry.command_type for entry in visible_history] == ["exec", "inspect", "reset"]
    assert [entry.label for entry in visible_history] == ["exec", "inspect alpha", "reset"]
    assert [entry.status for entry in visible_history] == ["ok", "ok", "ok"]

    selection = runtime.select_history(
        project_root=project_dir,
        query=JournalQuery(session_id="default", include_internal=True),
    )

    exec_user = _entry(selection.entries, command_type="exec", kind="user_command")
    exec_internal = _entry(selection.entries, command_type="exec", kind="kernel_execution")
    inspect_user = _entry(
        selection.entries,
        command_type="inspect",
        kind="user_command",
        label="inspect alpha",
    )
    inspect_internal = _entry(
        selection.entries,
        command_type="inspect",
        kind="kernel_execution",
        label="inspect alpha helper",
    )
    reset_user = _entry(selection.entries, command_type="reset", kind="user_command")
    reset_internal = _entry(selection.entries, command_type="reset", kind="kernel_execution")

    assert exec_user.execution_id == exec_run.record.execution_id
    assert exec_internal.execution_id == exec_run.record.execution_id
    assert exec_user.classification == "replayable"
    assert exec_internal.classification == "internal"
    assert exec_user.provenance_source == "execution_store"
    assert exec_internal.provenance_detail == "kernel_execution"
    assert exec_user.code == "alpha = 41\nalpha + 1"

    assert inspect_user.classification == "inspection"
    assert inspect_internal.classification == "internal"
    assert inspect_user.provenance_source == "history_store"
    assert inspect_internal.provenance_detail == "kernel_execution"

    assert reset_user.execution_id == reset_run.record.execution_id
    assert reset_internal.execution_id == reset_run.record.execution_id
    assert reset_user.classification == "replayable"
    assert reset_user.provenance_source == "execution_store"

    replay_plan = ReplayPlanner().build(selection)
    assert [step.command_type for step in replay_plan.steps] == ["exec", "reset"]
    assert [step.execution_id for step in replay_plan.steps] == [
        exec_run.record.execution_id,
        reset_run.record.execution_id,
    ]
    assert replay_plan.steps[0].code == "alpha = 41\nalpha + 1"
    assert replay_plan.steps[0].provenance_source == "execution_store"


def test_recording_contract_preserves_semantic_error_history_across_write_paths(
    started_runtime: tuple[KernelRuntime, Path],
) -> None:
    runtime, project_dir = started_runtime
    executions = ExecutionService(runtime)
    introspection = KernelIntrospection(runtime)

    exec_run = executions.execute(
        ExecutionCommandRequest(
            project_root=project_dir,
            command_type="exec",
            code="1 / 0",
            timeout_s=5,
        )
    )

    with pytest.raises(AgentNBException):
        introspection.inspect_var(project_root=project_dir, name="missing_value")

    assert exec_run.record.status == "error"
    assert exec_run.record.ename == "ZeroDivisionError"

    visible_errors = runtime.history(project_root=project_dir, errors_only=True)
    assert [entry.command_type for entry in visible_errors] == ["exec", "inspect"]
    assert [entry.label for entry in visible_errors] == ["exec", "inspect missing_value"]
    assert [entry.error_type for entry in visible_errors] == ["ZeroDivisionError", "NameError"]

    selection = runtime.select_history(
        project_root=project_dir,
        query=JournalQuery(
            session_id="default",
            include_internal=True,
            errors_only=True,
        ),
    )

    exec_user = _entry(selection.entries, command_type="exec", kind="user_command")
    exec_internal = _entry(selection.entries, command_type="exec", kind="kernel_execution")
    inspect_user = _entry(selection.entries, command_type="inspect", kind="user_command")
    inspect_internal = _entry(selection.entries, command_type="inspect", kind="kernel_execution")

    assert [entry.kind for entry in selection.entries] == [
        "kernel_execution",
        "user_command",
        "kernel_execution",
        "user_command",
    ]
    assert exec_user.execution_id == exec_run.record.execution_id
    assert exec_user.classification == "replayable"
    assert exec_internal.provenance_source == "execution_store"
    assert inspect_user.classification == "inspection"
    assert inspect_internal.provenance_source == "history_store"
