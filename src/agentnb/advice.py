from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from .contracts import SuggestionAction
from .suggestions import SessionScopeSource, SuggestionScope


@dataclass(slots=True, frozen=True)
class AdviceContext:
    command_name: str
    response_status: str
    data: Mapping[str, object]
    error_code: str | None = None
    error_name: str | None = None
    error_value: str | None = None
    session_id: str | None = None
    project_override: Path | None = None
    session_source: SessionScopeSource | None = None
    output_mode: str = "json"


class AdvicePolicy:
    def suggestions(self, context: AdviceContext) -> list[str]:
        command_name = context.command_name
        data = context.data
        scope = _scope(context)

        if context.error_code == "AMBIGUOUS_SESSION":
            return [
                f"Run `{scope.render_command('sessions', 'list')}` to see the live session names.",
                (
                    f"Retry with `{scope.render_command(command_name, '--session', 'NAME')}` "
                    "to target one explicitly."
                ),
            ]
        if context.error_code == "AMBIGUOUS_EXECUTION":
            return [
                f"Run `{scope.render_command('runs', 'list')}` to inspect matching run ids.",
                (
                    f"Retry with `{scope.render_command('runs', 'show', 'EXECUTION_ID')}` "
                    "to target one explicitly."
                ),
            ]
        if context.error_code == "SESSION_BUSY":
            execution_id = data.get("active_execution_id")
            if isinstance(execution_id, str) and execution_id:
                return [
                    (
                        f"Run `{scope.render_command('runs', 'wait', execution_id)}` "
                        "to wait for the blocking run."
                    ),
                    (
                        f"Run `{scope.render_command('runs', 'show', execution_id)}` "
                        "to inspect the blocking run."
                    ),
                ]
            return [
                f"Run `{scope.render_command('wait', session_scoped=True)}` "
                "to block until the session is idle, then retry."
            ]
        if context.error_code == "KERNEL_NOT_READY":
            return [
                _run_text(
                    scope,
                    "to wait for startup to finish.",
                    "wait",
                    session_scoped=True,
                ),
                _run_text(
                    scope,
                    "to inspect the current session state.",
                    "status",
                    session_scoped=True,
                ),
            ]
        if context.error_code in {"NO_KERNEL", "BACKEND_ERROR", "KERNEL_DEAD"}:
            return [
                _run_text(scope, "to start the kernel.", "start", session_scoped=True),
                _run_text(
                    scope,
                    "if startup has been failing.",
                    "doctor",
                    session_scoped=True,
                ),
            ]
        if command_name == "start":
            return []
        if command_name == "status":
            runtime_state = data.get("runtime_state")
            if runtime_state == "starting":
                return [
                    f"Run `{scope.render_command('wait', session_scoped=True)}` "
                    "to wait for startup to finish."
                ]
            if data.get("alive"):
                if data.get("busy"):
                    return [
                        (
                            f"Run `{scope.render_command('wait', session_scoped=True)}` "
                            "to wait until the session is usable."
                        ),
                    ]
                return []
            return [
                (
                    f"Run `{scope.render_command('start', session_scoped=True)}` "
                    "to start a project-scoped kernel."
                ),
                (
                    f"Run `{scope.render_command('doctor', session_scoped=True)}` "
                    "if startup has been failing."
                ),
            ]
        if command_name == "wait":
            if context.response_status == "ok":
                return []
            return [
                (
                    f"Run `{scope.render_command('status', session_scoped=True)}` "
                    "to inspect the current session state."
                ),
                (
                    f"Run `{scope.render_command('start', session_scoped=True)}` "
                    "if the target session is not running yet."
                ),
            ]
        if command_name == "exec":
            if context.response_status == "ok":
                if _file_exec_truncated(data):
                    return _file_exec_truncation_suggestions(context)
                if data.get("background"):
                    execution_id = _execution_id(data)
                    return [
                        (
                            f"Run `{scope.render_command('runs', 'wait', execution_id)}` "
                            "to wait for the final result."
                        ),
                        (
                            f"Run `{scope.render_command('runs', 'show', execution_id)}` "
                            "to inspect the current run record."
                        ),
                        (
                            f"Run `{scope.render_command('runs', 'cancel', execution_id)}` "
                            "to stop the background run."
                        ),
                    ]
                if _exec_output_is_empty(data):
                    execution_id = _execution_id(data)
                    return [
                        _run_text(
                            scope,
                            "to inspect namespace changes.",
                            "vars",
                            "--recent",
                            "5",
                            session_scoped=True,
                        ),
                        _run_text(
                            scope,
                            "to review this execution.",
                            "history",
                            execution_id,
                            session_scoped=True,
                        ),
                    ]
                return []
            if context.error_code == "INVALID_INPUT":
                if data.get("input_shape") == "exec_file_path":
                    file_path = str(data.get("source_path") or "PATH")
                    return [
                        _run_text(
                            scope,
                            "to execute the file through `exec --file`.",
                            "exec",
                            "--file",
                            file_path,
                            session_scoped=True,
                        ),
                        _run_text(
                            scope,
                            "to use the top-level file-execution hot path.",
                            file_path,
                            session_scoped=True,
                        ),
                    ]
                return []
            if context.error_code == "TIMEOUT":
                suggestions = [
                    _run_text(
                        scope,
                        "to review the latest failure.",
                        "history",
                        "@last-error",
                        session_scoped=True,
                    ),
                ]
                if data.get("interrupt_recommended"):
                    suggestions.append(
                        _run_text(
                            scope,
                            "if execution may still be stuck.",
                            "interrupt",
                            session_scoped=True,
                        )
                    )
                suggestions.append(
                    _run_text(
                        scope,
                        "if the namespace needs a clean slate.",
                        "reset",
                        session_scoped=True,
                    )
                )
                return suggestions
            if context.error_name == "ModuleNotFoundError":
                module = _extract_module_name(context.error_value)
                if module:
                    session_python = _session_python(context.data)
                    if session_python:
                        return [
                            (
                                "Repair the live session: run "
                                f"`uv pip install --python {session_python} {module}` "
                                "in your shell."
                            ),
                            (
                                f"For a durable project dependency, run `uv add {module}` "
                                "in your shell."
                            ),
                            "Then retry the execution.",
                        ]
                    return [
                        f"Install the missing module: run `uv add {module}` in your shell "
                        "(not inside the session).",
                        "Then retry the execution.",
                    ]
            if _missing_pip_in_called_process(context):
                package = _extract_pip_install_target(context.error_value)
                session_python = _session_python(context.data)
                if package and session_python:
                    install_hint = (
                        f"run `uv pip install --python {session_python} {package}` in your shell."
                    )
                elif session_python:
                    install_hint = (
                        f"use `uv pip install --python {session_python} PACKAGE` in your shell."
                    )
                elif package:
                    install_hint = f"run `uv add {package}` in your shell (not inside the session)."
                else:
                    install_hint = "use `uv add PACKAGE` in your shell (not inside the session)."
                return [
                    "The selected interpreter does not provide pip inside the live session.",
                    f"Install the dependency from this project with {install_hint}",
                ]
            if context.error_name == "NameError" and context.session_id:
                return [
                    (
                        f"Run `{scope.render_command('vars', session_scoped=True)}` "
                        "to inspect the namespace."
                    ),
                    f"Run `{scope.render_command('sessions', 'list')}` to see all live sessions.",
                    (
                        _run_text(
                            scope,
                            "to review the latest failure.",
                            "history",
                            "@last-error",
                            session_scoped=True,
                        )
                    ),
                ]
            return [
                _run_text(
                    scope,
                    "to review the latest failure.",
                    "history",
                    "@last-error",
                    session_scoped=True,
                ),
                _run_text(
                    scope,
                    "if execution may still be stuck.",
                    "interrupt",
                    session_scoped=True,
                ),
                _run_text(
                    scope,
                    "if the namespace needs a clean slate.",
                    "reset",
                    session_scoped=True,
                ),
            ]
        if command_name == "vars":
            if not data.get("vars"):
                return [f"Run `{scope.render_command('...')}` to create some live state first."]
            return []
        if command_name == "inspect":
            return []
        if command_name == "reload":
            stale_names = data.get("stale_names")
            if stale_names:
                return [
                    _run_text(
                        scope,
                        "if stale objects are still causing issues.",
                        "reset",
                        session_scoped=True,
                    )
                ]
            reloaded = data.get("reloaded_modules")
            if isinstance(reloaded, list) and not reloaded:
                return [
                    "No project-local modules were found to reload.",
                    "To reload a specific module, run `importlib.reload(module)` in the session.",
                ]
            return []
        if command_name == "history":
            if not data.get("entries"):
                return [f"Run `{scope.render_command('...')}` to record the first execution step."]
            return []
        if command_name == "interrupt":
            return [
                _retry_text(
                    scope,
                    "once the kernel is idle.",
                    "exec",
                    "...",
                    session_scoped=True,
                ),
                _run_text(
                    scope,
                    "if interrupted code left partial state behind.",
                    "reset",
                    session_scoped=True,
                ),
            ]
        if command_name == "reset":
            return [
                _run_text(
                    scope,
                    "to rebuild required state.",
                    "setup_code",
                    session_scoped=True,
                )
            ]
        if command_name == "stop":
            return []
        if command_name == "doctor":
            if data.get("ready"):
                if data.get("kernel_alive"):
                    return ["Kernel is already running."]
                if data.get("session_exists"):
                    return [
                        "Session exists but kernel is not running.",
                        _run_text(scope, "to restart the kernel.", "start", session_scoped=True),
                    ]
                return [_run_text(scope, "to start the kernel.", "start", session_scoped=True)]
            return [
                _run_text(
                    scope,
                    "in your shell.",
                    "doctor",
                    prefix="Run the install command shown by",
                ),
                (
                    f"Then restart with "
                    f"`{scope.render_command('--fresh', '...', include_output=False)}` "
                    f"or run `{scope.render_command('start', session_scoped=True)}` again."
                ),
                (
                    "Run `agentnb start --python /path/to/python --json` "
                    "to try a specific interpreter."
                ),
            ]
        if command_name == "sessions-list":
            if not data.get("sessions"):
                return [
                    _run_text(
                        scope,
                        "to start the default session.",
                        "start",
                        session_scoped=True,
                    ),
                    _run_text(scope, "to start and execute in one step.", "..."),
                ]
            return []
        if command_name == "sessions-delete":
            return []
        if command_name == "runs-list":
            if not data.get("runs"):
                return [
                    _run_text(
                        scope,
                        "to create a persisted run record.",
                        "--background",
                        "...",
                    )
                ]
            return []
        if command_name == "runs-show":
            run = data.get("run")
            run_payload = cast(Mapping[str, object], run) if isinstance(run, dict) else None
            run_status = run_payload.get("status") if run_payload is not None else None
            if _run_is_active(run_status):
                execution_id = _execution_id(run_payload)
                return [
                    _run_text(scope, "to stream new events.", "runs", "follow", execution_id),
                    _run_text(
                        scope,
                        "to wait for the final snapshot.",
                        "runs",
                        "wait",
                        execution_id,
                    ),
                    _run_text(
                        scope,
                        "to stop the background run.",
                        "runs",
                        "cancel",
                        execution_id,
                    ),
                ]
            return []
        if command_name == "runs-follow":
            run = data.get("run")
            run_payload = cast(Mapping[str, object], run) if isinstance(run, dict) else None
            run_status = run_payload.get("status") if run_payload is not None else None
            if _run_is_active(run_status):
                execution_id = _execution_id(run_payload)
                return [
                    _run_text(
                        scope,
                        "to wait for the final snapshot.",
                        "runs",
                        "wait",
                        execution_id,
                    ),
                    _run_text(
                        scope,
                        "to inspect the latest run snapshot.",
                        "runs",
                        "show",
                        execution_id,
                    ),
                    _run_text(
                        scope,
                        "to stop the background run.",
                        "runs",
                        "cancel",
                        execution_id,
                    ),
                ]
            return []
        if command_name == "runs-wait":
            return []
        if command_name == "runs-cancel":
            execution_id = _execution_id(data)
            if data.get("cancel_requested"):
                if data.get("status") == "ok":
                    return [
                        _run_text(
                            scope,
                            "to inspect the completed run.",
                            "runs",
                            "show",
                            execution_id,
                        ),
                        (
                            f"Run `{scope.render_command('wait', '--session', 'NAME')}` "
                            "to confirm the session is ready."
                        ),
                    ]
                if data.get("session_outcome") == "preserved":
                    session_id = data.get("session_id") or "default"
                    return [
                        (
                            f"Run `{scope.render_command('wait', session_id=str(session_id))}` "
                            "to confirm the session is ready for more work."
                        ),
                        (
                            f"Run `{scope.render_command('runs', 'show', execution_id)}` "
                            "to inspect the cancelled run record."
                        ),
                    ]
                if data.get("session_outcome") == "stopped":
                    return [
                        (
                            f"Run `{scope.render_command('start', '--session', 'NAME')}` "
                            "to start a fresh session explicitly."
                        ),
                        f"Run `{scope.render_command('...')}` to restart and execute in one step.",
                    ]
            return [
                _run_text(
                    scope,
                    "to inspect the persisted run snapshot.",
                    "runs",
                    "show",
                    execution_id,
                )
            ]
        return []

    def suggestion_actions(self, context: AdviceContext) -> list[SuggestionAction]:
        command_name = context.command_name
        data = context.data
        scope = _scope(context)

        if context.error_code == "AMBIGUOUS_SESSION":
            return [
                scope.command_action("List sessions", "sessions", "list"),
                scope.command_action("Retry with --session", command_name, "--session", "NAME"),
            ]
        if context.error_code == "AMBIGUOUS_EXECUTION":
            return [
                scope.command_action("List runs", "runs", "list"),
                scope.command_action("Show run", "runs", "show", "EXECUTION_ID"),
            ]
        if context.error_code == "SESSION_BUSY":
            execution_id = data.get("active_execution_id")
            if isinstance(execution_id, str) and execution_id:
                return [
                    scope.command_action("Wait for run", "runs", "wait", execution_id),
                    scope.command_action("Show run", "runs", "show", execution_id),
                ]
            return [scope.command_action("Wait for session", "wait", session_scoped=True)]
        if context.error_code == "KERNEL_NOT_READY":
            return [
                scope.command_action("Wait for kernel", "wait", session_scoped=True),
                scope.command_action("Check status", "status", session_scoped=True),
            ]
        if context.error_code in {"NO_KERNEL", "BACKEND_ERROR", "KERNEL_DEAD"}:
            return [
                scope.command_action("Start kernel", "start", session_scoped=True),
                scope.command_action("Run doctor", "doctor", session_scoped=True),
            ]
        if command_name == "exec":
            if context.response_status == "ok" and data.get("background"):
                execution_id = _execution_id(data)
                return [
                    scope.command_action("Wait for run", "runs", "wait", execution_id),
                    scope.command_action("Show run", "runs", "show", execution_id),
                    scope.command_action("Cancel run", "runs", "cancel", execution_id),
                ]
            if context.response_status == "ok" and _file_exec_truncated(data):
                return _file_exec_truncation_actions(context)
            if (
                context.error_code == "INVALID_INPUT"
                and data.get("input_shape") == "exec_file_path"
            ):
                file_path = str(data.get("source_path") or "PATH")
                return [
                    scope.command_action(
                        "Use exec --file",
                        "exec",
                        "--file",
                        file_path,
                        session_scoped=True,
                    ),
                    scope.command_action(
                        "Use top-level file exec",
                        file_path,
                        session_scoped=True,
                    ),
                ]
            if context.error_name == "ModuleNotFoundError":
                module = _extract_module_name(context.error_value)
                if module:
                    session_python = _session_python(data)
                    if session_python:
                        return [
                            _shell_action(
                                "Repair live session",
                                "uv",
                                "pip",
                                "install",
                                "--python",
                                session_python,
                                module,
                            ),
                            _shell_action("Add dependency", "uv", "add", module),
                        ]
                    return [_shell_action("Install dependency", "uv", "add", module)]
            if _missing_pip_in_called_process(context):
                package = _extract_pip_install_target(context.error_value)
                session_python = _session_python(data)
                if package and session_python:
                    return [
                        _shell_action(
                            "Repair live session",
                            "uv",
                            "pip",
                            "install",
                            "--python",
                            session_python,
                            package,
                        )
                    ]
                if package:
                    return [_shell_action("Install dependency", "uv", "add", package)]
        if command_name == "runs-follow":
            run = data.get("run")
            run_payload = cast(Mapping[str, object], run) if isinstance(run, dict) else None
            run_status = run_payload.get("status") if run_payload is not None else None
            if _run_is_active(run_status):
                execution_id = _execution_id(run_payload)
                return [
                    scope.command_action("Wait for run", "runs", "wait", execution_id),
                    scope.command_action("Show run", "runs", "show", execution_id),
                    scope.command_action("Cancel run", "runs", "cancel", execution_id),
                ]
        return []


def _scope(context: AdviceContext) -> SuggestionScope:
    return SuggestionScope(
        project_override=context.project_override,
        session_id=context.session_id,
        session_source=context.session_source,
    )


def _run_is_active(status: object) -> bool:
    return isinstance(status, str) and status in {"starting", "running"}


def _execution_id(data: Mapping[str, object] | None) -> str:
    if data is None:
        return "EXECUTION_ID"
    execution_id = data.get("execution_id")
    if isinstance(execution_id, str) and execution_id:
        return execution_id
    return "EXECUTION_ID"


def _extract_module_name(error_value: str | None) -> str | None:
    if not error_value:
        return None
    prefix = "No module named '"
    if error_value.startswith(prefix) and error_value.endswith("'"):
        name = error_value[len(prefix) : -1]
        return name.split(".")[0]
    return None


def _extract_pip_install_target(error_value: str | None) -> str | None:
    if not error_value:
        return None
    match = re.search(r"pip', 'install', '([^']+)'", error_value)
    if match is None:
        return None
    package = match.group(1).strip()
    return package or None


def _missing_pip_in_called_process(context: AdviceContext) -> bool:
    if context.command_name != "exec" or context.error_name != "CalledProcessError":
        return False
    stderr = context.data.get("stderr")
    if isinstance(stderr, str) and "No module named pip" in stderr:
        return True
    return bool(context.error_value and "No module named pip" in context.error_value)


def _session_python(data: Mapping[str, object]) -> str | None:
    value = data.get("session_python")
    if isinstance(value, str) and value:
        return value
    return None


def _exec_output_is_empty(data: Mapping[str, object]) -> bool:
    for key in ("result", "stdout", "stderr", "selected_text"):
        value = data.get(key)
        if isinstance(value, str) and value:
            return False
    namespace_delta = data.get("namespace_delta")
    entries = (
        cast(Mapping[str, object], namespace_delta).get("entries")
        if isinstance(namespace_delta, dict)
        else None
    )
    return not entries


def _file_exec_truncated(data: Mapping[str, object]) -> bool:
    if data.get("source_kind") != "file":
        return False
    truncation_keys = ("stdout_truncated", "stderr_truncated", "result_truncated")
    return any(data.get(key) is True for key in truncation_keys)


def _file_exec_truncation_suggestions(context: AdviceContext) -> list[str]:
    scope = _scope(context)
    source_path = str(context.data.get("source_path") or "PATH")
    return [
        _run_text(
            scope,
            "to rerun the file without truncation.",
            "exec",
            "--no-truncate",
            "--file",
            source_path,
            session_scoped=True,
            include_output=False,
        ),
        _run_text(
            scope,
            "to inspect the newest live variables.",
            "vars",
            "--recent",
            "5",
            session_scoped=True,
        ),
    ]


def _file_exec_truncation_actions(context: AdviceContext) -> list[SuggestionAction]:
    scope = _scope(context)
    source_path = str(context.data.get("source_path") or "PATH")
    return [
        scope.command_action(
            "Rerun without truncation",
            "exec",
            "--no-truncate",
            "--file",
            source_path,
            session_scoped=True,
            include_output=False,
        ),
        scope.command_action(
            "Inspect recent vars",
            "vars",
            "--recent",
            "5",
            session_scoped=True,
        ),
    ]


def _shell_action(label: str, command: str, *args: str) -> SuggestionAction:
    return {
        "kind": "shell",
        "label": label,
        "command": command,
        "args": list(args),
    }


def _run_text(
    scope: SuggestionScope,
    detail: str,
    *tokens: str,
    session_scoped: bool = False,
    include_output: bool = True,
    session_id: str | None = None,
    prefix: str = "Run",
) -> str:
    command = scope.render_command(
        *tokens,
        session_scoped=session_scoped,
        include_output=include_output,
        session_id=session_id,
    )
    return f"{prefix} `{command}` {detail}"


def _retry_text(
    scope: SuggestionScope,
    detail: str,
    *tokens: str,
    session_scoped: bool = False,
) -> str:
    command = scope.render_command(*tokens, session_scoped=session_scoped)
    return f"Retry with `{command}` {detail}"
