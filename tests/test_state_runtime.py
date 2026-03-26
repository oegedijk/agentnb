from __future__ import annotations

import json
import os

from agentnb.state import SessionPreferences
from agentnb.state_runtime import RuntimeStateRepository


def test_runtime_state_repository_session_files_excludes_session_preferences(project_dir) -> None:
    repository = RuntimeStateRepository(project_dir)
    repository.save_session_preferences(SessionPreferences(current_session_id="analysis"))

    assert repository.session_files() == []


def test_session_runtime_files_own_command_lock_lifecycle(project_dir) -> None:
    runtime_files = RuntimeStateRepository(project_dir).session_runtime("default")

    with runtime_files.acquire_command_lock() as acquired:
        assert acquired is True
        assert runtime_files.command_lock_file.exists()
        lock_info = runtime_files.command_lock_info()
        assert lock_info is not None
        assert lock_info.pid > 0
        assert lock_info.acquired_at is not None
        assert isinstance(lock_info.busy_for_ms(), int)

    assert not runtime_files.command_lock_file.exists()

    runtime_files.ensure_state_dir()
    runtime_files.command_lock_file.write_text("not-a-pid", encoding="utf-8")

    assert runtime_files.has_active_command_lock() is False
    assert not runtime_files.command_lock_file.exists()


def test_session_runtime_files_support_legacy_pid_lock_files(project_dir) -> None:
    runtime_files = RuntimeStateRepository(project_dir).session_runtime("default")
    runtime_files.ensure_state_dir()
    runtime_files.command_lock_file.write_text(str(os.getpid()), encoding="utf-8")

    lock_info = runtime_files.command_lock_info()

    assert lock_info is not None
    assert lock_info.pid == os.getpid()
    assert lock_info.acquired_at is None
    assert lock_info.busy_for_ms() is None


def test_session_runtime_files_read_structured_lock_metadata(project_dir) -> None:
    runtime_files = RuntimeStateRepository(project_dir).session_runtime("default")
    runtime_files.ensure_state_dir()
    runtime_files.command_lock_file.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "acquired_at": "2026-03-19T12:00:00+00:00",
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )

    lock_info = runtime_files.command_lock_info()

    assert lock_info is not None
    assert lock_info.pid == os.getpid()
    assert lock_info.acquired_at == "2026-03-19T12:00:00+00:00"


def test_runtime_state_repository_prunes_session_runtime_artifacts(project_dir) -> None:
    repository = RuntimeStateRepository(project_dir)
    runtime_files = repository.session_runtime("analysis")
    runtime_files.ensure_state_dir()
    runtime_files.connection_file.write_text("{}", encoding="utf-8")
    runtime_files.log_file.write_text("log", encoding="utf-8")
    runtime_files.command_lock_file.write_text(str(os.getpid()), encoding="utf-8")

    repository.prune_session_runtime_artifacts("analysis")

    assert not runtime_files.connection_file.exists()
    assert not runtime_files.log_file.exists()
    assert not runtime_files.command_lock_file.exists()


def test_runtime_state_repository_prunes_orphaned_runtime_artifacts(project_dir) -> None:
    repository = RuntimeStateRepository(project_dir)
    default_runtime = repository.session_runtime("default")
    orphan_runtime = repository.session_runtime("orphan")
    default_runtime.ensure_state_dir()
    default_runtime.log_file.write_text("keep", encoding="utf-8")
    orphan_runtime.connection_file.write_text("{}", encoding="utf-8")
    orphan_runtime.log_file.write_text("drop", encoding="utf-8")
    orphan_runtime.command_lock_file.write_text(str(os.getpid()), encoding="utf-8")

    removed = repository.prune_orphaned_runtime_artifacts(active_session_ids={"default"})

    assert removed == [
        "command.lock-orphan",
        "kernel-orphan.json",
        "kernel-orphan.log",
    ]
    assert default_runtime.log_file.exists()
    assert not orphan_runtime.connection_file.exists()
    assert not orphan_runtime.log_file.exists()
    assert not orphan_runtime.command_lock_file.exists()
