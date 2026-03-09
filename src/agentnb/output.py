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
        body = _render_error(response)
    else:
        command = response.command
        data = response.data

        if command == "start":
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
            body = (
                f"name: {inspect_data.get('name')}\n"
                f"type: {inspect_data.get('type')}\n"
                f"repr: {inspect_data.get('repr')}\n"
                f"members: {members_text}"
            )

        elif command == "reload":
            body = f"Reloaded module: {data.get('module')}"

        elif command == "history":
            entries = data.get("entries", [])
            if not entries:
                body = "No history entries."
            else:
                lines = [
                    (
                        f"{entry.get('ts')} [{entry.get('status')}] "
                        f"{entry.get('duration_ms')}ms {entry.get('code')}"
                    )
                    for entry in entries
                ]
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
        else:
            body = json.dumps(data, ensure_ascii=True, indent=2)

    return _append_suggestions(body, response.suggestions)


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


def _append_suggestions(body: str, suggestions: list[str]) -> str:
    if not suggestions:
        return body
    lines = [body, "", "Next:"]
    lines.extend(f"- {suggestion}" for suggestion in suggestions)
    return "\n".join(lines)
