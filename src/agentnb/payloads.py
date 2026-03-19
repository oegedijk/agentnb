from __future__ import annotations

from typing import Literal, TypeAlias, TypedDict

JSONScalar: TypeAlias = None | bool | int | float | str
JSONValue: TypeAlias = JSONScalar | list["JSONValue"] | dict[str, "JSONValue"]


class ExecutionEventPayload(TypedDict, total=False):
    kind: str
    content: str | None
    metadata: dict[str, object]


class RunSnapshot(TypedDict, total=False):
    execution_id: str
    ts: str
    session_id: str
    command_type: str
    status: str
    snapshot_stale: bool
    duration_ms: int
    code: str | None
    worker_pid: int | None
    stdout: str
    stderr: str
    result: str | None
    execution_count: int | None
    ename: str | None
    evalue: str | None
    traceback: list[str] | None
    events: list[ExecutionEventPayload]
    terminal_reason: str | None
    cancel_requested: bool
    cancel_requested_at: str | None
    cancel_request_source: str | None
    recorded_status: str | None
    recorded_ename: str | None
    recorded_evalue: str | None
    recorded_traceback: list[str] | None
    failure_origin: str | None
    error_data: dict[str, JSONValue]


class StoredRunSnapshot(RunSnapshot, total=False):
    outputs: list[dict[str, object]]
    journal_entries: list[dict[str, object]]


class CancelRunResult(TypedDict):
    execution_id: str
    session_id: str
    cancel_requested: bool
    status: str
    run_status: str
    session_outcome: str


class SessionSummary(TypedDict, total=False):
    session_id: str
    alive: bool
    pid: int | None
    connection_file: str | None
    started_at: str | None
    uptime_s: float | None
    python: str | None
    last_activity: str | None
    is_default: bool
    is_current: bool


class DeleteSessionResult(TypedDict):
    deleted: bool
    session_id: str
    stopped_running_kernel: bool


class VarEntry(TypedDict):
    name: str
    type: str
    repr: str


class DataframePreview(TypedDict, total=False):
    kind: Literal["dataframe-like"]
    shape: list[int]
    columns: list[str]
    column_count: int
    dtypes: dict[str, str] | None
    head: list[dict[str, JSONValue]] | None
    null_counts: dict[str, int]


class MappingPreview(TypedDict):
    kind: Literal["mapping-like"]
    length: int
    keys: list[str]
    sample: dict[str, JSONValue]


class SequencePreview(TypedDict, total=False):
    kind: Literal["sequence-like"]
    length: int
    sample: list[JSONValue]
    item_type: str
    sample_keys: list[str]


InspectPreview: TypeAlias = DataframePreview | MappingPreview | SequencePreview


class InspectPayload(TypedDict, total=False):
    name: str
    type: str
    repr: str
    members: list[str]
    doc: str
    preview: InspectPreview


class FailedModuleEntry(TypedDict):
    module: str
    error_type: str
    message: str


class ReloadReport(TypedDict, total=False):
    mode: Literal["module", "project"]
    requested_module: str | None
    reloaded_modules: list[str]
    failed_modules: list[FailedModuleEntry]
    skipped_modules: list[str]
    rebound_names: list[str]
    stale_names: list[str]
    excluded_module_count: int
    notes: list[str]


class StatusPayload(TypedDict, total=False):
    alive: bool
    pid: int | None
    connection_file: str | None
    started_at: str | None
    uptime_s: float | None
    python: str | None
    busy: bool | None
    lock_pid: int
    lock_acquired_at: str
    busy_for_ms: int
    runtime_state: Literal["missing", "starting", "ready", "busy", "dead", "stale"]
    session_exists: bool
    waited: bool
    waited_for: Literal["ready", "idle"]


class StartPayload(StatusPayload, total=False):
    started_new: bool
    auto_install: bool


class ExecPayload(TypedDict, total=False):
    status: str | None
    duration_ms: int
    execution_id: str | None
    execution_count: int
    stdout: str
    stderr: str
    result: str
    ename: str
    evalue: str
    background: bool
    ensured_started: bool
    started_new_session: bool
    wait_behavior: str
    waited_ms: int
    lock_pid: int
    lock_acquired_at: str
    busy_for_ms: int
    selected_output: str
    selected_text: str


class CompactExecPayloadInput(TypedDict, total=False):
    status: str | None
    duration_ms: int
    execution_id: str | None
    execution_count: int
    stdout: str
    stderr: str
    result: str | None
    ename: str | None
    evalue: str | None
    wait_behavior: str
    waited_ms: int
    lock_pid: int
    lock_acquired_at: str
    busy_for_ms: int
    selected_output: str
    selected_text: str


class VarDisplayEntry(TypedDict, total=False):
    name: str
    type: str
    repr: str


class VarsPayload(TypedDict):
    vars: list[VarDisplayEntry]


class InspectResponsePayload(TypedDict):
    inspect: InspectPayload


class HistoryEntryPayload(TypedDict, total=False):
    kind: str | None
    ts: str | None
    status: str | None
    duration_ms: int | None
    command_type: str | None
    label: str | None
    user_visible: bool | None
    error_type: str
    execution_id: str
    code: str


class HistoryPayload(TypedDict):
    entries: list[HistoryEntryPayload]


class InterruptPayload(TypedDict):
    interrupted: Literal[True]


class StopPayload(TypedDict):
    stopped: Literal[True]


class DoctorCheckPayload(TypedDict, total=False):
    name: str
    status: str
    message: str
    fix_hint: str | None


class DoctorPayload(TypedDict, total=False):
    ready: bool
    selected_python: str | None
    python_source: str | None
    checks: list[DoctorCheckPayload]
    stale_session_cleaned: bool
    session_exists: bool
    kernel_alive: bool
    kernel_pid: int | None


class SessionsListPayload(TypedDict):
    sessions: list[SessionSummary]


class RunListEntryPayload(TypedDict, total=False):
    execution_id: str | None
    ts: str | None
    session_id: str | None
    command_type: str | None
    status: str | None
    duration_ms: int | None
    terminal_reason: str | None
    cancel_requested: bool
    result_preview: str
    stdout_preview: str
    error_type: str


class RunsListPayload(TypedDict):
    runs: list[RunListEntryPayload]


class RunLookupPayload(TypedDict):
    run: RunSnapshot
