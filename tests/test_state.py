from __future__ import annotations

import pytest

from agentnb.errors import StateCompatibilityError
from agentnb.execution import ExecutionRecord, ExecutionStore
from agentnb.history import HistoryStore, user_command_record
from agentnb.runtime import KernelRuntime
from agentnb.session import SessionInfo, SessionStore
from agentnb.state import STATE_SCHEMA_VERSION, StateManifest, StateRepository, session_file_name


def test_state_repository_exposes_registered_resource_paths(project_dir) -> None:
    repository = StateRepository(project_dir)
    session_state = repository.session_state("default")

    resources = repository.resources()

    assert set(resources) >= {
        "history",
        "executions",
        "legacy_session",
        "snapshots",
        "artifacts",
        "exports",
        "metadata",
    }
    assert repository.history_file == project_dir / ".agentnb" / "history.jsonl"
    assert session_state.session_record == project_dir / ".agentnb" / session_file_name("default")
    assert session_state.connection_file == project_dir / ".agentnb" / "kernel-default.json"
    assert session_state.log_file == project_dir / ".agentnb" / "kernel-default.log"
    assert session_state.command_lock_file == project_dir / ".agentnb" / "command.lock-default"
    assert repository.snapshots_dir == project_dir / ".agentnb" / "snapshots"
    assert repository.artifacts_dir == project_dir / ".agentnb" / "artifacts"
    assert repository.exports_dir == project_dir / ".agentnb" / "exports"


def test_state_repository_exposes_snapshot_resource_selection(project_dir) -> None:
    repository = StateRepository(project_dir)

    resources = repository.snapshot_resources()

    assert [resource.name for resource in resources] == [
        "snapshots",
        "artifacts",
        "exports",
        "metadata",
    ]


def test_state_repository_roundtrips_manifest(project_dir) -> None:
    repository = StateRepository(project_dir)
    manifest = StateManifest(
        schema_version=STATE_SCHEMA_VERSION,
        resource_versions={"history": "1", "executions": "1"},
    )

    repository.save_manifest(manifest)

    assert repository.manifest() == manifest


def test_state_repository_rejects_incompatible_manifest_schema(project_dir) -> None:
    repository = StateRepository(project_dir)
    repository.ensure_state_dir()
    repository.manifest_file.write_text('{"schema_version": "999"}', encoding="utf-8")

    with pytest.raises(StateCompatibilityError):
        repository.ensure_compatible()


def test_state_repository_rejects_unknown_manifest_resources(project_dir) -> None:
    repository = StateRepository(project_dir)
    repository.save_manifest(
        StateManifest(
            schema_version=STATE_SCHEMA_VERSION,
            resource_versions={"future_resource": "1"},
        )
    )

    with pytest.raises(StateCompatibilityError):
        repository.ensure_compatible()


def test_session_runtime_files_own_command_lock_lifecycle(project_dir) -> None:
    runtime_files = StateRepository(project_dir).session_runtime("default")

    with runtime_files.acquire_command_lock() as acquired:
        assert acquired is True
        assert runtime_files.command_lock_file.exists()

    assert not runtime_files.command_lock_file.exists()

    runtime_files.ensure_state_dir()
    runtime_files.command_lock_file.write_text("not-a-pid", encoding="utf-8")

    assert runtime_files.has_active_command_lock() is False
    assert not runtime_files.command_lock_file.exists()


def test_state_repository_allocates_and_commits_snapshot_descriptors(project_dir) -> None:
    repository = StateRepository(project_dir)
    manifest = StateManifest(
        schema_version=STATE_SCHEMA_VERSION,
        resource_versions={"history": "1", "executions": "1", "snapshots": "1"},
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


def test_state_repository_allocates_and_commits_export_descriptors(project_dir) -> None:
    repository = StateRepository(project_dir)

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


def test_state_repository_reports_invalid_snapshot_descriptors(project_dir) -> None:
    repository = StateRepository(project_dir)
    allocation = repository.allocate_snapshot()
    allocation.paths.descriptor_file.write_text("{}", encoding="utf-8")

    with pytest.raises(StateCompatibilityError):
        repository.get_snapshot(allocation.descriptor.id)


def test_state_repository_rejects_unknown_snapshot_resources(project_dir) -> None:
    repository = StateRepository(project_dir)
    allocation = repository.allocate_snapshot()

    with pytest.raises(StateCompatibilityError):
        repository.commit_snapshot(
            allocation.descriptor.id,
            selected_resources=["history", "future-resource"],
            source_manifest=StateManifest(schema_version=STATE_SCHEMA_VERSION),
        )


def test_state_repository_rejects_incompatible_snapshot_manifest(project_dir) -> None:
    repository = StateRepository(project_dir)
    allocation = repository.allocate_snapshot()

    with pytest.raises(StateCompatibilityError):
        repository.commit_snapshot(
            allocation.descriptor.id,
            selected_resources=["history"],
            source_manifest=StateManifest(schema_version="999"),
        )


def test_history_store_enforces_manifest_compatibility(project_dir) -> None:
    repository = StateRepository(project_dir)
    repository.save_manifest(StateManifest(schema_version="999"))

    with pytest.raises(StateCompatibilityError):
        HistoryStore(project_dir).read()


def test_execution_store_enforces_manifest_compatibility(project_dir) -> None:
    repository = StateRepository(project_dir)
    repository.save_manifest(StateManifest(schema_version="999"))

    with pytest.raises(StateCompatibilityError):
        ExecutionStore(project_dir).append(
            ExecutionRecord(
                execution_id="run-1",
                ts="2026-03-10T00:00:00+00:00",
                session_id="default",
                command_type="exec",
                status="ok",
                duration_ms=1,
            )
        )


def test_runtime_history_surfaces_manifest_compatibility_errors(project_dir) -> None:
    store = SessionStore(project_dir)
    store.ensure_state_dir()
    store.save_session(
        SessionInfo(
            session_id="default",
            pid=123,
            connection_file=str(store.connection_file),
            python_executable="python",
            project_root=str(project_dir),
            started_at="2026-03-10T00:00:00+00:00",
        )
    )
    HistoryStore(project_dir).append(
        user_command_record(
            session_id="default",
            command_type="vars",
            label="vars",
            status="ok",
            duration_ms=1,
        )
    )

    repository = StateRepository(project_dir)
    repository.save_manifest(StateManifest(schema_version="999"))

    with pytest.raises(StateCompatibilityError):
        KernelRuntime().history(project_root=project_dir)
