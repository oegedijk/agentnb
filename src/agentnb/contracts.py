from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol, TypedDict

if TYPE_CHECKING:
    from .execution_models import ExecutionOutcome
    from .execution_output import OutputItem

SCHEMA_VERSION = "1.0"

ResponseStatus = Literal["ok", "error"]
EventKind = Literal["stdout", "stderr", "result", "display", "error", "status"]
HelperWaitFor = Literal["ready", "idle"]
HelperInitialRuntimeState = Literal["missing", "starting", "ready", "busy", "dead", "stale"]


class SuggestionAction(TypedDict, total=False):
    kind: str
    command: str
    label: str
    args: list[str]


def utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class AgentNBError:
    code: str
    message: str
    ename: str | None = None
    evalue: str | None = None
    traceback: list[str] | None = None

    def __post_init__(self) -> None:
        from .compact import compact_traceback

        self.traceback = compact_traceback(self.traceback)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "ename": self.ename,
            "evalue": self.evalue,
            "traceback": self.traceback,
        }
        return payload

    def to_agent_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
        }
        if self.ename is not None:
            payload["ename"] = self.ename
        if self.evalue is not None:
            payload["evalue"] = self.evalue
        if self.traceback:
            payload["traceback"] = list(self.traceback)
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
    _outcome: ExecutionOutcome = field(init=False, repr=False)

    def __post_init__(self) -> None:
        from .execution_models import ExecutionOutcome

        outcome = ExecutionOutcome.from_execution_result(self)
        self._outcome = outcome
        self.outputs = list(outcome.outputs)
        self.events = list(outcome.events)
        self.stdout = outcome.stdout
        self.stderr = outcome.stderr
        self.result = outcome.result
        self.status = outcome.status
        self.ename = outcome.ename
        self.evalue = outcome.evalue
        self.traceback = outcome.traceback
        self.execution_count = outcome.execution_count

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("_outcome", None)
        payload["events"] = [event.to_dict() for event in self.events]
        payload.pop("outputs", None)
        return payload

    def to_outcome(self) -> ExecutionOutcome:
        from .execution_models import ExecutionOutcome

        assert isinstance(self._outcome, ExecutionOutcome)
        return self._outcome


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


@dataclass(slots=True, frozen=True)
class HelperAccessMetadata:
    started_new_session: bool = False
    waited: bool = False
    waited_for: HelperWaitFor | None = None
    waited_ms: int = 0
    initial_runtime_state: HelperInitialRuntimeState | None = None
    blocking_execution_id: str | None = None

    def with_updates(self, **changes: object) -> HelperAccessMetadata:
        return replace(self, **changes)

    def merge_data(self, data: Mapping[str, object] | None = None) -> dict[str, object]:
        payload = dict(data) if data is not None else {}
        if self.started_new_session:
            payload["started_new_session"] = True
        payload["waited"] = self.waited
        payload["waited_ms"] = self.waited_ms
        if self.waited_for is not None:
            payload["waited_for"] = self.waited_for
        if self.initial_runtime_state is not None:
            payload["initial_runtime_state"] = self.initial_runtime_state
        if self.blocking_execution_id is not None:
            payload["blocking_execution_id"] = self.blocking_execution_id
        return payload


@dataclass(slots=True)
class CommandResponse:
    status: ResponseStatus
    command: str
    project: str
    session_id: str
    data: dict[str, Any] = field(default_factory=dict)
    suggestions: list[str] = field(default_factory=list)
    suggestion_actions: list[SuggestionAction] = field(default_factory=list)
    error: AgentNBError | None = None
    schema_version: str = SCHEMA_VERSION
    timestamp: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
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
        if self.suggestion_actions:
            payload["suggestion_actions"] = self.suggestion_actions
        return payload


def success_response(
    *,
    command: str,
    project: str,
    session_id: str,
    data: Mapping[str, object] | None = None,
    suggestions: list[str] | None = None,
    suggestion_actions: list[SuggestionAction] | None = None,
) -> CommandResponse:
    return CommandResponse(
        status="ok",
        command=command,
        project=project,
        session_id=session_id,
        data=dict(data) if data is not None else {},
        suggestions=suggestions or [],
        suggestion_actions=suggestion_actions or [],
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
    suggestion_actions: list[SuggestionAction] | None = None,
) -> CommandResponse:
    return CommandResponse(
        status="error",
        command=command,
        project=project,
        session_id=session_id,
        data=dict(data) if data is not None else {},
        suggestions=suggestions or [],
        suggestion_actions=suggestion_actions or [],
        error=AgentNBError(
            code=code,
            message=message,
            ename=ename,
            evalue=evalue,
            traceback=traceback,
        ),
    )
