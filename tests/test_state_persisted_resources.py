from __future__ import annotations

import pytest

from agentnb.errors import StateCompatibilityError
from agentnb.state import STATE_SCHEMA_VERSION, StateManifest
from agentnb.state_layout import StateLayout
from agentnb.state_persisted_resources import PersistedResourceRepository


def test_persisted_resource_repository_allocates_and_commits_snapshot_descriptors(
    project_dir,
) -> None:
    repository = PersistedResourceRepository(project_dir)
    manifest = StateManifest(
        schema_version=STATE_SCHEMA_VERSION,
        resource_versions={"history": "2", "executions": "2", "snapshots": "1"},
    )

    allocation = repository.allocate_snapshot(
        label="baseline",
        source_session_id="default",
        source_execution_id="run-1",
    )
    committed = repository.commit_snapshot(
        allocation.descriptor.id,
        selected_resources=["history", "executions", "metadata"],
        source_manifest=manifest,
    )

    loaded = repository.get_snapshot(allocation.descriptor.id)
    expected_root = project_dir / ".agentnb" / "snapshots" / allocation.descriptor.id

    assert allocation.paths.root_dir == expected_root
    assert allocation.paths.payload_dir == allocation.paths.root_dir / "payload"
    assert allocation.descriptor.lifecycle == "allocating"
    assert committed.lifecycle == "ready"
    assert committed.selected_resources == ["history", "executions", "metadata"]
    assert committed.source_manifest_schema_version == STATE_SCHEMA_VERSION
    assert committed.source_resource_versions == manifest.resource_versions
    assert loaded == committed
    assert repository.list_snapshots() == [committed]


def test_persisted_resource_repository_allocates_and_commits_export_descriptors(
    project_dir,
) -> None:
    repository = PersistedResourceRepository(project_dir)

    allocation = repository.allocate_export(
        label="notebook export",
        source_session_id="default",
        source_execution_id="run-2",
    )
    committed = repository.commit_export(
        allocation.descriptor.id,
        export_format="ipynb",
        source_kind="snapshot",
        source_id="snap-1",
        output_files=["notebook.ipynb", "transcript.md"],
    )

    loaded = repository.get_export(allocation.descriptor.id)
    expected_root = project_dir / ".agentnb" / "exports" / allocation.descriptor.id

    assert allocation.paths.root_dir == expected_root
    assert allocation.descriptor.lifecycle == "allocating"
    assert committed.lifecycle == "ready"
    assert committed.export_format == "ipynb"
    assert committed.source_kind == "snapshot"
    assert committed.source_id == "snap-1"
    assert committed.output_files == ["notebook.ipynb", "transcript.md"]
    assert loaded == committed
    assert repository.list_exports() == [committed]


def test_persisted_resource_repository_reports_invalid_snapshot_descriptors(project_dir) -> None:
    repository = PersistedResourceRepository(project_dir)
    allocation = repository.allocate_snapshot()
    allocation.paths.descriptor_file.write_text("{}", encoding="utf-8")

    with pytest.raises(StateCompatibilityError):
        repository.get_snapshot(allocation.descriptor.id)


def test_persisted_resource_repository_rejects_unknown_snapshot_resources(project_dir) -> None:
    repository = PersistedResourceRepository(project_dir)
    allocation = repository.allocate_snapshot()

    with pytest.raises(StateCompatibilityError):
        repository.commit_snapshot(
            allocation.descriptor.id,
            selected_resources=["history", "future-resource"],
            source_manifest=StateManifest(schema_version=STATE_SCHEMA_VERSION),
        )


def test_persisted_resource_repository_rejects_incompatible_snapshot_manifest(project_dir) -> None:
    repository = PersistedResourceRepository(project_dir)
    allocation = repository.allocate_snapshot()

    with pytest.raises(StateCompatibilityError):
        repository.commit_snapshot(
            allocation.descriptor.id,
            selected_resources=["history"],
            source_manifest=StateManifest(schema_version="999"),
        )


def test_snapshot_resource_plan_resolves_paths_from_state_layout(project_dir) -> None:
    plan = PersistedResourceRepository(project_dir).snapshot_resources()
    layout = StateLayout(project_dir)

    assert {resource.name for resource in plan} == {
        "snapshots",
        "artifacts",
        "exports",
        "metadata",
    }
    assert {resource.resolve(layout.state_dir) for resource in plan} == {
        project_dir / ".agentnb" / "snapshots",
        project_dir / ".agentnb" / "artifacts",
        project_dir / ".agentnb" / "exports",
        project_dir / ".agentnb" / "metadata",
    }
