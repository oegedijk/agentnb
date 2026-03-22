from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from .errors import AgentNBException
from .execution import ExecutionService
from .journal import JournalQuery

RunReferenceKind = Literal["execution_id", "latest", "active", "last_error", "last_success"]
HistoryReferenceKind = Literal["execution_id", "latest", "last_error", "last_success"]
RunDefaultBehavior = Literal["latest", "active"]
_ACTIVE_RUN_STATUSES = frozenset({"starting", "running"})


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


def parse_run_reference(value: str | None) -> RunReference | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    if normalized == "@latest":
        return RunReference(kind="latest", value=None, raw=normalized)
    if normalized == "@active":
        return RunReference(kind="active", value=None, raw=normalized)
    if normalized == "@last-error":
        return RunReference(kind="last_error", value=None, raw=normalized)
    if normalized == "@last-success":
        return RunReference(kind="last_success", value=None, raw=normalized)
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
    if normalized == "@last-success":
        return HistoryReference(kind="last_success", value=None, raw=normalized)
    return HistoryReference(kind="execution_id", value=normalized, raw=normalized)


class RunSelectorResolver:
    def __init__(self, executions: ExecutionService) -> None:
        self._executions = executions

    def resolve_execution_id(
        self,
        *,
        project_root: Path,
        reference: RunReference | None,
        current_session_id: str | None = None,
        default_behavior: RunDefaultBehavior = "latest",
    ) -> str:
        if reference is None:
            if default_behavior == "active":
                return self._resolve_active_run(
                    project_root=project_root,
                    current_session_id=current_session_id,
                    raw="@active",
                )
            return self._resolve_latest_run(
                project_root=project_root,
                current_session_id=current_session_id,
                raw="@latest",
            )

        if reference.kind == "execution_id":
            assert reference.value is not None
            return reference.value

        if reference.kind == "latest":
            return self._resolve_latest_run(
                project_root=project_root,
                current_session_id=current_session_id,
                raw=reference.raw,
            )
        if reference.kind == "last_error":
            return self._resolve_matching_run(
                project_root=project_root,
                current_session_id=current_session_id,
                raw=reference.raw,
                predicate=lambda run: run.get("status") == "error",
            )
        if reference.kind == "last_success":
            return self._resolve_matching_run(
                project_root=project_root,
                current_session_id=current_session_id,
                raw=reference.raw,
                predicate=lambda run: run.get("status") == "ok",
            )
        return self._resolve_active_run(
            project_root=project_root,
            current_session_id=current_session_id,
            raw=reference.raw,
        )

    def _resolve_latest_run(
        self,
        *,
        project_root: Path,
        current_session_id: str | None,
        raw: str,
    ) -> str:
        return self._resolve_matching_run(
            project_root=project_root,
            current_session_id=current_session_id,
            raw=raw,
            predicate=lambda _: True,
        )

    def _resolve_matching_run(
        self,
        *,
        project_root: Path,
        current_session_id: str | None,
        raw: str,
        predicate,
    ) -> str:
        if current_session_id is not None:
            preferred = self._matching_runs(
                project_root=project_root,
                session_id=current_session_id,
                predicate=predicate,
            )
            if preferred:
                return _require_execution_id(_latest_run(preferred), raw=raw)

        runs = self._matching_runs(project_root=project_root, session_id=None, predicate=predicate)
        return _require_execution_id(_latest_run(runs), raw=raw)

    def _resolve_active_run(
        self,
        *,
        project_root: Path,
        current_session_id: str | None,
        raw: str,
    ) -> str:
        if current_session_id is not None:
            preferred = self._matching_runs(
                project_root=project_root,
                session_id=current_session_id,
                predicate=_is_active_run,
            )
            if preferred:
                return _require_execution_id(_latest_run(preferred), raw=raw)

        active_runs = self._matching_runs(
            project_root=project_root,
            session_id=None,
            predicate=_is_active_run,
        )
        if not active_runs:
            raise AgentNBException(
                code="EXECUTION_NOT_FOUND",
                message=f"No runs found for selector: {raw}",
            )
        if len(active_runs) > 1:
            execution_ids = [
                str(run.get("execution_id"))
                for run in active_runs
                if isinstance(run.get("execution_id"), str)
            ]
            raise AgentNBException(
                code="AMBIGUOUS_EXECUTION",
                message="Multiple active runs match; pass an execution_id explicitly.",
                data={"execution_ids": execution_ids},
            )
        return _require_execution_id(active_runs[0], raw=raw)

    def _matching_runs(
        self,
        *,
        project_root: Path,
        session_id: str | None,
        predicate,
    ) -> list[Mapping[str, object]]:
        runs = cast(
            list[Mapping[str, object]],
            self._executions.list_runs(project_root=project_root, session_id=session_id),
        )
        return [run for run in runs if predicate(run)]


class HistorySelectorResolver:
    def resolve_query(
        self,
        *,
        session_id: str | None,
        include_internal: bool,
        errors_only: bool,
        success_only: bool,
        latest: bool,
        last: int | None,
        reference: HistoryReference | None,
    ) -> JournalQuery:
        if reference is None:
            return JournalQuery(
                session_id=session_id,
                include_internal=include_internal,
                errors_only=errors_only,
                success_only=success_only,
                latest=latest,
                last=last,
            )

        if reference.kind == "execution_id":
            if errors_only or success_only or latest or last is not None:
                raise ValueError(
                    "Execution-id history references cannot be combined with "
                    "--errors/--successes/--latest/--last filters."
                )
            assert reference.value is not None
            return JournalQuery(
                session_id=session_id,
                include_internal=include_internal,
                execution_id=reference.value,
            )
        if reference.kind == "latest":
            if errors_only or success_only or last is not None:
                raise ValueError(
                    "History selectors can only be combined with equivalent "
                    "--errors/--successes/--latest filters."
                )
            return JournalQuery(
                session_id=session_id,
                include_internal=include_internal,
                latest=True,
            )
        if reference.kind == "last_error":
            if success_only or last is not None:
                raise ValueError(
                    "History selectors can only be combined with equivalent "
                    "--errors/--successes/--latest filters."
                )
            return JournalQuery(
                session_id=session_id,
                include_internal=include_internal,
                errors_only=True,
                latest=True,
                prefer_execution_errors=True,
            )
        if errors_only or last is not None:
            raise ValueError(
                "History selectors can only be combined with equivalent "
                "--errors/--successes/--latest filters."
            )
        return JournalQuery(
            session_id=session_id,
            include_internal=include_internal,
            success_only=True,
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


def _require_execution_id(run: Mapping[str, object] | None, *, raw: str) -> str:
    if run is None:
        raise AgentNBException(
            code="EXECUTION_NOT_FOUND",
            message=f"No runs found for selector: {raw}",
        )
    execution_id = run.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        raise AgentNBException(
            code="EXECUTION_NOT_FOUND",
            message=f"No runs found for selector: {raw}",
        )
    return execution_id


def _is_active_run(run: Mapping[str, object]) -> bool:
    status = run.get("status")
    return isinstance(status, str) and status in _ACTIVE_RUN_STATUSES
