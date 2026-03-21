from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from .contracts import ExecutionResult
from .errors import AgentNBException
from .introspection import HelperExecutionPolicy, KernelHelperResult, KernelIntrospection
from .payloads import InspectPayload, ReloadReport, VarEntry
from .runtime import KernelRuntime
from .session import DEFAULT_SESSION_ID


class NotebookOps:
    def __init__(
        self,
        runtime: KernelRuntime,
        introspection: KernelIntrospection | None = None,
    ) -> None:
        self.runtime = runtime
        self.introspection = introspection or KernelIntrospection(runtime)
        self._registry: dict[str, Callable[..., Any]] = {
            "vars": self.list_vars,
            "inspect": self.inspect_var,
            "reload": self.reload_module,
        }

    def run(self, op_name: str, *args: Any, **kwargs: Any) -> Any:
        operation = self._registry.get(op_name)
        if operation is None:
            raise AgentNBException(
                code="UNKNOWN_OPERATION", message=f"Unknown operation: {op_name}"
            )
        return operation(*args, **kwargs)

    def list_vars(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
        execution_policy: HelperExecutionPolicy | None = None,
    ) -> list[VarEntry]:
        return self.introspection.list_vars(
            project_root=project_root,
            session_id=session_id,
            timeout_s=timeout_s,
            execution_policy=execution_policy,
        )

    def list_vars_result(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
        execution_policy: HelperExecutionPolicy | None = None,
    ) -> KernelHelperResult[list[VarEntry]]:
        if getattr(self.list_vars, "__func__", None) is NotebookOps.list_vars:
            return self.introspection.list_vars_result(
                project_root=project_root,
                session_id=session_id,
                timeout_s=timeout_s,
                execution_policy=execution_policy,
            )
        result = self.list_vars(
            project_root=project_root,
            session_id=session_id,
            timeout_s=timeout_s,
            execution_policy=execution_policy,
        )
        if isinstance(result, KernelHelperResult):
            return result
        return KernelHelperResult(
            execution=ExecutionResult(status="ok"),
            payload=result,
        )

    def inspect_var(
        self,
        project_root: Path,
        name: str,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
        execution_policy: HelperExecutionPolicy | None = None,
    ) -> InspectPayload:
        return self.introspection.inspect_var(
            project_root=project_root,
            name=name,
            session_id=session_id,
            timeout_s=timeout_s,
            execution_policy=execution_policy,
        )

    def inspect_var_result(
        self,
        project_root: Path,
        name: str,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
        execution_policy: HelperExecutionPolicy | None = None,
    ) -> KernelHelperResult[InspectPayload]:
        if getattr(self.inspect_var, "__func__", None) is NotebookOps.inspect_var:
            return self.introspection.inspect_var_result(
                project_root=project_root,
                name=name,
                session_id=session_id,
                timeout_s=timeout_s,
                execution_policy=execution_policy,
            )
        result = self.inspect_var(
            project_root=project_root,
            name=name,
            session_id=session_id,
            timeout_s=timeout_s,
            execution_policy=execution_policy,
        )
        if isinstance(result, KernelHelperResult):
            return result
        return KernelHelperResult(
            execution=ExecutionResult(status="ok"),
            payload=result,
        )

    def reload_module(
        self,
        project_root: Path,
        module_name: str | None = None,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
        execution_policy: HelperExecutionPolicy | None = None,
    ) -> ReloadReport:
        return self.introspection.reload_module(
            project_root=project_root,
            module_name=module_name,
            session_id=session_id,
            timeout_s=timeout_s,
            execution_policy=execution_policy,
        )

    def reload_module_result(
        self,
        project_root: Path,
        module_name: str | None = None,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
        execution_policy: HelperExecutionPolicy | None = None,
    ) -> KernelHelperResult[ReloadReport]:
        if getattr(self.reload_module, "__func__", None) is NotebookOps.reload_module:
            return self.introspection.reload_module_result(
                project_root=project_root,
                module_name=module_name,
                session_id=session_id,
                timeout_s=timeout_s,
                execution_policy=execution_policy,
            )
        result = self.reload_module(
            project_root=project_root,
            module_name=module_name,
            session_id=session_id,
            timeout_s=timeout_s,
            execution_policy=execution_policy,
        )
        if isinstance(result, KernelHelperResult):
            return result
        return KernelHelperResult(
            execution=ExecutionResult(status="ok"),
            payload=result,
        )
