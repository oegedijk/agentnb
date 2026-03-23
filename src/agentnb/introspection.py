from __future__ import annotations

import ast
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generic, Literal, TypeVar, cast

from .contracts import (
    ExecutionResult,
    HelperAccessMetadata,
    HelperInitialRuntimeState,
    HelperWaitFor,
)
from .errors import AgentNBException, KernelNotReadyError, NoKernelRunningError, SessionBusyError
from .execution import ExecutionService
from .history import HistoryStore
from .payloads import (
    DataframePreview,
    FailedModuleEntry,
    InspectPayload,
    InspectPreview,
    JSONValue,
    MappingPreview,
    ReloadReport,
    SequencePreview,
    VarEntry,
)
from .recording import CommandRecorder, CommandRecording
from .runtime import KernelRuntime, KernelWaitResult
from .session import DEFAULT_SESSION_ID
from .state import StateRepository

PayloadT = TypeVar("PayloadT")


@dataclass(slots=True, frozen=True)
class KernelHelperRequest:
    command_type: str
    label: str
    context: str
    code: str
    recording: CommandRecording | None = None
    input_text: str | None = None


@dataclass(slots=True, frozen=True)
class KernelHelperResult(Generic[PayloadT]):
    execution: ExecutionResult
    payload: PayloadT
    access_metadata: HelperAccessMetadata = field(default_factory=HelperAccessMetadata)


@dataclass(slots=True, frozen=True)
class HelperExecutionPolicy:
    ensure_started: bool = False
    wait_for_usable: bool = False
    retry_on_busy: bool = False
    record_history: bool = True


@dataclass(slots=True, frozen=True)
class InspectAccessor:
    kind: Literal["attr", "subscript"]
    value: str | int | float | bool | None


@dataclass(slots=True, frozen=True)
class InspectReference:
    raw: str
    root_name: str
    accessors: tuple[InspectAccessor, ...] = ()


class KernelIntrospection:
    def __init__(
        self,
        runtime: KernelRuntime,
        executions: ExecutionService | None = None,
        recorder: CommandRecorder | None = None,
    ) -> None:
        self.runtime = runtime
        self.executions = executions
        self.recorder = recorder or CommandRecorder()

    def list_vars(
        self,
        project_root: Path,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
        execution_policy: HelperExecutionPolicy | None = None,
    ) -> KernelHelperResult[list[VarEntry]]:
        result = self._run_json_helper(
            project_root=project_root,
            session_id=session_id,
            timeout_s=timeout_s,
            helper=_list_vars_helper(),
            execution_policy=execution_policy,
        )
        return KernelHelperResult(
            execution=result.execution,
            payload=_parse_var_entries(result.payload),
            access_metadata=result.access_metadata,
        )

    def inspect_var(
        self,
        project_root: Path,
        name: str,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
        execution_policy: HelperExecutionPolicy | None = None,
    ) -> KernelHelperResult[InspectPayload]:
        reference = _parse_inspect_reference(name)
        result = self._run_json_helper(
            project_root=project_root,
            session_id=session_id,
            timeout_s=timeout_s,
            helper=_inspect_helper(reference),
            execution_policy=execution_policy,
        )
        return KernelHelperResult(
            execution=result.execution,
            payload=_parse_inspect_payload(result.payload),
            access_metadata=result.access_metadata,
        )

    def reload_module(
        self,
        project_root: Path,
        module_name: str | None = None,
        session_id: str = DEFAULT_SESSION_ID,
        timeout_s: float = 10.0,
        execution_policy: HelperExecutionPolicy | None = None,
    ) -> KernelHelperResult[ReloadReport]:
        result = self._run_json_helper(
            project_root=project_root,
            session_id=session_id,
            timeout_s=timeout_s,
            helper=_reload_helper(project_root=project_root, module_name=module_name),
            execution_policy=execution_policy,
        )
        return KernelHelperResult(
            execution=result.execution,
            payload=_parse_reload_report(result.payload),
            access_metadata=result.access_metadata,
        )

    def _run_json_helper(
        self,
        *,
        project_root: Path,
        session_id: str,
        timeout_s: float,
        helper: KernelHelperRequest,
        execution_policy: HelperExecutionPolicy | None = None,
    ) -> KernelHelperResult[JSONValue]:
        policy = execution_policy or HelperExecutionPolicy()
        access_metadata = HelperAccessMetadata()
        history = (
            HistoryStore(project_root=project_root, session_id=session_id)
            if policy.record_history
            else None
        )
        recording = self._recording_for_helper(helper) if policy.record_history else None

        try:
            if policy.ensure_started:
                started_new_session = self._ensure_helper_session_started(
                    project_root=project_root,
                    session_id=session_id,
                )
                access_metadata = access_metadata.with_updates(
                    started_new_session=started_new_session
                )
            execution, access_metadata = self._execute_helper(
                project_root=project_root,
                session_id=session_id,
                timeout_s=timeout_s,
                helper=helper,
                policy=policy,
                access_metadata=access_metadata,
            )
        except Exception as exc:
            original_exc = exc
            if isinstance(exc, (NoKernelRunningError, KernelNotReadyError)):
                raise
            if isinstance(exc, SessionBusyError):
                exc = _augment_session_busy_error(exc, access_metadata)
            elif isinstance(exc, AgentNBException):
                exc = _augment_helper_error(exc, access_metadata)
            if history is not None:
                self._append_execution_error(
                    history=history,
                    session_id=session_id,
                    helper=helper,
                    error=exc,
                )
            if exc is original_exc:
                raise
            raise exc from original_exc

        if history is not None and recording is not None:
            internal_record = recording.build_internal_record(
                session_id=session_id,
                execution=execution,
            )
            if internal_record is not None:
                history.append(internal_record)
        if execution.status == "error":
            if history is not None and recording is not None:
                history.append(
                    recording.build_user_record(
                        session_id=session_id,
                        execution=execution,
                    )
                )
            raise AgentNBException(
                code="EXECUTION_ERROR",
                message=f"Failed to {helper.context}",
                ename=execution.ename,
                evalue=execution.evalue,
                traceback=execution.traceback,
                data=access_metadata.merge_data(),
            )

        try:
            payload = self._parse_json_payload(
                execution=execution,
                session_id=session_id,
                history=history,
                helper=helper,
            )
        except AgentNBException as exc:
            raise _augment_helper_error(exc, access_metadata) from exc
        if history is not None and recording is not None:
            history.append(
                recording.build_user_record(
                    session_id=session_id,
                    execution=execution,
                )
            )
        return KernelHelperResult(
            execution=execution,
            payload=payload,
            access_metadata=access_metadata,
        )

    def _ensure_helper_session_started(
        self,
        *,
        project_root: Path,
        session_id: str,
    ) -> bool:
        state = self.runtime.runtime_state(project_root=project_root, session_id=session_id)
        if state.kind == "starting":
            raise AgentNBException(
                code="KERNEL_NOT_READY",
                message="Kernel startup is still in progress or not yet ready. Wait and retry.",
                data={
                    "runtime_state": state.kind,
                    "session_exists": state.session_exists,
                },
            )
        if state.kind in {"ready", "busy"}:
            return False
        _, started_new_session = self.runtime.ensure_started(
            project_root=project_root,
            session_id=session_id,
        )
        return started_new_session

    def _execute_helper(
        self,
        *,
        project_root: Path,
        session_id: str,
        timeout_s: float,
        helper: KernelHelperRequest,
        policy: HelperExecutionPolicy,
        access_metadata: HelperAccessMetadata,
    ) -> tuple[ExecutionResult, HelperAccessMetadata]:
        attempts = 0
        accumulated_access = access_metadata

        while True:
            if policy.wait_for_usable:
                next_access = self._wait_for_helper_access(
                    project_root=project_root,
                    session_id=session_id,
                    timeout_s=timeout_s,
                )
                accumulated_access = _merge_helper_access_metadata(
                    accumulated_access,
                    next_access,
                )
            try:
                execution = self.runtime.execute(
                    project_root=project_root,
                    session_id=session_id,
                    code=helper.code,
                    timeout_s=timeout_s,
                )
                return execution, accumulated_access
            except SessionBusyError as exc:
                if not policy.retry_on_busy:
                    raise _augment_session_busy_error(exc, accumulated_access) from exc
                attempts += 1
                if attempts >= 3:
                    raise _augment_session_busy_error(exc, accumulated_access) from exc

    def _wait_for_helper_access(
        self,
        *,
        project_root: Path,
        session_id: str,
        timeout_s: float,
    ) -> HelperAccessMetadata:
        if self.executions is not None:
            return self.executions.wait_for_helper_session_access(
                project_root=project_root,
                session_id=session_id,
                timeout_s=timeout_s,
            )
        wait_result = self.runtime.wait_for_usable(
            project_root=project_root,
            session_id=session_id,
            timeout_s=timeout_s,
        )
        return _helper_access_from_wait_result(wait_result)

    def _append_execution_error(
        self,
        *,
        history: HistoryStore,
        session_id: str,
        helper: KernelHelperRequest,
        error: Exception,
    ) -> None:
        self._recording_for_helper(helper).append_to(
            history,
            session_id=session_id,
            error=error,
        )

    def _parse_json_payload(
        self,
        *,
        execution: ExecutionResult,
        session_id: str,
        history: HistoryStore | None,
        helper: KernelHelperRequest,
    ) -> JSONValue:
        lines = [line.strip() for line in execution.stdout.splitlines() if line.strip()]
        if not lines:
            if history is not None:
                self._append_parse_error(
                    history=history,
                    session_id=session_id,
                    helper=helper,
                    execution=execution,
                    error_type="PARSE_ERROR",
                )
            raise AgentNBException(
                code="PARSE_ERROR",
                message=f"No output while attempting to {helper.context}",
            )

        try:
            return json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            if history is not None:
                self._append_parse_error(
                    history=history,
                    session_id=session_id,
                    helper=helper,
                    execution=execution,
                    error_type=type(exc).__name__,
                )
            raise AgentNBException(
                code="PARSE_ERROR",
                message=f"Unable to parse JSON payload while attempting to {helper.context}",
                ename=type(exc).__name__,
                evalue=str(exc),
            ) from exc

    def _append_parse_error(
        self,
        *,
        history: HistoryStore,
        session_id: str,
        helper: KernelHelperRequest,
        execution: ExecutionResult,
        error_type: str,
    ) -> None:
        history.append(
            self._recording_for_helper(helper).build_user_record(
                session_id=session_id,
                status="error",
                duration_ms=execution.duration_ms,
                error_type=error_type,
                stdout=execution.stdout,
                result=execution.result,
            )
        )

    def _recording_for_helper(self, helper: KernelHelperRequest) -> CommandRecording:
        if helper.recording is not None:
            return helper.recording
        if helper.command_type == "vars":
            return self.recorder.vars(code=helper.code)
        if helper.command_type == "inspect":
            name = helper.input_text or helper.label.removeprefix("inspect ").strip()
            return self.recorder.inspect(name=name, code=helper.code)
        if helper.command_type == "reload":
            return self.recorder.reload(module_name=helper.input_text, code=helper.code)
        raise ValueError(f"Unsupported helper command type: {helper.command_type}")


def _merge_helper_access_metadata(
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


def _helper_access_from_wait_result(wait_result: KernelWaitResult) -> HelperAccessMetadata:
    return HelperAccessMetadata(
        waited=wait_result.waited,
        waited_for=wait_result.waited_for,
        waited_ms=wait_result.waited_ms,
        initial_runtime_state=wait_result.initial_runtime_state,
    )


def _helper_access_from_data(data: Mapping[str, object]) -> HelperAccessMetadata:
    waited_for = data.get("waited_for")
    initial_runtime_state = data.get("initial_runtime_state")
    blocking_execution_id = data.get("blocking_execution_id")
    waited_ms = data.get("waited_ms")
    waited_for_value: HelperWaitFor | None = (
        cast(HelperWaitFor, waited_for) if waited_for in {"ready", "idle"} else None
    )
    initial_runtime_state_value: HelperInitialRuntimeState | None = (
        cast(HelperInitialRuntimeState, initial_runtime_state)
        if initial_runtime_state in {"missing", "starting", "ready", "busy", "dead", "stale"}
        else None
    )
    return HelperAccessMetadata(
        started_new_session=data.get("started_new_session") is True,
        waited=data.get("waited") is True,
        waited_for=waited_for_value,
        waited_ms=waited_ms if isinstance(waited_ms, int) else 0,
        initial_runtime_state=initial_runtime_state_value,
        blocking_execution_id=blocking_execution_id
        if isinstance(blocking_execution_id, str)
        else None,
    )


def _session_busy_after_wait(
    error: SessionBusyError,
    access_metadata: HelperAccessMetadata,
) -> SessionBusyError:
    data = error.data
    return SessionBusyError(
        wait_behavior="after_wait",
        waited_ms=access_metadata.waited_ms,
        lock_pid=cast(int | None, data.get("lock_pid")),
        lock_acquired_at=cast(str | None, data.get("lock_acquired_at")),
        busy_for_ms=cast(int | None, data.get("busy_for_ms")),
        active_execution_id=cast(str | None, data.get("active_execution_id"))
        or access_metadata.blocking_execution_id,
    )


def _augment_session_busy_error(
    error: SessionBusyError,
    access_metadata: HelperAccessMetadata,
) -> SessionBusyError:
    combined_access = _merge_helper_access_metadata(
        access_metadata,
        _helper_access_from_data(error.data),
    )
    augmented = _session_busy_after_wait(error, combined_access)
    augmented.data = combined_access.merge_data(augmented.data)
    return augmented


def _augment_helper_error(
    error: AgentNBException,
    access_metadata: HelperAccessMetadata,
) -> AgentNBException:
    return AgentNBException(
        code=error.code,
        message=error.message,
        ename=error.ename,
        evalue=error.evalue,
        traceback=error.traceback,
        data=access_metadata.merge_data(error.data),
    )


def _parse_var_entries(payload: JSONValue) -> list[VarEntry]:
    if not isinstance(payload, list):
        raise AgentNBException(code="PARSE_ERROR", message="Vars helper returned an invalid shape")

    entries: list[VarEntry] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        value_type = item.get("type")
        repr_text = item.get("repr")
        if not all(isinstance(value, str) for value in (name, value_type, repr_text)):
            continue
        entries.append(
            VarEntry(
                name=name,
                type=value_type,
                repr=repr_text,
            )
        )
    return entries


def _parse_inspect_payload(payload: JSONValue) -> InspectPayload:
    if not isinstance(payload, dict):
        raise AgentNBException(
            code="PARSE_ERROR",
            message="Inspect helper returned an invalid shape",
        )

    name = payload.get("name")
    value_type = payload.get("type")
    if not isinstance(name, str) or not isinstance(value_type, str):
        raise AgentNBException(
            code="PARSE_ERROR",
            message="Inspect helper returned an invalid shape",
        )

    inspect_payload: InspectPayload = {
        "name": name,
        "type": value_type,
    }

    repr_text = payload.get("repr")
    if isinstance(repr_text, str):
        inspect_payload["repr"] = repr_text

    members = payload.get("members")
    if isinstance(members, list):
        inspect_payload["members"] = [member for member in members if isinstance(member, str)]

    doc = payload.get("doc")
    if isinstance(doc, str):
        inspect_payload["doc"] = doc

    preview = payload.get("preview")
    parsed_preview = _parse_inspect_preview(preview)
    if parsed_preview is not None:
        inspect_payload["preview"] = parsed_preview

    return inspect_payload


def _parse_inspect_preview(payload: JSONValue) -> InspectPreview | None:
    if not isinstance(payload, dict):
        return None

    kind = payload.get("kind")
    if kind == "dataframe-like":
        preview: DataframePreview = {"kind": "dataframe-like"}
        shape = payload.get("shape")
        if isinstance(shape, list) and all(isinstance(item, int) for item in shape):
            preview["shape"] = shape
        columns = payload.get("columns")
        if isinstance(columns, list):
            preview["columns"] = [column for column in columns if isinstance(column, str)]
        column_count = payload.get("column_count")
        if isinstance(column_count, int):
            preview["column_count"] = column_count
        columns_shown = payload.get("columns_shown")
        if isinstance(columns_shown, int):
            preview["columns_shown"] = columns_shown
        dtypes = payload.get("dtypes")
        if isinstance(dtypes, dict):
            preview["dtypes"] = {
                str(key): str(value)
                for key, value in dtypes.items()
                if isinstance(key, str) and isinstance(value, str)
            }
        dtypes_shown = payload.get("dtypes_shown")
        if isinstance(dtypes_shown, int):
            preview["dtypes_shown"] = dtypes_shown
        head = payload.get("head")
        if isinstance(head, list):
            preview["head"] = [
                cast(dict[str, JSONValue], row) for row in head if isinstance(row, dict)
            ]
        head_rows_shown = payload.get("head_rows_shown")
        if isinstance(head_rows_shown, int):
            preview["head_rows_shown"] = head_rows_shown
        null_counts = payload.get("null_counts")
        if isinstance(null_counts, dict):
            preview["null_counts"] = {
                str(key): value
                for key, value in null_counts.items()
                if isinstance(key, str) and isinstance(value, int)
            }
        null_count_fields_shown = payload.get("null_count_fields_shown")
        if isinstance(null_count_fields_shown, int):
            preview["null_count_fields_shown"] = null_count_fields_shown
        return preview

    if kind == "mapping-like":
        keys = payload.get("keys")
        sample = payload.get("sample")
        length = payload.get("length")
        if (
            not isinstance(length, int)
            or not isinstance(keys, list)
            or not isinstance(sample, dict)
        ):
            return None
        preview: MappingPreview = {
            "kind": "mapping-like",
            "length": length,
            "keys": [key for key in keys if isinstance(key, str)],
            "sample": cast(dict[str, JSONValue], sample),
        }
        keys_shown = payload.get("keys_shown")
        if isinstance(keys_shown, int):
            preview["keys_shown"] = keys_shown
        sample_items_shown = payload.get("sample_items_shown")
        if isinstance(sample_items_shown, int):
            preview["sample_items_shown"] = sample_items_shown
        if isinstance(payload.get("sample_truncated"), bool):
            preview["sample_truncated"] = cast(bool, payload.get("sample_truncated"))
        return preview

    if kind == "sequence-like":
        sample = payload.get("sample")
        length = payload.get("length")
        if not isinstance(length, int) or not isinstance(sample, list):
            return None
        preview: SequencePreview = {
            "kind": "sequence-like",
            "length": length,
            "sample": cast(list[JSONValue], sample),
        }
        item_type = payload.get("item_type")
        if isinstance(item_type, str):
            preview["item_type"] = item_type
        sample_keys = payload.get("sample_keys")
        if isinstance(sample_keys, list):
            preview["sample_keys"] = [key for key in sample_keys if isinstance(key, str)]
        sample_items_shown = payload.get("sample_items_shown")
        if isinstance(sample_items_shown, int):
            preview["sample_items_shown"] = sample_items_shown
        sample_keys_shown = payload.get("sample_keys_shown")
        if isinstance(sample_keys_shown, int):
            preview["sample_keys_shown"] = sample_keys_shown
        if isinstance(payload.get("sample_truncated"), bool):
            preview["sample_truncated"] = cast(bool, payload.get("sample_truncated"))
        return preview

    return None


def _parse_reload_report(payload: JSONValue) -> ReloadReport:
    if not isinstance(payload, dict):
        raise AgentNBException(
            code="PARSE_ERROR",
            message="Reload helper returned an invalid shape",
        )

    report: ReloadReport = {}
    mode = payload.get("mode")
    if mode in {"module", "project"}:
        report["mode"] = mode
    requested_module = payload.get("requested_module")
    if requested_module is None or isinstance(requested_module, str):
        report["requested_module"] = requested_module
    for key in ("reloaded_modules", "skipped_modules", "rebound_names", "stale_names", "notes"):
        value = payload.get(key)
        if isinstance(value, list):
            report[key] = [item for item in value if isinstance(item, str)]
    excluded_count = payload.get("excluded_module_count")
    if isinstance(excluded_count, int):
        report["excluded_module_count"] = excluded_count

    failed_modules = payload.get("failed_modules")
    if isinstance(failed_modules, list):
        parsed_failed: list[FailedModuleEntry] = []
        for item in failed_modules:
            if not isinstance(item, dict):
                continue
            module = item.get("module")
            error_type = item.get("error_type")
            message = item.get("message")
            if all(isinstance(value, str) for value in (module, error_type, message)):
                parsed_failed.append(
                    FailedModuleEntry(
                        module=module,
                        error_type=error_type,
                        message=message,
                    )
                )
        report["failed_modules"] = parsed_failed

    return report


def _parse_inspect_reference(reference: str) -> InspectReference:
    raw = reference.strip()
    if not raw:
        raise AgentNBException(code="INVALID_INPUT", message="Inspect reference cannot be empty.")
    try:
        expression = ast.parse(raw, mode="eval")
    except SyntaxError as exc:
        raise AgentNBException(
            code="INVALID_INPUT",
            message="Inspect only supports names, dotted access, and constant subscripts.",
            ename=type(exc).__name__,
            evalue=str(exc),
        ) from exc

    root_name, accessors = _parse_inspect_expression(expression.body)
    return InspectReference(raw=raw, root_name=root_name, accessors=tuple(accessors))


def _parse_inspect_expression(node: ast.AST) -> tuple[str, list[InspectAccessor]]:
    if isinstance(node, ast.Name):
        return node.id, []
    if isinstance(node, ast.Attribute):
        root_name, accessors = _parse_inspect_expression(node.value)
        accessors.append(InspectAccessor(kind="attr", value=node.attr))
        return root_name, accessors
    if isinstance(node, ast.Subscript):
        root_name, accessors = _parse_inspect_expression(node.value)
        accessors.append(
            InspectAccessor(kind="subscript", value=_parse_inspect_subscript(node.slice))
        )
        return root_name, accessors
    raise AgentNBException(
        code="INVALID_INPUT",
        message="Inspect only supports names, dotted access, and constant subscripts.",
    )


def _parse_inspect_subscript(node: ast.AST) -> str | int | float | bool | None:
    if isinstance(node, ast.Constant) and isinstance(
        node.value, str | int | float | bool | type(None)
    ):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        operand = node.operand
        if isinstance(operand, ast.Constant) and isinstance(operand.value, int | float):
            value = operand.value if isinstance(node.op, ast.UAdd) else -operand.value
            return value
    raise AgentNBException(
        code="INVALID_INPUT",
        message="Inspect subscripts must use constant string or numeric indexes.",
    )


def _list_vars_helper() -> KernelHelperRequest:
    return KernelHelperRequest(
        command_type="vars",
        label="vars",
        context="list vars",
        code="""
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
        _row_keys = _mapping_keys(items[0]) if items else None
        if _row_keys is not None:
            keys = [str(key) for key in _row_keys[:5]]
            suffix = ", ..." if len(items[0]) > len(keys) else ""
            return summary + " item_keys=" + ", ".join(keys) + suffix
        if items:
            return summary + " sample=" + _truncate_repr(items, 80)
        return summary

    return None


def _mapping_keys(value):
    if isinstance(value, dict):
        return list(value.keys())
    if hasattr(value, "keys") and hasattr(value, "__getitem__"):
        try:
            return list(value.keys())
        except Exception:
            return None
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


for _name, _value in list(_user_ns.items()):
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

print(json.dumps(_items, default=str))
""",
    )


def _inspect_helper(reference: InspectReference) -> KernelHelperRequest:
    escaped_display_name = json.dumps(reference.raw)
    escaped_root_name = json.dumps(reference.root_name)
    escaped_steps = json.dumps(
        [{"kind": accessor.kind, "value": accessor.value} for accessor in reference.accessors]
    )
    return KernelHelperRequest(
        command_type="inspect",
        label=f"inspect {reference.raw}",
        context="inspect variable",
        input_text=reference.raw,
        code=f"""
import json
from IPython import get_ipython

def _truncate_text(value, limit):
    text = str(value)
    if len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _safe_head_rows(value, limit, max_columns=10):
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
        cols = list(head_value.columns)
        if len(cols) > max_columns:
            head_value = head_value[cols[:max_columns]]
    except Exception:
        pass

    try:
        rows = head_value.to_dict(orient="records")
    except Exception:
        return None

    if not isinstance(rows, list):
        return None
    rows = [{{str(k): v for k, v in row.items()}} for row in rows]
    return rows


def _dtype_summary(value, limit):
    try:
        dtypes = value.dtypes
    except Exception:
        return None, 0

    try:
        if hasattr(dtypes, "astype"):
            dtypes = dtypes.astype(str)
    except Exception:
        pass

    try:
        mapping = dtypes.to_dict()
    except Exception:
        return None, 0

    if not isinstance(mapping, dict):
        return None, 0
    items = list(mapping.items())[:limit]
    return ({{str(key): str(item) for key, item in items}}, len(items))


def _null_counts(value, limit):
    try:
        counts = value.isna().sum()
    except Exception:
        return None

    try:
        mapping = counts.to_dict()
    except Exception:
        return None, 0

    if not isinstance(mapping, dict):
        return None, 0

    items = list(mapping.items())[:limit]
    return ({{str(key): int(item) for key, item in items}}, len(items))


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

    dtypes, dtypes_shown = _dtype_summary(value, 10)
    head = _safe_head_rows(value, 5)

    preview = {{
        "kind": "dataframe-like",
        "shape": list(shape),
        "columns": columns,
        "column_count": len(getattr(value, "columns", [])),
        "columns_shown": len(columns),
        "head": head,
        "head_rows_shown": len(head) if isinstance(head, list) else 0,
    }}
    if dtypes is not None:
        preview["dtypes"] = dtypes
        preview["dtypes_shown"] = dtypes_shown
    nulls, nulls_shown = _null_counts(value, 10)
    if nulls is not None:
        preview["null_counts"] = nulls
        preview["null_count_fields_shown"] = nulls_shown
    return preview


_sample_truncated = False


def _mark_sample_truncated():
    global _sample_truncated
    _sample_truncated = True


def _simple_text(value, limit):
    text = str(value)
    if len(text) > limit:
        _mark_sample_truncated()
        return text[: limit - 3] + "..."
    return text


def _json_safe(value, depth=0):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _simple_text(value, 80)
    if depth >= 6:
        _mark_sample_truncated()
        return _truncate_text(value, 80)
    _mapping = _mapping_items(value)
    if _mapping is not None:
        if len(_mapping) > 5:
            _mark_sample_truncated()
        _sample = {{}}
        for _index, (_key, _item) in enumerate(_mapping):
            if _index >= 5:
                break
            _sample[str(_key)] = _json_safe(_item, depth + 1)
        return _sample
    if isinstance(value, dict):
        if len(value) > 5:
            _mark_sample_truncated()
        _sample = {{}}
        for _index, (_key, _item) in enumerate(value.items()):
            if _index >= 5:
                break
            _sample[str(_key)] = _json_safe(_item, depth + 1)
        return _sample
    if isinstance(value, (list, tuple, set)):
        if len(value) > 3:
            _mark_sample_truncated()
        return [_json_safe(_item, depth + 1) for _item in list(value)[:3]]
    _mark_sample_truncated()
    return _truncate_text(value, 80)


def _mapping_items(value):
    if isinstance(value, dict):
        return list(value.items())
    if hasattr(value, "keys") and hasattr(value, "__getitem__"):
        try:
            _keys = list(value.keys())
            return [(_key, value[_key]) for _key in _keys]
        except Exception:
            return None
    return None


def _mapping_preview(value):
    global _sample_truncated
    _sample_truncated = False
    _items = _mapping_items(value)
    if _items is None:
        return None

    _keys = [str(_key) for _key, _ in _items[:10]]
    _sample = {{}}
    for _index, (_key, _item) in enumerate(_items):
        if _index >= 3:
            break
        _sample[str(_key)] = _json_safe(_item)

    return {{
        "kind": "mapping-like",
        "length": len(_items),
        "keys": _keys,
        "keys_shown": len(_keys),
        "sample": _sample,
        "sample_items_shown": len(_sample),
        "sample_truncated": (
            _sample_truncated
            or len(_items) > len(_keys)
            or len(_items) > len(_sample)
        ),
    }}


def _sequence_preview(value):
    global _sample_truncated
    _sample_truncated = False
    if not isinstance(value, (list, tuple, set)):
        return None

    _items = list(value)
    _sample = [_json_safe(_item) for _item in _items[:3]]
    _preview = {{
        "kind": "sequence-like",
        "length": len(_items),
        "sample": _sample,
        "sample_items_shown": len(_sample),
        "sample_truncated": _sample_truncated or len(_items) > len(_sample),
    }}
    if _items:
        _preview["item_type"] = type(_items[0]).__name__
        _sample_keys = _mapping_items(_items[0])
        if _sample_keys is not None:
            _preview["sample_keys"] = [str(_key) for _key, _ in _sample_keys[:10]]
            _preview["sample_keys_shown"] = len(_preview["sample_keys"])
    return _preview


_user_ns = get_ipython().user_ns if get_ipython() is not None else globals()
_name = {escaped_display_name}
_root_name = {escaped_root_name}
_steps = {escaped_steps}
if _root_name not in _user_ns:
    raise NameError(f"Variable '{{_root_name}}' is not defined")

_value = _user_ns[_root_name]
for _step in _steps:
    if _step["kind"] == "attr":
        _value = getattr(_value, _step["value"])
    else:
        _value = _value[_step["value"]]
_repr_text = _truncate_text(_value, 500)
_preview = _dataframe_preview(_value)
if _preview is None:
    _preview = _mapping_preview(_value)
if _preview is None:
    _preview = _sequence_preview(_value)
_SCALAR_TYPES = (int, float, str, bool, bytes, complex, type(None))
_members = []
_doc = ""
if _preview is None and not isinstance(_value, _SCALAR_TYPES):
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
print(json.dumps(_payload, default=str))
""",
    )


def _reload_helper(*, project_root: Path, module_name: str | None) -> KernelHelperRequest:
    escaped_module = repr(module_name)
    escaped_root = repr(str(project_root.resolve()))
    escaped_state_dir = repr(str(StateRepository(project_root).state_dir))
    return KernelHelperRequest(
        command_type="reload",
        label="reload" if module_name is None else f"reload {module_name}",
        context="reload modules" if module_name is None else "reload module",
        input_text=module_name,
        code=f"""
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
    {escaped_state_dir},
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
print(json.dumps(_report, default=str))
""",
    )
