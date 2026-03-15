from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentnb.errors import AgentNBException
from agentnb.execution import ExecutionService
from agentnb.selectors import RunReference, RunSelectorResolver, parse_run_reference


def test_parse_run_reference_parses_latest_selector() -> None:
    reference = parse_run_reference("@latest")

    assert reference == RunReference(kind="latest", value=None, raw="@latest")


def test_parse_run_reference_treats_plain_value_as_execution_id() -> None:
    reference = parse_run_reference("run-123")

    assert reference == RunReference(kind="execution_id", value="run-123", raw="run-123")


def test_run_selector_resolver_returns_explicit_execution_id_without_lookup(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    resolver = RunSelectorResolver(executions)

    execution_id = resolver.resolve_execution_id(
        project_root=project_dir,
        reference=parse_run_reference("run-123"),
    )

    assert execution_id == "run-123"
    executions.list_runs.assert_not_called()


def test_run_selector_resolver_resolves_latest_run_by_timestamp(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    executions.list_runs.return_value = [
        {"execution_id": "run-1", "ts": "2026-03-10T00:00:00+00:00"},
        {"execution_id": "run-2", "ts": "2026-03-11T00:00:00+00:00"},
    ]
    resolver = RunSelectorResolver(executions)

    execution_id = resolver.resolve_execution_id(
        project_root=project_dir,
        reference=parse_run_reference("@latest"),
    )

    assert execution_id == "run-2"
    executions.list_runs.assert_called_once_with(project_root=project_dir)


def test_run_selector_resolver_rejects_latest_when_no_runs_exist(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    executions.list_runs.return_value = []
    resolver = RunSelectorResolver(executions)

    with pytest.raises(AgentNBException, match="No runs found for selector: @latest"):
        resolver.resolve_execution_id(
            project_root=project_dir,
            reference=parse_run_reference("@latest"),
        )
