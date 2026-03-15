from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Literal, cast

from .contracts import utc_now_iso
from .errors import StateCompatibilityError

STATE_DIR_NAME = ".agentnb"
STATE_MANIFEST_FILE_NAME = "state-manifest.json"
STATE_SCHEMA_VERSION = "1"
HISTORY_FILE_NAME = "history.jsonl"
EXECUTIONS_FILE_NAME = "executions.jsonl"
LEGACY_SESSION_FILE_NAME = "session.json"
COMMAND_LOCK_FILE_NAME = "command.lock"
SESSION_FILE_GLOB = "session-*.json"
RESOURCE_DESCRIPTOR_FILE_NAME = "descriptor.json"
RESOURCE_PAYLOAD_DIR_NAME = "payload"
SNAPSHOT_DESCRIPTOR_SCHEMA_VERSION = "1"
EXPORT_DESCRIPTOR_SCHEMA_VERSION = "1"

ResourceKind = Literal["file", "directory"]
PersistedResourceKind = Literal["snapshot", "export"]
ResourceLifecycle = Literal["allocating", "ready", "failed", "deleted"]

_RESOURCE_ID_PATTERN = re.compile(r"^[a-f0-9]{16}$")


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

    def has_active_command_lock(self) -> bool:
        if not self.command_lock_file.exists():
            return False
        return not self._clear_stale_command_lock()

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
            os.write(fd, str(os.getpid()).encode("utf-8"))
        finally:
            os.close(fd)
        return True

    def _clear_stale_command_lock(self) -> bool:
        try:
            raw_pid = self.command_lock_file.read_text(encoding="utf-8").strip()
        except OSError:
            return False

        try:
            lock_pid = int(raw_pid)
        except ValueError:
            _safe_unlink(self.command_lock_file)
            return True

        if _pid_exists(lock_pid):
            return False

        _safe_unlink(self.command_lock_file)
        return True


@dataclass(slots=True, frozen=True)
class StateManifest:
    schema_version: str = STATE_SCHEMA_VERSION
    resource_versions: dict[str, str] = field(default_factory=dict)

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


@dataclass(slots=True, frozen=True)
class StateRepository:
    project_root: Path

    def __post_init__(self) -> None:
        object.__setattr__(self, "project_root", self.project_root.resolve())

    @property
    def state_dir(self) -> Path:
        return self.project_root / STATE_DIR_NAME

    @property
    def manifest_file(self) -> Path:
        return self.state_dir / STATE_MANIFEST_FILE_NAME

    @property
    def history_file(self) -> Path:
        return self.resource_path("history")

    @property
    def executions_file(self) -> Path:
        return self.resource_path("executions")

    @property
    def legacy_session_file(self) -> Path:
        return self.resource_path("legacy_session")

    @property
    def snapshots_dir(self) -> Path:
        return self.resource_path("snapshots")

    @property
    def artifacts_dir(self) -> Path:
        return self.resource_path("artifacts")

    @property
    def exports_dir(self) -> Path:
        return self.resource_path("exports")

    @property
    def metadata_dir(self) -> Path:
        return self.resource_path("metadata")

    def session_file(self, session_id: str) -> Path:
        return self.session_state(session_id).session_record

    def connection_file(self, session_id: str) -> Path:
        return self.session_state(session_id).connection_file

    def log_file(self, session_id: str) -> Path:
        return self.session_state(session_id).log_file

    def command_lock_file(self, session_id: str) -> Path:
        return self.session_state(session_id).command_lock_file

    def session_files(self) -> list[Path]:
        if not self.state_dir.exists():
            return []
        return sorted(self.state_dir.glob(SESSION_FILE_GLOB))

    def ensure_state_dir(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def ensure_initialized(self) -> StateManifest:
        manifest = self.ensure_compatible()
        self.ensure_state_dir()
        return manifest

    def ensure_gitignore_entry(self) -> bool:
        self.ensure_state_dir()
        gitignore = self.project_root / ".gitignore"
        entry = f"{STATE_DIR_NAME}/"

        if not gitignore.exists():
            gitignore.write_text(f"{entry}\n", encoding="utf-8")
            return True

        existing = gitignore.read_text(encoding="utf-8")
        lines = existing.splitlines()
        if entry in lines:
            return False

        suffix = "" if existing.endswith("\n") else "\n"
        with gitignore.open("a", encoding="utf-8") as handle:
            handle.write(f"{suffix}{entry}\n")
        return True

    def manifest(self) -> StateManifest:
        if not self.manifest_file.exists():
            return StateManifest()
        try:
            payload = json.loads(self.manifest_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateCompatibilityError(
                "State manifest is unreadable.",
                data={"manifest_file": str(self.manifest_file)},
            ) from exc
        if not isinstance(payload, dict):
            raise StateCompatibilityError(
                "State manifest has an invalid shape.",
                data={"manifest_file": str(self.manifest_file)},
            )
        try:
            manifest = StateManifest.from_dict(payload)
        except ValueError as exc:
            raise StateCompatibilityError(
                "State manifest is missing required fields.",
                data={"manifest_file": str(self.manifest_file)},
            ) from exc
        self.validate_manifest(manifest)
        return manifest

    def save_manifest(self, manifest: StateManifest) -> None:
        self.ensure_state_dir()
        self.manifest_file.write_text(
            json.dumps(manifest.to_dict(), ensure_ascii=True),
            encoding="utf-8",
        )

    def validate_manifest(self, manifest: StateManifest) -> None:
        if manifest.schema_version != STATE_SCHEMA_VERSION:
            raise StateCompatibilityError(
                (
                    f"State schema {manifest.schema_version} is incompatible with "
                    f"agentnb schema {STATE_SCHEMA_VERSION}."
                ),
                data={
                    "manifest_schema_version": manifest.schema_version,
                    "supported_schema_version": STATE_SCHEMA_VERSION,
                },
            )

        known_resources = set(self.resources())
        unknown_resources = sorted(set(manifest.resource_versions) - known_resources)
        if unknown_resources:
            raise StateCompatibilityError(
                "State manifest references unknown resources.",
                data={"unknown_resources": unknown_resources},
            )

    def ensure_compatible(self) -> StateManifest:
        return self.manifest()

    def session_runtime(self, session_id: str) -> SessionStateFiles:
        state_dir = self.state_dir
        return SessionStateFiles(
            session_id=session_id,
            state_dir=state_dir,
            session_record=state_dir / session_file_name(session_id),
            legacy_session_record=self.resource("legacy_session").resolve(state_dir),
            connection_file=kernel_connection_file(state_dir, session_id),
            log_file=kernel_log_file(state_dir, session_id),
            command_lock_file=command_lock_file(state_dir, session_id),
        )

    def session_state(self, session_id: str) -> SessionStateFiles:
        return self.session_runtime(session_id)

    def resource(self, name: str) -> StateResource:
        try:
            return _STATE_RESOURCES[name]
        except KeyError as exc:
            raise KeyError(f"Unknown state resource: {name}") from exc

    def resource_path(self, name: str) -> Path:
        return self.resource(name).resolve(self.state_dir)

    def snapshot_resources(self) -> tuple[StateResource, ...]:
        return (
            self.resource("snapshots"),
            self.resource("artifacts"),
            self.resource("exports"),
            self.resource("metadata"),
        )

    def resources(self) -> dict[str, StateResource]:
        return dict(_STATE_RESOURCES)

    def allocate_snapshot(
        self,
        *,
        label: str | None = None,
        source_session_id: str | None = None,
        source_execution_id: str | None = None,
    ) -> SnapshotAllocation:
        paths = self._allocate_resource_paths(kind="snapshot")
        created_at = utc_now_iso()
        descriptor = SnapshotDescriptor(
            id=paths.id,
            kind="snapshot",
            schema_version=SNAPSHOT_DESCRIPTOR_SCHEMA_VERSION,
            created_at=created_at,
            updated_at=created_at,
            lifecycle="allocating",
            label=label,
            source_session_id=source_session_id,
            source_execution_id=source_execution_id,
        )
        self._write_descriptor(paths.descriptor_file, descriptor)
        return SnapshotAllocation(descriptor=descriptor, paths=paths)

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
        self.validate_manifest(source_manifest)
        validated_resources = self._validate_selected_resources(selected_resources)
        current, paths = self._load_snapshot(resource_id)
        descriptor = replace(
            current,
            updated_at=utc_now_iso(),
            lifecycle="ready",
            label=current.label if label is None else label,
            source_session_id=(
                current.source_session_id if source_session_id is None else source_session_id
            ),
            source_execution_id=(
                current.source_execution_id if source_execution_id is None else source_execution_id
            ),
            error=None,
            selected_resources=validated_resources,
            source_manifest_schema_version=source_manifest.schema_version,
            source_resource_versions=dict(source_manifest.resource_versions),
        )
        self._write_descriptor(paths.descriptor_file, descriptor)
        return descriptor

    def fail_snapshot(self, resource_id: str, *, error: str) -> SnapshotDescriptor:
        current, paths = self._load_snapshot(resource_id)
        descriptor = replace(
            current,
            updated_at=utc_now_iso(),
            lifecycle="failed",
            error=error,
        )
        self._write_descriptor(paths.descriptor_file, descriptor)
        return descriptor

    def get_snapshot(self, resource_id: str) -> SnapshotDescriptor:
        descriptor, _ = self._load_snapshot(resource_id)
        return descriptor

    def list_snapshots(self, *, include_deleted: bool = False) -> list[SnapshotDescriptor]:
        descriptors = cast(list[SnapshotDescriptor], self._list_descriptors(kind="snapshot"))
        if include_deleted:
            return descriptors
        return [descriptor for descriptor in descriptors if descriptor.lifecycle != "deleted"]

    def allocate_export(
        self,
        *,
        label: str | None = None,
        source_session_id: str | None = None,
        source_execution_id: str | None = None,
    ) -> ExportAllocation:
        paths = self._allocate_resource_paths(kind="export")
        created_at = utc_now_iso()
        descriptor = ExportDescriptor(
            id=paths.id,
            kind="export",
            schema_version=EXPORT_DESCRIPTOR_SCHEMA_VERSION,
            created_at=created_at,
            updated_at=created_at,
            lifecycle="allocating",
            label=label,
            source_session_id=source_session_id,
            source_execution_id=source_execution_id,
        )
        self._write_descriptor(paths.descriptor_file, descriptor)
        return ExportAllocation(descriptor=descriptor, paths=paths)

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
        current, paths = self._load_export(resource_id)
        descriptor = replace(
            current,
            updated_at=utc_now_iso(),
            lifecycle="ready",
            label=current.label if label is None else label,
            source_session_id=(
                current.source_session_id if source_session_id is None else source_session_id
            ),
            source_execution_id=(
                current.source_execution_id if source_execution_id is None else source_execution_id
            ),
            error=None,
            export_format=export_format,
            source_kind=source_kind,
            source_id=source_id,
            output_files=[] if output_files is None else list(output_files),
        )
        self._write_descriptor(paths.descriptor_file, descriptor)
        return descriptor

    def fail_export(self, resource_id: str, *, error: str) -> ExportDescriptor:
        current, paths = self._load_export(resource_id)
        descriptor = replace(
            current,
            updated_at=utc_now_iso(),
            lifecycle="failed",
            error=error,
        )
        self._write_descriptor(paths.descriptor_file, descriptor)
        return descriptor

    def get_export(self, resource_id: str) -> ExportDescriptor:
        descriptor, _ = self._load_export(resource_id)
        return descriptor

    def list_exports(self, *, include_deleted: bool = False) -> list[ExportDescriptor]:
        descriptors = cast(list[ExportDescriptor], self._list_descriptors(kind="export"))
        if include_deleted:
            return descriptors
        return [descriptor for descriptor in descriptors if descriptor.lifecycle != "deleted"]

    def _allocate_resource_paths(self, *, kind: PersistedResourceKind) -> PersistedResourcePaths:
        self.ensure_initialized()
        resource_id = _new_resource_id()
        paths = self._persisted_resource_paths(kind=kind, resource_id=resource_id)
        paths.root_dir.mkdir(parents=True, exist_ok=False)
        paths.payload_dir.mkdir()
        return paths

    def _persisted_resource_paths(
        self,
        *,
        kind: PersistedResourceKind,
        resource_id: str,
    ) -> PersistedResourcePaths:
        validated_id = _validate_resource_id(resource_id)
        root_parent = self.snapshots_dir if kind == "snapshot" else self.exports_dir
        root_dir = root_parent / validated_id
        return PersistedResourcePaths(
            id=validated_id,
            kind=kind,
            root_dir=root_dir,
            descriptor_file=root_dir / RESOURCE_DESCRIPTOR_FILE_NAME,
            payload_dir=root_dir / RESOURCE_PAYLOAD_DIR_NAME,
        )

    def _load_snapshot(self, resource_id: str) -> tuple[SnapshotDescriptor, PersistedResourcePaths]:
        paths = self._persisted_resource_paths(kind="snapshot", resource_id=resource_id)
        descriptor = cast(SnapshotDescriptor, self._read_descriptor(paths, kind="snapshot"))
        return descriptor, paths

    def _load_export(self, resource_id: str) -> tuple[ExportDescriptor, PersistedResourcePaths]:
        paths = self._persisted_resource_paths(kind="export", resource_id=resource_id)
        descriptor = cast(ExportDescriptor, self._read_descriptor(paths, kind="export"))
        return descriptor, paths

    def _list_descriptors(
        self,
        *,
        kind: PersistedResourceKind,
    ) -> list[SnapshotDescriptor] | list[ExportDescriptor]:
        self.ensure_compatible()
        parent = self.snapshots_dir if kind == "snapshot" else self.exports_dir
        if not parent.exists():
            return []

        descriptors: list[SnapshotDescriptor] | list[ExportDescriptor] = []
        for child in sorted(parent.iterdir()):
            if not child.is_dir():
                continue
            paths = PersistedResourcePaths(
                id=child.name,
                kind=kind,
                root_dir=child,
                descriptor_file=child / RESOURCE_DESCRIPTOR_FILE_NAME,
                payload_dir=child / RESOURCE_PAYLOAD_DIR_NAME,
            )
            descriptors.append(self._read_descriptor(paths, kind=kind))
        descriptors.sort(key=lambda descriptor: (descriptor.created_at, descriptor.id))
        return descriptors

    def _read_descriptor(
        self,
        paths: PersistedResourcePaths,
        *,
        kind: PersistedResourceKind,
    ) -> SnapshotDescriptor | ExportDescriptor:
        self.ensure_compatible()
        if not paths.descriptor_file.exists():
            raise StateCompatibilityError(
                "Persisted resource descriptor is missing.",
                data={
                    "resource_id": paths.id,
                    "resource_kind": kind,
                    "descriptor_file": str(paths.descriptor_file),
                },
            )
        try:
            payload = json.loads(paths.descriptor_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StateCompatibilityError(
                "Persisted resource descriptor is unreadable.",
                data={
                    "resource_id": paths.id,
                    "resource_kind": kind,
                    "descriptor_file": str(paths.descriptor_file),
                },
            ) from exc
        if not isinstance(payload, dict):
            raise StateCompatibilityError(
                "Persisted resource descriptor has an invalid shape.",
                data={
                    "resource_id": paths.id,
                    "resource_kind": kind,
                    "descriptor_file": str(paths.descriptor_file),
                },
            )
        try:
            if kind == "snapshot":
                descriptor = SnapshotDescriptor.from_dict(payload)
            else:
                descriptor = ExportDescriptor.from_dict(payload)
        except ValueError as exc:
            raise StateCompatibilityError(
                "Persisted resource descriptor is invalid.",
                data={
                    "resource_id": paths.id,
                    "resource_kind": kind,
                    "descriptor_file": str(paths.descriptor_file),
                },
            ) from exc
        if descriptor.id != paths.id:
            raise StateCompatibilityError(
                "Persisted resource descriptor id does not match its directory.",
                data={
                    "resource_id": paths.id,
                    "descriptor_id": descriptor.id,
                    "resource_kind": kind,
                },
            )
        self._validate_descriptor_schema(descriptor)
        return descriptor

    def _validate_descriptor_schema(
        self,
        descriptor: SnapshotDescriptor | ExportDescriptor,
    ) -> None:
        expected = (
            SNAPSHOT_DESCRIPTOR_SCHEMA_VERSION
            if descriptor.kind == "snapshot"
            else EXPORT_DESCRIPTOR_SCHEMA_VERSION
        )
        if descriptor.schema_version != expected:
            raise StateCompatibilityError(
                "Persisted resource schema is incompatible.",
                data={
                    "resource_id": descriptor.id,
                    "resource_kind": descriptor.kind,
                    "resource_schema_version": descriptor.schema_version,
                    "supported_schema_version": expected,
                },
            )

    def _write_descriptor(
        self,
        descriptor_file: Path,
        descriptor: SnapshotDescriptor | ExportDescriptor,
    ) -> None:
        descriptor_file.write_text(
            json.dumps(descriptor.to_dict(), ensure_ascii=True),
            encoding="utf-8",
        )

    def _validate_selected_resources(self, selected_resources: list[str]) -> list[str]:
        known_resources = set(self.resources())
        unknown_resources = sorted(set(selected_resources) - known_resources)
        if unknown_resources:
            raise StateCompatibilityError(
                "Snapshot selection references unknown state resources.",
                data={"unknown_resources": unknown_resources},
            )

        seen: set[str] = set()
        duplicates: list[str] = []
        validated: list[str] = []
        for resource_name in selected_resources:
            if resource_name in seen:
                duplicates.append(resource_name)
                continue
            seen.add(resource_name)
            validated.append(resource_name)
        if duplicates:
            raise StateCompatibilityError(
                "Snapshot selection must not contain duplicate state resources.",
                data={"duplicate_resources": sorted(set(duplicates))},
            )
        return validated


@dataclass(slots=True, frozen=True)
class StateLayout(StateRepository):
    pass


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


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


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
