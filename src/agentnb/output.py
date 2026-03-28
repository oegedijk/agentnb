from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any, cast

from .command_data import (
    DoctorCommandData,
    ExecCommandData,
    HistoryCommandData,
    InspectCommandData,
    InterruptCommandData,
    KernelSessionData,
    ReloadCommandData,
    RunCancelCommandData,
    RunLookupCommandData,
    RunsListCommandData,
    SessionDeleteCommandData,
    SessionsDeleteBulkCommandData,
    SessionsListCommandData,
    StopCommandData,
    VarsCommandData,
)
from .compact import preview_text
from .contracts import CommandResponse
from .introspection_models import (
    DataframePreviewData,
    MappingPreviewData,
    ReloadResult,
    SequencePreviewData,
    VariableEntry,
)
from .payloads import HistoryEntryPayload
from .projection import ResponseProjector
from .response_serialization import (
    compact_history_entry,
    full_history_entry,
    summarize_history_text,
)

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


def render_stream_completion(
    response: CommandResponse,
    *,
    options: RenderOptions,
    output_emitted: bool,
) -> str:
    if (
        options.as_json
        or response.status == "error"
        or response.command != "exec"
        or not output_emitted
    ):
        return render_response(response, options=options)

    body = ""
    exec_notice = _exec_notice(response)
    if exec_notice:
        body = exec_notice

    switched = response.data.get("switched_session")
    if not options.quiet and switched and not response.data.get("selected_output"):
        switch_note = f"(now targeting session: {switched})"
        body = f"{body}\n{switch_note}" if body else switch_note

    show_suggestions = options.show_suggestions and not options.quiet
    suggestions = response.suggestions if show_suggestions else []
    return _append_suggestions(body, suggestions)


def render_human(response: CommandResponse, *, options: RenderOptions) -> str:
    command_data = response.command_data
    if response.status == "error":
        body = _render_error(response)
    else:
        command = response.command
        data = response.data

        quiet_commands = {"start", "status", "wait", "stop", "interrupt", "reload", "doctor"}
        if options.quiet and command in quiet_commands:
            body = ""
        elif command in {"start", "status", "wait"} and isinstance(command_data, KernelSessionData):
            body = _render_kernel_session(command_data, response.session_id, mode=command)
        elif command == "stop" and isinstance(command_data, StopCommandData):
            body = _render_stop_command_data(command_data)
        elif command == "interrupt" and isinstance(command_data, InterruptCommandData):
            body = _render_interrupt_command_data(command_data)
        elif command == "exec" and isinstance(command_data, ExecCommandData):
            body = _render_exec_command_data(command_data)
        elif command == "reset":
            body = "Namespace cleared."
        elif command == "vars" and isinstance(command_data, VarsCommandData):
            body = _render_vars_command_data(command_data, response.session_id)
        elif command == "inspect" and isinstance(command_data, InspectCommandData):
            body = _render_inspect_command_data(command_data, response.session_id)
        elif command == "reload" and isinstance(command_data, ReloadCommandData):
            body = _render_reload_command_data(command_data)
            body = _append_helper_access_note(
                body,
                command_data.access_metadata.merge_data(),
            )
        elif command == "history" and isinstance(command_data, HistoryCommandData):
            body = _render_history_command_data(command_data)
        elif command == "doctor" and isinstance(command_data, DoctorCommandData):
            body = _render_doctor_command_data(command_data)
        elif command == "sessions-list" and isinstance(command_data, SessionsListCommandData):
            body = _render_sessions_list_command_data(command_data)
        elif command == "sessions-delete" and isinstance(command_data, SessionDeleteCommandData):
            body = _render_session_delete_command_data(command_data)
        elif command == "sessions-delete-bulk" and isinstance(
            command_data, SessionsDeleteBulkCommandData
        ):
            body = _render_sessions_delete_bulk_command_data(command_data)
        elif command == "runs-list" and isinstance(command_data, RunsListCommandData):
            body = _render_runs_list_command_data(command_data)
        elif command in {"runs-show", "runs-wait", "runs-follow"} and isinstance(
            command_data, RunLookupCommandData
        ):
            body = _render_run_lookup_command_data(
                command_data,
                snapshot_only=(command == "runs-show"),
            )
            if command == "runs-follow":
                follow_note = _render_follow_completion_note_data(command_data)
                if follow_note is not None:
                    body = f"{body}\n{follow_note}" if body else follow_note
        elif command == "runs-cancel" and isinstance(command_data, RunCancelCommandData):
            body = _render_run_cancel_command_data(command_data)
        else:
            body = json.dumps(data, ensure_ascii=True, indent=2)

    if response.status != "error":
        exec_notice = _exec_notice(response)
        if exec_notice:
            body = f"{body}\n{exec_notice}" if body else exec_notice

    switched = response.data.get("switched_session")
    if not options.quiet and switched and not response.data.get("selected_output"):
        body = f"{body}\n(now targeting session: {switched})"

    show_suggestions = options.show_suggestions and (
        response.status == "error" or not options.quiet
    )
    suggestions = response.suggestions if show_suggestions else []
    return _append_suggestions(body, suggestions)


def _render_exec_command_data(data: ExecCommandData) -> str:
    selector = data.selected_output
    if selector is not None:
        return (data.selected_text or "").rstrip("\n")

    lines: list[str] = []
    outcome = data.record.outcome()
    stdout = outcome.stdout
    stderr = outcome.stderr
    result = outcome.result

    if stdout:
        lines.append(stdout.rstrip("\n"))
    if stderr:
        lines.append("[stderr]")
        lines.append(stderr.rstrip("\n"))
    if result is not None:
        lines.append(_render_exec_result_data(data))
    if not lines:
        if data.background:
            return f"Background execution started ({data.record.execution_id})."
        file_exec_hint = _render_file_exec_hint_data(data)
        if file_exec_hint is not None:
            return file_exec_hint
        lines.append("Execution completed.")
    return "\n".join(lines)


def _render_exec_result_data(data: ExecCommandData) -> str:
    outcome = data.record.outcome()
    preview = outcome.result_preview
    result = outcome.result
    if isinstance(preview, dict):
        preview_payload = cast(Mapping[str, object], preview)
        if _prefer_preview_for_human_result(preview_payload, result):
            return preview_text(cast(Any, preview))
    return "" if result is None else str(result)


def _render_file_exec_hint_data(data: ExecCommandData) -> str | None:
    if data.source_kind != "file":
        return None
    if data.namespace_delta is None:
        return "File executed."
    entries = data.namespace_delta.entries
    lines = ["File executed. Namespace changes:"]
    for entry in entries:
        suffix = f" ({entry.type_name})" if entry.type_name else ""
        summary = f"{entry.name}: {entry.repr_text}{suffix}" if entry.repr_text else entry.name
        lines.append(f"- {entry.change}: {summary}")
    if data.namespace_delta.truncated:
        lines.append("- ... additional namespace changes omitted")
    return "\n".join(lines)


def _prefer_preview_for_human_result(preview: Mapping[str, object], result: object) -> bool:
    kind = preview.get("kind")
    if kind == "dataframe-like":
        return True
    length = preview.get("length")
    if kind in {"sequence-like", "mapping-like"} and isinstance(length, int) and length > 3:
        return True
    if not isinstance(result, str):
        return True
    return "\n" in result or len(result) > 240


def _render_kernel_session(
    data: KernelSessionData,
    session_id: str | None,
    *,
    mode: str,
) -> str:
    session_label = f"session: {session_id}, " if session_id else ""
    session_name = f"session: {session_id}" if session_id else None
    payload = _kernel_session_mapping(data)
    wait_note = _wait_note(payload)
    if mode == "start":
        if data.alive:
            if data.started_new:
                if data.python:
                    return f"Kernel started (pid {data.pid}) using {data.python}."
                return f"Kernel started (pid {data.pid})."
            if data.python:
                return f"Kernel already running (pid {data.pid}) using {data.python}."
            return f"Kernel already running (pid {data.pid})."
        return "Kernel is not running."

    if data.alive:
        if mode == "wait":
            waited_for = data.waited_for
            if waited_for == "ready":
                return (
                    f"Kernel is ready ({session_label}pid {data.pid}{_detail_suffix(wait_note)})."
                )
            return f"Kernel is idle ({session_label}pid {data.pid}{_detail_suffix(wait_note)})."
        if data.busy:
            if isinstance(data.busy_for_ms, int):
                busy_detail = (
                    f"busy for {_format_duration_ms(data.busy_for_ms)}{_detail_suffix(wait_note)}"
                )
                return f"Kernel is running ({session_label}pid {data.pid}, {busy_detail})."
            busy_detail = f"busy{_detail_suffix(wait_note)}"
            return f"Kernel is running ({session_label}pid {data.pid}, {busy_detail})."
        state_label = "idle" if data.waited_for == "idle" else "running"
        return (
            f"Kernel is {state_label} ({session_label}pid {data.pid}{_detail_suffix(wait_note)})."
        )

    if data.runtime_state == "starting":
        return (
            f"Kernel is starting ({session_name})."
            if session_name is not None
            else "Kernel is starting."
        )
    if data.runtime_state == "dead":
        return (
            f"Kernel is dead ({session_name})." if session_name is not None else "Kernel is dead."
        )
    return "Kernel is not running."


def _render_vars_command_data(data: VarsCommandData, session_id: str | None) -> str:
    if not data.values:
        body = "No user variables found."
    else:
        body = "\n".join(_render_var_entry(item) for item in data.values)
    body = _prepend_session_identity(body, session_id)
    return _append_helper_access_note(body, data.access_metadata.merge_data())


def _render_inspect_command_data(data: InspectCommandData, session_id: str | None) -> str:
    value = data.value
    members_text = ", ".join(value.members[:30]) if value.members else "(none)"
    preview = value.preview
    if isinstance(preview, DataframePreviewData):
        lines = [
            f"name: {value.name}",
            f"type: {value.type_name}",
        ]
        lines.extend(_render_dataframe_preview(preview))
    elif isinstance(preview, MappingPreviewData | SequencePreviewData):
        lines = [
            f"name: {value.name}",
            f"type: {value.type_name}",
        ]
        lines.extend(_render_collection_preview(preview))
    else:
        repr_text = value.repr_text
        summarized_repr = (
            summarize_history_text(repr_text, limit=240)
            if isinstance(repr_text, str) and repr_text
            else None
        )
        lines = [
            f"name: {value.name}",
            f"type: {value.type_name}",
            f"repr: {summarized_repr}",
        ]
        lines.append(f"members: {members_text}")
    body = "\n".join(lines)
    body = _prepend_session_identity(body, session_id)
    return _append_helper_access_note(body, data.access_metadata.merge_data())


def _render_reload_command_data(data: ReloadCommandData) -> str:
    return _render_reload(data.result)


def _render_history_command_data(data: HistoryCommandData) -> str:
    entries = [
        full_history_entry(entry) if data.full else compact_history_entry(entry)
        for entry in data.entries
    ]
    if not entries:
        return "No history entries."
    return "\n".join(_render_history_entry(entry) for entry in entries)


def _render_interrupt_command_data(data: InterruptCommandData) -> str:
    del data
    return "Interrupt signal sent."


def _render_stop_command_data(data: StopCommandData) -> str:
    del data
    return "Kernel stopped."


def _render_doctor_command_data(data: DoctorCommandData) -> str:
    headline = "Doctor checks passed." if data.ready else "Doctor found issues."
    lines = [headline]
    for check in data.checks:
        lines.append(f"[{check.status.upper()}] {check.name}: {check.message}")
        if check.fix_hint:
            lines.append(f"  fix: {check.fix_hint}")
    if data.kernel_alive:
        lines.append(f"[OK] kernel: Kernel is running (pid {data.kernel_pid}).")
    elif data.session_exists:
        lines.append("[WARN] kernel: Session exists but kernel is not running.")
    return "\n".join(lines)


def _render_sessions_list_command_data(data: SessionsListCommandData) -> str:
    if not data.sessions:
        body = "No live sessions found." if data.hidden_non_live_count else "No sessions found."
        hidden_note = _render_hidden_session_note(data.hidden_non_live_count)
        if hidden_note is not None:
            body = f"{body}\n{hidden_note}"
        return body

    lines = []
    for session in data.sessions:
        markers: list[str] = []
        if session.is_default:
            markers.append("default")
        if session.is_preferred:
            markers.append("preferred")
        marker = f" ({', '.join(markers)})" if markers else ""
        python_text = f" using {session.python}" if session.python else ""
        activity = _staleness_hint(session.last_activity)
        activity_text = f", last activity {activity}" if activity else ""
        lines.append(f"{session.session_id}{marker}: pid {session.pid}{python_text}{activity_text}")
    hidden_note = _render_hidden_session_note(data.hidden_non_live_count)
    if hidden_note is not None:
        lines.append(hidden_note)
    return "\n".join(lines)


def _render_session_delete_command_data(data: SessionDeleteCommandData) -> str:
    stopped = " and stopped its kernel" if data.stopped_running_kernel else ""
    return f"Deleted session {data.session_id}{stopped}."


def _render_sessions_delete_bulk_command_data(data: SessionsDeleteBulkCommandData) -> str:
    if data.deleted:
        return f"Deleted {len(data.deleted)} session(s): {', '.join(str(s) for s in data.deleted)}"
    return "No sessions to delete."


def _render_runs_list_command_data(data: RunsListCommandData) -> str:
    if not data.runs:
        return "No runs found."
    lines = []
    for run in data.runs:
        display_status = "cancelled" if run.terminal_reason == "cancelled" else run.status
        lines.append(
            f"{run.ts} [{display_status}] {run.execution_id} {run.command_type} {run.duration_ms}ms"
        )
    return "\n".join(lines)


def _render_run_lookup_command_data(data: RunLookupCommandData, *, snapshot_only: bool) -> str:
    return _render_run_snapshot_data(data, snapshot_only=snapshot_only)


def _render_run_snapshot_data(data: RunLookupCommandData, *, snapshot_only: bool) -> str:
    run = data.run
    status = "cancelled" if run.terminal_reason == "cancelled" else run.status
    lines = [f"Run {run.execution_id} [{status}] {run.command_type} on session {run.session_id}."]
    lines.append(f"duration: {run.duration_ms}ms")
    if snapshot_only and status in {"starting", "running"}:
        lines.append("snapshot: persisted state only; use `agentnb runs follow` for live events")

    if data.include_output:
        if run.stdout:
            lines.extend(_render_output_block("stdout", run.stdout, limit=200))
        if run.stderr:
            lines.extend(_render_output_block("stderr", run.stderr, limit=200))
        if isinstance(run.result, str) and run.result:
            lines.append(
                f"result: {summarize_history_text(run.result, limit=240) or run.result[:240]}"
            )

    ename = run.ename
    evalue = run.evalue
    if ename or evalue:
        if ename and evalue:
            lines.append(f"error: {ename}: {evalue}")
        elif ename:
            lines.append(f"error: {ename}")
        else:
            lines.append(f"error: {evalue}")

    if data.include_output:
        lines.append(f"events: {len(run.events)} recorded")

    return "\n".join(lines)


def _render_follow_completion_note_data(run_lookup: RunLookupCommandData) -> str | None:
    if run_lookup.completion_reason != "window_elapsed":
        return None
    if run_lookup.run.status in {"starting", "running"}:
        return "Observation window elapsed; the run is still active."
    return "Observation window elapsed."


def _render_run_cancel_command_data(data: RunCancelCommandData) -> str:
    if data.cancel_requested:
        if data.status == "ok":
            return (
                f"Cancel requested for run {data.execution_id}, "
                "but it completed before cancellation took effect."
            )
        if data.session_outcome == "preserved":
            return f"Cancelled run {data.execution_id}. The session was preserved."
        if data.session_outcome == "stopped":
            return f"Cancelled run {data.execution_id}. The still-starting session was stopped."
        return f"Cancel requested for run {data.execution_id}."
    terminal_labels = {"ok": "finished", "error": "failed", "cancelled": "cancelled"}
    label = terminal_labels.get(data.status, data.status)
    return f"Run {data.execution_id} already {label}."


def _kernel_session_mapping(data: KernelSessionData) -> dict[str, object]:
    payload: dict[str, object] = {
        "alive": data.alive,
        "pid": data.pid,
        "busy": data.busy,
    }
    if data.waited is not None:
        payload["waited"] = data.waited
    if data.waited_for is not None:
        payload["waited_for"] = data.waited_for
    if data.waited_ms is not None:
        payload["waited_ms"] = data.waited_ms
    if data.initial_runtime_state is not None:
        payload["initial_runtime_state"] = data.initial_runtime_state
    return payload


def _prepend_session_identity(body: str, session_id: str | None) -> str:
    if not session_id:
        return body
    return f"session: {session_id}\n{body}"


def _render_var_entry(item: VariableEntry) -> str:
    name = item.name
    repr_text = item.repr_text
    type_name = item.type_name
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
    exec_notice = _exec_notice(response)
    if exec_notice:
        lines.append(exec_notice)

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


def _exec_notice(response: CommandResponse) -> str | None:
    if response.command != "exec":
        return None
    if not response.data.get("session_restarted"):
        return None
    previous_state = response.data.get("initial_runtime_state")
    if previous_state == "dead":
        return (
            "Notice: session was restarted after the previous kernel died; "
            "prior in-memory state was lost."
        )
    if previous_state == "stale":
        return (
            "Notice: session was restarted after stale kernel state was detected; "
            "prior in-memory state was lost."
        )
    return "Notice: session was restarted; prior in-memory state was lost."


def _append_suggestions(body: str, suggestions: list[str]) -> str:
    if not suggestions:
        return body
    lines = [body, "", "Next:"]
    lines.extend(f"- {suggestion}" for suggestion in suggestions)
    return "\n".join(lines)


def _render_output_block(label: str, text: str, *, limit: int) -> list[str]:
    rendered = _truncate_preserving_newlines(text, limit=limit)
    if "\n" not in rendered:
        return [f"{label}: {rendered}"]
    return [f"{label}:", rendered]


def _truncate_preserving_newlines(text: str, *, limit: int) -> str:
    trimmed = text.rstrip("\n")
    if len(trimmed) <= limit:
        return trimmed
    truncated = trimmed[:limit].rstrip("\n")
    omitted = len(trimmed) - len(truncated)
    if not truncated:
        return f"[{omitted} chars truncated]"
    return f"{truncated}\n...[{omitted} chars truncated]"


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


def _render_dataframe_preview(preview: DataframePreviewData) -> list[str]:
    lines: list[str] = []
    shape = preview.shape
    row_count = shape[0] if isinstance(shape, list) and len(shape) == 2 else None
    if isinstance(shape, list) and len(shape) == 2:
        lines.append(f"shape: ({shape[0]}, {shape[1]})")

    columns = preview.columns
    if columns:
        columns_text = ", ".join(str(column) for column in columns)
        omission = _omission_suffix(preview.column_count, preview.columns_shown)
        if omission:
            columns_text = f"{columns_text}{omission}"
        lines.append("columns: " + columns_text)

    dtypes = preview.dtypes
    if isinstance(dtypes, dict) and dtypes:
        dtype_text = ", ".join(f"{name}={dtype}" for name, dtype in dtypes.items())
        omission = _omission_suffix(preview.column_count, preview.dtypes_shown)
        if omission:
            dtype_text = f"{dtype_text}{omission}"
        lines.append(f"dtypes: {dtype_text}")

    null_counts = preview.null_counts
    if isinstance(null_counts, dict) and null_counts:
        null_text = ", ".join(f"{name}={count}" for name, count in null_counts.items())
        omission = _omission_suffix(
            preview.column_count,
            preview.null_count_fields_shown,
        )
        if omission:
            null_text = f"{null_text}{omission}"
        lines.append(f"nulls: {null_text}")

    head = preview.head
    if isinstance(head, list):
        head_text = json.dumps(head, ensure_ascii=True)
        omission = _omission_suffix(row_count, preview.head_rows_shown, unit="rows")
        if omission:
            head_text = f"{head_text}{omission}"
        lines.append("head: " + head_text)

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


def _render_reload(data: ReloadResult) -> str:
    reloaded_modules = data.reloaded_modules
    rebound_names = data.rebound_names
    stale_names = data.stale_names
    failed_modules = data.failed_modules
    notes = data.notes
    requested_module = data.requested_module

    if reloaded_modules:
        if isinstance(requested_module, str) and len(reloaded_modules) == 1:
            lines = [f"Reloaded module: {reloaded_modules[0]}"]
        else:
            modules = ", ".join(str(module) for module in reloaded_modules[:10])
            lines = [f"Reloaded {len(reloaded_modules)} project modules: {modules}"]
    else:
        lines = ["No imported project-local modules were reloaded."]

    if rebound_names:
        lines.append("Rebound names: " + ", ".join(str(name) for name in rebound_names[:10]))
    if stale_names:
        lines.append("Possible stale objects: " + ", ".join(str(name) for name in stale_names[:10]))
        lines.append("Recreate them or run `agentnb reset` if stale state is widespread.")
    if failed_modules:
        failed_names = ", ".join(item.module for item in failed_modules[:10])
        lines.append("Failed modules: " + failed_names)
    lines.extend(note for note in notes if note)
    return "\n".join(lines)


def _render_collection_preview(
    preview: MappingPreviewData | SequencePreviewData,
) -> list[str]:
    lines: list[str] = []

    lines.append(f"length: {preview.length}")

    item_type = preview.item_type if isinstance(preview, SequencePreviewData) else None
    if isinstance(item_type, str) and item_type:
        lines.append(f"item_type: {item_type}")

    keys = preview.keys if isinstance(preview, MappingPreviewData) else None
    if isinstance(keys, list) and keys:
        keys_text = ", ".join(str(key) for key in keys[:10])
        keys_shown = preview.keys_shown if isinstance(preview, MappingPreviewData) else None
        omission = _omission_suffix(preview.length, keys_shown)
        if omission:
            keys_text = f"{keys_text}{omission}"
        lines.append("keys: " + keys_text)

    sample_keys = preview.sample_keys if isinstance(preview, SequencePreviewData) else None
    if isinstance(sample_keys, list) and sample_keys:
        lines.append("sample_keys: " + ", ".join(str(key) for key in sample_keys[:10]))

    sample = preview.sample
    if sample is not None:
        sample_text = json.dumps(sample, ensure_ascii=True)
        omission = _omission_suffix(
            preview.length,
            preview.sample_items_shown,
            truncated=bool(preview.sample_truncated),
        )
        if omission:
            sample_text = sample_text + omission
        lines.append("sample: " + sample_text)

    return lines


def _omitted_count(total: object, shown: object) -> int:
    if not isinstance(total, int) or not isinstance(shown, int):
        return 0
    return max(total - shown, 0)


def _omission_suffix(
    total: object,
    shown: object,
    *,
    unit: str | None = None,
    truncated: bool = False,
) -> str:
    omitted = _omitted_count(total, shown)
    if omitted <= 0:
        return " (truncated)" if truncated else ""
    label = "more" if not unit else f"more {unit}"
    if truncated:
        return f" (+{omitted} {label}, truncated)"
    return f" (+{omitted} {label})"


def _render_hidden_session_note(hidden_non_live_count: object) -> str | None:
    if not isinstance(hidden_non_live_count, int) or hidden_non_live_count <= 0:
        return None
    noun = "record is" if hidden_non_live_count == 1 else "records are"
    pronoun = "it" if hidden_non_live_count == 1 else "them"
    return (
        f"{hidden_non_live_count} non-live session {noun} hidden; "
        f"use `agentnb sessions delete --stale` to remove {pronoun}."
    )
