from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Literal, cast

from .contracts import HelperAccessMetadata, HelperInitialRuntimeState, HelperWaitFor


@dataclass(slots=True, frozen=True)
class ErrorContext:
    helper_access: HelperAccessMetadata = field(default_factory=HelperAccessMetadata)
    session_id: str | None = None
    session_source: str | None = None
    runtime_state: str | None = None
    current_runtime_state: str | None = None
    session_exists: bool | None = None
    session_busy: bool | None = None
    interrupt_recommended: bool | None = None
    wait_behavior: Literal["immediate", "after_wait"] | None = None
    lock_pid: int | None = None
    lock_acquired_at: str | None = None
    busy_for_ms: int | None = None
    active_execution_id: str | None = None
    execution_id: str | None = None
    waiting_for: str | None = None
    timeout_s: float | None = None
    available_sessions: list[str] | None = None
    execution_ids: list[str] | None = None
    input_shape: str | None = None
    source_path: str | None = None
    extras: dict[str, object] = field(default_factory=dict)
    _include_helper_access: bool = field(default=False, repr=False)
    _include_null_fields: frozenset[str] = field(default_factory=frozenset, repr=False)

    def to_data(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if self._include_helper_access or _has_helper_access_metadata(self.helper_access):
            if self.helper_access.started_new_session:
                payload["started_new_session"] = True
            if self.helper_access.waited:
                payload["waited"] = True
            if self.helper_access.waited_for is not None:
                payload["waited_for"] = self.helper_access.waited_for
            if self.helper_access.waited_ms or self._include_helper_access:
                payload["waited_ms"] = self.helper_access.waited_ms
            if self.helper_access.initial_runtime_state is not None:
                payload["initial_runtime_state"] = self.helper_access.initial_runtime_state
            if self.helper_access.blocking_execution_id is not None:
                payload["blocking_execution_id"] = self.helper_access.blocking_execution_id
        if self.session_id is not None:
            payload["session_id"] = self.session_id
        if self.session_source is not None:
            payload["session_source"] = self.session_source
        if self.runtime_state is not None:
            payload["runtime_state"] = self.runtime_state
        if self.current_runtime_state is not None:
            payload["current_runtime_state"] = self.current_runtime_state
        if self.session_exists is not None:
            payload["session_exists"] = self.session_exists
        if self.session_busy is not None:
            payload["session_busy"] = self.session_busy
        if self.interrupt_recommended is not None:
            payload["interrupt_recommended"] = self.interrupt_recommended
        if self.wait_behavior is not None:
            payload["wait_behavior"] = self.wait_behavior
        if self.lock_pid is not None:
            payload["lock_pid"] = self.lock_pid
        if self.lock_acquired_at is not None:
            payload["lock_acquired_at"] = self.lock_acquired_at
        if self.busy_for_ms is not None:
            payload["busy_for_ms"] = self.busy_for_ms
        if (
            self.active_execution_id is not None
            or "active_execution_id" in self._include_null_fields
        ):
            payload["active_execution_id"] = self.active_execution_id
        if self.execution_id is not None:
            payload["execution_id"] = self.execution_id
        if self.waiting_for is not None:
            payload["waiting_for"] = self.waiting_for
        if self.timeout_s is not None:
            payload["timeout_s"] = self.timeout_s
        if self.available_sessions is not None:
            payload["available_sessions"] = list(self.available_sessions)
        if self.execution_ids is not None:
            payload["execution_ids"] = list(self.execution_ids)
        if self.input_shape is not None:
            payload["input_shape"] = self.input_shape
        if self.source_path is not None:
            payload["source_path"] = self.source_path
        payload.update(self.extras)
        return payload

    def merge(self, other: ErrorContext | None) -> ErrorContext:
        if other is None:
            return self
        known_fields = (
            "session_id",
            "session_source",
            "runtime_state",
            "current_runtime_state",
            "session_exists",
            "session_busy",
            "interrupt_recommended",
            "wait_behavior",
            "lock_pid",
            "lock_acquired_at",
            "busy_for_ms",
            "active_execution_id",
            "execution_id",
            "waiting_for",
            "timeout_s",
            "available_sessions",
            "execution_ids",
            "input_shape",
            "source_path",
        )
        changes: dict[str, object] = {
            "helper_access": _merge_helper_access(self.helper_access, other.helper_access),
            "extras": {**self.extras, **other.extras},
            "_include_helper_access": self._include_helper_access or other._include_helper_access,
            "_include_null_fields": self._include_null_fields | other._include_null_fields,
        }
        for name in known_fields:
            value = getattr(other, name)
            if value is not None:
                changes[name] = value
            elif name in other._include_null_fields:
                changes[name] = None
        return replace(self, **changes)

    def with_helper_access(self, access: HelperAccessMetadata) -> ErrorContext:
        return replace(
            self,
            helper_access=_merge_helper_access(self.helper_access, access),
            _include_helper_access=True,
        )

    @classmethod
    def from_data(cls, data: Mapping[str, object] | None = None) -> ErrorContext:
        if data is None:
            return cls()
        payload = dict(data)
        include_null_fields = frozenset(
            key for key in ("active_execution_id",) if key in payload and payload[key] is None
        )
        helper_keys = {
            "started_new_session",
            "waited",
            "waited_for",
            "waited_ms",
            "initial_runtime_state",
            "blocking_execution_id",
        }
        helper_access = HelperAccessMetadata(
            started_new_session=payload.get("started_new_session") is True,
            waited=payload.get("waited") is True,
            waited_for=_helper_wait_for(payload.get("waited_for")),
            waited_ms=_int_value(payload.get("waited_ms")) or 0,
            initial_runtime_state=_initial_runtime_state(payload.get("initial_runtime_state")),
            blocking_execution_id=_str_value(payload.get("blocking_execution_id")),
        )
        consumed = {
            "started_new_session",
            "waited",
            "waited_for",
            "waited_ms",
            "initial_runtime_state",
            "blocking_execution_id",
            "session_id",
            "session_source",
            "runtime_state",
            "current_runtime_state",
            "session_exists",
            "session_busy",
            "interrupt_recommended",
            "wait_behavior",
            "lock_pid",
            "lock_acquired_at",
            "busy_for_ms",
            "active_execution_id",
            "execution_id",
            "waiting_for",
            "timeout_s",
            "available_sessions",
            "execution_ids",
            "input_shape",
            "source_path",
        }
        return cls(
            helper_access=helper_access,
            session_id=_str_value(payload.get("session_id")),
            session_source=_str_value(payload.get("session_source")),
            runtime_state=_str_value(payload.get("runtime_state")),
            current_runtime_state=_str_value(payload.get("current_runtime_state")),
            session_exists=_bool_value(payload.get("session_exists")),
            session_busy=_bool_value(payload.get("session_busy")),
            interrupt_recommended=_bool_value(payload.get("interrupt_recommended")),
            wait_behavior=_wait_behavior(payload.get("wait_behavior")),
            lock_pid=_int_value(payload.get("lock_pid")),
            lock_acquired_at=_str_value(payload.get("lock_acquired_at")),
            busy_for_ms=_int_value(payload.get("busy_for_ms")),
            active_execution_id=_str_value(payload.get("active_execution_id")),
            execution_id=_str_value(payload.get("execution_id")),
            waiting_for=_str_value(payload.get("waiting_for")),
            timeout_s=_float_value(payload.get("timeout_s")),
            available_sessions=_str_list(payload.get("available_sessions")),
            execution_ids=_str_list(payload.get("execution_ids")),
            input_shape=_str_value(payload.get("input_shape")),
            source_path=_str_value(payload.get("source_path")),
            extras={key: value for key, value in payload.items() if key not in consumed},
            _include_helper_access=any(key in payload for key in helper_keys),
            _include_null_fields=include_null_fields,
        )


class AgentNBException(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        ename: str | None = None,
        evalue: str | None = None,
        traceback: list[str] | None = None,
        data: dict[str, object] | None = None,
        error_context: ErrorContext | None = None,
        command_data: object | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.ename = ename
        self.evalue = evalue
        self.traceback = traceback
        self.error_context = ErrorContext.from_data(data).merge(error_context)
        self.command_data = command_data

    @property
    def data(self) -> dict[str, object]:
        return self.error_context.to_data()

    @data.setter
    def data(self, value: Mapping[str, object] | None) -> None:
        self.error_context = ErrorContext.from_data(value)


class NoKernelRunningError(AgentNBException):
    def __init__(self) -> None:
        super().__init__(
            code="NO_KERNEL",
            message="No kernel running. Start one with: agentnb start",
        )


class KernelNotReadyError(AgentNBException):
    def __init__(self) -> None:
        super().__init__(
            code="KERNEL_NOT_READY",
            message=("Kernel startup is still in progress or not yet ready. Wait and retry."),
        )


class KernelDiedError(AgentNBException):
    def __init__(self, message: str | None = None) -> None:
        super().__init__(
            code="KERNEL_DEAD",
            message=message or "Kernel died. Restart the session with: agentnb start",
        )


class SessionBusyError(AgentNBException):
    def __init__(
        self,
        *,
        wait_behavior: Literal["immediate", "after_wait"] = "immediate",
        waited_ms: int = 0,
        lock_pid: int | None = None,
        lock_acquired_at: str | None = None,
        busy_for_ms: int | None = None,
        active_execution_id: str | None = None,
    ) -> None:
        super().__init__(
            code="SESSION_BUSY",
            message=(
                "Another agentnb command is already using this session. "
                "Wait for the prior command to finish, then retry. "
                "Use one command at a time per project session."
            ),
            error_context=ErrorContext(
                wait_behavior=wait_behavior,
                lock_pid=lock_pid,
                lock_acquired_at=lock_acquired_at,
                busy_for_ms=max(busy_for_ms, 0) if busy_for_ms is not None else None,
                active_execution_id=active_execution_id,
                helper_access=HelperAccessMetadata(waited_ms=max(waited_ms, 0)),
                _include_helper_access=True,
            ),
        )


class InvalidInputError(AgentNBException):
    def __init__(self, message: str, *, data: dict[str, object] | None = None) -> None:
        super().__init__(code="INVALID_INPUT", message=message, data=data)


class SessionNotFoundError(AgentNBException):
    def __init__(self, session_id: str) -> None:
        super().__init__(
            code="SESSION_NOT_FOUND",
            message=f"Session not found: {session_id}",
        )


class AmbiguousSessionError(AgentNBException):
    def __init__(self, session_ids: list[str]) -> None:
        super().__init__(
            code="AMBIGUOUS_SESSION",
            message="Multiple live sessions exist; pass --session to select one explicitly.",
            error_context=ErrorContext(available_sessions=list(session_ids)),
        )


class KernelWaitTimedOutError(AgentNBException):
    def __init__(self, timeout_s: float, *, waiting_for: str = "ready") -> None:
        super().__init__(
            code="TIMEOUT",
            message=f"Kernel did not become {waiting_for} within {timeout_s:g}s.",
            ename="TimeoutError",
            evalue=f"Kernel {waiting_for} wait exceeded timeout of {timeout_s:g}s",
            error_context=ErrorContext(timeout_s=timeout_s, waiting_for=waiting_for),
        )


class RunWaitTimedOutError(AgentNBException):
    def __init__(self, timeout_s: float) -> None:
        super().__init__(
            code="TIMEOUT",
            message=f"Run did not finish within {timeout_s:g}s.",
            ename="TimeoutError",
            evalue=f"Run wait exceeded timeout of {timeout_s:g}s",
            error_context=ErrorContext(timeout_s=timeout_s),
        )


class ExecutionTimedOutError(AgentNBException):
    def __init__(
        self,
        timeout_s: float,
        *,
        duration_ms: int = 0,
        data: dict[str, object] | None = None,
        error_context: ErrorContext | None = None,
    ) -> None:
        super().__init__(
            code="TIMEOUT",
            message=f"Execution timed out after {timeout_s:g}s.",
            ename="TimeoutError",
            evalue=f"Execution exceeded timeout of {timeout_s:g}s",
            error_context=ErrorContext.from_data(data).merge(error_context),
        )
        self.duration_ms = duration_ms


class BackendOperationError(AgentNBException):
    def __init__(self, message: str) -> None:
        super().__init__(code="BACKEND_ERROR", message=message)


class ProvisioningError(AgentNBException):
    def __init__(self, message: str, *, recovery_command: str | None = None) -> None:
        super().__init__(code="PROVISIONING_ERROR", message=message)
        self.recovery_command = recovery_command


class StateCompatibilityError(AgentNBException):
    def __init__(self, message: str, *, data: dict[str, object] | None = None) -> None:
        super().__init__(
            code="STATE_SCHEMA_INCOMPATIBLE",
            message=message,
            data=data,
        )


def _bool_value(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _float_value(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _helper_wait_for(value: object) -> HelperWaitFor | None:
    if value in {"ready", "idle"}:
        return cast(HelperWaitFor, value)
    return None


def _initial_runtime_state(value: object) -> HelperInitialRuntimeState | None:
    if value in {"missing", "starting", "ready", "busy", "dead", "stale"}:
        return cast(HelperInitialRuntimeState, value)
    return None


def _int_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    return None


def _merge_helper_access(
    previous: HelperAccessMetadata,
    current: HelperAccessMetadata,
) -> HelperAccessMetadata:
    waited = previous.waited or current.waited
    waited_for = current.waited_for or previous.waited_for
    initial_runtime_state = previous.initial_runtime_state or current.initial_runtime_state
    return HelperAccessMetadata(
        started_new_session=previous.started_new_session or current.started_new_session,
        waited=waited,
        waited_for=waited_for,
        waited_ms=previous.waited_ms + current.waited_ms,
        initial_runtime_state=initial_runtime_state,
        blocking_execution_id=previous.blocking_execution_id or current.blocking_execution_id,
    )


def _str_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    return [str(item) for item in value]


def _str_value(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _wait_behavior(value: object) -> Literal["immediate", "after_wait"] | None:
    if value in {"immediate", "after_wait"}:
        return cast(Literal["immediate", "after_wait"], value)
    return None


def _has_helper_access_metadata(access: HelperAccessMetadata) -> bool:
    return bool(
        access.started_new_session
        or access.waited
        or access.waited_for is not None
        or access.waited_ms
        or access.initial_runtime_state is not None
        or access.blocking_execution_id is not None
    )
