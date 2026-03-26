from __future__ import annotations

from agentnb.state import SessionPreferences, StateRepository, session_file_name


def test_state_repository_exposes_registered_resource_paths(project_dir) -> None:
    repository = StateRepository(project_dir)
    session_state = repository.session_state("default")

    resources = repository.resources()

    assert set(resources) >= {
        "history",
        "executions",
        "session_preferences",
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


def test_state_repository_roundtrips_session_preferences(project_dir) -> None:
    repository = StateRepository(project_dir)
    preferences = SessionPreferences(current_session_id="analysis")

    repository.save_session_preferences(preferences)

    assert repository.session_preferences() == preferences


def test_state_repository_exposes_snapshot_resource_selection(project_dir) -> None:
    repository = StateRepository(project_dir)

    resources = repository.snapshot_resources()

    assert [resource.name for resource in resources] == [
        "snapshots",
        "artifacts",
        "exports",
        "metadata",
    ]
