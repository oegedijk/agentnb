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


def reset_integration_kernel(
    runtime: KernelRuntime,
    project_dir: Path,
    *,
    clear_project_modules: bool = False,
) -> None:
    store = SessionStore(project_dir)
    history_store = HistoryStore(project_dir)
    _safe_unlink(history_store.history_file)
    _safe_unlink(store.command_lock_file)

    with suppress(Exception):
        runtime.execute(
            project_root=project_dir,
            timeout_s=5,
            code=_kernel_cleanup_code(
                project_dir,
                clear_project_modules=clear_project_modules,
            ),
        )


def cleanup_integration_project(
    runtime: KernelRuntime,
    project_dir: Path,
    *,
    clear_project_modules: bool = True,
) -> None:
    reset_integration_kernel(
        runtime,
        project_dir,
        clear_project_modules=clear_project_modules,
    )

    for child in project_dir.iterdir():
        if child.name in {"pyproject.toml", ".agentnb", ".gitignore"}:
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
            continue
        _safe_unlink(child)


def _kernel_cleanup_code(project_dir: Path, *, clear_project_modules: bool) -> str:
    return f"""
import importlib as _importlib
import sys as _sys
from pathlib import Path as _Path
from IPython import get_ipython as _get_ipython

_project_root = _Path({str(project_dir)!r}).resolve()
_clear_project_modules = {clear_project_modules!r}
_ip = _get_ipython()
_user_ns = _ip.user_ns if _ip is not None else globals()
_keep_names = {{
    "In",
    "Out",
    "exit",
    "get_ipython",
    "open",
    "quit",
}}

for _name in list(_user_ns):
    if _name.startswith("_") or _name in _keep_names:
        continue
    _user_ns.pop(_name, None)

if _ip is not None:
    _user_ns_hidden = getattr(_ip, "user_ns_hidden", None)
    if isinstance(_user_ns_hidden, dict):
        for _name in list(_user_ns_hidden):
            if _name.startswith("_"):
                continue
            _user_ns_hidden.pop(_name, None)

if _clear_project_modules:
    for _name, _module in list(_sys.modules.items()):
        _module_file = getattr(_module, "__file__", None)
        if not _module_file:
            continue
        try:
            _module_path = _Path(_module_file).resolve()
        except Exception:
            continue
        try:
            _module_path.relative_to(_project_root)
        except ValueError:
            continue
        _sys.modules.pop(_name, None)

_importlib.invalidate_caches()
"""


def _safe_unlink(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass
