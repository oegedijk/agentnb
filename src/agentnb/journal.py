from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .execution import ExecutionStore
from .history import (
    FailureOrigin,
    HistoryClassification,
    HistoryProvenanceDetail,
    HistoryRecord,
    HistoryStore,
)
from .session import DEFAULT_SESSION_ID

JournalProvenanceSource = Literal["history_store", "execution_store"]
JournalProvenanceDetail = HistoryProvenanceDetail


@dataclass(slots=True, frozen=True, kw_only=True)
class JournalQuery:
    session_id: str | None = DEFAULT_SESSION_ID
    include_internal: bool = False
    errors_only: bool = False
    success_only: bool = False
    latest: bool = False
    last: int | None = None
    replayable_only: bool = False
    execution_id: str | None = None
    prefer_execution_errors: bool = False

    def __post_init__(self) -> None:
        if self.errors_only and self.success_only:
            raise ValueError("Use either errors_only or success_only, not both.")
        if self.latest and self.last is not None:
            raise ValueError("Use either --latest or --last, not both.")
        if self.last is not None and self.last < 1:
            raise ValueError("--last must be at least 1.")


@dataclass(slots=True, frozen=True)
class JournalEntry:
    kind: str
    ts: str
    session_id: str
    execution_id: str | None
    status: str
    duration_ms: int
    command_type: str
    label: str
    user_visible: bool
    classification: HistoryClassification
    provenance_source: JournalProvenanceSource
    provenance_detail: JournalProvenanceDetail
    input: str | None = None
    code: str | None = None
    origin: str | None = None
    error_type: str | None = None
    failure_origin: FailureOrigin | None = None
    result_preview: str | None = None
    stdout_preview: str | None = None

    @property
    def replayable(self) -> bool:
        return self.classification == "replayable"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "ts": self.ts,
            "session_id": self.session_id,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "command_type": self.command_type,
            "label": self.label,
            "user_visible": self.user_visible,
            "classification": self.classification,
            "provenance_source": self.provenance_source,
            "provenance_detail": self.provenance_detail,
            **({"execution_id": self.execution_id} if self.execution_id is not None else {}),
            **({"input": self.input} if self.input is not None else {}),
            **({"code": self.code} if self.code is not None else {}),
            **({"origin": self.origin} if self.origin is not None else {}),
            **({"error_type": self.error_type} if self.error_type is not None else {}),
            **({"failure_origin": self.failure_origin} if self.failure_origin is not None else {}),
            **({"result_preview": self.result_preview} if self.result_preview is not None else {}),
            **({"stdout_preview": self.stdout_preview} if self.stdout_preview is not None else {}),
        }

    @classmethod
    def from_history_record(
        cls,
        record: HistoryRecord,
        *,
        provenance_source: JournalProvenanceSource = "history_store",
    ) -> JournalEntry:
        return cls(
            kind=record.kind,
            ts=record.ts,
            session_id=record.session_id,
            execution_id=record.execution_id,
            status=record.status,
            duration_ms=record.duration_ms,
            command_type=record.command_type,
            label=record.label,
            user_visible=record.user_visible,
            classification=record.classification,
            provenance_source=provenance_source,
            provenance_detail=record.provenance_detail,
            input=record.input,
            code=record.code,
            origin=record.origin,
            error_type=record.error_type,
            failure_origin=record.failure_origin,
            result_preview=record.result_preview,
            stdout_preview=record.stdout_preview,
        )


@dataclass(slots=True, frozen=True)
class JournalSelection:
    query: JournalQuery
    entries: list[JournalEntry]

    def to_dicts(self) -> list[dict[str, Any]]:
        return [entry.to_dict() for entry in self.entries]

    def latest_entry(self) -> JournalEntry | None:
        if not self.entries:
            return None
        return self.entries[-1]


class CommandJournal:
    def select(
        self,
        *,
        project_root: Path,
        query: JournalQuery,
    ) -> JournalSelection:
        history_entries = [
            JournalEntry.from_history_record(record)
            for record in HistoryStore(
                project_root=project_root,
                session_id=query.session_id,
            ).read(
                include_internal=True,
                errors_only=query.errors_only,
            )
        ]
        execution_entries = self._execution_entries(
            project_root=project_root,
            query=query,
        )
        entries = sorted([*history_entries, *execution_entries], key=lambda entry: entry.ts)
        entries = self._apply_query(entries, query)
        return JournalSelection(query=query, entries=entries)

    def entries(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        include_internal: bool = False,
        errors_only: bool = False,
    ) -> list[JournalEntry]:
        selection = self.select(
            project_root=project_root,
            query=JournalQuery(
                session_id=session_id,
                include_internal=include_internal,
                errors_only=errors_only,
            ),
        )
        return selection.entries

    def replayable_entries(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        errors_only: bool = False,
    ) -> list[JournalEntry]:
        selection = self.select(
            project_root=project_root,
            query=JournalQuery(
                session_id=session_id,
                errors_only=errors_only,
                replayable_only=True,
            ),
        )
        return selection.entries

    def last_activity(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> str | None:
        selection = self.select(
            project_root=project_root,
            query=JournalQuery(
                session_id=session_id,
                include_internal=True,
                latest=True,
            ),
        )
        latest = selection.latest_entry()
        if latest is None:
            return None
        return latest.ts

    def _execution_entries(
        self,
        *,
        project_root: Path,
        query: JournalQuery,
    ) -> list[JournalEntry]:
        records = ExecutionStore(project_root).read(
            session_id=query.session_id,
            command_types={"exec", "reset"},
            errors_only=query.errors_only,
        )
        entries: list[JournalEntry] = []
        for record in records:
            if record.status not in {"ok", "error"}:
                continue
            entries.extend(
                JournalEntry.from_history_record(entry, provenance_source="execution_store")
                for entry in record.journal_entries
            )
        return entries

    def _apply_query(
        self,
        entries: list[JournalEntry],
        query: JournalQuery,
    ) -> list[JournalEntry]:
        selected = entries
        if query.execution_id is not None:
            selected = [entry for entry in selected if entry.execution_id == query.execution_id]
        if not query.include_internal:
            selected = [entry for entry in selected if entry.classification != "internal"]
        if query.replayable_only:
            selected = [entry for entry in selected if entry.replayable]
        if query.success_only:
            selected = [entry for entry in selected if entry.status == "ok"]
        if query.prefer_execution_errors and query.errors_only:
            execution_errors = [entry for entry in selected if entry.failure_origin == "kernel"]
            if execution_errors:
                selected = execution_errors
        if query.latest:
            return selected[-1:]
        if query.last is not None:
            return selected[-query.last :]
        return selected
