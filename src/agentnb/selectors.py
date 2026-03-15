from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from .errors import AgentNBException
from .execution import ExecutionService
from .journal import JournalQuery

RunReferenceKind = Literal["execution_id", "latest"]
HistoryReferenceKind = Literal["execution_id", "latest", "last_error"]


@dataclass(slots=True, frozen=True)
class RunReference:
    kind: RunReferenceKind
    value: str | None
    raw: str


@dataclass(slots=True, frozen=True)
class HistoryReference:
    kind: HistoryReferenceKind
    value: str | None
    raw: str


def parse_run_reference(value: str) -> RunReference:
    normalized = value.strip()
    if normalized == "@latest":
        return RunReference(kind="latest", value=None, raw=normalized)
    return RunReference(kind="execution_id", value=normalized, raw=normalized)


def parse_history_reference(value: str | None) -> HistoryReference | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized == "@latest":
        return HistoryReference(kind="latest", value=None, raw=normalized)
    if normalized == "@last-error":
        return HistoryReference(kind="last_error", value=None, raw=normalized)
    return HistoryReference(kind="execution_id", value=normalized, raw=normalized)


class RunSelectorResolver:
    def __init__(self, executions: ExecutionService) -> None:
        self._executions = executions

    def resolve_execution_id(self, *, project_root: Path, reference: RunReference) -> str:
        if reference.kind == "execution_id":
            assert reference.value is not None
            return reference.value

        runs = cast(
            list[Mapping[str, object]],
            self._executions.list_runs(project_root=project_root),
        )
        latest = _latest_run(runs)
        if latest is None:
            raise AgentNBException(
                code="EXECUTION_NOT_FOUND",
                message=f"No runs found for selector: {reference.raw}",
            )
        execution_id = latest.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id:
            raise AgentNBException(
                code="EXECUTION_NOT_FOUND",
                message=f"No runs found for selector: {reference.raw}",
            )
        return execution_id


class HistorySelectorResolver:
    def resolve_query(
        self,
        *,
        session_id: str | None,
        include_internal: bool,
        errors_only: bool,
        latest: bool,
        last: int | None,
        reference: HistoryReference | None,
    ) -> JournalQuery:
        if reference is None:
            return JournalQuery(
                session_id=session_id,
                include_internal=include_internal,
                errors_only=errors_only,
                latest=latest,
                last=last,
            )

        if errors_only or latest or last is not None:
            raise ValueError(
                "Use either a history selector or --errors/--latest/--last filters, not both."
            )

        if reference.kind == "execution_id":
            assert reference.value is not None
            return JournalQuery(
                session_id=session_id,
                include_internal=include_internal,
                execution_id=reference.value,
            )
        if reference.kind == "latest":
            return JournalQuery(
                session_id=session_id,
                include_internal=include_internal,
                latest=True,
            )
        return JournalQuery(
            session_id=session_id,
            include_internal=include_internal,
            errors_only=True,
            latest=True,
        )


def _latest_run(runs: list[Mapping[str, object]]) -> Mapping[str, object] | None:
    if not runs:
        return None
    indexed_runs = list(enumerate(runs))
    _, latest = max(
        indexed_runs,
        key=lambda item: (str(item[1].get("ts", "")), item[0]),
    )
    return latest
