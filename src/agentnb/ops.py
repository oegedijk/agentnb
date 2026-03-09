from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .errors import AgentNBException, KernelNotReadyError, NoKernelRunningError
from .history import HistoryStore, kernel_execution_record, user_command_record
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
import inspect
import types
from IPython import get_ipython

_max_len = 160
_items = []
_user_ns = get_ipython().user_ns if get_ipython() is not None else globals()
_skip_names = {
    "In",
    "Out",
    "exit",
    "get_ipython",
    "open",
    "quit",
}


def _dataframe_summary(value):
    try:
        shape = tuple(value.shape)
        columns = [str(column) for column in list(value.columns)[:5]]
        total_columns = len(getattr(value, "columns", []))
    except Exception:
        return None

    if len(columns) < total_columns:
        columns_text = ", ".join(columns) + ", ..."
    else:
        columns_text = ", ".join(columns)
    return f"DataFrame shape={shape} columns={columns_text}"


def _truncate_repr(value, limit):
    text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _container_summary(value):
    if isinstance(value, dict):
        keys = [str(key) for key in list(value.keys())[:5]]
        suffix = ", ..." if len(value) > len(keys) else ""
        keys_text = ", ".join(keys)
        return f"dict len={len(value)} keys={keys_text}{suffix}"

    if isinstance(value, (list, tuple, set)):
        items = list(value)[:3]
        summary = f"{type(value).__name__} len={len(value)}"
        if items and isinstance(items[0], dict):
            keys = [str(key) for key in list(items[0].keys())[:5]]
            suffix = ", ..." if len(items[0]) > len(keys) else ""
            return summary + " item_keys=" + ", ".join(keys) + suffix
        if items:
            return summary + " sample=" + _truncate_repr(items, 80)
        return summary

    return None


def _external_object_summary(value):
    module_name = getattr(type(value), "__module__", "")
    if module_name in {"", "__main__", "builtins"}:
        return None

    text = repr(value)
    if " object at 0x" not in text:
        return None

    parts = [type(value).__name__]
    for attr_name in ("status", "closed"):
        if hasattr(value, attr_name):
            try:
                parts.append(f"{attr_name}={getattr(value, attr_name)}")
            except Exception:
                continue
    return " ".join(parts)


for _name, _value in sorted(_user_ns.items()):
    if _name.startswith("_"):
        continue
    if _name in _skip_names:
        continue
    if isinstance(_value, types.ModuleType):
        continue
    if inspect.isroutine(_value) or inspect.isclass(_value):
        continue
    _repr_text = _dataframe_summary(_value)
    if _repr_text is None:
        _repr_text = _container_summary(_value)
    if _repr_text is None:
        _repr_text = _external_object_summary(_value)
    if _repr_text is None:
        _repr_text = _truncate_repr(_value, _max_len)
    _items.append({"name": _name, "type": type(_value).__name__, "repr": _repr_text})

print(json.dumps(_items))
"""
        return self._execute_json_payload(
            project_root=project_root,
            session_id=session_id,
            code=code,
            timeout_s=timeout_s,
            context="list vars",
            command_type="vars",
            label="vars",
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

def _truncate_text(value, limit):
    text = repr(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _safe_head_rows(value, limit):
    try:
        head_value = value.head(limit)
    except Exception:
        return None

    try:
        if hasattr(head_value, "reset_index"):
            head_value = head_value.reset_index()
    except Exception:
        pass

    try:
        rows = head_value.to_dict(orient="records")
    except Exception:
        return None

    return rows if isinstance(rows, list) else None


def _dtype_summary(value):
    try:
        dtypes = value.dtypes
    except Exception:
        return None

    try:
        if hasattr(dtypes, "astype"):
            dtypes = dtypes.astype(str)
    except Exception:
        pass

    try:
        mapping = dtypes.to_dict()
    except Exception:
        return None

    if not isinstance(mapping, dict):
        return None
    return {{str(key): str(item) for key, item in mapping.items()}}


def _null_counts(value, limit):
    try:
        counts = value.isna().sum()
    except Exception:
        return None

    try:
        mapping = counts.to_dict()
    except Exception:
        return None

    if not isinstance(mapping, dict):
        return None

    items = list(mapping.items())[:limit]
    return {{str(key): int(item) for key, item in items}}


def _dataframe_preview(value):
    required_attrs = ("shape", "columns", "dtypes", "head")
    if not all(hasattr(value, attr) for attr in required_attrs):
        return None

    try:
        shape = tuple(value.shape)
    except Exception:
        return None

    try:
        columns = [str(column) for column in list(value.columns)[:20]]
    except Exception:
        columns = []

    preview = {{
        "kind": "dataframe-like",
        "shape": list(shape),
        "columns": columns,
        "column_count": len(getattr(value, "columns", [])),
        "dtypes": _dtype_summary(value),
        "head": _safe_head_rows(value, 5),
    }}
    nulls = _null_counts(value, 20)
    if nulls is not None:
        preview["null_counts"] = nulls
    return preview


def _simple_text(value, limit):
    text = str(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _json_safe(value, depth=0):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _simple_text(value, 80)
    if depth >= 2:
        return _truncate_text(value, 80)
    if isinstance(value, dict):
        _sample = {{}}
        for _index, (_key, _item) in enumerate(value.items()):
            if _index >= 5:
                break
            _sample[str(_key)] = _json_safe(_item, depth + 1)
        return _sample
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(_item, depth + 1) for _item in list(value)[:3]]
    return _truncate_text(value, 80)


def _mapping_preview(value):
    if not isinstance(value, dict):
        return None

    _keys = [str(_key) for _key in list(value.keys())[:10]]
    _sample = {{}}
    for _index, (_key, _item) in enumerate(value.items()):
        if _index >= 3:
            break
        _sample[str(_key)] = _json_safe(_item)

    return {{
        "kind": "mapping-like",
        "length": len(value),
        "keys": _keys,
        "sample": _sample,
    }}


def _sequence_preview(value):
    if not isinstance(value, (list, tuple, set)):
        return None

    _items = list(value)
    _sample = [_json_safe(_item) for _item in _items[:3]]
    _preview = {{
        "kind": "sequence-like",
        "length": len(_items),
        "sample": _sample,
    }}
    if _items:
        _preview["item_type"] = type(_items[0]).__name__
        if isinstance(_items[0], dict):
            _preview["sample_keys"] = [str(_key) for _key in list(_items[0].keys())[:10]]
    return _preview


_user_ns = get_ipython().user_ns if get_ipython() is not None else globals()
_name = {escaped_name}
if _name not in _user_ns:
    raise NameError(f"Variable '{{_name}}' is not defined")

_value = _user_ns[_name]
_repr_text = _truncate_text(_value, 500)
_preview = _dataframe_preview(_value)
if _preview is None:
    _preview = _mapping_preview(_value)
if _preview is None:
    _preview = _sequence_preview(_value)
_members = []
_doc = ""
if _preview is None:
    _members = [member for member in dir(_value) if not member.startswith("_")]
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
    "preview": _preview,
}}
print(json.dumps(_payload))
"""
        return self._execute_json_payload(
            project_root=project_root,
            session_id=session_id,
            code=code,
            timeout_s=timeout_s,
            context="inspect variable",
            command_type="inspect",
            label=f"inspect {name}",
            input_text=name,
        )

    def reload_module(
        self,
        project_root: Path,
        module_name: str | None = None,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
    ) -> dict[str, Any]:
        escaped_module = repr(module_name)
        escaped_root = repr(str(project_root.resolve()))
        code = f"""
import importlib
import importlib.util
import json
import sys
import types
from pathlib import Path
from IPython import get_ipython

_project_root = Path({escaped_root}).resolve()
_requested = {escaped_module}
_user_ns = get_ipython().user_ns if get_ipython() is not None else globals()
_excluded_roots = []

for _root in {{
    getattr(sys, "prefix", None),
    getattr(sys, "base_prefix", None),
    getattr(sys, "exec_prefix", None),
    getattr(sys, "base_exec_prefix", None),
    str(_project_root / ".venv"),
    str(_project_root / ".agentnb"),
}}:
    if not _root:
        continue
    try:
        _resolved_root = Path(_root).resolve()
    except Exception:
        continue
    if _resolved_root == _project_root:
        continue
    _excluded_roots.append(_resolved_root)


def _is_relative_to(path, root):
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _module_path(module):
    _path = getattr(module, "__file__", None)
    if not _path:
        return None
    try:
        return Path(_path).resolve()
    except Exception:
        return None


def _classify_module(module):
    _path = _module_path(module)
    if _path is None:
        return False, "no_file"
    if not _is_relative_to(_path, _project_root):
        return False, "outside_project"
    for _root in _excluded_roots:
        if _is_relative_to(_path, _root):
            return False, "environment"
    return True, None


def _rebind_names(module_name, reloaded_module):
    _rebound = []
    _stale = []

    for _alias, _value in list(_user_ns.items()):
        if _alias.startswith("_"):
            continue

        if isinstance(_value, types.ModuleType):
            if getattr(_value, "__name__", None) == module_name:
                _user_ns[_alias] = reloaded_module
                _rebound.append(_alias)
            continue

        _value_module = getattr(_value, "__module__", None)
        _value_name = getattr(_value, "__name__", None)
        if _value_module == module_name and isinstance(_value_name, str):
            if hasattr(reloaded_module, _value_name):
                _user_ns[_alias] = getattr(reloaded_module, _value_name)
                _rebound.append(_alias)
            continue

        _value_type = getattr(_value, "__class__", None)
        if getattr(_value_type, "__module__", None) == module_name:
            _stale.append(_alias)

    return _rebound, _stale


def _prepare_reload(module):
    _path = _module_path(module)
    if _path is None:
        return
    try:
        _cache_path = Path(importlib.util.cache_from_source(str(_path)))
    except Exception:
        return
    try:
        if _cache_path.exists():
            _cache_path.unlink()
    except Exception:
        pass


def _project_modules():
    _candidates = []
    _excluded_count = 0

    for _name, _module in sorted(sys.modules.items()):
        if not _name or _name == "__main__" or _module is None:
            continue
        if not isinstance(_module, types.ModuleType):
            continue

        _is_local, _reason = _classify_module(_module)
        if _is_local:
            _candidates.append(_name)
        elif _reason in {{"outside_project", "environment"}}:
            _excluded_count += 1

    _ordered = sorted(set(_candidates), key=lambda _name: (-_name.count("."), _name))
    return _ordered, _excluded_count


_report = {{
    "mode": "module" if _requested else "project",
    "requested_module": _requested,
    "reloaded_modules": [],
    "failed_modules": [],
    "skipped_modules": [],
    "rebound_names": [],
    "stale_names": [],
    "excluded_module_count": 0,
    "notes": [],
}}
_rebound_names = set()
_stale_names = set()

if _requested:
    _module = importlib.import_module(_requested)
    _is_local, _reason = _classify_module(_module)
    if not _is_local:
        raise ValueError(
            f"Module '{{_requested}}' is not a project-local module (reason: {{_reason}})"
        )

    _resolved_name = _module.__name__
    importlib.invalidate_caches()
    _prepare_reload(_module)
    _reloaded = importlib.reload(_module)
    _report["reloaded_modules"].append(_resolved_name)
    _rebound, _stale = _rebind_names(_resolved_name, _reloaded)
    _rebound_names.update(_rebound)
    _stale_names.update(_stale)
    _report["notes"].append(
        "Only the requested module was reloaded. "
        "Use bare reload to refresh all imported project-local modules."
    )
else:
    _module_names, _excluded_count = _project_modules()
    _report["excluded_module_count"] = _excluded_count

    if not _module_names:
        _report["notes"].append("No imported project-local modules were found.")

    for _module_name in _module_names:
        try:
            _module = importlib.import_module(_module_name)
            importlib.invalidate_caches()
            _prepare_reload(_module)
            _reloaded = importlib.reload(_module)
        except Exception as _exc:
            _report["failed_modules"].append(
                {{
                    "module": _module_name,
                    "error_type": type(_exc).__name__,
                    "message": str(_exc),
                }}
            )
            continue

        _report["reloaded_modules"].append(_module_name)
        _rebound, _stale = _rebind_names(_module_name, _reloaded)
        _rebound_names.update(_rebound)
        _stale_names.update(_stale)

if _stale_names:
    _report["notes"].append(
        "Existing instances or cached objects may still reference old definitions. "
        "Recreate them or run reset if stale state is widespread."
    )

_report["rebound_names"] = sorted(_rebound_names)
_report["stale_names"] = sorted(_stale_names)
print(json.dumps(_report))
"""
        return self._execute_json_payload(
            project_root=project_root,
            session_id=session_id,
            code=code,
            timeout_s=timeout_s,
            context="reload modules" if module_name is None else "reload module",
            command_type="reload",
            label="reload" if module_name is None else f"reload {module_name}",
            input_text=module_name,
        )

    def _execute_json_payload(
        self,
        project_root: Path,
        session_id: str,
        code: str,
        timeout_s: float,
        context: str,
        command_type: str,
        label: str,
        input_text: str | None = None,
    ) -> Any:
        history = HistoryStore(project_root=project_root, session_id=session_id)

        try:
            execution = self.runtime.execute(
                project_root=project_root,
                session_id=session_id,
                code=code,
                timeout_s=timeout_s,
            )
        except Exception as exc:
            if isinstance(exc, (NoKernelRunningError, KernelNotReadyError)):
                raise
            history.append(
                kernel_execution_record(
                    session_id=session_id,
                    command_type=command_type,
                    label=f"{label} helper",
                    code=code,
                    origin="ops_helper",
                    error=exc,
                )
            )
            history.append(
                user_command_record(
                    session_id=session_id,
                    command_type=command_type,
                    label=label,
                    input_text=input_text,
                    origin="ops",
                    error=exc,
                )
            )
            raise

        history.append(
            kernel_execution_record(
                session_id=session_id,
                command_type=command_type,
                label=f"{label} helper",
                code=code,
                origin="ops_helper",
                execution=execution,
            )
        )
        if execution.status == "error":
            history.append(
                user_command_record(
                    session_id=session_id,
                    command_type=command_type,
                    label=label,
                    input_text=input_text,
                    origin="ops",
                    execution=execution,
                )
            )
            raise AgentNBException(
                code="EXECUTION_ERROR",
                message=f"Failed to {context}",
                ename=execution.ename,
                evalue=execution.evalue,
                traceback=execution.traceback,
            )

        lines = [line.strip() for line in execution.stdout.splitlines() if line.strip()]
        if not lines:
            history.append(
                user_command_record(
                    session_id=session_id,
                    command_type=command_type,
                    label=label,
                    input_text=input_text,
                    origin="ops",
                    status="error",
                    duration_ms=execution.duration_ms,
                    error_type="PARSE_ERROR",
                    stdout=execution.stdout,
                    result=execution.result,
                )
            )
            raise AgentNBException(
                code="PARSE_ERROR", message=f"No output while attempting to {context}"
            )

        try:
            payload = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            history.append(
                user_command_record(
                    session_id=session_id,
                    command_type=command_type,
                    label=label,
                    input_text=input_text,
                    origin="ops",
                    status="error",
                    duration_ms=execution.duration_ms,
                    error_type=type(exc).__name__,
                    stdout=execution.stdout,
                    result=execution.result,
                )
            )
            raise AgentNBException(
                code="PARSE_ERROR",
                message=f"Unable to parse JSON payload while attempting to {context}",
                ename=type(exc).__name__,
                evalue=str(exc),
            ) from exc

        history.append(
            user_command_record(
                session_id=session_id,
                command_type=command_type,
                label=label,
                input_text=input_text,
                origin="ops",
                execution=execution,
            )
        )
        return payload
