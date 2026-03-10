from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from .contracts import CommandResponse


@dataclass(slots=True)
class RenderOptions:
    as_json: bool = False
    show_suggestions: bool = True
    quiet: bool = False


def render_response(response: CommandResponse, *, options: RenderOptions) -> str:
    if options.as_json:
        return json.dumps(response.to_dict(), ensure_ascii=True)
    return render_human(response, options=options)


def render_human(response: CommandResponse, *, options: RenderOptions) -> str:
    if response.status == "error":
        body = _render_error(response)
    else:
        command = response.command
        data = response.data

        quiet_commands = {"start", "status", "stop", "interrupt", "reload", "doctor"}
        if options.quiet and command in quiet_commands:
            body = ""
        elif command == "start":
            if data.get("alive"):
                python = data.get("python")
                if data.get("started_new"):
                    if python:
                        body = f"Kernel started (pid {data.get('pid')}) using {python}."
                    else:
                        body = f"Kernel started (pid {data.get('pid')})."
                elif python:
                    body = f"Kernel already running (pid {data.get('pid')}) using {python}."
                else:
                    body = f"Kernel already running (pid {data.get('pid')})."
            else:
                body = "Kernel is not running."

        elif command == "status":
            if data.get("alive"):
                body = f"Kernel is running (pid {data.get('pid')})."
            else:
                body = "Kernel is not running."

        elif command == "stop":
            body = "Kernel stopped."

        elif command == "interrupt":
            body = "Interrupt signal sent."

        elif command in {"exec", "reset"}:
            body = _render_exec_like(data)

        elif command == "vars":
            vars_data = data.get("vars", [])
            if not vars_data:
                body = "No user variables found."
            else:
                lines = [
                    f"{item.get('name')}: {item.get('repr')} ({item.get('type')})"
                    for item in vars_data
                ]
                body = "\n".join(lines)

        elif command == "inspect":
            inspect_data = data.get("inspect", {})
            members = inspect_data.get("members", [])
            members_text = ", ".join(members[:30]) if members else "(none)"
            preview = inspect_data.get("preview")
            if isinstance(preview, dict) and preview.get("kind") == "dataframe-like":
                lines = [
                    f"name: {inspect_data.get('name')}",
                    f"type: {inspect_data.get('type')}",
                ]
                lines.extend(_render_dataframe_preview(preview))
            elif isinstance(preview, dict) and preview.get("kind") in {
                "sequence-like",
                "mapping-like",
            }:
                lines = [
                    f"name: {inspect_data.get('name')}",
                    f"type: {inspect_data.get('type')}",
                ]
                lines.extend(_render_collection_preview(preview))
            else:
                lines = [
                    f"name: {inspect_data.get('name')}",
                    f"type: {inspect_data.get('type')}",
                    f"repr: {inspect_data.get('repr')}",
                ]
                lines.append(f"members: {members_text}")
            body = "\n".join(lines)

        elif command == "reload":
            body = _render_reload(data)

        elif command == "history":
            entries = data.get("entries", [])
            if not entries:
                body = "No history entries."
            else:
                lines = [_render_history_entry(entry) for entry in entries]
                body = "\n".join(lines)

        elif command == "doctor":
            checks = data.get("checks", [])
            headline = "Doctor checks passed." if data.get("ready") else "Doctor found issues."
            lines = [headline]
            for check in checks:
                status = str(check.get("status", "unknown")).upper()
                message = check.get("message")
                lines.append(f"[{status}] {check.get('name')}: {message}")
                hint = check.get("fix_hint")
                if hint:
                    lines.append(f"  fix: {hint}")
            body = "\n".join(lines)
        elif command == "sessions-list":
            sessions = data.get("sessions", [])
            if not sessions:
                body = "No sessions found."
            else:
                lines = []
                for session in sessions:
                    marker = " (default)" if session.get("is_default") else ""
                    python = session.get("python")
                    python_text = f" using {python}" if python else ""
                    session_label = session.get("session_id")
                    lines.append(f"{session_label}{marker}: pid {session.get('pid')}{python_text}")
                body = "\n".join(lines)
        elif command == "sessions-delete":
            stopped = " and stopped its kernel" if data.get("stopped_running_kernel") else ""
            body = f"Deleted session {data.get('session_id')}{stopped}."
        else:
            body = json.dumps(data, ensure_ascii=True, indent=2)

    suggestions = response.suggestions if options.show_suggestions else []
    return _append_suggestions(body, suggestions)


def _render_exec_like(data: dict[str, Any]) -> str:
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
        lines.append("Execution completed.")
    return "\n".join(lines)


def _render_error(response: CommandResponse) -> str:
    if response.error is None:
        return "Error: unknown error"

    lines = [f"Error: {response.error.message}"]
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


def _render_dataframe_preview(preview: dict[str, Any]) -> list[str]:
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


def _render_history_entry(entry: dict[str, Any]) -> str:
    label = _history_label(entry)
    prefix = "[internal] " if entry.get("kind") == "kernel_execution" else ""
    return f"{entry.get('ts')} [{entry.get('status')}] {entry.get('duration_ms')}ms {prefix}{label}"


def _history_label(entry: dict[str, Any]) -> str:
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


def _render_reload(data: dict[str, Any]) -> str:
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


def _render_collection_preview(preview: dict[str, Any]) -> list[str]:
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
