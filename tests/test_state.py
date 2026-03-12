from __future__ import annotations

import pytest

from agentnb.errors import StateCompatibilityError
from agentnb.execution import ExecutionRecord, ExecutionStore
from agentnb.history import HistoryStore, user_command_record
from agentnb.runtime import KernelRuntime
from agentnb.session import SessionInfo, SessionStore
from agentnb.state import STATE_SCHEMA_VERSION, StateManifest, StateRepository


def test_state_repository_exposes_registered_resource_paths(project_dir) -> None:
    repository = StateRepository(project_dir)

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
    assert repository.snapshots_dir == project_dir / ".agentnb" / "snapshots"
    assert repository.artifacts_dir == project_dir / ".agentnb" / "artifacts"
    assert repository.exports_dir == project_dir / ".agentnb" / "exports"


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
