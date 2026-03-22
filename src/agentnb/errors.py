from __future__ import annotations

from typing import Literal


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
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.ename = ename
        self.evalue = evalue
        self.traceback = traceback
        self.data = data or {}


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
        data: dict[str, object] = {
            "wait_behavior": wait_behavior,
            "waited_ms": max(waited_ms, 0),
        }
        if lock_pid is not None:
            data["lock_pid"] = lock_pid
        if lock_acquired_at is not None:
            data["lock_acquired_at"] = lock_acquired_at
        if busy_for_ms is not None:
            data["busy_for_ms"] = max(busy_for_ms, 0)
        if active_execution_id is not None:
            data["active_execution_id"] = active_execution_id
        super().__init__(
            code="SESSION_BUSY",
            message=(
                "Another agentnb command is already using this session. "
                "Wait for the prior command to finish, then retry. "
                "Use one command at a time per project session."
            ),
            data=data,
        )


class InvalidInputError(AgentNBException):
    def __init__(self, message: str) -> None:
        super().__init__(code="INVALID_INPUT", message=message)


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
            data={"available_sessions": session_ids},
        )


class KernelWaitTimedOutError(AgentNBException):
    def __init__(self, timeout_s: float, *, waiting_for: str = "ready") -> None:
        super().__init__(
            code="TIMEOUT",
            message=f"Kernel did not become {waiting_for} within {timeout_s:g}s.",
            ename="TimeoutError",
            evalue=f"Kernel {waiting_for} wait exceeded timeout of {timeout_s:g}s",
            data={"timeout_s": timeout_s, "waiting_for": waiting_for},
        )


class RunWaitTimedOutError(AgentNBException):
    def __init__(self, timeout_s: float) -> None:
        super().__init__(
            code="TIMEOUT",
            message=f"Run did not finish within {timeout_s:g}s.",
            ename="TimeoutError",
            evalue=f"Run wait exceeded timeout of {timeout_s:g}s",
            data={"timeout_s": timeout_s},
        )


class ExecutionTimedOutError(AgentNBException):
    def __init__(self, timeout_s: float, *, duration_ms: int = 0) -> None:
        super().__init__(
            code="TIMEOUT",
            message=(
                f"Execution timed out after {timeout_s:g}s. Use --timeout to increase, "
                "or run: agentnb interrupt"
            ),
            ename="TimeoutError",
            evalue=f"Execution exceeded timeout of {timeout_s:g}s",
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
