from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, TypeAlias, cast

from .contracts import (
    HelperAccessMetadata,
    HelperInitialRuntimeState,
    HelperWaitFor,
    KernelStatus,
)
from .journal import JournalEntry
from .kernel.provisioner import DoctorCheck
from .payloads import (
    HistoryEntryPayload,
    InspectPayload,
    NamespaceDeltaPayload,
    ReloadReport,
    RunSnapshot,
    VarDisplayEntry,
)
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

SerializedPayload: TypeAlias = Mapping[str, object]


@dataclass(slots=True, kw_only=True)
class CommandData:
    switched_session: str | None = None


@dataclass(slots=True)
class SerializedCommandData(CommandData):
    payload: dict[str, object] = field(default_factory=dict)


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
    namespace_delta: NamespaceDeltaPayload | None = None
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

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> DoctorCheckData:
        return cls(
            name=str(payload.get("name") or ""),
            status=str(payload.get("status") or ""),
            message=str(payload.get("message") or ""),
            fix_hint=cast(str | None, payload.get("fix_hint")),
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
    def from_status(cls, status: DoctorStatus | Mapping[str, object]) -> DoctorCommandData:
        if isinstance(status, Mapping):
            return cls.from_mapping(cast(Mapping[str, object], status))
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

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> DoctorCommandData:
        checks = payload.get("checks")
        return cls(
            ready=bool(payload.get("ready")),
            selected_python=cast(str | None, payload.get("selected_python")),
            python_source=cast(str | None, payload.get("python_source")),
            checks=[
                DoctorCheckData.from_mapping(cast(Mapping[str, object], check))
                for check in checks
                if isinstance(check, Mapping)
            ]
            if isinstance(checks, list)
            else [],
            stale_session_cleaned=bool(payload.get("stale_session_cleaned")),
            session_exists=bool(payload.get("session_exists")),
            kernel_alive=bool(payload.get("kernel_alive")),
            kernel_pid=cast(int | None, payload.get("kernel_pid")),
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
    def from_runtime_entry(
        cls, entry: SessionListEntry | Mapping[str, object]
    ) -> SessionListEntryData:
        if isinstance(entry, Mapping):
            return cls.from_mapping(cast(Mapping[str, object], entry))
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

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> SessionListEntryData:
        return cls(
            session_id=str(payload.get("session_id") or ""),
            alive=bool(payload.get("alive")),
            pid=cast(int | None, payload.get("pid")),
            connection_file=cast(str | None, payload.get("connection_file")),
            started_at=cast(str | None, payload.get("started_at")),
            uptime_s=cast(float | None, payload.get("uptime_s")),
            python=cast(str | None, payload.get("python")),
            last_activity=cast(str | None, payload.get("last_activity")),
            is_default=bool(payload.get("is_default")),
            is_current=bool(payload.get("is_current")),
            is_preferred=bool(payload.get("is_preferred")),
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
    def from_outcome(
        cls, outcome: DeleteSessionOutcome | Mapping[str, object]
    ) -> SessionDeleteCommandData:
        if isinstance(outcome, Mapping):
            return cls.from_mapping(cast(Mapping[str, object], outcome))
        return cls(
            deleted=outcome.deleted,
            session_id=outcome.session_id,
            stopped_running_kernel=outcome.stopped_running_kernel,
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> SessionDeleteCommandData:
        return cls(
            deleted=bool(payload.get("deleted", True)),
            session_id=str(payload.get("session_id") or ""),
            stopped_running_kernel=bool(payload.get("stopped_running_kernel")),
        )


@dataclass(slots=True)
class SessionsDeleteBulkCommandData(CommandData):
    deleted: list[str]
    count: int


@dataclass(slots=True)
class RunSnapshotData(CommandData):
    payload: dict[str, object]
    include_output: bool = True
    snapshot_stale: bool = False


@dataclass(slots=True)
class RunLookupCommandData(CommandData):
    run: RunSnapshotData
    status: str | None = None
    completion_reason: Literal["terminal", "window_elapsed"] | None = None
    replayed_event_count: int | None = None
    emitted_event_count: int | None = None


@dataclass(slots=True)
class RunListEntryData(CommandData):
    payload: dict[str, object]


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
    def from_outcome(cls, outcome: RunCancelOutcome | Mapping[str, object]) -> RunCancelCommandData:
        if isinstance(outcome, Mapping):
            return cls.from_mapping(cast(Mapping[str, object], outcome))
        return cls(
            execution_id=outcome.execution_id,
            session_id=outcome.session_id,
            cancel_requested=outcome.cancel_requested,
            status=outcome.status,
            run_status=outcome.run_status,
            session_outcome=outcome.session_outcome,
        )

    @classmethod
    def from_mapping(cls, payload: Mapping[str, object]) -> RunCancelCommandData:
        return cls(
            execution_id=str(payload.get("execution_id") or ""),
            session_id=str(payload.get("session_id") or "default"),
            cancel_requested=bool(payload.get("cancel_requested")),
            status=str(payload.get("status") or ""),
            run_status=str(payload.get("run_status") or payload.get("status") or ""),
            session_outcome=cast(
                Literal["unchanged", "preserved", "stopped"],
                payload.get("session_outcome") or "unchanged",
            ),
        )


@dataclass(slots=True)
class VarsCommandData(CommandData):
    values: list[VarDisplayEntry]
    access_metadata: HelperAccessMetadata = field(default_factory=HelperAccessMetadata)


@dataclass(slots=True)
class InspectCommandData(CommandData):
    payload: InspectPayload
    access_metadata: HelperAccessMetadata = field(default_factory=HelperAccessMetadata)


@dataclass(slots=True)
class ReloadCommandData(CommandData):
    payload: ReloadReport
    access_metadata: HelperAccessMetadata = field(default_factory=HelperAccessMetadata)


@dataclass(slots=True)
class HistoryCommandData(CommandData):
    entries: list[JournalEntry]
    full: bool = False


CommandDataLike: TypeAlias = CommandData | SerializedPayload


def ensure_command_data(data: CommandDataLike) -> CommandData:
    if isinstance(data, CommandData):
        return data
    return SerializedCommandData(payload=_mapping_to_dict(data))


def compat_command_data(command: str, data: Mapping[str, object] | None) -> CommandData | None:
    payload = data or {}
    if command == "interrupt":
        return InterruptCommandData(interrupted=bool(payload.get("interrupted", True)))
    if command == "stop":
        return StopCommandData(stopped=bool(payload.get("stopped", True)))
    if command == "doctor":
        return DoctorCommandData.from_mapping(payload)
    if command == "sessions-list":
        sessions = payload.get("sessions")
        return SessionsListCommandData(
            sessions=[
                SessionListEntryData.from_mapping(cast(Mapping[str, object], session))
                for session in sessions
                if isinstance(session, Mapping)
            ]
            if isinstance(sessions, list)
            else [],
            hidden_non_live_count=cast(int, payload.get("hidden_non_live_count") or 0),
        )
    if command == "sessions-delete":
        return SessionDeleteCommandData.from_mapping(payload)
    if command == "sessions-delete-bulk":
        deleted = payload.get("deleted")
        return SessionsDeleteBulkCommandData(
            deleted=[str(item) for item in deleted] if isinstance(deleted, list) else [],
            count=cast(int, payload.get("count") or 0),
        )
    if command == "runs-cancel":
        return RunCancelCommandData.from_mapping(payload)
    return None


def with_switched_session(data: CommandDataLike, switched_session: str) -> CommandDataLike:
    if isinstance(data, CommandData):
        data.switched_session = switched_session
        return data
    payload = _mapping_to_dict(data)
    payload["switched_session"] = switched_session
    return payload


def run_lookup_session_id(data: RunLookupCommandData) -> str | None:
    session_id = data.run.payload.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None


def normalize_run_payload(run: ExecutionRecord | Mapping[str, object]) -> dict[str, object]:
    if isinstance(run, ExecutionRecord):
        return _mapping_to_dict(run.to_dict())
    return _mapping_to_dict(run)


def run_snapshot_payload(
    payload: Mapping[str, object],
) -> RunSnapshot:
    return cast(RunSnapshot, _mapping_to_dict(payload))


def history_entries_payload(
    payload: list[HistoryEntryPayload],
) -> list[HistoryEntryPayload]:
    return [cast(HistoryEntryPayload, _mapping_to_dict(entry)) for entry in payload]


def _mapping_to_dict(payload: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items()}
