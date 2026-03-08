from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import AgentNBException
from .runtime import KernelRuntime
from .session import DEFAULT_SESSION_ID


class NotebookOps:
    def __init__(self, runtime: KernelRuntime) -> None:
        self.runtime = runtime
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
    ) -> list[dict[str, Any]]:
        code = """
import json
import types
from IPython import get_ipython

_max_len = 160
_items = []
_user_ns = get_ipython().user_ns if get_ipython() is not None else globals()

for _name, _value in sorted(_user_ns.items()):
    if _name.startswith("_"):
        continue
    if isinstance(_value, types.ModuleType):
        continue
    _repr_text = repr(_value)
    if len(_repr_text) > _max_len:
        _repr_text = _repr_text[: _max_len - 3] + "..."
    _items.append({"name": _name, "type": type(_value).__name__, "repr": _repr_text})

print(json.dumps(_items))
"""
        return self._execute_json_payload(
            project_root=project_root,
            session_id=session_id,
            code=code,
            timeout_s=timeout_s,
            context="list vars",
        )

    def inspect_var(
        self,
        project_root: Path,
        name: str,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
    ) -> dict[str, Any]:
        escaped_name = json.dumps(name)
        code = f"""
import json
from IPython import get_ipython

_user_ns = get_ipython().user_ns if get_ipython() is not None else globals()
_name = {escaped_name}
if _name not in _user_ns:
    raise NameError(f"Variable '{{_name}}' is not defined")

_value = _user_ns[_name]
_members = [member for member in dir(_value) if not member.startswith("_")]
_repr_text = repr(_value)
if len(_repr_text) > 500:
    _repr_text = _repr_text[:497] + "..."
_doc = getattr(_value, "__doc__", None)
if _doc is None:
    _doc = ""
if len(_doc) > 1000:
    _doc = _doc[:997] + "..."

_payload = {{
    "name": _name,
    "type": type(_value).__name__,
    "repr": _repr_text,
    "members": _members[:200],
    "doc": _doc,
}}
print(json.dumps(_payload))
"""
        return self._execute_json_payload(
            project_root=project_root,
            session_id=session_id,
            code=code,
            timeout_s=timeout_s,
            context="inspect variable",
        )

    def reload_module(
        self,
        project_root: Path,
        module_name: str,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
    ) -> dict[str, Any]:
        escaped_module = json.dumps(module_name)
        code = f"""
import importlib
import json

_name = {escaped_module}
_module = importlib.import_module(_name)
_reloaded = importlib.reload(_module)
print(json.dumps({{"module": _reloaded.__name__}}))
"""
        return self._execute_json_payload(
            project_root=project_root,
            session_id=session_id,
            code=code,
            timeout_s=timeout_s,
            context="reload module",
        )

    def _execute_json_payload(
        self,
        project_root: Path,
        session_id: str,
        code: str,
        timeout_s: float,
        context: str,
    ) -> Any:
        execution = self.runtime.execute(
            project_root=project_root,
            session_id=session_id,
            code=code,
            timeout_s=timeout_s,
        )
        if execution.status == "error":
            raise AgentNBException(
                code="EXECUTION_ERROR",
                message=f"Failed to {context}",
                ename=execution.ename,
                evalue=execution.evalue,
                traceback=execution.traceback,
            )

        lines = [line.strip() for line in execution.stdout.splitlines() if line.strip()]
        if not lines:
            raise AgentNBException(
                code="PARSE_ERROR", message=f"No output while attempting to {context}"
            )

        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise AgentNBException(
                code="PARSE_ERROR",
                message=f"Unable to parse JSON payload while attempting to {context}",
                ename=type(exc).__name__,
                evalue=str(exc),
            ) from exc
