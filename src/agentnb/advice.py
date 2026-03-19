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
    error_name: str | None = None
    error_value: str | None = None
    session_id: str | None = None


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
        if context.error_code == "AMBIGUOUS_EXECUTION":
            return [
                "Run `agentnb runs list --json` to inspect matching run ids.",
                "Retry with `agentnb runs show EXECUTION_ID --json` to target one explicitly.",
            ]
        if context.error_code == "SESSION_BUSY":
            return ["Run `agentnb wait --json` to block until the session is idle, then retry."]
        if context.error_code in {"NO_KERNEL", "BACKEND_ERROR", "KERNEL_DEAD"}:
            return [
                "Run `agentnb start --json` to start the kernel.",
                "Run `agentnb doctor --json` if startup has been failing.",
            ]
        if command_name == "start":
            return []
        if command_name == "status":
            runtime_state = data.get("runtime_state")
            if runtime_state == "starting":
                return ["Run `agentnb wait --json` to wait for startup to finish."]
            if data.get("alive"):
                if data.get("busy"):
                    return [
                        "Run `agentnb wait --json` to wait until the session is ready.",
                    ]
                return []
            return [
                "Run `agentnb start --json` to start a project-scoped kernel.",
                "Run `agentnb doctor --json` if startup has been failing.",
            ]
        if command_name == "wait":
            if context.response_status == "ok":
                return []
            return [
                "Run `agentnb status --json` to inspect the current session state.",
                "Run `agentnb start --json` if the target session is not running yet.",
            ]
        if command_name == "exec":
            if context.response_status == "ok":
                if data.get("background"):
                    execution_id = _execution_id(data)
                    return [
                        f"Run `{_run_command('wait', execution_id)}` to wait for the final result.",
                        (
                            f"Run `{_run_command('show', execution_id)}` "
                            "to inspect the current run record."
                        ),
                        f"Run `{_run_command('cancel', execution_id)}` to stop the background run.",
                    ]
                if _exec_output_is_empty(data):
                    execution_id = _execution_id(data)
                    return [
                        "Run `agentnb vars --recent 5 --json` to inspect namespace changes.",
                        f"Run `agentnb history {execution_id} --json` to review this execution.",
                    ]
                return []
            if context.error_code == "INVALID_INPUT":
                return []
            if context.error_name == "ModuleNotFoundError":
                module = _extract_module_name(context.error_value)
                if module:
                    return [
                        (
                            f"Install the missing module: run `uv add {module}` "
                            "in your shell (not inside the session)."
                        ),
                        "Then retry the execution.",
                    ]
            if context.error_name == "NameError" and context.session_id:
                session = context.session_id
                return [
                    f"Run `agentnb vars --session {session} --json` to inspect the namespace.",
                    "Run `agentnb sessions list --json` to see all live sessions.",
                    "Run `agentnb history @last-error --json` to review the latest failure.",
                ]
            return [
                "Run `agentnb history @last-error --json` to review the latest failure.",
                "Run `agentnb interrupt --json` if execution may still be stuck.",
                "Run `agentnb reset --json` if the namespace needs a clean slate.",
            ]
        if command_name == "vars":
            if not data.get("vars"):
                return ['Run `agentnb "..." --json` to create some live state first.']
            return []
        if command_name == "inspect":
            return []
        if command_name == "reload":
            stale_names = data.get("stale_names")
            if stale_names:
                return ["Run `agentnb reset --json` if stale objects are still causing issues."]
            reloaded = data.get("reloaded_modules")
            if isinstance(reloaded, list) and not reloaded:
                return [
                    "No project-local modules were found to reload.",
                    "To reload a specific module, run `importlib.reload(module)` in the session.",
                ]
            return []
        if command_name == "history":
            if not data.get("entries"):
                return ['Run `agentnb "..." --json` to record the first execution step.']
            return []
        if command_name == "interrupt":
            return [
                'Retry with `agentnb exec "..." --json` once the kernel is idle.',
                "Run `agentnb reset --json` if interrupted code left partial state behind.",
            ]
        if command_name == "reset":
            return ['Run `agentnb "setup_code" --json` to rebuild required state.']
        if command_name == "stop":
            return []
        if command_name == "doctor":
            if data.get("ready"):
                if data.get("kernel_alive"):
                    return ["Kernel is already running."]
                if data.get("session_exists"):
                    return [
                        "Session exists but kernel is not running.",
                        "Run `agentnb start --json` to restart the kernel.",
                    ]
                return ["Run `agentnb start --json` to start the kernel."]
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
                    'Run `agentnb "..." --json` to start and execute in one step.',
                ]
            return []
        if command_name == "sessions-delete":
            return []
        if command_name == "runs-list":
            if not data.get("runs"):
                return ['Run `agentnb --background "..." --json` to create a persisted run record.']
            return []
        if command_name == "runs-show":
            run = data.get("run")
            run_payload = cast(Mapping[str, object], run) if isinstance(run, dict) else None
            run_status = run_payload.get("status") if run_payload is not None else None
            if _run_is_active(run_status):
                execution_id = _execution_id(run_payload)
                return [
                    f"Run `{_run_command('follow', execution_id)}` to stream new events.",
                    f"Run `{_run_command('wait', execution_id)}` to wait for the final snapshot.",
                    f"Run `{_run_command('cancel', execution_id)}` to stop the background run.",
                ]
            return []
        if command_name == "runs-follow":
            return []
        if command_name == "runs-wait":
            return []
        if command_name == "runs-cancel":
            execution_id = _execution_id(data)
            if data.get("cancel_requested"):
                if data.get("status") == "ok":
                    return [
                        f"Run `{_run_command('show', execution_id)}` to inspect the completed run.",
                        (
                            "Run `agentnb wait --session NAME --json` "
                            "to confirm the session is ready."
                        ),
                    ]
                if data.get("session_outcome") == "preserved":
                    session_id = data.get("session_id") or "default"
                    return [
                        (
                            f"Run `agentnb wait --session {session_id} --json` "
                            "to confirm the session is ready for more work."
                        ),
                        (
                            f"Run `{_run_command('show', execution_id)}` "
                            "to inspect the cancelled run record."
                        ),
                    ]
                if data.get("session_outcome") == "stopped":
                    return [
                        (
                            "Run `agentnb start --session NAME --json` "
                            "to start a fresh session explicitly."
                        ),
                        'Run `agentnb "..." --json` to restart and execute in one step.',
                    ]
            return [
                (
                    f"Run `{_run_command('show', execution_id)}` "
                    "to inspect the persisted run snapshot."
                )
            ]
        return []


def _run_is_active(status: object) -> bool:
    return isinstance(status, str) and status in {"starting", "running"}


def _execution_id(data: Mapping[str, object] | None) -> str:
    if data is None:
        return "EXECUTION_ID"
    execution_id = data.get("execution_id")
    if isinstance(execution_id, str) and execution_id:
        return execution_id
    return "EXECUTION_ID"


def _run_command(action: str, execution_id: str) -> str:
    return f"agentnb runs {action} {execution_id} --json"


def _extract_module_name(error_value: str | None) -> str | None:
    if not error_value:
        return None
    prefix = "No module named '"
    if error_value.startswith(prefix) and error_value.endswith("'"):
        name = error_value[len(prefix) : -1]
        return name.split(".")[0]
    return None


def _exec_output_is_empty(data: Mapping[str, object]) -> bool:
    for key in ("result", "stdout", "stderr", "selected_text"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return False
    return True
