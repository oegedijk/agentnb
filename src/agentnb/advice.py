from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast


@dataclass(slots=True, frozen=True)
class AdviceContext:
    command_name: str
    response_status: str
    data: Mapping[str, object]
    error_code: str | None = None


class AdvicePolicy:
    def suggestions(self, context: AdviceContext) -> list[str]:
        command_name = context.command_name
        data = context.data

        if context.error_code == "AMBIGUOUS_SESSION":
            return [
                "Run `agentnb sessions list --json` to see the live session names.",
                (
                    f"Retry with `agentnb {command_name} --session NAME --json` "
                    "to target one explicitly."
                ),
            ]
        if command_name == "start":
            return [
                'Run `agentnb exec "..." --json` to execute code in the live kernel.',
                "Run `agentnb vars --recent 5 --json` to inspect the newest namespace changes.",
                "Run `agentnb status --json` to confirm the kernel is still alive.",
            ]
        if command_name == "status":
            if data.get("alive"):
                return [
                    'Run `agentnb exec "..." --json` to execute code.',
                    "Run `agentnb vars --recent 5 --json` to inspect current variables.",
                    "Run `agentnb stop --json` when the session is no longer needed.",
                ]
            return [
                "Run `agentnb start --json` to start a project-scoped kernel.",
                "Run `agentnb doctor --json` if startup has been failing.",
            ]
        if command_name == "exec":
            if context.response_status == "ok":
                if data.get("background"):
                    return [
                        "Run `agentnb runs wait EXECUTION_ID --json` to wait for the final result.",
                        (
                            "Run `agentnb runs show EXECUTION_ID --json` "
                            "to inspect the current run record."
                        ),
                        (
                            "Run `agentnb runs cancel EXECUTION_ID --json` "
                            "to stop a long-running background run."
                        ),
                    ]
                return [
                    "Run `agentnb vars --recent 5 --json` to inspect the updated namespace.",
                    "Run `agentnb inspect NAME --json` to inspect a specific variable.",
                    "Run `agentnb history --json` to review prior executions.",
                ]
            return [
                "Run `agentnb history --errors --json` to review recent failures.",
                "Run `agentnb interrupt --json` if execution may still be stuck.",
                "Run `agentnb reset --json` if the namespace needs a clean slate.",
            ]
        if command_name == "vars":
            return [
                "Run `agentnb inspect NAME --json` for details on a variable.",
                "Run `agentnb vars --match TEXT --json` to filter noisy namespaces by name.",
                'Run `agentnb exec "..." --json` to add or modify live state.',
            ]
        if command_name == "inspect":
            return [
                "Run `agentnb vars --recent 5 --json` to inspect more of the namespace.",
                'Run `agentnb exec "..." --json` to probe or transform that value.',
            ]
        if command_name == "reload":
            return [
                'Run `agentnb exec "..." --json` to verify the reloaded module behavior.',
                "Run `agentnb reset --json` if stale state is still causing issues.",
            ]
        if command_name == "history":
            return [
                'Run `agentnb exec "..." --json` to continue iterating.',
                "Run `agentnb history --errors --json` to focus on failures only.",
            ]
        if command_name == "interrupt":
            return [
                'Retry with `agentnb exec "..." --json` once the kernel is idle.',
                "Run `agentnb reset --json` if interrupted code left partial state behind.",
            ]
        if command_name == "reset":
            return [
                'Run `agentnb exec "setup_code" --json` to rebuild required state.',
                "Run `agentnb vars --json` to confirm the namespace is clean.",
            ]
        if command_name == "stop":
            return [
                "Run `agentnb start --json` to create a fresh kernel later.",
            ]
        if command_name == "doctor":
            if data.get("ready"):
                return [
                    "Run `agentnb start --json` to start the kernel.",
                ]
            return [
                "Run `agentnb doctor --fix --json` to attempt automatic fixes.",
                (
                    "Run `agentnb start --python /path/to/python --json` "
                    "to try a specific interpreter."
                ),
            ]
        if command_name == "sessions-list":
            if not data.get("sessions"):
                return [
                    "Run `agentnb start --json` to start the default session.",
                    (
                        'Run `agentnb exec --ensure-started --json "..."` '
                        "to start and execute in one step."
                    ),
                ]
            return [
                "Run `agentnb start --session NAME --json` to start another named session.",
                "Run `agentnb status --session NAME --json` to inspect one session.",
            ]
        if command_name == "sessions-delete":
            return [
                "Run `agentnb sessions list --json` to confirm the remaining sessions.",
            ]
        if command_name == "runs-list":
            return [
                "Run `agentnb runs show EXECUTION_ID --json` to inspect one run in detail.",
                "Run `agentnb history --json` to review the semantic session history view.",
            ]
        if command_name == "runs-show":
            run = data.get("run")
            run_payload = cast(Mapping[str, object], run) if isinstance(run, dict) else None
            run_status = run_payload.get("status") if run_payload is not None else None
            if _run_is_active(run_status):
                return [
                    (
                        "Run `agentnb runs follow EXECUTION_ID --json` "
                        "to stream new events until the run finishes."
                    ),
                    "Run `agentnb runs wait EXECUTION_ID --json` to block for the final snapshot.",
                    "Run `agentnb runs cancel EXECUTION_ID --json` to stop the background run.",
                ]
            return [
                "Run `agentnb runs list --json` to inspect more recorded runs.",
                "Run `agentnb history --json` to review the session-level history view.",
            ]
        if command_name == "runs-follow":
            return [
                (
                    "Run `agentnb runs show EXECUTION_ID --json` "
                    "to inspect the latest persisted snapshot."
                ),
            ]
        if command_name == "runs-wait":
            return [
                "Run `agentnb runs show EXECUTION_ID --json` to inspect the completed run.",
            ]
        if command_name == "runs-cancel":
            if data.get("cancel_requested"):
                if data.get("status") == "ok":
                    return [
                        "Run `agentnb runs show EXECUTION_ID --json` to inspect the completed run.",
                        (
                            "Run `agentnb status --session NAME --wait-idle --json` "
                            "to confirm the session is ready."
                        ),
                    ]
                if data.get("session_outcome") == "preserved":
                    session_id = data.get("session_id") or "default"
                    return [
                        (
                            f"Run `agentnb status --session {session_id} --wait-idle --json` "
                            "to confirm the session is ready for more work."
                        ),
                        (
                            "Run `agentnb runs show EXECUTION_ID --json` "
                            "to inspect the cancelled run record."
                        ),
                    ]
                if data.get("session_outcome") == "stopped":
                    return [
                        (
                            "Run `agentnb start --session NAME --json` "
                            "to start a fresh session explicitly."
                        ),
                        (
                            'Run `agentnb exec --ensure-started "..." --json` '
                            "to restart and execute in one step."
                        ),
                    ]
            return [
                (
                    "Run `agentnb runs show EXECUTION_ID --json` "
                    "to inspect the persisted run snapshot."
                ),
            ]
        return []


def _run_is_active(status: object) -> bool:
    return isinstance(status, str) and status in {"starting", "running"}
