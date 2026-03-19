from __future__ import annotations

from unittest.mock import Mock

import pytest

from agentnb.errors import AgentNBException
from agentnb.execution import ExecutionService
from agentnb.journal import JournalQuery
from agentnb.selectors import (
    HistoryReference,
    HistorySelectorResolver,
    RunReference,
    RunSelectorResolver,
    parse_history_reference,
    parse_run_reference,
)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("@latest", RunReference(kind="latest", value=None, raw="@latest")),
        ("@active", RunReference(kind="active", value=None, raw="@active")),
        (
            "@last-error",
            RunReference(kind="last_error", value=None, raw="@last-error"),
        ),
        (
            "@last-success",
            RunReference(kind="last_success", value=None, raw="@last-success"),
        ),
        ("run-123", RunReference(kind="execution_id", value="run-123", raw="run-123")),
        (None, None),
    ],
)
def test_parse_run_reference_parses_supported_targets(
    value: str | None,
    expected: RunReference | None,
) -> None:
    assert parse_run_reference(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("@latest", HistoryReference(kind="latest", value=None, raw="@latest")),
        (
            "@last-error",
            HistoryReference(kind="last_error", value=None, raw="@last-error"),
        ),
        (
            "@last-success",
            HistoryReference(kind="last_success", value=None, raw="@last-success"),
        ),
        (
            "run-123",
            HistoryReference(kind="execution_id", value="run-123", raw="run-123"),
        ),
        (None, None),
    ],
)
def test_parse_history_reference_parses_supported_targets(
    value: str | None,
    expected: HistoryReference | None,
) -> None:
    assert parse_history_reference(value) == expected


def test_run_selector_resolver_returns_explicit_execution_id_without_lookup(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    resolver = RunSelectorResolver(executions)

    execution_id = resolver.resolve_execution_id(
        project_root=project_dir,
        reference=parse_run_reference("run-123"),
    )

    assert execution_id == "run-123"
    executions.list_runs.assert_not_called()


def test_run_selector_resolver_prefers_current_session_for_latest(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    executions.list_runs.side_effect = [
        [{"execution_id": "run-analysis", "ts": "2026-03-12T00:00:00+00:00"}],
    ]
    resolver = RunSelectorResolver(executions)

    execution_id = resolver.resolve_execution_id(
        project_root=project_dir,
        reference=parse_run_reference("@latest"),
        current_session_id="analysis",
    )

    assert execution_id == "run-analysis"
    executions.list_runs.assert_called_once_with(project_root=project_dir, session_id="analysis")


def test_run_selector_resolver_falls_back_to_project_latest(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    executions.list_runs.side_effect = [
        [],
        [
            {"execution_id": "run-1", "ts": "2026-03-10T00:00:00+00:00"},
            {"execution_id": "run-2", "ts": "2026-03-11T00:00:00+00:00"},
        ],
    ]
    resolver = RunSelectorResolver(executions)

    execution_id = resolver.resolve_execution_id(
        project_root=project_dir,
        reference=None,
        current_session_id="analysis",
        default_behavior="latest",
    )

    assert execution_id == "run-2"


def test_run_selector_resolver_uses_last_error_and_last_success(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    executions.list_runs.side_effect = [
        [],
        [
            {"execution_id": "run-ok", "ts": "2026-03-10T00:00:00+00:00", "status": "ok"},
            {
                "execution_id": "run-error",
                "ts": "2026-03-11T00:00:00+00:00",
                "status": "error",
            },
        ],
        [],
        [
            {"execution_id": "run-ok", "ts": "2026-03-10T00:00:00+00:00", "status": "ok"},
            {
                "execution_id": "run-error",
                "ts": "2026-03-11T00:00:00+00:00",
                "status": "error",
            },
        ],
    ]
    resolver = RunSelectorResolver(executions)

    assert (
        resolver.resolve_execution_id(
            project_root=project_dir,
            reference=parse_run_reference("@last-error"),
            current_session_id="analysis",
        )
        == "run-error"
    )
    assert (
        resolver.resolve_execution_id(
            project_root=project_dir,
            reference=parse_run_reference("@last-success"),
            current_session_id="analysis",
        )
        == "run-ok"
    )


def test_run_selector_resolver_defaults_waits_to_active_run(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    executions.list_runs.side_effect = [
        [{"execution_id": "run-analysis", "ts": "2026-03-12T00:00:00+00:00", "status": "running"}],
    ]
    resolver = RunSelectorResolver(executions)

    execution_id = resolver.resolve_execution_id(
        project_root=project_dir,
        reference=None,
        current_session_id="analysis",
        default_behavior="active",
    )

    assert execution_id == "run-analysis"


def test_run_selector_resolver_rejects_ambiguous_active_run_without_preference(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    executions.list_runs.return_value = [
        {"execution_id": "run-1", "ts": "2026-03-10T00:00:00+00:00", "status": "running"},
        {"execution_id": "run-2", "ts": "2026-03-11T00:00:00+00:00", "status": "starting"},
    ]
    resolver = RunSelectorResolver(executions)

    with pytest.raises(AgentNBException, match="Multiple active runs match"):
        resolver.resolve_execution_id(
            project_root=project_dir,
            reference=parse_run_reference("@active"),
        )


def test_run_selector_resolver_rejects_missing_active_default(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    executions.list_runs.return_value = []
    resolver = RunSelectorResolver(executions)

    with pytest.raises(AgentNBException, match="No runs found for selector: @active"):
        resolver.resolve_execution_id(
            project_root=project_dir,
            reference=None,
            default_behavior="active",
        )


def test_history_selector_resolver_uses_plain_query_without_reference() -> None:
    resolver = HistorySelectorResolver()

    query = resolver.resolve_query(
        session_id="analysis",
        include_internal=True,
        errors_only=False,
        latest=False,
        last=2,
        reference=None,
    )

    assert query == JournalQuery(
        session_id="analysis",
        include_internal=True,
        errors_only=False,
        latest=False,
        last=2,
    )


@pytest.mark.parametrize(
    ("reference", "expected"),
    [
        (
            parse_history_reference("@latest"),
            JournalQuery(session_id="default", include_internal=False, latest=True),
        ),
        (
            parse_history_reference("@last-error"),
            JournalQuery(
                session_id="default",
                include_internal=False,
                errors_only=True,
                latest=True,
                prefer_execution_errors=True,
            ),
        ),
        (
            parse_history_reference("@last-success"),
            JournalQuery(
                session_id="default",
                include_internal=False,
                success_only=True,
                latest=True,
            ),
        ),
        (
            parse_history_reference("run-123"),
            JournalQuery(
                session_id="default",
                include_internal=False,
                execution_id="run-123",
            ),
        ),
    ],
)
def test_history_selector_resolver_maps_references_to_queries(
    reference: HistoryReference | None,
    expected: JournalQuery,
) -> None:
    resolver = HistorySelectorResolver()

    query = resolver.resolve_query(
        session_id="default",
        include_internal=False,
        errors_only=False,
        latest=False,
        last=None,
        reference=reference,
    )

    assert query == expected


def test_history_selector_resolver_rejects_reference_and_filters_together() -> None:
    resolver = HistorySelectorResolver()

    with pytest.raises(
        ValueError,
        match=(r"Use either a history selector or --errors/--latest/--last filters, not both\."),
    ):
        resolver.resolve_query(
            session_id="default",
            include_internal=False,
            errors_only=True,
            latest=False,
            last=None,
            reference=parse_history_reference("@latest"),
        )
