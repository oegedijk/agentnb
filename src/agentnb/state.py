from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from .contracts import utc_now_iso

if TYPE_CHECKING:
    from .state_layout import StateLayout
    from .state_manifest import StateManifestRepository
    from .state_persisted_resources import PersistedResourceRepository
    from .state_runtime import RuntimeStateRepository

STATE_DIR_NAME = ".agentnb"
STATE_MANIFEST_FILE_NAME = "state-manifest.json"
STATE_SCHEMA_VERSION = "2"
HISTORY_FILE_NAME = "history.jsonl"
EXECUTIONS_FILE_NAME = "executions.jsonl"
SESSION_PREFERENCES_FILE_NAME = "session-preferences.json"
LEGACY_SESSION_FILE_NAME = "session.json"
COMMAND_LOCK_FILE_NAME = "command.lock"
RESOURCE_DESCRIPTOR_FILE_NAME = "descriptor.json"
RESOURCE_PAYLOAD_DIR_NAME = "payload"
SNAPSHOT_DESCRIPTOR_SCHEMA_VERSION = "1"
EXPORT_DESCRIPTOR_SCHEMA_VERSION = "1"

ResourceKind = Literal["file", "directory"]
PersistedResourceKind = Literal["snapshot", "export"]
ResourceLifecycle = Literal["allocating", "ready", "failed", "deleted"]

_RESOURCE_ID_PATTERN = re.compile(r"^[a-f0-9]{16}$")
_SESSION_RECORD_FILE_PATTERN = re.compile(r"^session-[a-f0-9]{12}\.json$")
_KERNEL_CONNECTION_FILE_PATTERN = re.compile(r"^kernel-(.+)\.json$")
_KERNEL_LOG_FILE_PATTERN = re.compile(r"^kernel-(.+)\.log$")
_COMMAND_LOCK_ARTIFACT_PATTERN = re.compile(r"^command\.lock-(.+)$")
HISTORY_RESOURCE_VERSION = "2"
EXECUTIONS_RESOURCE_VERSION = "2"


def _default_resource_versions() -> dict[str, str]:
    return {
        "history": HISTORY_RESOURCE_VERSION,
        "executions": EXECUTIONS_RESOURCE_VERSION,
    }


@dataclass(slots=True, frozen=True)
class StateResource:
    name: str
    kind: ResourceKind
    relative_path: Path

    def resolve(self, state_dir: Path) -> Path:
        return state_dir / self.relative_path


@dataclass(slots=True, frozen=True)
class SessionStateFiles:
    session_id: str
    state_dir: Path
    session_record: Path
    legacy_session_record: Path
    connection_file: Path
    log_file: Path
    command_lock_file: Path

    def record_candidates(self) -> tuple[Path, ...]:
        if self.legacy_session_record == self.session_record:
            return (self.session_record,)
        return (self.session_record, self.legacy_session_record)

    def ensure_state_dir(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def clear_runtime_files(self) -> None:
        _safe_unlink(self.connection_file)
        _safe_unlink(self.log_file)
        _safe_unlink(self.command_lock_file)

    def command_lock_info(self) -> CommandLockInfo | None:
        if not self.command_lock_file.exists():
            return None
        return self._read_command_lock_info()

    def has_active_command_lock(self) -> bool:
        return self.command_lock_info() is not None

    @contextmanager
    def acquire_command_lock(self) -> Iterator[bool]:
        self.ensure_state_dir()
        lock_acquired = self._try_create_command_lock()
        if not lock_acquired and self._clear_stale_command_lock():
            lock_acquired = self._try_create_command_lock()
        try:
            yield lock_acquired
        finally:
            if lock_acquired:
                _safe_unlink(self.command_lock_file)

    def _try_create_command_lock(self) -> bool:
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        try:
            fd = os.open(self.command_lock_file, flags)
        except FileExistsError:
            return False

        try:
            payload = {
                "pid": os.getpid(),
                "acquired_at": utc_now_iso(),
            }
            os.write(fd, json.dumps(payload, ensure_ascii=True).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def _clear_stale_command_lock(self) -> bool:
        if not self.command_lock_file.exists():
            return False
        info = self._read_command_lock_info()
        return info is None and not self.command_lock_file.exists()

    def _read_command_lock_info(self) -> CommandLockInfo | None:
        try:
            raw_payload = self.command_lock_file.read_text(encoding="utf-8")
        except OSError:
            return None

        lock_info = _parse_command_lock_payload(raw_payload)
        if lock_info is None:
            _safe_unlink(self.command_lock_file)
            return None

        if _pid_exists(lock_info.pid):
            return lock_info

        _safe_unlink(self.command_lock_file)
        return None


@dataclass(slots=True, frozen=True)
class CommandLockInfo:
    pid: int
    acquired_at: str | None = None

    def busy_for_ms(self) -> int | None:
        acquired_at = _parse_iso_datetime(self.acquired_at)
        if acquired_at is None:
            return None
        elapsed_ms = int((datetime.now(UTC) - acquired_at).total_seconds() * 1000)
        return max(elapsed_ms, 0)


@dataclass(slots=True, frozen=True)
class StateManifest:
    schema_version: str = STATE_SCHEMA_VERSION
    resource_versions: dict[str, str] = field(default_factory=_default_resource_versions)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"schema_version": self.schema_version}
        if self.resource_versions:
            payload["resource_versions"] = dict(self.resource_versions)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> StateManifest:
        schema_version = payload.get("schema_version")
        if not isinstance(schema_version, str) or not schema_version:
            raise ValueError("Missing schema_version")

        raw_versions = payload.get("resource_versions", {})
        resource_versions: dict[str, str] = {}
        if isinstance(raw_versions, dict):
            for key, value in raw_versions.items():
                if isinstance(key, str) and isinstance(value, str):
                    resource_versions[key] = value

        return cls(schema_version=schema_version, resource_versions=resource_versions)


@dataclass(slots=True, frozen=True)
class SessionPreferences:
    current_session_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if self.current_session_id is not None:
            payload["current_session_id"] = self.current_session_id
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SessionPreferences:
        current_session_id = payload.get("current_session_id")
        if current_session_id is None:
            return cls()
        if not isinstance(current_session_id, str):
            raise ValueError("Invalid current_session_id")
        return cls(current_session_id=current_session_id)


@dataclass(slots=True, frozen=True)
class PersistedResourcePaths:
    id: str
    kind: PersistedResourceKind
    root_dir: Path
    descriptor_file: Path
    payload_dir: Path


@dataclass(slots=True, frozen=True)
class PersistedResourceDescriptor:
    id: str
    kind: PersistedResourceKind
    schema_version: str
    created_at: str
    updated_at: str
    lifecycle: ResourceLifecycle
    label: str | None = None
    source_session_id: str | None = None
    source_execution_id: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "lifecycle": self.lifecycle,
        }
        if self.label is not None:
            payload["label"] = self.label
        if self.source_session_id is not None:
            payload["source_session_id"] = self.source_session_id
        if self.source_execution_id is not None:
            payload["source_execution_id"] = self.source_execution_id
        if self.error is not None:
            payload["error"] = self.error
        return payload


@dataclass(slots=True, frozen=True)
class SnapshotDescriptor(PersistedResourceDescriptor):
    selected_resources: list[str] = field(default_factory=list)
    source_manifest_schema_version: str | None = None
    source_resource_versions: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = PersistedResourceDescriptor.to_dict(self)
        if self.selected_resources:
            payload["selected_resources"] = list(self.selected_resources)
        if self.source_manifest_schema_version is not None:
            payload["source_manifest_schema_version"] = self.source_manifest_schema_version
        if self.source_resource_versions:
            payload["source_resource_versions"] = dict(self.source_resource_versions)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> SnapshotDescriptor:
        descriptor = _parse_resource_descriptor(payload, expected_kind="snapshot")
        raw_selected_resources = payload.get("selected_resources", [])
        selected_resources = _read_str_list(
            raw_selected_resources,
            field_name="selected_resources",
            allow_empty=True,
        )
        raw_resource_versions = payload.get("source_resource_versions", {})
        source_resource_versions = _read_str_map(
            raw_resource_versions,
            field_name="source_resource_versions",
        )
        source_manifest_schema_version = _optional_str(
            payload,
            "source_manifest_schema_version",
        )
        return cls(
            id=descriptor.id,
            kind=descriptor.kind,
            schema_version=descriptor.schema_version,
            created_at=descriptor.created_at,
            updated_at=descriptor.updated_at,
            lifecycle=descriptor.lifecycle,
            label=descriptor.label,
            source_session_id=descriptor.source_session_id,
            source_execution_id=descriptor.source_execution_id,
            error=descriptor.error,
            selected_resources=selected_resources,
            source_manifest_schema_version=source_manifest_schema_version,
            source_resource_versions=source_resource_versions,
        )


@dataclass(slots=True, frozen=True)
class ExportDescriptor(PersistedResourceDescriptor):
    export_format: str | None = None
    source_kind: str | None = None
    source_id: str | None = None
    output_files: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = PersistedResourceDescriptor.to_dict(self)
        if self.export_format is not None:
            payload["export_format"] = self.export_format
        if self.source_kind is not None:
            payload["source_kind"] = self.source_kind
        if self.source_id is not None:
            payload["source_id"] = self.source_id
        if self.output_files:
            payload["output_files"] = list(self.output_files)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExportDescriptor:
        descriptor = _parse_resource_descriptor(payload, expected_kind="export")
        raw_output_files = payload.get("output_files", [])
        output_files = _read_str_list(
            raw_output_files,
            field_name="output_files",
            allow_empty=True,
        )
        return cls(
            id=descriptor.id,
            kind=descriptor.kind,
            schema_version=descriptor.schema_version,
            created_at=descriptor.created_at,
            updated_at=descriptor.updated_at,
            lifecycle=descriptor.lifecycle,
            label=descriptor.label,
            source_session_id=descriptor.source_session_id,
            source_execution_id=descriptor.source_execution_id,
            error=descriptor.error,
            export_format=_optional_str(payload, "export_format"),
            source_kind=_optional_str(payload, "source_kind"),
            source_id=_optional_str(payload, "source_id"),
            output_files=output_files,
        )


@dataclass(slots=True, frozen=True)
class SnapshotAllocation:
    descriptor: SnapshotDescriptor
    paths: PersistedResourcePaths


@dataclass(slots=True, frozen=True)
class ExportAllocation:
    descriptor: ExportDescriptor
    paths: PersistedResourcePaths


def session_file_name(session_id: str) -> str:
    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]
    return f"session-{digest}.json"


def kernel_connection_file(state_dir: Path, session_id: str) -> Path:
    return state_dir / f"kernel-{session_id}.json"


def kernel_log_file(state_dir: Path, session_id: str) -> Path:
    return state_dir / f"kernel-{session_id}.log"


def command_lock_file(state_dir: Path, session_id: str) -> Path:
    return state_dir / f"{COMMAND_LOCK_FILE_NAME}-{session_id}"


_STATE_RESOURCES: dict[str, StateResource] = {
    "history": StateResource(
        name="history",
        kind="file",
        relative_path=Path(HISTORY_FILE_NAME),
    ),
    "executions": StateResource(
        name="executions",
        kind="file",
        relative_path=Path(EXECUTIONS_FILE_NAME),
    ),
    "session_preferences": StateResource(
        name="session_preferences",
        kind="file",
        relative_path=Path(SESSION_PREFERENCES_FILE_NAME),
    ),
    "legacy_session": StateResource(
        name="legacy_session",
        kind="file",
        relative_path=Path(LEGACY_SESSION_FILE_NAME),
    ),
    "snapshots": StateResource(
        name="snapshots",
        kind="directory",
        relative_path=Path("snapshots"),
    ),
    "artifacts": StateResource(
        name="artifacts",
        kind="directory",
        relative_path=Path("artifacts"),
    ),
    "exports": StateResource(
        name="exports",
        kind="directory",
        relative_path=Path("exports"),
    ),
    "metadata": StateResource(
        name="metadata",
        kind="directory",
        relative_path=Path("metadata"),
    ),
}


def _new_resource_id() -> str:
    return uuid.uuid4().hex[:16]


def _validate_resource_id(resource_id: str) -> str:
    if _RESOURCE_ID_PATTERN.fullmatch(resource_id):
        return resource_id
    raise ValueError(f"Invalid resource id: {resource_id}")


def _parse_resource_descriptor(
    payload: dict[str, Any],
    *,
    expected_kind: PersistedResourceKind,
) -> PersistedResourceDescriptor:
    descriptor = PersistedResourceDescriptor(
        id=_require_str(payload, "id"),
        kind=cast(PersistedResourceKind, _require_literal(payload, "kind", {"snapshot", "export"})),
        schema_version=_require_str(payload, "schema_version"),
        created_at=_require_str(payload, "created_at"),
        updated_at=_require_str(payload, "updated_at"),
        lifecycle=cast(
            ResourceLifecycle,
            _require_literal(
                payload,
                "lifecycle",
                {"allocating", "ready", "failed", "deleted"},
            ),
        ),
        label=_optional_str(payload, "label"),
        source_session_id=_optional_str(payload, "source_session_id"),
        source_execution_id=_optional_str(payload, "source_execution_id"),
        error=_optional_str(payload, "error"),
    )
    if descriptor.kind != expected_kind:
        raise ValueError(f"Expected {expected_kind} descriptor")
    _validate_resource_id(descriptor.id)
    return descriptor


def _read_str_map(value: object, *, field_name: str) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"Invalid {field_name}")
    parsed: dict[str, str] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not isinstance(item, str):
            raise ValueError(f"Invalid {field_name}")
        parsed[key] = item
    return parsed


def _read_str_list(
    value: object,
    *,
    field_name: str,
    allow_empty: bool,
) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"Invalid {field_name}")
    if not all(isinstance(item, str) for item in value):
        raise ValueError(f"Invalid {field_name}")
    if not allow_empty and not value:
        raise ValueError(f"Invalid {field_name}")
    return cast(list[str], list(value))


def _require_str(payload: dict[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Missing {key}")
    return value


def _optional_str(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"Invalid {key}")
    return value


def _require_literal(
    payload: dict[str, Any],
    key: str,
    allowed: set[str],
) -> str:
    value = _require_str(payload, key)
    if value not in allowed:
        raise ValueError(f"Invalid {key}")
    return value


def _parse_command_lock_payload(raw_payload: str) -> CommandLockInfo | None:
    raw_payload = raw_payload.strip()
    if not raw_payload:
        return None

    try:
        pid = int(raw_payload)
    except ValueError:
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        raw_pid = payload.get("pid")
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError):
            return None
        acquired_at = payload.get("acquired_at")
        if not isinstance(acquired_at, str) or _parse_iso_datetime(acquired_at) is None:
            acquired_at = None
    else:
        acquired_at = None

    if pid <= 0:
        return None
    return CommandLockInfo(pid=pid, acquired_at=acquired_at)


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _runtime_artifact_session_id(name: str) -> str | None:
    for pattern in (
        _KERNEL_CONNECTION_FILE_PATTERN,
        _KERNEL_LOG_FILE_PATTERN,
        _COMMAND_LOCK_ARTIFACT_PATTERN,
    ):
        match = pattern.fullmatch(name)
        if match is not None:
            return match.group(1)
    return None


def _pid_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@dataclass(slots=True, frozen=True)
class StateRepository:
    project_root: Path
    _layout: StateLayout = field(init=False, repr=False)
    _manifests: StateManifestRepository = field(init=False, repr=False)
    _runtime: RuntimeStateRepository = field(init=False, repr=False)
    _resources: PersistedResourceRepository = field(init=False, repr=False)

    def __post_init__(self) -> None:
        from .state_layout import StateLayout
        from .state_manifest import StateManifestRepository
        from .state_persisted_resources import PersistedResourceRepository
        from .state_runtime import RuntimeStateRepository

        layout = StateLayout(self.project_root)
        object.__setattr__(self, "project_root", layout.project_root)
        object.__setattr__(self, "_layout", layout)
        manifests = StateManifestRepository(layout)
        object.__setattr__(self, "_manifests", manifests)
        object.__setattr__(self, "_runtime", RuntimeStateRepository(layout, manifests))
        object.__setattr__(self, "_resources", PersistedResourceRepository(layout, manifests))

    @property
    def state_dir(self) -> Path:
        return self._layout.state_dir

    @property
    def manifest_file(self) -> Path:
        return self._layout.manifest_file

    @property
    def history_file(self) -> Path:
        return self._layout.history_file

    @property
    def executions_file(self) -> Path:
        return self._layout.executions_file

    @property
    def legacy_session_file(self) -> Path:
        return self._layout.legacy_session_file

    @property
    def session_preferences_file(self) -> Path:
        return self._layout.session_preferences_file

    @property
    def snapshots_dir(self) -> Path:
        return self._layout.snapshots_dir

    @property
    def artifacts_dir(self) -> Path:
        return self._layout.artifacts_dir

    @property
    def exports_dir(self) -> Path:
        return self._layout.exports_dir

    @property
    def metadata_dir(self) -> Path:
        return self._layout.metadata_dir

    def session_file(self, session_id: str) -> Path:
        return self._layout.session_file(session_id)

    def connection_file(self, session_id: str) -> Path:
        return self._layout.connection_file(session_id)

    def log_file(self, session_id: str) -> Path:
        return self._layout.log_file(session_id)

    def command_lock_file(self, session_id: str) -> Path:
        return self._layout.command_lock_file(session_id)

    def session_runtime(self, session_id: str) -> SessionStateFiles:
        return self._runtime.session_runtime(session_id)

    def session_state(self, session_id: str) -> SessionStateFiles:
        return self._runtime.session_state(session_id)

    def session_files(self) -> list[Path]:
        return self._runtime.session_files()

    def ensure_state_dir(self) -> None:
        self._layout.ensure_state_dir()

    def ensure_gitignore_entry(self) -> bool:
        return self._runtime.ensure_gitignore_entry()

    def ensure_initialized(self) -> StateManifest:
        return self._manifests.ensure_initialized()

    def manifest(self) -> StateManifest:
        return self._manifests.manifest()

    def save_manifest(self, manifest: StateManifest) -> None:
        self._manifests.save_manifest(manifest)

    def validate_manifest(
        self,
        manifest: StateManifest,
        *,
        required_versions: Mapping[str, str] | None = None,
    ) -> None:
        self._manifests.validate_manifest(
            manifest,
            required_versions=dict(StateManifest().resource_versions)
            if required_versions is None
            else required_versions,
        )

    def ensure_compatible(
        self,
        *,
        required_versions: Mapping[str, str] | None = None,
    ) -> StateManifest:
        return self._manifests.require_compatible(
            required_versions=dict(StateManifest().resource_versions)
            if required_versions is None
            else required_versions
        )

    def session_preferences(self) -> SessionPreferences:
        return self._runtime.session_preferences()

    def save_session_preferences(self, preferences: SessionPreferences) -> None:
        self._runtime.save_session_preferences(preferences)

    def set_current_session_id(self, session_id: str) -> None:
        self._runtime.set_current_session_id(session_id)

    def clear_current_session_id(self, *, expected_session_id: str | None = None) -> None:
        self._runtime.clear_current_session_id(expected_session_id=expected_session_id)

    def prune_session_runtime_artifacts(self, session_id: str) -> None:
        self._runtime.prune_session_runtime_artifacts(session_id)

    def prune_orphaned_runtime_artifacts(self, *, active_session_ids: Collection[str]) -> list[str]:
        return self._runtime.prune_orphaned_runtime_artifacts(active_session_ids=active_session_ids)

    def resource(self, name: str) -> StateResource:
        return self._layout.resource(name)

    def resource_path(self, name: str) -> Path:
        return self._layout.resource_path(name)

    def resources(self) -> dict[str, StateResource]:
        return self._layout.resources()

    def snapshot_resources(self) -> tuple[StateResource, ...]:
        return self._resources.snapshot_resources()

    def allocate_snapshot(
        self,
        *,
        label: str | None = None,
        source_session_id: str | None = None,
        source_execution_id: str | None = None,
    ) -> SnapshotAllocation:
        return self._resources.allocate_snapshot(
            label=label,
            source_session_id=source_session_id,
            source_execution_id=source_execution_id,
        )

    def commit_snapshot(
        self,
        resource_id: str,
        *,
        selected_resources: list[str],
        source_manifest: StateManifest,
        label: str | None = None,
        source_session_id: str | None = None,
        source_execution_id: str | None = None,
    ) -> SnapshotDescriptor:
        return self._resources.commit_snapshot(
            resource_id,
            selected_resources=selected_resources,
            source_manifest=source_manifest,
            label=label,
            source_session_id=source_session_id,
            source_execution_id=source_execution_id,
        )

    def fail_snapshot(self, resource_id: str, *, error: str) -> SnapshotDescriptor:
        return self._resources.fail_snapshot(resource_id, error=error)

    def get_snapshot(self, resource_id: str) -> SnapshotDescriptor:
        return self._resources.get_snapshot(resource_id)

    def list_snapshots(self, *, include_deleted: bool = False) -> list[SnapshotDescriptor]:
        return self._resources.list_snapshots(include_deleted=include_deleted)

    def allocate_export(
        self,
        *,
        label: str | None = None,
        source_session_id: str | None = None,
        source_execution_id: str | None = None,
    ) -> ExportAllocation:
        return self._resources.allocate_export(
            label=label,
            source_session_id=source_session_id,
            source_execution_id=source_execution_id,
        )

    def commit_export(
        self,
        resource_id: str,
        *,
        export_format: str,
        source_kind: str | None = None,
        source_id: str | None = None,
        output_files: list[str] | None = None,
        label: str | None = None,
        source_session_id: str | None = None,
        source_execution_id: str | None = None,
    ) -> ExportDescriptor:
        return self._resources.commit_export(
            resource_id,
            export_format=export_format,
            source_kind=source_kind,
            source_id=source_id,
            output_files=output_files,
            label=label,
            source_session_id=source_session_id,
            source_execution_id=source_execution_id,
        )

    def fail_export(self, resource_id: str, *, error: str) -> ExportDescriptor:
        return self._resources.fail_export(resource_id, error=error)

    def get_export(self, resource_id: str) -> ExportDescriptor:
        return self._resources.get_export(resource_id)

    def list_exports(self, *, include_deleted: bool = False) -> list[ExportDescriptor]:
        return self._resources.list_exports(include_deleted=include_deleted)
