from __future__ import annotations

import json
import shutil
from contextlib import suppress
from pathlib import Path
from typing import Any, cast

from agentnb.command_data import (
    CommandData,
    DoctorCheckData,
    DoctorCommandData,
    ExecCommandData,
    HistoryCommandData,
    InspectCommandData,
    InterruptCommandData,
    KernelSessionData,
    ReloadCommandData,
    RunCancelCommandData,
    RunListEntryData,
    RunLookupCommandData,
    RunsListCommandData,
    RunSnapshotData,
    SessionDeleteCommandData,
    SessionListEntryData,
    SessionsDeleteBulkCommandData,
    SessionsListCommandData,
    StopCommandData,
    VarsCommandData,
)
from agentnb.contracts import (
    CommandResponse,
    HelperAccessMetadata,
    SuggestionAction,
    error_response,
    success_response,
)
from agentnb.execution import ExecutionRecord
from agentnb.execution_output import OutputItem
from agentnb.history import HistoryStore
from agentnb.journal import JournalEntry
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
    raw_outputs = payload.get("outputs")
    if isinstance(raw_outputs, list):
        payload["outputs"] = [
            item.to_dict() if isinstance(item, OutputItem) else item for item in raw_outputs
        ]
    return ExecutionRecord.from_dict(cast(dict[str, Any], payload))


def build_command_data(command: str, data: dict[str, object] | None = None) -> CommandData | None:
    payload = {} if data is None else dict(data)
    switched_session = payload.pop("switched_session", None)
    command_data: CommandData | None

    if command in {"start", "status", "wait"}:
        command_data = KernelSessionData(
            alive=bool(payload.get("alive")),
            pid=cast(int | None, payload.get("pid")),
            connection_file=cast(str | None, payload.get("connection_file")),
            started_at=cast(str | None, payload.get("started_at")),
            uptime_s=cast(float | None, payload.get("uptime_s")),
            python=cast(str | None, payload.get("python")),
            busy=cast(bool | None, payload.get("busy")),
            runtime_state=cast(Any, payload.get("runtime_state")),
            session_exists=cast(bool | None, payload.get("session_exists")),
            lock_pid=cast(int | None, payload.get("lock_pid")),
            lock_acquired_at=cast(str | None, payload.get("lock_acquired_at")),
            busy_for_ms=cast(int | None, payload.get("busy_for_ms")),
            waited=cast(bool | None, payload.get("waited")),
            waited_for=cast(Any, payload.get("waited_for")),
            waited_ms=_optional_int_value(payload.get("waited_ms")),
            initial_runtime_state=cast(Any, payload.get("initial_runtime_state")),
            started_new=cast(bool | None, payload.get("started_new")),
        )
    elif command == "interrupt":
        command_data = InterruptCommandData(
            interrupted=bool(payload.get("interrupted", True)),
        )
    elif command == "stop":
        command_data = StopCommandData(
            stopped=bool(payload.get("stopped", True)),
        )
    elif command in {"exec", "reset"}:
        stdout = cast(str, payload.get("stdout") or "")
        stderr = cast(str, payload.get("stderr") or "")
        result = cast(str | None, payload.get("result"))
        if payload.get("stdout_truncated") is True and not stdout:
            stdout = "x" * 201
        if payload.get("stderr_truncated") is True and not stderr:
            stderr = "x" * 201
        if payload.get("result_truncated") is True and not result:
            result = "x" * 241
        outputs = _exec_outputs(payload, result=result, stdout=stdout, stderr=stderr)
        record = build_execution_record(
            execution_id=payload.get("execution_id", "run-1"),
            ts=payload.get("ts", "2026-03-12T00:00:00+00:00"),
            session_id=payload.get("session_id", "default"),
            command_type=payload.get("command_type", command),
            status=payload.get("status", "ok"),
            duration_ms=payload.get("duration_ms", 0),
            stdout=stdout,
            stderr=stderr,
            result=result,
            execution_count=payload.get("execution_count"),
            ename=payload.get("ename"),
            evalue=payload.get("evalue"),
            traceback=payload.get("traceback"),
            events=payload.get("events", []),
            outputs=outputs,
            terminal_reason=payload.get("terminal_reason"),
            cancel_requested=payload.get("cancel_requested", False),
            error_data=payload.get("error_data"),
        )
        command_data = ExecCommandData(
            record=record,
            source_kind=cast(Any, payload.get("source_kind")),
            source_path=cast(str | None, payload.get("source_path")),
            background=bool(payload.get("background")),
            ensured_started=bool(
                payload.get("ensured_started")
                or payload.get("started_new_session")
                or payload.get("session_restarted")
                or payload.get("initial_runtime_state") is not None
            ),
            started_new_session=bool(payload.get("started_new_session")),
            initial_runtime_state=cast(Any, payload.get("initial_runtime_state")),
            session_restarted=bool(payload.get("session_restarted")),
            session_python=cast(str | None, payload.get("session_python")),
            namespace_delta=cast(Any, payload.get("namespace_delta")),
            selected_output=cast(str | None, payload.get("selected_output")),
            selected_text=cast(str | None, payload.get("selected_text")),
        )
    elif command == "vars":
        command_data = VarsCommandData(
            values=cast(Any, payload.get("vars", [])),
            access_metadata=_helper_access_metadata(payload),
        )
    elif command == "inspect":
        command_data = InspectCommandData(
            payload=cast(Any, payload.get("inspect", {})),
            access_metadata=_helper_access_metadata(payload),
        )
    elif command == "reload":
        command_data = ReloadCommandData(
            payload=cast(Any, _without_helper_access(payload)),
            access_metadata=_helper_access_metadata(payload),
        )
    elif command == "history":
        entries = cast(list[object], payload.get("entries", []))
        command_data = HistoryCommandData(
            entries=[
                _journal_entry_from_mapping(cast(dict[str, object], entry))
                for entry in entries
                if isinstance(entry, dict)
            ],
            full=bool(payload.get("full")),
        )
    elif command == "doctor":
        checks = cast(list[object], payload.get("checks", []))
        command_data = DoctorCommandData(
            ready=bool(payload.get("ready")),
            selected_python=cast(str | None, payload.get("selected_python")),
            python_source=cast(str | None, payload.get("python_source")),
            checks=[
                DoctorCheckData(
                    name=str(cast(dict[str, object], check).get("name") or ""),
                    status=str(cast(dict[str, object], check).get("status") or ""),
                    message=str(cast(dict[str, object], check).get("message") or ""),
                    fix_hint=cast(str | None, cast(dict[str, object], check).get("fix_hint")),
                )
                for check in checks
                if isinstance(check, dict)
            ],
            stale_session_cleaned=bool(payload.get("stale_session_cleaned")),
            session_exists=bool(payload.get("session_exists")),
            kernel_alive=bool(payload.get("kernel_alive")),
            kernel_pid=cast(int | None, payload.get("kernel_pid")),
        )
    elif command == "sessions-list":
        sessions = cast(list[object], payload.get("sessions", []))
        command_data = SessionsListCommandData(
            sessions=[
                SessionListEntryData(
                    session_id=str(cast(dict[str, object], session).get("session_id") or ""),
                    alive=bool(cast(dict[str, object], session).get("alive")),
                    pid=cast(int | None, cast(dict[str, object], session).get("pid")),
                    connection_file=cast(
                        str | None,
                        cast(dict[str, object], session).get("connection_file"),
                    ),
                    started_at=cast(str | None, cast(dict[str, object], session).get("started_at")),
                    uptime_s=cast(float | None, cast(dict[str, object], session).get("uptime_s")),
                    python=cast(str | None, cast(dict[str, object], session).get("python")),
                    last_activity=cast(
                        str | None,
                        cast(dict[str, object], session).get("last_activity"),
                    ),
                    is_default=bool(cast(dict[str, object], session).get("is_default")),
                    is_current=bool(cast(dict[str, object], session).get("is_current")),
                    is_preferred=bool(cast(dict[str, object], session).get("is_preferred")),
                )
                for session in sessions
                if isinstance(session, dict)
            ],
            hidden_non_live_count=_int_value(payload.get("hidden_non_live_count")),
        )
    elif command == "sessions-delete":
        command_data = SessionDeleteCommandData(
            deleted=bool(payload.get("deleted", True)),
            session_id=str(payload.get("session_id") or ""),
            stopped_running_kernel=bool(payload.get("stopped_running_kernel")),
        )
    elif command == "sessions-delete-bulk":
        deleted = payload.get("deleted", [])
        command_data = SessionsDeleteBulkCommandData(
            deleted=[str(item) for item in deleted] if isinstance(deleted, list) else [],
            count=_int_value(payload.get("count")),
        )
    elif command == "runs-list":
        runs = cast(list[object], payload.get("runs", []))
        command_data = RunsListCommandData(
            runs=[
                RunListEntryData(payload=dict(cast(dict[str, object], run)))
                for run in runs
                if isinstance(run, dict)
            ],
        )
    elif command in {"runs-show", "runs-wait", "runs-follow"}:
        run = payload.get("run")
        command_data = RunLookupCommandData(
            run=RunSnapshotData(
                payload=cast(dict[str, object], dict(run) if isinstance(run, dict) else {}),
                include_output=bool(payload.get("include_output", True)),
                snapshot_stale=bool(payload.get("snapshot_stale")),
            ),
            status=cast(str | None, payload.get("status")),
            completion_reason=cast(Any, payload.get("completion_reason")),
            replayed_event_count=cast(int | None, payload.get("replayed_event_count")),
            emitted_event_count=cast(int | None, payload.get("emitted_event_count")),
        )
    elif command == "runs-cancel":
        command_data = RunCancelCommandData(
            execution_id=str(payload.get("execution_id") or "run-1"),
            session_id=str(payload.get("session_id") or "default"),
            cancel_requested=bool(payload.get("cancel_requested")),
            status=str(payload.get("status") or ""),
            run_status=str(payload.get("run_status") or payload.get("status") or ""),
            session_outcome=cast(Any, payload.get("session_outcome") or "unchanged"),
        )
    else:
        command_data = None

    if command_data is not None and isinstance(switched_session, str):
        command_data.switched_session = switched_session
    return command_data


def _helper_access_metadata(payload: dict[str, object]) -> HelperAccessMetadata:
    return HelperAccessMetadata(
        started_new_session=payload.get("started_new_session") is True,
        waited=payload.get("waited") is True,
        waited_for=cast(Any, payload.get("waited_for")),
        waited_ms=_optional_int_value(payload.get("waited_ms")) or 0,
        initial_runtime_state=cast(Any, payload.get("initial_runtime_state")),
        blocking_execution_id=cast(str | None, payload.get("blocking_execution_id")),
    )


def _without_helper_access(payload: dict[str, object]) -> dict[str, object]:
    ignored = {
        "started_new_session",
        "waited",
        "waited_for",
        "waited_ms",
        "initial_runtime_state",
        "blocking_execution_id",
        "switched_session",
    }
    return {key: value for key, value in payload.items() if key not in ignored}


def _journal_entry_from_mapping(payload: dict[str, object]) -> JournalEntry:
    label = payload.get("label")
    if not isinstance(label, str):
        label = cast(str | None, payload.get("code")) or cast(str | None, payload.get("input"))
    if not isinstance(label, str):
        label = str(payload.get("command_type") or "exec")
    return JournalEntry(
        kind=str(payload.get("kind") or "execution"),
        ts=str(payload.get("ts") or "2026-03-12T00:00:00+00:00"),
        session_id=str(payload.get("session_id") or "default"),
        execution_id=cast(str | None, payload.get("execution_id")),
        status=str(payload.get("status") or "ok"),
        duration_ms=_int_value(payload.get("duration_ms")),
        command_type=str(payload.get("command_type") or "exec"),
        label=label,
        user_visible=bool(payload.get("user_visible", True)),
        classification=cast(Any, payload.get("classification") or "replayable"),
        provenance_source=cast(Any, payload.get("provenance_source") or "history_store"),
        provenance_detail=cast(Any, payload.get("provenance_detail") or "user"),
        input=cast(str | None, payload.get("input")),
        code=cast(str | None, payload.get("code")),
        origin=cast(str | None, payload.get("origin")),
        error_type=cast(str | None, payload.get("error_type")),
        failure_origin=cast(Any, payload.get("failure_origin")),
        result_preview=cast(str | None, payload.get("result_preview")),
        stdout_preview=cast(str | None, payload.get("stdout_preview")),
    )


def build_success_response(
    *,
    command: str = "status",
    data: dict[str, object] | None = None,
    command_data: CommandData | None = None,
    session_id: str = "default",
    project: str = "/tmp/project",
    suggestions: list[str] | None = None,
    suggestion_actions: list[SuggestionAction] | None = None,
) -> CommandResponse:
    resolved_command_data = (
        build_command_data(command, data) if command_data is None else command_data
    )
    if resolved_command_data is None:
        return CommandResponse(
            status="ok",
            command=command,
            project=project,
            session_id=session_id,
            data={} if data is None else dict(data),
            suggestions=[] if suggestions is None else suggestions,
            suggestion_actions=[] if suggestion_actions is None else suggestion_actions,
        )
    return success_response(
        command=command,
        project=project,
        session_id=session_id,
        command_data=resolved_command_data,
        suggestions=[] if suggestions is None else suggestions,
        suggestion_actions=[] if suggestion_actions is None else suggestion_actions,
    )


def build_error_response(
    *,
    command: str = "exec",
    code: str,
    message: str,
    data: dict[str, object] | None = None,
    command_data: CommandData | None = None,
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
        command_data=command_data,
        error_data={} if data is None else data,
        ename=ename,
        evalue=evalue,
        traceback=traceback,
        suggestions=[] if suggestions is None else suggestions,
        suggestion_actions=[] if suggestion_actions is None else suggestion_actions,
    )


def _exec_outputs(
    payload: dict[str, object],
    *,
    result: str | None,
    stdout: str,
    stderr: str,
) -> list[OutputItem]:
    outputs: list[OutputItem] = []
    if stdout:
        outputs.append(OutputItem.stdout(stdout))
    if stderr:
        outputs.append(OutputItem.stderr(stderr))
    result_preview = payload.get("result_preview")
    if isinstance(result_preview, dict):
        preview_payload = cast(dict[str, object], result_preview)
        mime = {"text/plain": result or ""}
        preview_kind = preview_payload.get("kind")
        if preview_kind == "dataframe-like":
            mime["text/plain"] = _dataframe_text(preview_payload)
            mime["text/html"] = _dataframe_html(preview_payload)
        elif preview_kind in {"sequence-like", "mapping-like"}:
            preview_value = _preview_value(preview_payload)
            mime["application/json"] = json.dumps(preview_value)
        outputs.append(OutputItem.result(text=result, mime=mime))
    elif result is not None:
        outputs.append(OutputItem.result(text=result))
    return outputs


def _dataframe_text(preview: dict[str, object]) -> str:
    columns = preview.get("columns")
    shape = preview.get("shape")
    headers = [str(column) for column in columns] if isinstance(columns, list) else ["value"]
    row = "  ".join("0" for _ in headers)
    body = f"  {'  '.join(headers)}\n0 {row}"
    if isinstance(shape, list) and len(shape) == 2:
        return f"{body}\n\n[{shape[0]} rows x {shape[1]} columns]"
    return body


def _dataframe_html(preview: dict[str, object]) -> str:
    columns = preview.get("columns")
    headers = [str(column) for column in columns] if isinstance(columns, list) else ["value"]
    head_cells = "".join(f"<th>{column}</th>" for column in headers)
    data_cells = "".join("<td>0</td>" for _ in headers)
    return (
        '<table border="1" class="dataframe">'
        f"<thead><tr><th></th>{head_cells}</tr></thead>"
        f"<tbody><tr><th>0</th>{data_cells}</tr></tbody>"
        "</table>"
    )


def _preview_value(preview: dict[str, object]) -> object:
    kind = preview.get("kind")
    if kind == "sequence-like":
        sample = preview.get("sample")
        if not isinstance(sample, list):
            return []
        length = preview.get("length")
        if not isinstance(length, int) or length <= len(sample):
            return sample
        if not sample:
            return [None] * length
        return [*sample, *[sample[-1]] * (length - len(sample))]
    if kind == "mapping-like":
        sample = preview.get("sample")
        if not isinstance(sample, dict):
            return {}
        value = dict(sample)
        keys = preview.get("keys")
        if isinstance(keys, list):
            for key in keys:
                if isinstance(key, str) and key not in value:
                    value[key] = None
        return value
    return {}


def _int_value(value: object) -> int:
    return value if isinstance(value, int) else 0


def _optional_int_value(value: object) -> int | None:
    return value if isinstance(value, int) else None
