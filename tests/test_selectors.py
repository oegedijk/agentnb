from __future__ import annotations

from typing import Literal
from unittest.mock import Mock

import pytest

from agentnb.errors import AgentNBException
from agentnb.execution import (
    ExecutionService,
    RunSelectionRequest,
    RunSelectorCandidate,
)
from agentnb.journal import JournalQuery
from agentnb.selectors import (
    HistoryReference,
    HistorySelectorResolver,
    RunReference,
    RunSelectorResolver,
    parse_history_reference,
    parse_run_reference,
)


def _candidate(
    *,
    execution_id: str = "run-1",
    ts: str = "2026-03-11T00:00:00+00:00",
    session_id: str = "default",
    status: Literal["starting", "running", "ok", "error"] = "ok",
) -> RunSelectorCandidate:
    return RunSelectorCandidate(
        execution_id=execution_id,
        ts=ts,
        session_id=session_id,
        status=status,
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
    executions.list_run_selector_candidates.assert_not_called()


def test_run_selector_resolver_prefers_current_session_for_latest(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    executions.list_run_selector_candidates.side_effect = [
        [_candidate(execution_id="run-analysis", ts="2026-03-12T00:00:00+00:00")],
    ]
    resolver = RunSelectorResolver(executions)

    execution_id = resolver.resolve_execution_id(
        project_root=project_dir,
        reference=parse_run_reference("@latest"),
        current_session_id="analysis",
    )

    assert execution_id == "run-analysis"
    executions.list_run_selector_candidates.assert_called_once()
    request = executions.list_run_selector_candidates.call_args.kwargs["request"]
    assert isinstance(request, RunSelectionRequest)
    assert request.project_root == project_dir
    assert request.session_id == "analysis"


def test_run_selector_resolver_falls_back_to_project_latest(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    executions.list_run_selector_candidates.side_effect = [
        [],
        [
            _candidate(execution_id="run-1", ts="2026-03-10T00:00:00+00:00"),
            _candidate(execution_id="run-2", ts="2026-03-11T00:00:00+00:00"),
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
    executions.list_run_selector_candidates.side_effect = [
        [],
        [
            _candidate(execution_id="run-ok", ts="2026-03-10T00:00:00+00:00", status="ok"),
            _candidate(
                execution_id="run-error",
                ts="2026-03-11T00:00:00+00:00",
                status="error",
            ),
        ],
        [],
        [
            _candidate(execution_id="run-ok", ts="2026-03-10T00:00:00+00:00", status="ok"),
            _candidate(
                execution_id="run-error",
                ts="2026-03-11T00:00:00+00:00",
                status="error",
            ),
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
    executions.list_run_selector_candidates.side_effect = [
        [
            _candidate(
                execution_id="run-analysis",
                ts="2026-03-12T00:00:00+00:00",
                status="running",
            )
        ],
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
    executions.list_run_selector_candidates.return_value = [
        _candidate(execution_id="run-1", ts="2026-03-10T00:00:00+00:00", status="running"),
        _candidate(execution_id="run-2", ts="2026-03-11T00:00:00+00:00", status="starting"),
    ]
    resolver = RunSelectorResolver(executions)

    with pytest.raises(AgentNBException, match="Multiple active runs match"):
        resolver.resolve_execution_id(
            project_root=project_dir,
            reference=parse_run_reference("@active"),
        )


def test_run_selector_resolver_rejects_missing_active_default(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    executions.list_run_selector_candidates.return_value = []
    resolver = RunSelectorResolver(executions)

    with pytest.raises(AgentNBException, match="No runs found for selector: @active"):
        resolver.resolve_execution_id(
            project_root=project_dir,
            reference=None,
            default_behavior="active",
        )


def test_run_selector_resolver_no_longer_depends_on_mapping_style_runs(project_dir) -> None:
    executions = Mock(spec=ExecutionService)
    executions.list_run_selector_candidates.return_value = [
        _candidate(execution_id="run-typed", ts="2026-03-12T00:00:00+00:00", status="running")
    ]
    resolver = RunSelectorResolver(executions)

    execution_id = resolver.resolve_execution_id(
        project_root=project_dir,
        reference=parse_run_reference("@active"),
    )

    assert execution_id == "run-typed"


def test_history_selector_resolver_uses_plain_query_without_reference() -> None:
    resolver = HistorySelectorResolver()

    query = resolver.resolve_query(
        session_id="analysis",
        include_internal=True,
        errors_only=False,
        success_only=False,
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
        success_only=False,
        latest=False,
        last=None,
        reference=reference,
    )

    assert query == expected


def test_history_selector_resolver_allows_equivalent_selector_flag_combinations() -> None:
    resolver = HistorySelectorResolver()

    latest_query = resolver.resolve_query(
        session_id="default",
        include_internal=False,
        errors_only=False,
        success_only=False,
        latest=True,
        last=None,
        reference=parse_history_reference("@latest"),
    )
    error_query = resolver.resolve_query(
        session_id="default",
        include_internal=False,
        errors_only=True,
        success_only=False,
        latest=True,
        last=None,
        reference=parse_history_reference("@last-error"),
    )
    success_query = resolver.resolve_query(
        session_id="default",
        include_internal=False,
        errors_only=False,
        success_only=True,
        latest=True,
        last=None,
        reference=parse_history_reference("@last-success"),
    )

    assert latest_query == JournalQuery(
        session_id="default",
        include_internal=False,
        latest=True,
    )
    assert error_query == JournalQuery(
        session_id="default",
        include_internal=False,
        errors_only=True,
        latest=True,
        prefer_execution_errors=True,
    )
    assert success_query == JournalQuery(
        session_id="default",
        include_internal=False,
        success_only=True,
        latest=True,
    )


@pytest.mark.parametrize(
    ("reference", "errors_only", "success_only", "latest", "last", "message"),
    [
        (
            parse_history_reference("@latest"),
            True,
            False,
            False,
            None,
            "History selectors can only be combined with equivalent",
        ),
        (
            parse_history_reference("@last-error"),
            False,
            True,
            True,
            None,
            "History selectors can only be combined with equivalent",
        ),
        (
            parse_history_reference("@last-success"),
            True,
            False,
            True,
            None,
            "History selectors can only be combined with equivalent",
        ),
        (
            parse_history_reference("run-123"),
            False,
            False,
            True,
            None,
            "Execution-id history references cannot be combined",
        ),
    ],
)
def test_history_selector_resolver_rejects_contradictory_selector_flag_combinations(
    reference: HistoryReference | None,
    errors_only: bool,
    success_only: bool,
    latest: bool,
    last: int | None,
    message: str,
) -> None:
    resolver = HistorySelectorResolver()

    with pytest.raises(ValueError, match=message):
        resolver.resolve_query(
            session_id="default",
            include_internal=False,
            errors_only=errors_only,
            success_only=success_only,
            latest=latest,
            last=last,
            reference=reference,
        )
