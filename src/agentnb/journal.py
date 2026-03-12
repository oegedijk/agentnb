from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .execution import ExecutionRecord, ExecutionStore
from .history import HistoryRecord, HistoryStore, kernel_execution_record, user_command_record
from .session import DEFAULT_SESSION_ID

_REPLAYABLE_COMMAND_TYPES = frozenset({"exec", "reset"})


@dataclass(slots=True)
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
    input: str | None = None
    code: str | None = None
    origin: str | None = None
    error_type: str | None = None
    result_preview: str | None = None
    stdout_preview: str | None = None

    @property
    def replayable(self) -> bool:
        return self.user_visible and self.command_type in _REPLAYABLE_COMMAND_TYPES

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
            **({"execution_id": self.execution_id} if self.execution_id is not None else {}),
            **({"input": self.input} if self.input is not None else {}),
            **({"code": self.code} if self.code is not None else {}),
            **({"origin": self.origin} if self.origin is not None else {}),
            **({"error_type": self.error_type} if self.error_type is not None else {}),
            **({"result_preview": self.result_preview} if self.result_preview is not None else {}),
            **({"stdout_preview": self.stdout_preview} if self.stdout_preview is not None else {}),
        }

    @classmethod
    def from_history_record(cls, record: HistoryRecord) -> JournalEntry:
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
            input=record.input,
            code=record.code,
            origin=record.origin,
            error_type=record.error_type,
            result_preview=record.result_preview,
            stdout_preview=record.stdout_preview,
        )


class CommandJournal:
    def entries(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        include_internal: bool = False,
        errors_only: bool = False,
    ) -> list[JournalEntry]:
        history_entries = [
            JournalEntry.from_history_record(record)
            for record in HistoryStore(project_root=project_root, session_id=session_id).read(
                include_internal=include_internal,
                errors_only=errors_only,
            )
        ]
        execution_entries = self._execution_entries(
            project_root=project_root,
            session_id=session_id,
            include_internal=include_internal,
            errors_only=errors_only,
        )
        return sorted(
            [*history_entries, *execution_entries],
            key=lambda entry: entry.ts,
        )

    def replayable_entries(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        errors_only: bool = False,
    ) -> list[JournalEntry]:
        return [
            entry
            for entry in self.entries(
                project_root=project_root,
                session_id=session_id,
                include_internal=False,
                errors_only=errors_only,
            )
            if entry.replayable
        ]

    def last_activity(
        self,
        *,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
    ) -> str | None:
        entries = self.entries(
            project_root=project_root,
            session_id=session_id,
            include_internal=True,
        )
        if not entries:
            return None
        return entries[-1].ts

    def _execution_entries(
        self,
        *,
        project_root: Path,
        session_id: str,
        include_internal: bool,
        errors_only: bool,
    ) -> list[JournalEntry]:
        records = ExecutionStore(project_root).read(
            session_id=session_id,
            command_types={"exec", "reset"},
            errors_only=errors_only,
        )
        entries: list[JournalEntry] = []
        for record in records:
            if record.status not in {"ok", "error"}:
                continue
            projections = self._project_execution_record(record)
            if include_internal:
                entries.extend(projections)
            else:
                entries.append(projections[-1])
        return entries

    def _project_execution_record(self, record: ExecutionRecord) -> list[JournalEntry]:
        label = "reset" if record.command_type == "reset" else "exec"
        helper_label = (
            "reset kernel state" if record.command_type == "reset" else "exec kernel execution"
        )
        return [
            JournalEntry.from_history_record(
                kernel_execution_record(
                    ts=record.ts,
                    session_id=record.session_id,
                    execution_id=record.execution_id,
                    command_type=record.command_type,
                    label=helper_label,
                    code=record.code,
                    origin="execution_service",
                    status=record.status,
                    duration_ms=record.duration_ms,
                    error_type=record.ename,
                    stdout=record.stdout,
                    result=record.result,
                )
            ),
            JournalEntry.from_history_record(
                user_command_record(
                    ts=record.ts,
                    session_id=record.session_id,
                    execution_id=record.execution_id,
                    command_type=record.command_type,
                    label=label,
                    input_text=record.code,
                    code=record.code,
                    origin="execution_service",
                    status=record.status,
                    duration_ms=record.duration_ms,
                    error_type=record.ename,
                    stdout=record.stdout,
                    result=record.result,
                )
            ),
        ]
