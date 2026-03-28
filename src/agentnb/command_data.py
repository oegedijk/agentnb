from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from .contracts import (
    ExecutionEvent,
    HelperAccessMetadata,
    HelperInitialRuntimeState,
    HelperWaitFor,
    KernelStatus,
)
from .introspection_models import (
    InspectValue,
    NamespaceDelta,
    ReloadResult,
    VariableEntry,
)
from .journal import JournalEntry
from .kernel.provisioner import DoctorCheck
from .payloads import JSONValue
from .runs import RunCancelOutcome
from .runs.store import ExecutionRecord
from .runtime import (
    DeleteSessionOutcome,
    DoctorStatus,
    RuntimeState,
    RuntimeStateKind,
    SessionListEntry,
)
from .state import CommandLockInfo


@dataclass(slots=True, kw_only=True)
class CommandData:
    switched_session: str | None = None


@dataclass(slots=True)
class KernelSessionData(CommandData):
    alive: bool
    pid: int | None = None
    connection_file: str | None = None
    started_at: str | None = None
    uptime_s: float | None = None
    python: str | None = None
    busy: bool | None = None
    runtime_state: RuntimeStateKind | None = None
    session_exists: bool | None = None
    lock_pid: int | None = None
    lock_acquired_at: str | None = None
    busy_for_ms: int | None = None
    waited: bool | None = None
    waited_for: HelperWaitFor | None = None
    waited_ms: int | None = None
    initial_runtime_state: HelperInitialRuntimeState | None = None
    started_new: bool | None = None

    @classmethod
    def from_kernel_status(
        cls,
        status: KernelStatus,
        *,
        runtime_state: RuntimeStateKind | None = None,
        session_exists: bool | None = None,
        command_lock: CommandLockInfo | None = None,
        started_new: bool | None = None,
        waited: bool | None = None,
        waited_for: HelperWaitFor | None = None,
        waited_ms: int | None = None,
        initial_runtime_state: HelperInitialRuntimeState | None = None,
    ) -> KernelSessionData:
        busy_for_ms = command_lock.busy_for_ms() if command_lock is not None else None
        return cls(
            alive=status.alive,
            pid=status.pid,
            connection_file=status.connection_file,
            started_at=status.started_at,
            uptime_s=status.uptime_s,
            python=status.python,
            busy=status.busy,
            runtime_state=runtime_state,
            session_exists=session_exists,
            lock_pid=command_lock.pid if command_lock is not None else None,
            lock_acquired_at=command_lock.acquired_at if command_lock is not None else None,
            busy_for_ms=busy_for_ms,
            waited=waited,
            waited_for=waited_for,
            waited_ms=waited_ms,
            initial_runtime_state=initial_runtime_state,
            started_new=started_new,
        )

    @classmethod
    def from_runtime_state(cls, state: RuntimeState) -> KernelSessionData:
        return cls.from_kernel_status(
            state.to_kernel_status(),
            runtime_state=state.kind,
            session_exists=state.session_exists,
            command_lock=state.command_lock,
        )


@dataclass(slots=True)
class ExecCommandData(CommandData):
    record: ExecutionRecord
    no_truncate: bool = False
    source_kind: Literal["argument", "file", "stdin"] | None = None
    source_path: str | None = None
    background: bool = False
    ensured_started: bool = False
    started_new_session: bool = False
    initial_runtime_state: HelperInitialRuntimeState | None = None
    session_restarted: bool = False
    session_python: str | None = None
    namespace_delta: NamespaceDelta | None = None
    selected_output: str | None = None
    selected_text: str | None = None


@dataclass(slots=True)
class InterruptCommandData(CommandData):
    interrupted: bool = True


@dataclass(slots=True)
class StopCommandData(CommandData):
    stopped: bool = True


@dataclass(slots=True, frozen=True)
class DoctorCheckData:
    name: str
    status: str
    message: str
    fix_hint: str | None = None

    @classmethod
    def from_check(cls, check: DoctorCheck) -> DoctorCheckData:
        return cls(
            name=check.name,
            status=check.status,
            message=check.message,
            fix_hint=check.fix_hint,
        )


@dataclass(slots=True)
class DoctorCommandData(CommandData):
    ready: bool
    selected_python: str | None = None
    python_source: str | None = None
    checks: list[DoctorCheckData] = field(default_factory=list)
    stale_session_cleaned: bool = False
    session_exists: bool = False
    kernel_alive: bool = False
    kernel_pid: int | None = None

    @classmethod
    def from_status(cls, status: DoctorStatus) -> DoctorCommandData:
        return cls(
            ready=status.ready,
            selected_python=status.selected_python,
            python_source=status.python_source,
            checks=[DoctorCheckData.from_check(check) for check in status.checks],
            stale_session_cleaned=status.stale_session_cleaned,
            session_exists=status.session_exists,
            kernel_alive=status.kernel_alive,
            kernel_pid=status.kernel_pid,
        )


@dataclass(slots=True, frozen=True)
class SessionListEntryData:
    session_id: str
    alive: bool
    pid: int | None = None
    connection_file: str | None = None
    started_at: str | None = None
    uptime_s: float | None = None
    python: str | None = None
    last_activity: str | None = None
    is_default: bool = False
    is_current: bool = False
    is_preferred: bool = False

    @classmethod
    def from_runtime_entry(cls, entry: SessionListEntry) -> SessionListEntryData:
        return cls(
            session_id=entry.session_id,
            alive=entry.alive,
            pid=entry.pid,
            connection_file=entry.connection_file,
            started_at=entry.started_at,
            uptime_s=entry.uptime_s,
            python=entry.python,
            last_activity=entry.last_activity,
            is_default=entry.is_default,
            is_current=entry.is_current,
            is_preferred=entry.is_preferred,
        )


@dataclass(slots=True)
class SessionsListCommandData(CommandData):
    sessions: list[SessionListEntryData]
    hidden_non_live_count: int = 0


@dataclass(slots=True)
class SessionDeleteCommandData(CommandData):
    deleted: bool
    session_id: str
    stopped_running_kernel: bool = False

    @classmethod
    def from_outcome(cls, outcome: DeleteSessionOutcome) -> SessionDeleteCommandData:
        return cls(
            deleted=outcome.deleted,
            session_id=outcome.session_id,
            stopped_running_kernel=outcome.stopped_running_kernel,
        )


@dataclass(slots=True)
class SessionsDeleteBulkCommandData(CommandData):
    deleted: list[str]
    count: int


@dataclass(slots=True, frozen=True)
class RunListEntryData:
    execution_id: str
    ts: str
    session_id: str
    command_type: str
    status: str
    duration_ms: int
    cancel_requested: bool = False
    terminal_reason: str | None = None
    result: str | None = None
    result_preview: object | None = None
    stdout: str = ""
    error_type: str | None = None


@dataclass(slots=True, frozen=True)
class RunSnapshotData:
    execution_id: str
    ts: str
    session_id: str
    command_type: str
    status: str
    duration_ms: int
    code: str | None = None
    worker_pid: int | None = None
    stdout: str = ""
    stderr: str = ""
    result: str | None = None
    execution_count: int | None = None
    ename: str | None = None
    evalue: str | None = None
    traceback: list[str] | None = None
    events: list[ExecutionEvent] = field(default_factory=list)
    terminal_reason: str | None = None
    cancel_requested: bool = False
    cancel_requested_at: str | None = None
    cancel_request_source: str | None = None
    recorded_status: str | None = None
    recorded_ename: str | None = None
    recorded_evalue: str | None = None
    recorded_traceback: list[str] | None = None
    failure_origin: str | None = None
    error_data: dict[str, JSONValue] | None = None


@dataclass(slots=True)
class RunLookupCommandData(CommandData):
    run: RunSnapshotData
    include_output: bool = True
    snapshot_stale: bool = False
    status: str | None = None
    completion_reason: Literal["terminal", "window_elapsed"] | None = None
    replayed_event_count: int | None = None
    emitted_event_count: int | None = None


@dataclass(slots=True)
class RunsListCommandData(CommandData):
    runs: list[RunListEntryData]


@dataclass(slots=True)
class RunCancelCommandData(CommandData):
    execution_id: str
    session_id: str
    cancel_requested: bool
    status: str
    run_status: str
    session_outcome: Literal["unchanged", "preserved", "stopped"] = "unchanged"

    @classmethod
    def from_outcome(cls, outcome: RunCancelOutcome) -> RunCancelCommandData:
        return cls(
            execution_id=outcome.execution_id,
            session_id=outcome.session_id,
            cancel_requested=outcome.cancel_requested,
            status=outcome.status,
            run_status=outcome.run_status,
            session_outcome=outcome.session_outcome,
        )


@dataclass(slots=True)
class VarsCommandData(CommandData):
    values: list[VariableEntry]
    access_metadata: HelperAccessMetadata = field(default_factory=HelperAccessMetadata)


@dataclass(slots=True)
class InspectCommandData(CommandData):
    value: InspectValue
    access_metadata: HelperAccessMetadata = field(default_factory=HelperAccessMetadata)


@dataclass(slots=True)
class ReloadCommandData(CommandData):
    result: ReloadResult
    access_metadata: HelperAccessMetadata = field(default_factory=HelperAccessMetadata)


@dataclass(slots=True)
class HistoryCommandData(CommandData):
    entries: list[JournalEntry]
    full: bool = False


def with_switched_session(data: CommandData, switched_session: str) -> CommandData:
    data.switched_session = switched_session
    return data


def run_lookup_session_id(data: RunLookupCommandData) -> str | None:
    session_id = data.run.session_id
    return session_id if session_id else None
