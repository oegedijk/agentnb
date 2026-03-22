from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import cast

from .compact import summarize_history_text
from .contracts import CommandResponse
from .payloads import (
    DataframePreview,
    DoctorPayload,
    ExecPayload,
    HistoryEntryPayload,
    HistoryPayload,
    InspectResponsePayload,
    MappingPreview,
    ReloadReport,
    RunLookupPayload,
    RunsListPayload,
    RunSnapshot,
    SequencePreview,
    SessionsListPayload,
    StartPayload,
    StatusPayload,
    VarDisplayEntry,
    VarsPayload,
)
from .projection import ResponseProjector

projector = ResponseProjector()


class OutputProfile(StrEnum):
    HUMAN = "human"
    FULL_JSON = "full-json"
    AGENT = "agent"


@dataclass(slots=True)
class RenderOptions:
    profile: OutputProfile = OutputProfile.HUMAN
    quiet_human: bool = False
    suppress_suggestions: bool = False

    @property
    def as_json(self) -> bool:
        return self.profile in {OutputProfile.FULL_JSON, OutputProfile.AGENT}

    @property
    def quiet(self) -> bool:
        return self.profile == OutputProfile.AGENT or self.quiet_human

    @property
    def show_suggestions(self) -> bool:
        if self.profile == OutputProfile.AGENT:
            return False
        if self.quiet_human:
            return False
        return not self.suppress_suggestions

    def with_local_json(self) -> RenderOptions:
        if self.as_json:
            return self
        return replace(self, profile=OutputProfile.FULL_JSON)

    @classmethod
    def resolve(
        cls,
        *,
        root_as_json: bool,
        agent: bool,
        quiet: bool,
        no_suggestions: bool,
        env: Mapping[str, str] | None = None,
    ) -> RenderOptions:
        environment = os.environ if env is None else env
        env_mode = environment.get("AGENTNB_FORMAT", "").strip().lower()
        env_quiet = _env_flag(environment, "AGENTNB_QUIET")
        env_no_suggestions = _env_flag(environment, "AGENTNB_NO_SUGGESTIONS")

        if agent or env_mode == OutputProfile.AGENT.value:
            profile = OutputProfile.AGENT
        elif root_as_json or env_mode == "json":
            profile = OutputProfile.FULL_JSON
        else:
            profile = OutputProfile.HUMAN

        return cls(
            profile=profile,
            quiet_human=quiet or env_quiet,
            suppress_suggestions=no_suggestions or env_no_suggestions,
        )


def render_response(response: CommandResponse, *, options: RenderOptions) -> str:
    if options.as_json:
        return json.dumps(
            projector.project(response, profile=options.profile.value),
            ensure_ascii=True,
        )
    return render_human(response, options=options)


def render_human(response: CommandResponse, *, options: RenderOptions) -> str:
    if response.status == "error":
        body = _render_error(response)
    else:
        command = response.command
        data = response.data

        quiet_commands = {"start", "status", "wait", "stop", "interrupt", "reload", "doctor"}
        if options.quiet and command in quiet_commands:
            body = ""
        elif command == "start":
            start_data = cast(StartPayload, data)
            if start_data.get("alive"):
                python = start_data.get("python")
                if start_data.get("started_new"):
                    if python:
                        body = f"Kernel started (pid {start_data.get('pid')}) using {python}."
                    else:
                        body = f"Kernel started (pid {start_data.get('pid')})."
                elif python:
                    body = f"Kernel already running (pid {start_data.get('pid')}) using {python}."
                else:
                    body = f"Kernel already running (pid {start_data.get('pid')})."
            else:
                body = "Kernel is not running."

        elif command == "status":
            status_data = cast(StatusPayload, data)
            session_label = f"session: {response.session_id}, " if response.session_id else ""
            session_name = f"session: {response.session_id}" if response.session_id else None
            wait_note = _wait_note(status_data)
            if status_data.get("alive"):
                if status_data.get("busy"):
                    busy_for_ms = status_data.get("busy_for_ms")
                    if isinstance(busy_for_ms, int):
                        busy_detail = (
                            f"busy for {_format_duration_ms(busy_for_ms)}"
                            f"{_detail_suffix(wait_note)}"
                        )
                        body = (
                            "Kernel is running "
                            f"({session_label}pid {status_data.get('pid')}, {busy_detail})."
                        )
                    else:
                        busy_detail = f"busy{_detail_suffix(wait_note)}"
                        body = (
                            "Kernel is running "
                            f"({session_label}pid {status_data.get('pid')}, {busy_detail})."
                        )
                else:
                    body = (
                        f"Kernel is running ({session_label}pid {status_data.get('pid')}"
                        f"{_detail_suffix(wait_note)})."
                    )
            elif status_data.get("runtime_state") == "starting":
                body = (
                    f"Kernel is starting ({session_name})."
                    if session_name is not None
                    else "Kernel is starting."
                )
            elif status_data.get("runtime_state") == "dead":
                body = (
                    f"Kernel is dead ({session_name})."
                    if session_name is not None
                    else "Kernel is dead."
                )
            else:
                body = "Kernel is not running."
        elif command == "wait":
            status_data = cast(StatusPayload, data)
            session_label = f"session: {response.session_id}, " if response.session_id else ""
            session_name = f"session: {response.session_id}" if response.session_id else None
            wait_note = _wait_note(status_data)
            if status_data.get("alive"):
                waited_for = status_data.get("waited_for")
                if waited_for == "ready":
                    body = (
                        f"Kernel is ready ({session_label}pid {status_data.get('pid')}"
                        f"{_detail_suffix(wait_note)})."
                    )
                else:
                    body = (
                        f"Kernel is idle ({session_label}pid {status_data.get('pid')}"
                        f"{_detail_suffix(wait_note)})."
                    )
            elif status_data.get("runtime_state") == "starting":
                body = (
                    f"Kernel is starting ({session_name})."
                    if session_name is not None
                    else "Kernel is starting."
                )
            elif status_data.get("runtime_state") == "dead":
                body = (
                    f"Kernel is dead ({session_name})."
                    if session_name is not None
                    else "Kernel is dead."
                )
            else:
                body = "Kernel is not running."

        elif command == "stop":
            body = "Kernel stopped."

        elif command == "interrupt":
            body = "Interrupt signal sent."

        elif command == "exec":
            body = _render_exec_like(cast(ExecPayload, data))

        elif command == "reset":
            body = "Namespace cleared."

        elif command == "vars":
            vars_data = cast(VarsPayload, data).get("vars", [])
            if not vars_data:
                body = "No user variables found."
            else:
                lines = [_render_var_entry(item) for item in vars_data]
                body = "\n".join(lines)
            body = _prepend_session_identity(body, response.session_id)
            body = _append_helper_access_note(body, cast(Mapping[str, object], data))

        elif command == "inspect":
            inspect_response = cast(InspectResponsePayload, data)
            inspect_data = inspect_response.get("inspect", {})
            members = inspect_data.get("members", [])
            members_text = ", ".join(members[:30]) if members else "(none)"
            preview = inspect_data.get("preview")
            if isinstance(preview, dict) and preview.get("kind") == "dataframe-like":
                lines = [
                    f"name: {inspect_data.get('name')}",
                    f"type: {inspect_data.get('type')}",
                ]
                lines.extend(_render_dataframe_preview(cast(DataframePreview, preview)))
            elif isinstance(preview, dict) and preview.get("kind") in {
                "sequence-like",
                "mapping-like",
            }:
                lines = [
                    f"name: {inspect_data.get('name')}",
                    f"type: {inspect_data.get('type')}",
                ]
                lines.extend(
                    _render_collection_preview(cast(MappingPreview | SequencePreview, preview))
                )
            else:
                lines = [
                    f"name: {inspect_data.get('name')}",
                    f"type: {inspect_data.get('type')}",
                    f"repr: {inspect_data.get('repr')}",
                ]
                lines.append(f"members: {members_text}")
            body = "\n".join(lines)
            body = _prepend_session_identity(body, response.session_id)
            body = _append_helper_access_note(body, cast(Mapping[str, object], data))

        elif command == "reload":
            body = _render_reload(cast(ReloadReport, data))
            body = _append_helper_access_note(body, cast(Mapping[str, object], data))

        elif command == "history":
            entries = cast(HistoryPayload, data).get("entries", [])
            if not entries:
                body = "No history entries."
            else:
                lines = [_render_history_entry(entry) for entry in entries]
                body = "\n".join(lines)

        elif command == "doctor":
            doctor_data = cast(DoctorPayload, data)
            checks = doctor_data.get("checks", [])
            headline = (
                "Doctor checks passed." if doctor_data.get("ready") else "Doctor found issues."
            )
            lines = [headline]
            for check in checks:
                status = str(check.get("status", "unknown")).upper()
                message = check.get("message")
                lines.append(f"[{status}] {check.get('name')}: {message}")
                hint = check.get("fix_hint")
                if hint:
                    lines.append(f"  fix: {hint}")
            kernel_alive = doctor_data.get("kernel_alive")
            if kernel_alive is True:
                kernel_pid = doctor_data.get("kernel_pid")
                lines.append(f"[OK] kernel: Kernel is running (pid {kernel_pid}).")
            elif doctor_data.get("session_exists"):
                lines.append("[WARN] kernel: Session exists but kernel is not running.")
            body = "\n".join(lines)
        elif command == "sessions-list":
            sessions = cast(SessionsListPayload, data).get("sessions", [])
            if not sessions:
                body = "No sessions found."
            else:
                lines = []
                for session in sessions:
                    markers: list[str] = []
                    if session.get("is_default"):
                        markers.append("default")
                    if session.get("is_current"):
                        markers.append("current")
                    marker = f" ({', '.join(markers)})" if markers else ""
                    python = session.get("python")
                    python_text = f" using {python}" if python else ""
                    activity = _staleness_hint(session.get("last_activity"))
                    activity_text = f", last activity {activity}" if activity else ""
                    session_label = session.get("session_id")
                    lines.append(
                        f"{session_label}{marker}: pid {session.get('pid')}"
                        f"{python_text}{activity_text}"
                    )
                body = "\n".join(lines)
        elif command == "sessions-delete":
            stopped = " and stopped its kernel" if data.get("stopped_running_kernel") else ""
            body = f"Deleted session {data.get('session_id')}{stopped}."
        elif command == "sessions-delete-bulk":
            deleted = data.get("deleted", [])
            if isinstance(deleted, list) and deleted:
                body = f"Deleted {len(deleted)} session(s): {', '.join(str(s) for s in deleted)}"
            else:
                body = "No sessions to delete."
        elif command == "runs-list":
            runs = cast(RunsListPayload, data).get("runs", [])
            if not runs:
                body = "No runs found."
            else:
                lines = []
                for run in runs:
                    display_status = (
                        "cancelled"
                        if run.get("terminal_reason") == "cancelled"
                        else run.get("status")
                    )
                    lines.append(
                        f"{run.get('ts')} [{display_status}] {run.get('execution_id')} "
                        f"{run.get('command_type')} {run.get('duration_ms')}ms"
                    )
                body = "\n".join(lines)
        elif command == "runs-show" or command == "runs-wait":
            run_data = cast(RunLookupPayload, data).get("run", {})
            body = _render_run_snapshot(run_data, snapshot_only=(command == "runs-show"))
        elif command == "runs-cancel":
            if data.get("cancel_requested"):
                if data.get("status") == "ok":
                    body = (
                        f"Cancel requested for run {data.get('execution_id')}, "
                        "but it completed before cancellation took effect."
                    )
                elif data.get("session_outcome") == "preserved":
                    body = f"Cancelled run {data.get('execution_id')}. The session was preserved."
                elif data.get("session_outcome") == "stopped":
                    body = (
                        f"Cancelled run {data.get('execution_id')}. "
                        "The still-starting session was stopped."
                    )
                else:
                    body = f"Cancel requested for run {data.get('execution_id')}."
            else:
                _terminal_labels = {"ok": "finished", "error": "failed", "cancelled": "cancelled"}
                label = _terminal_labels.get(str(data.get("status")), str(data.get("status")))
                body = f"Run {data.get('execution_id')} already {label}."
        else:
            body = json.dumps(data, ensure_ascii=True, indent=2)

    switched = response.data.get("switched_session")
    if switched and not response.data.get("selected_output"):
        body = f"{body}\n(now targeting session: {switched})"

    suggestions = response.suggestions if options.show_suggestions else []
    return _append_suggestions(body, suggestions)


def _render_exec_like(data: ExecPayload) -> str:
    selector = data.get("selected_output")
    if selector is not None:
        selected_text = data.get("selected_text", "")
        return str(selected_text).rstrip("\n")

    lines: list[str] = []
    stdout = data.get("stdout")
    stderr = data.get("stderr")
    result = data.get("result")

    if stdout:
        lines.append(stdout.rstrip("\n"))
    if stderr:
        lines.append("[stderr]")
        lines.append(stderr.rstrip("\n"))
    if result is not None:
        lines.append(str(result))
    if not lines:
        if data.get("background"):
            execution_id = data.get("execution_id", "")
            return f"Background execution started ({execution_id})."
        lines.append("Execution completed.")
    return "\n".join(lines)


def _prepend_session_identity(body: str, session_id: str | None) -> str:
    if not session_id:
        return body
    return f"session: {session_id}\n{body}"


def _render_var_entry(item: VarDisplayEntry) -> str:
    name = item.get("name")
    repr_text = item.get("repr")
    type_name = item.get("type")
    if type_name:
        return f"{name}: {repr_text} ({type_name})"
    return f"{name}: {repr_text}"


def _render_error(response: CommandResponse) -> str:
    lines: list[str] = []

    stdout = response.data.get("stdout") if response.data else None
    if isinstance(stdout, str) and stdout:
        lines.append(stdout.rstrip("\n"))
    stderr = response.data.get("stderr") if response.data else None
    if isinstance(stderr, str) and stderr:
        lines.append("[stderr]")
        lines.append(stderr.rstrip("\n"))

    if response.error is None:
        lines.append("Error: unknown error")
        return "\n".join(lines)

    lines.append(f"Error: {response.error.message}")
    if response.error.ename:
        lines.append(f"Type: {response.error.ename}")
    if response.error.evalue:
        lines.append(f"Detail: {response.error.evalue}")
    if response.error.traceback:
        lines.extend(response.error.traceback)
    return "\n".join(lines)


def _append_suggestions(body: str, suggestions: list[str]) -> str:
    if not suggestions:
        return body
    lines = [body, "", "Next:"]
    lines.extend(f"- {suggestion}" for suggestion in suggestions)
    return "\n".join(lines)


def _format_duration_ms(duration_ms: int) -> str:
    if duration_ms < 1000:
        return f"{duration_ms}ms"
    seconds = duration_ms / 1000
    if seconds < 10:
        return f"{seconds:.1f}s"
    return f"{int(seconds)}s"


def _detail_suffix(detail: str | None) -> str:
    if not detail:
        return ""
    return f", {detail}"


def _wait_note(data: Mapping[str, object]) -> str | None:
    if not data.get("waited"):
        return None
    waited_ms = data.get("waited_ms")
    waited_for = data.get("waited_for")
    initial_runtime_state = data.get("initial_runtime_state")
    if (
        not isinstance(waited_ms, int)
        and not isinstance(waited_for, str)
        and not isinstance(initial_runtime_state, str)
    ):
        return None
    parts: list[str] = []
    if isinstance(waited_ms, int):
        parts.append(f"after waiting {_format_duration_ms(waited_ms)}")
    else:
        parts.append("after waiting")
    if isinstance(waited_for, str) and waited_for:
        parts.append(f"for {waited_for}")
    if isinstance(initial_runtime_state, str) and initial_runtime_state:
        parts.append(f"from {initial_runtime_state}")
    return " ".join(parts)


def _append_helper_access_note(body: str, data: Mapping[str, object]) -> str:
    parts: list[str] = []
    if data.get("started_new_session"):
        parts.append("auto-started session")
    wait_note = _wait_note(data)
    if wait_note:
        parts.append(wait_note)
    if not parts:
        return body
    note = f"({'; '.join(parts)})"
    if not body:
        return note
    return f"{body}\n{note}"


def _staleness_hint(iso_timestamp: str | None) -> str | None:
    if not isinstance(iso_timestamp, str) or not iso_timestamp:
        return None
    try:
        from datetime import UTC, datetime

        ts = datetime.fromisoformat(iso_timestamp)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        delta = datetime.now(UTC) - ts
        seconds = int(delta.total_seconds())
        if seconds < 0:
            return None
        if seconds < 60:
            return "just now"
        if seconds < 3600:
            minutes = seconds // 60
            return f"{minutes}m ago"
        if seconds < 86400:
            hours = seconds // 3600
            return f"{hours}h ago"
        days = seconds // 86400
        return f"{days}d ago"
    except Exception:
        return None


def _env_flag(env: Mapping[str, str], name: str) -> bool:
    value = env.get(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _render_run_snapshot(run: RunSnapshot, *, snapshot_only: bool) -> str:
    if not run:
        return "{}"

    execution_id = run.get("execution_id", "(unknown)")
    status = (
        "cancelled" if run.get("terminal_reason") == "cancelled" else run.get("status", "unknown")
    )
    command_type = run.get("command_type", "exec")
    session_id = run.get("session_id", "default")
    duration_ms = run.get("duration_ms")

    lines = [f"Run {execution_id} [{status}] {command_type} on session {session_id}."]
    if isinstance(duration_ms, int):
        lines.append(f"duration: {duration_ms}ms")
    if snapshot_only and status in {"starting", "running"}:
        lines.append("snapshot: persisted state only; use `agentnb runs follow` for live events")

    stdout = run.get("stdout")
    if isinstance(stdout, str) and stdout:
        lines.append(f"stdout: {summarize_history_text(stdout, limit=200) or stdout[:200]}")

    stderr = run.get("stderr")
    if isinstance(stderr, str) and stderr:
        lines.append(f"stderr: {summarize_history_text(stderr, limit=200) or stderr[:200]}")

    result = run.get("result")
    if isinstance(result, str) and result:
        lines.append(f"result: {summarize_history_text(result, limit=240) or result[:240]}")

    ename = run.get("ename")
    evalue = run.get("evalue")
    if ename or evalue:
        if ename and evalue:
            lines.append(f"error: {ename}: {evalue}")
        elif ename:
            lines.append(f"error: {ename}")
        else:
            lines.append(f"error: {evalue}")

    events = run.get("events")
    if isinstance(events, list):
        lines.append(f"events: {len(events)} recorded")

    return "\n".join(lines)


def _render_dataframe_preview(preview: DataframePreview) -> list[str]:
    lines: list[str] = []
    shape = preview.get("shape")
    if isinstance(shape, list) and len(shape) == 2:
        lines.append(f"shape: ({shape[0]}, {shape[1]})")

    columns = preview.get("columns")
    if isinstance(columns, list) and columns:
        lines.append("columns: " + ", ".join(str(column) for column in columns[:20]))

    dtypes = preview.get("dtypes")
    if isinstance(dtypes, dict) and dtypes:
        dtype_items = list(dtypes.items())[:10]
        dtype_text = ", ".join(f"{name}={dtype}" for name, dtype in dtype_items)
        lines.append(f"dtypes: {dtype_text}")

    null_counts = preview.get("null_counts")
    if isinstance(null_counts, dict) and null_counts:
        null_items = list(null_counts.items())[:10]
        null_text = ", ".join(f"{name}={count}" for name, count in null_items)
        lines.append(f"nulls: {null_text}")

    head = preview.get("head")
    if isinstance(head, list):
        lines.append("head: " + json.dumps(head, ensure_ascii=True))

    return lines


def _render_history_entry(entry: HistoryEntryPayload) -> str:
    label = _history_label(entry)
    prefix = "[internal] " if entry.get("kind") == "kernel_execution" else ""
    line = f"{entry.get('ts')} [{entry.get('status')}] {entry.get('duration_ms')}ms {prefix}{label}"
    code = entry.get("code")
    if code and entry.get("status") == "error" and code not in label:
        line += f"\n  code: {code}"
    return line


def _history_label(entry: HistoryEntryPayload) -> str:
    label = entry.get("label")
    if isinstance(label, str) and label:
        return label

    command_type = entry.get("command_type")
    if command_type == "exec":
        code = entry.get("code") or entry.get("input")
        summarized = _summarize_history_text(code)
        return "exec" if summarized is None else f"exec {summarized}"

    code = entry.get("code")
    summarized = _summarize_history_text(code)
    if summarized is not None:
        return summarized
    return "history entry"


def _summarize_history_text(value: object, limit: int = 100) -> str | None:
    if not isinstance(value, str):
        return None
    compact = " ".join(value.strip().split())
    if not compact:
        return None
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _render_reload(data: ReloadReport) -> str:
    reloaded_modules = data.get("reloaded_modules", [])
    rebound_names = data.get("rebound_names", [])
    stale_names = data.get("stale_names", [])
    failed_modules = data.get("failed_modules", [])
    notes = data.get("notes", [])
    requested_module = data.get("requested_module")

    if isinstance(reloaded_modules, list) and reloaded_modules:
        if isinstance(requested_module, str) and len(reloaded_modules) == 1:
            lines = [f"Reloaded module: {reloaded_modules[0]}"]
        else:
            modules = ", ".join(str(module) for module in reloaded_modules[:10])
            lines = [f"Reloaded {len(reloaded_modules)} project modules: {modules}"]
    else:
        lines = ["No imported project-local modules were reloaded."]

    if isinstance(rebound_names, list) and rebound_names:
        lines.append("Rebound names: " + ", ".join(str(name) for name in rebound_names[:10]))
    if isinstance(stale_names, list) and stale_names:
        lines.append("Possible stale objects: " + ", ".join(str(name) for name in stale_names[:10]))
        lines.append("Recreate them or run `agentnb reset` if stale state is widespread.")
    if isinstance(failed_modules, list) and failed_modules:
        failed_names = ", ".join(str(item.get("module")) for item in failed_modules[:10])
        lines.append("Failed modules: " + failed_names)
    if isinstance(notes, list):
        lines.extend(str(note) for note in notes if isinstance(note, str) and note)
    return "\n".join(lines)


def _render_collection_preview(preview: MappingPreview | SequencePreview) -> list[str]:
    lines: list[str] = []

    length = preview.get("length")
    if isinstance(length, int):
        lines.append(f"length: {length}")

    item_type = preview.get("item_type")
    if isinstance(item_type, str) and item_type:
        lines.append(f"item_type: {item_type}")

    keys = preview.get("keys")
    if isinstance(keys, list) and keys:
        lines.append("keys: " + ", ".join(str(key) for key in keys[:10]))

    sample_keys = preview.get("sample_keys")
    if isinstance(sample_keys, list) and sample_keys:
        lines.append("sample_keys: " + ", ".join(str(key) for key in sample_keys[:10]))

    sample = preview.get("sample")
    if sample is not None:
        lines.append("sample: " + json.dumps(sample, ensure_ascii=True))

    return lines
