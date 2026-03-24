from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, TypeAlias, cast

from .contracts import HelperAccessMetadata, HelperInitialRuntimeState, HelperWaitFor, KernelStatus
from .journal import JournalEntry
from .payloads import (
    HistoryEntryPayload,
    InspectPayload,
    NamespaceDeltaPayload,
    ReloadReport,
    RunSnapshot,
    VarDisplayEntry,
)
from .runs.store import ExecutionRecord
from .runtime import RuntimeState, RuntimeStateKind
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
