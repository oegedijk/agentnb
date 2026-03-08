from __future__ import annotations


class AgentNBException(Exception):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        ename: str | None = None,
        evalue: str | None = None,
        traceback: list[str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.ename = ename
        self.evalue = evalue
        self.traceback = traceback


class NoKernelRunningError(AgentNBException):
    def __init__(self) -> None:
        super().__init__(
            code="NO_KERNEL",
            message="No kernel running. Start one with: agentnb start",
        )


class InvalidInputError(AgentNBException):
    def __init__(self, message: str) -> None:
        super().__init__(code="INVALID_INPUT", message=message)


class ExecutionTimedOutError(AgentNBException):
    def __init__(self, timeout_s: float) -> None:
        super().__init__(
            code="TIMEOUT",
            message=(
                f"Execution timed out after {timeout_s:g}s. Use --timeout to increase, "
                "or run: agentnb interrupt"
            ),
            ename="TimeoutError",
            evalue=f"Execution exceeded timeout of {timeout_s:g}s",
        )


class BackendOperationError(AgentNBException):
    def __init__(self, message: str) -> None:
        super().__init__(code="BACKEND_ERROR", message=message)


class ProvisioningError(AgentNBException):
    def __init__(self, message: str) -> None:
        super().__init__(code="PROVISIONING_ERROR", message=message)
