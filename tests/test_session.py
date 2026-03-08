from __future__ import annotations

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


def test_history_append_and_filter(project_dir: Path) -> None:
    store = SessionStore(project_dir)
    store.append_history({"ts": "a", "code": "1+1", "status": "ok", "duration_ms": 1})
    store.append_history({"ts": "b", "code": "1/0", "status": "error", "duration_ms": 2})

    all_entries = store.read_history()
    err_entries = store.read_history(errors_only=True)

    assert len(all_entries) == 2
    assert len(err_entries) == 1
    assert err_entries[0]["code"] == "1/0"
