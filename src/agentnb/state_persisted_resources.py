from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

from .contracts import utc_now_iso
from .errors import StateCompatibilityError
from .state import (
    EXPORT_DESCRIPTOR_SCHEMA_VERSION,
    RESOURCE_DESCRIPTOR_FILE_NAME,
    RESOURCE_PAYLOAD_DIR_NAME,
    SNAPSHOT_DESCRIPTOR_SCHEMA_VERSION,
    ExportAllocation,
    ExportDescriptor,
    PersistedResourceKind,
    PersistedResourcePaths,
    SnapshotAllocation,
    SnapshotDescriptor,
    StateManifest,
    _new_resource_id,
    _validate_resource_id,
)
from .state_layout import StateLayout
from .state_manifest import StateManifestRepository


class PersistedResourceRepository:
    def __init__(
        self,
        layout: Path | StateLayout,
        manifest_repository: StateManifestRepository | None = None,
    ) -> None:
        self.layout = layout if isinstance(layout, StateLayout) else StateLayout(layout)
        self.manifest_repository = manifest_repository or StateManifestRepository(self.layout)

    def snapshot_resources(self):
        return (
            self.layout.resource("snapshots"),
            self.layout.resource("artifacts"),
            self.layout.resource("exports"),
            self.layout.resource("metadata"),
        )

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
        self.manifest_repository.validate_manifest(
            source_manifest,
            required_versions=StateManifest().resource_versions,
        )
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
        self.manifest_repository.ensure_initialized()
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
        root_parent = self.layout.snapshots_dir if kind == "snapshot" else self.layout.exports_dir
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
        self.manifest_repository.require_compatible()
        parent = self.layout.snapshots_dir if kind == "snapshot" else self.layout.exports_dir
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
        self.manifest_repository.require_compatible()
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
            descriptor = (
                SnapshotDescriptor.from_dict(payload)
                if kind == "snapshot"
                else ExportDescriptor.from_dict(payload)
            )
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
        descriptor_file,
        descriptor: SnapshotDescriptor | ExportDescriptor,
    ) -> None:
        descriptor_file.write_text(
            json.dumps(descriptor.to_dict(), ensure_ascii=True),
            encoding="utf-8",
        )

    def _validate_selected_resources(self, selected_resources: list[str]) -> list[str]:
        known_resources = set(self.layout.resources())
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
