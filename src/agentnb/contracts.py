from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from .execution_output import OutputItem

SCHEMA_VERSION = "1.0"

ResponseStatus = Literal["ok", "error"]
EventKind = Literal["stdout", "stderr", "result", "display", "error", "status"]


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


class ExecutionSink(Protocol):
    def started(self, *, execution_id: str, session_id: str) -> None: ...

    def accept(self, event: ExecutionEvent) -> None: ...


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
    outputs: list[OutputItem] = field(default_factory=list)
    events: list[ExecutionEvent] = field(default_factory=list)

    def __post_init__(self) -> None:
        from .execution_output import (
            ExecutionOutput,
            compatibility_output,
            execution_output_from_events,
            execution_output_from_legacy_fields,
        )

        if self.outputs:
            output = ExecutionOutput(
                items=list(self.outputs),
                execution_count=self.execution_count,
            )
        elif self.events:
            output = execution_output_from_events(self.events, execution_count=self.execution_count)
        else:
            output = execution_output_from_legacy_fields(
                stdout=self.stdout,
                stderr=self.stderr,
                result=self.result,
                ename=self.ename,
                evalue=self.evalue,
                traceback=self.traceback,
                status=self.status,
                execution_count=self.execution_count,
            )

        if not self.outputs:
            self.outputs = list(output.items)
        if not self.events:
            self.events = output.to_events()

        projected = compatibility_output(output)
        self.stdout = projected.stdout
        self.stderr = projected.stderr
        self.result = projected.result

        explicit_error = (
            self.status == "error"
            or self.ename is not None
            or self.evalue is not None
            or self.traceback is not None
        )
        if projected.status == "error":
            self.status = projected.status
            self.ename = projected.ename
            self.evalue = projected.evalue
            self.traceback = projected.traceback
        elif explicit_error:
            self.status = "error"
        else:
            self.status = projected.status
            self.ename = projected.ename
            self.evalue = projected.evalue
            self.traceback = projected.traceback

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["events"] = [event.to_dict() for event in self.events]
        payload.pop("outputs", None)
        return payload


@dataclass(slots=True)
class KernelStatus:
    alive: bool
    pid: int | None = None
    connection_file: str | None = None
    started_at: str | None = None
    uptime_s: float | None = None
    python: str | None = None
    busy: bool | None = None

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
    data: Mapping[str, object] | None = None,
    suggestions: list[str] | None = None,
) -> CommandResponse:
    return CommandResponse(
        status="ok",
        command=command,
        project=project,
        session_id=session_id,
        data=dict(data) if data is not None else {},
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
    data: Mapping[str, object] | None = None,
    suggestions: list[str] | None = None,
) -> CommandResponse:
    return CommandResponse(
        status="error",
        command=command,
        project=project,
        session_id=session_id,
        data=dict(data) if data is not None else {},
        suggestions=suggestions or [],
        error=AgentNBError(
            code=code,
            message=message,
            ename=ename,
            evalue=evalue,
            traceback=traceback,
        ),
    )
