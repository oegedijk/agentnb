from __future__ import annotations

import json
from typing import Any

from .contracts import CommandResponse


def render_response(response: CommandResponse, *, as_json: bool) -> str:
    if as_json:
        return json.dumps(response.to_dict(), ensure_ascii=True)
    return render_human(response)


def render_human(response: CommandResponse) -> str:
    if response.status == "error":
        return _render_error(response)

    command = response.command
    data = response.data

    if command == "start":
        if data.get("alive"):
            python = data.get("python")
            if data.get("started_new"):
                if python:
                    return f"Kernel started (pid {data.get('pid')}) using {python}."
                return f"Kernel started (pid {data.get('pid')})."
            if python:
                return f"Kernel already running (pid {data.get('pid')}) using {python}."
            return f"Kernel already running (pid {data.get('pid')})."
        return "Kernel is not running."

    if command == "status":
        if data.get("alive"):
            return f"Kernel is running (pid {data.get('pid')})."
        return "Kernel is not running."

    if command == "stop":
        return "Kernel stopped."

    if command == "interrupt":
        return "Interrupt signal sent."

    if command in {"exec", "reset"}:
        return _render_exec_like(data)

    if command == "vars":
        vars_data = data.get("vars", [])
        if not vars_data:
            return "No user variables found."
        lines = [
            f"{item.get('name')}: {item.get('repr')} ({item.get('type')})" for item in vars_data
        ]
        return "\n".join(lines)

    if command == "inspect":
        inspect_data = data.get("inspect", {})
        members = inspect_data.get("members", [])
        members_text = ", ".join(members[:30]) if members else "(none)"
        return (
            f"name: {inspect_data.get('name')}\n"
            f"type: {inspect_data.get('type')}\n"
            f"repr: {inspect_data.get('repr')}\n"
            f"members: {members_text}"
        )

    if command == "reload":
        return f"Reloaded module: {data.get('module')}"

    if command == "history":
        entries = data.get("entries", [])
        if not entries:
            return "No history entries."
        lines = [
            (
                f"{entry.get('ts')} [{entry.get('status')}] "
                f"{entry.get('duration_ms')}ms {entry.get('code')}"
            )
            for entry in entries
        ]
        return "\n".join(lines)

    if command == "doctor":
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
        return "\n".join(lines)

    return json.dumps(data, ensure_ascii=True, indent=2)


def _render_exec_like(data: dict[str, Any]) -> str:
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
