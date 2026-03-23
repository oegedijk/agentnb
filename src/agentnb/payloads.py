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
    result_preview: InspectPreview
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
    is_preferred: bool


class DeleteSessionResult(TypedDict):
    deleted: bool
    session_id: str
    stopped_running_kernel: bool


class BulkDeleteResult(TypedDict):
    deleted: list[str]
    count: int


class VarEntry(TypedDict):
    name: str
    type: str
    repr: str


class NamespaceDeltaEntry(VarEntry):
    change: Literal["new", "updated"]


class NamespaceDeltaPayload(TypedDict):
    entries: list[NamespaceDeltaEntry]
    new_count: int
    updated_count: int
    truncated: bool


class DataframePreview(TypedDict, total=False):
    kind: Literal["dataframe-like"]
    shape: list[int]
    columns: list[str]
    column_count: int
    columns_shown: int
    dtypes: dict[str, str] | None
    dtypes_shown: int
    head: list[dict[str, JSONValue]] | None
    head_rows_shown: int
    null_counts: dict[str, int]
    null_count_fields_shown: int


class MappingPreview(TypedDict, total=False):
    kind: Literal["mapping-like"]
    length: int
    keys: list[str]
    keys_shown: int
    sample: dict[str, JSONValue]
    sample_items_shown: int
    sample_truncated: bool


class SequencePreview(TypedDict, total=False):
    kind: Literal["sequence-like"]
    length: int
    sample: list[JSONValue]
    sample_items_shown: int
    sample_truncated: bool
    item_type: str
    sample_keys: list[str]
    sample_keys_shown: int


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
    started_new_session: bool
    waited: bool
    waited_for: Literal["ready", "idle"]
    waited_ms: int
    initial_runtime_state: Literal["missing", "starting", "ready", "busy", "dead", "stale"]


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
    waited_ms: int
    initial_runtime_state: Literal["missing", "starting", "ready", "busy", "dead", "stale"]


class StartPayload(StatusPayload, total=False):
    started_new: bool


class ExecPayload(TypedDict, total=False):
    status: str | None
    duration_ms: int
    execution_id: str | None
    execution_count: int
    stdout: str
    stderr: str
    result: str
    stdout_truncated: bool
    stderr_truncated: bool
    result_truncated: bool
    result_preview: InspectPreview
    ename: str
    evalue: str
    background: bool
    ensured_started: bool
    started_new_session: bool
    initial_runtime_state: Literal["missing", "starting", "ready", "busy", "dead", "stale"]
    session_restarted: bool
    session_python: str
    source_kind: Literal["argument", "file", "stdin"]
    source_path: str
    namespace_delta: NamespaceDeltaPayload
    wait_behavior: str
    waited_ms: int
    lock_pid: int
    lock_acquired_at: str
    busy_for_ms: int
    active_execution_id: str
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
    stdout_truncated: bool
    stderr_truncated: bool
    result_truncated: bool
    result_preview: InspectPreview
    ename: str | None
    evalue: str | None
    session_python: str
    source_kind: Literal["argument", "file", "stdin"]
    source_path: str
    namespace_delta: NamespaceDeltaPayload
    wait_behavior: str
    waited_ms: int
    lock_pid: int
    lock_acquired_at: str
    busy_for_ms: int
    active_execution_id: str
    selected_output: str
    selected_text: str


class VarDisplayEntry(TypedDict, total=False):
    name: str
    type: str
    repr: str


class HelperAccessPayload(TypedDict, total=False):
    started_new_session: bool
    waited: bool
    waited_for: Literal["ready", "idle"]
    waited_ms: int
    initial_runtime_state: Literal["missing", "starting", "ready", "busy", "dead", "stale"]
    blocking_execution_id: str


class VarsPayload(HelperAccessPayload):
    vars: list[VarDisplayEntry]


class InspectResponsePayload(HelperAccessPayload):
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


class SessionsListPayload(TypedDict, total=False):
    sessions: list[SessionSummary]
    hidden_non_live_count: int


class RunListEntryPayload(TypedDict, total=False):
    execution_id: str | None
    ts: str | None
    session_id: str | None
    command_type: str | None
    status: str | None
    duration_ms: int | None
    terminal_reason: str | None
    cancel_requested: bool
    result_preview: str | InspectPreview
    stdout_preview: str
    error_type: str


class RunsListPayload(TypedDict):
    runs: list[RunListEntryPayload]


class RunLookupPayload(TypedDict, total=False):
    run: RunSnapshot
    status: str
    completion_reason: Literal["terminal", "window_elapsed"]
    replayed_event_count: int
    emitted_event_count: int
