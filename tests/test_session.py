from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentnb.session import SessionInfo, SessionStore, resolve_project_root


@pytest.mark.parametrize("use_override", [False, True])
def test_resolve_project_root_walks_up_to_nearest_pyproject(
    tmp_path: Path, use_override: bool
) -> None:
    root = tmp_path / "root"
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    (root / "pyproject.toml").write_text("[project]\nname='x'\nversion='0.0.0'\n", encoding="utf-8")

    if use_override:
        assert resolve_project_root(cwd=nested, override=root) == root
    else:
        assert resolve_project_root(cwd=nested) == root


def test_session_store_roundtrip_and_stale_cleanup(project_dir: Path) -> None:
    store = SessionStore(project_dir)
    store.ensure_state_dir()
    connection_file = store.connection_file
    connection_file.write_text("{}", encoding="utf-8")

    session = SessionInfo(
        session_id="default",
        pid=999_999,
        connection_file=str(connection_file),
        python_executable="python",
        project_root=str(project_dir),
        started_at="2026-01-01T00:00:00+00:00",
    )
    store.save_session(session)

    assert store.load_session() is not None
    assert store.cleanup_stale() is True
    assert store.load_session() is None
    assert not connection_file.exists()


def test_session_store_ignores_untrusted_connection_file_path(project_dir: Path) -> None:
    store = SessionStore(project_dir)
    store.ensure_state_dir()
    victim = project_dir.parent / "victim.txt"
    victim.write_text("keep me", encoding="utf-8")

    session = SessionInfo(
        session_id="default",
        pid=999_999,
        connection_file=str(victim),
        python_executable="python",
        project_root=str(project_dir),
        started_at="2026-01-01T00:00:00+00:00",
    )
    store.save_session(session)

    loaded = store.load_session()

    assert loaded is not None
    assert loaded.connection_file == str(store.connection_file)
    assert store.cleanup_stale() is True
    assert victim.exists()


def test_session_store_isolated_by_session_id(project_dir: Path) -> None:
    primary = SessionStore(project_dir, session_id="primary")
    secondary = SessionStore(project_dir, session_id="secondary")
    primary.ensure_state_dir()

    session_primary = SessionInfo(
        session_id="primary",
        pid=111,
        connection_file=str(primary.connection_file),
        python_executable="python",
        project_root=str(project_dir),
        started_at="2026-01-01T00:00:00+00:00",
    )
    session_secondary = SessionInfo(
        session_id="secondary",
        pid=222,
        connection_file=str(secondary.connection_file),
        python_executable="python",
        project_root=str(project_dir),
        started_at="2026-01-01T00:00:00+00:00",
    )

    primary.save_session(session_primary)
    secondary.save_session(session_secondary)

    loaded_primary = primary.load_session()
    loaded_secondary = secondary.load_session()

    assert loaded_primary is not None
    assert loaded_primary.session_id == "primary"
    assert loaded_secondary is not None
    assert loaded_secondary.session_id == "secondary"


def test_session_store_corrupt_session_file_is_treated_as_missing(project_dir: Path) -> None:
    store = SessionStore(project_dir)
    store.ensure_state_dir()
    store.session_file.write_text("{invalid json", encoding="utf-8")

    assert store.load_session() is None
    assert not store.session_file.exists()


def test_session_store_loads_legacy_file_and_migrates(project_dir: Path) -> None:
    store = SessionStore(project_dir)
    store.ensure_state_dir()
    legacy_payload = {
        "session_id": "default",
        "pid": 123,
        "connection_file": str(store.connection_file),
        "python_executable": "python",
        "project_root": str(project_dir),
        "started_at": "2026-01-01T00:00:00+00:00",
    }
    store.legacy_session_file.write_text(json.dumps(legacy_payload), encoding="utf-8")

    loaded = store.load_session()

    assert loaded is not None
    assert loaded.session_id == "default"
    assert store.session_file.exists()
    assert not store.legacy_session_file.exists()
