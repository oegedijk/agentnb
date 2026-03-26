from __future__ import annotations

import json

import pytest

from agentnb.errors import StateCompatibilityError
from agentnb.execution import ExecutionRecord, ExecutionStore
from agentnb.history import HistoryStore, user_command_record
from agentnb.runtime import KernelRuntime
from agentnb.session import SessionInfo, SessionStore
from agentnb.state import STATE_SCHEMA_VERSION, StateManifest
from agentnb.state_layout import StateLayout
from agentnb.state_manifest import StateManifestRepository


def test_state_manifest_repository_roundtrips_manifest(project_dir) -> None:
    repository = StateManifestRepository(StateLayout(project_dir))
    manifest = StateManifest(
        schema_version=STATE_SCHEMA_VERSION,
        resource_versions={"history": "2", "executions": "2"},
    )

    repository.save_manifest(manifest)

    assert repository.manifest() == manifest


def test_state_manifest_repository_rejects_incompatible_manifest_schema(project_dir) -> None:
    layout = StateLayout(project_dir)
    repository = StateManifestRepository(layout)
    layout.ensure_state_dir()
    layout.manifest_file.write_text('{"schema_version": "999"}', encoding="utf-8")

    with pytest.raises(StateCompatibilityError):
        repository.require_compatible(required_versions=StateManifest().resource_versions)


@pytest.mark.parametrize(
    "reader",
    [
        lambda repository: repository.manifest(),
        lambda repository: repository.ensure_initialized(),
        lambda repository: repository.require_compatible(),
    ],
)
def test_state_manifest_repository_rejects_incompatible_resource_versions(
    project_dir,
    reader,
) -> None:
    repository = StateManifestRepository(StateLayout(project_dir))
    repository.save_manifest(
        StateManifest(
            schema_version=STATE_SCHEMA_VERSION,
            resource_versions={"history": "1", "executions": "1"},
        )
    )

    with pytest.raises(StateCompatibilityError):
        reader(repository)


def test_state_manifest_repository_rejects_unknown_manifest_resources(project_dir) -> None:
    repository = StateManifestRepository(StateLayout(project_dir))
    repository.save_manifest(
        StateManifest(
            schema_version=STATE_SCHEMA_VERSION,
            resource_versions={"future_resource": "1"},
        )
    )

    with pytest.raises(StateCompatibilityError):
        repository.require_compatible(required_versions=StateManifest().resource_versions)


def test_state_manifest_repository_rejects_existing_state_without_manifest(project_dir) -> None:
    layout = StateLayout(project_dir)
    repository = StateManifestRepository(layout)
    layout.ensure_state_dir()
    layout.executions_file.write_text(
        json.dumps(
            {
                "execution_id": "run-1",
                "ts": "2026-03-10T00:00:00+00:00",
                "session_id": "default",
                "command_type": "exec",
                "status": "ok",
                "duration_ms": 1,
                "result": "2",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(StateCompatibilityError):
        repository.require_compatible(required_versions=StateManifest().resource_versions)


def test_history_store_enforces_manifest_compatibility(project_dir) -> None:
    repository = StateManifestRepository(StateLayout(project_dir))
    repository.save_manifest(StateManifest(schema_version="999"))

    with pytest.raises(StateCompatibilityError):
        HistoryStore(project_dir).read()


def test_execution_store_enforces_manifest_compatibility(project_dir) -> None:
    repository = StateManifestRepository(StateLayout(project_dir))
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


@pytest.mark.parametrize(
    "append_call",
    [
        lambda project_dir: HistoryStore(project_dir).append(
            user_command_record(
                session_id="default",
                classification="inspection",
                command_type="vars",
                label="vars",
                status="ok",
                duration_ms=1,
            )
        ),
        lambda project_dir: ExecutionStore(project_dir).append(
            ExecutionRecord(
                execution_id="run-1",
                ts="2026-03-10T00:00:00+00:00",
                session_id="default",
                command_type="exec",
                status="ok",
                duration_ms=1,
            )
        ),
    ],
)
def test_store_append_rejects_stale_resource_versions(project_dir, append_call) -> None:
    repository = StateManifestRepository(StateLayout(project_dir))
    repository.save_manifest(
        StateManifest(
            schema_version=STATE_SCHEMA_VERSION,
            resource_versions={"history": "1", "executions": "1"},
        )
    )

    with pytest.raises(StateCompatibilityError):
        append_call(project_dir)


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
            classification="inspection",
            command_type="vars",
            label="vars",
            status="ok",
            duration_ms=1,
        )
    )

    repository = StateManifestRepository(StateLayout(project_dir))
    repository.save_manifest(StateManifest(schema_version="999"))

    with pytest.raises(StateCompatibilityError):
        KernelRuntime().history(project_root=project_dir)
