from __future__ import annotations

import shutil
from contextlib import suppress
from pathlib import Path

from agentnb.history import HistoryStore
from agentnb.runtime import KernelRuntime
from agentnb.session import SessionStore


def create_project_dir(base: Path, name: str = "project") -> Path:
    project = base / name
    project.mkdir()
    (project / "pyproject.toml").write_text(
        """
[project]
name = "fixture-project"
version = "0.0.0"
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return project


def cleanup_integration_project(runtime: KernelRuntime, project_dir: Path) -> None:
    store = SessionStore(project_dir)
    history_store = HistoryStore(project_dir)
    _safe_unlink(history_store.history_file)
    _safe_unlink(store.command_lock_file)

    try:
        status = runtime.status(project_dir)
    except Exception:
        status = None

    if status is not None and status.alive:
        with suppress(Exception):
            runtime.execute(
                project_root=project_dir,
                timeout_s=5,
                code=_module_cleanup_code(project_dir),
            )

        with suppress(Exception):
            runtime.reset(project_root=project_dir, timeout_s=5)

    for child in project_dir.iterdir():
        if child.name in {"pyproject.toml", ".agentnb", ".gitignore"}:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            continue
        _safe_unlink(child)


def _module_cleanup_code(project_dir: Path) -> str:
    return f"""
import importlib
import sys
from pathlib import Path

_project_root = Path({str(project_dir)!r}).resolve()

for _name, _module in list(sys.modules.items()):
    _module_file = getattr(_module, "__file__", None)
    if not _module_file:
        continue
    try:
        _module_path = Path(_module_file).resolve()
    except Exception:
        continue
    try:
        _module_path.relative_to(_project_root)
    except ValueError:
        continue
    sys.modules.pop(_name, None)

importlib.invalidate_caches()
"""


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
