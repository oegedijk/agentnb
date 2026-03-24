from __future__ import annotations

import shutil
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from agentnb.contracts import (
    CommandResponse,
    SuggestionAction,
    error_response,
    success_response,
)
from agentnb.execution import ExecutionRecord
from agentnb.history import HistoryStore
from agentnb.payloads import RunSnapshot
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


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self._now = start

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def advance(self, seconds: float) -> None:
        self._now += max(seconds, 0.0)


def install_fake_clock(mocker: Any, module_name: str, *, start: float = 1_000.0) -> FakeClock:
    clock = FakeClock(start)
    mocker.patch(f"{module_name}.time.monotonic", side_effect=clock.monotonic)
    mocker.patch(f"{module_name}.time.sleep", side_effect=clock.sleep)
    return clock


def build_run_snapshot(**overrides: object) -> RunSnapshot:
    payload: dict[str, object] = {
        "execution_id": "run-1",
        "ts": "2026-03-12T00:00:00+00:00",
        "session_id": "default",
        "command_type": "exec",
        "status": "ok",
        "duration_ms": 5,
    }
    payload.update(overrides)
    return cast(RunSnapshot, payload)


def build_execution_record(**overrides: object) -> ExecutionRecord:
    payload = dict(build_run_snapshot(**overrides))
    return ExecutionRecord.from_dict(cast(dict[str, Any], payload))


def build_success_response(
    *,
    command: str = "status",
    data: dict[str, object] | None = None,
    session_id: str = "default",
    project: str = "/tmp/project",
    suggestions: list[str] | None = None,
    suggestion_actions: list[SuggestionAction] | None = None,
) -> CommandResponse:
    return success_response(
        command=command,
        project=project,
        session_id=session_id,
        data={} if data is None else data,
        suggestions=[] if suggestions is None else suggestions,
        suggestion_actions=[] if suggestion_actions is None else suggestion_actions,
    )


def build_error_response(
    *,
    command: str = "exec",
    code: str,
    message: str,
    data: dict[str, object] | None = None,
    session_id: str = "default",
    project: str = "/tmp/project",
    ename: str | None = None,
    evalue: str | None = None,
    traceback: list[str] | None = None,
    suggestions: list[str] | None = None,
    suggestion_actions: list[SuggestionAction] | None = None,
) -> CommandResponse:
    return error_response(
        command=command,
        project=project,
        session_id=session_id,
        code=code,
        message=message,
        data={} if data is None else data,
        ename=ename,
        evalue=evalue,
        traceback=traceback,
        suggestions=[] if suggestions is None else suggestions,
        suggestion_actions=[] if suggestion_actions is None else suggestion_actions,
    )
