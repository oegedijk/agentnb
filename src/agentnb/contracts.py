from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

SCHEMA_VERSION = "1.0"

ResponseStatus = Literal["ok", "error"]
EventKind = Literal["stdout", "stderr", "result", "error", "status"]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class AgentNBError:
    code: str
    message: str
    ename: str | None = None
    evalue: str | None = None
    traceback: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "ename": self.ename,
            "evalue": self.evalue,
            "traceback": self.traceback,
        }
        return payload


@dataclass(slots=True)
class ExecutionEvent:
    kind: EventKind
    content: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ExecutionResult:
    status: ResponseStatus
    stdout: str = ""
    stderr: str = ""
    result: str | None = None
    execution_count: int | None = None
    duration_ms: int = 0
    ename: str | None = None
    evalue: str | None = None
    traceback: list[str] | None = None
    events: list[ExecutionEvent] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["events"] = [event.to_dict() for event in self.events]
        return payload


@dataclass(slots=True)
class KernelStatus:
    alive: bool
    pid: int | None = None
    connection_file: str | None = None
    started_at: str | None = None
    uptime_s: float | None = None
    python: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class CommandResponse:
    status: ResponseStatus
    command: str
    project: str
    session_id: str
    data: dict[str, Any] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    error: AgentNBError | None = None
    schema_version: str = SCHEMA_VERSION
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "command": self.command,
            "project": self.project,
            "session_id": self.session_id,
            "timestamp": self.timestamp,
            "data": self.data,
            "suggestions": self.suggestions,
            "error": self.error.to_dict() if self.error else None,
        }


def success_response(
    *,
    command: str,
    project: str,
    session_id: str,
    data: dict[str, Any] | None = None,
    suggestions: list[str] | None = None,
) -> CommandResponse:
    return CommandResponse(
        status="ok",
        command=command,
        project=project,
        session_id=session_id,
        data=data or {},
        suggestions=suggestions or [],
    )


def error_response(
    *,
    command: str,
    project: str,
    session_id: str,
    code: str,
    message: str,
    ename: str | None = None,
    evalue: str | None = None,
    traceback: list[str] | None = None,
    data: dict[str, Any] | None = None,
    suggestions: list[str] | None = None,
) -> CommandResponse:
    return CommandResponse(
        status="error",
        command=command,
        project=project,
        session_id=session_id,
        data=data or {},
        suggestions=suggestions or [],
        error=AgentNBError(
            code=code,
            message=message,
            ename=ename,
            evalue=evalue,
            traceback=traceback,
        ),
    )
