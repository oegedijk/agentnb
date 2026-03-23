from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import cast

from .contracts import SuggestionAction

_IMPORT_PACKAGE_NAME_OVERRIDES = {
    "sklearn": "scikit-learn",
}


@dataclass(slots=True, frozen=True)
class AdviceContext:
    command_name: str
    response_status: str
    data: Mapping[str, object]
    error_code: str | None = None
    error_name: str | None = None
    error_value: str | None = None
    session_id: str | None = None


@dataclass(slots=True, frozen=True)
class _AdviceStep:
    text: str
    action: SuggestionAction | None = None


class AdvicePolicy:
    def suggestions(self, context: AdviceContext) -> list[str]:
        return [step.text for step in self._steps(context)]

    def suggestion_actions(self, context: AdviceContext) -> list[SuggestionAction]:
        return [step.action for step in self._steps(context) if step.action is not None]

    def _steps(self, context: AdviceContext) -> list[_AdviceStep]:
        command_name = context.command_name
        data = context.data

        if context.error_code == "AMBIGUOUS_SESSION":
            return [
                _command_step(
                    "List sessions",
                    "Run `agentnb sessions list --json` to see the live session names.",
                    "sessions",
                    "list",
                    "--json",
                ),
                _command_step(
                    "Retry with --session",
                    (
                        f"Retry with `agentnb {command_name} --session NAME --json` "
                        "to target one explicitly."
                    ),
                    command_name,
                    "--session",
                    "NAME",
                    "--json",
                ),
            ]

        if context.error_code == "AMBIGUOUS_EXECUTION":
            return [
                _command_step(
                    "List runs",
                    "Run `agentnb runs list --json` to inspect matching run ids.",
                    "runs",
                    "list",
                    "--json",
                ),
                _command_step(
                    "Show run",
                    "Retry with `agentnb runs show EXECUTION_ID --json` to target one explicitly.",
                    "runs",
                    "show",
                    "EXECUTION_ID",
                    "--json",
                ),
            ]

        if context.error_code == "SESSION_BUSY":
            execution_id = data.get("active_execution_id")
            if isinstance(execution_id, str) and execution_id:
                return [
                    _command_step(
                        "Wait for run",
                        f"Run `{_run_command('wait', execution_id)}` to wait for the blocking run.",
                        "runs",
                        "wait",
                        execution_id,
                        "--json",
                    ),
                    _command_step(
                        "Show run",
                        f"Run `{_run_command('show', execution_id)}` to inspect the blocking run.",
                        "runs",
                        "show",
                        execution_id,
                        "--json",
                    ),
                ]
            return [
                _command_step(
                    "Wait for session",
                    "Run `agentnb wait --json` to block until the session is idle, then retry.",
                    "wait",
                    "--json",
                )
            ]

        if context.error_code == "KERNEL_NOT_READY":
            return [
                _command_step(
                    "Wait for kernel",
                    "Run `agentnb wait --json` to wait for startup to finish.",
                    "wait",
                    "--json",
                ),
                _command_step(
                    "Check status",
                    "Run `agentnb status --json` to inspect the current session state.",
                    "status",
                    "--json",
                ),
            ]

        if context.error_code in {"NO_KERNEL", "BACKEND_ERROR", "KERNEL_DEAD"}:
            return [
                _command_step(
                    "Start kernel",
                    "Run `agentnb start --json` to start the kernel.",
                    "start",
                    "--json",
                ),
                _command_step(
                    "Run doctor",
                    "Run `agentnb doctor --json` if startup has been failing.",
                    "doctor",
                    "--json",
                ),
            ]

        if command_name == "start":
            return []

        if command_name == "status":
            runtime_state = data.get("runtime_state")
            if runtime_state == "starting":
                return [_text_step("Run `agentnb wait --json` to wait for startup to finish.")]
            if data.get("alive"):
                if data.get("busy"):
                    return [
                        _text_step("Run `agentnb wait --json` to wait until the session is usable.")
                    ]
                return []
            return [
                _text_step("Run `agentnb start --json` to start a project-scoped kernel."),
                _text_step("Run `agentnb doctor --json` if startup has been failing."),
            ]

        if command_name == "wait":
            if context.response_status == "ok":
                return []
            return [
                _text_step("Run `agentnb status --json` to inspect the current session state."),
                _text_step("Run `agentnb start --json` if the target session is not running yet."),
            ]

        if command_name == "exec":
            if context.response_status == "ok":
                if _file_exec_truncated(data, session_id=context.session_id):
                    return _file_exec_truncation_steps(data, session_id=context.session_id)
                if data.get("background"):
                    execution_id = _execution_id(data)
                    return [
                        _command_step(
                            "Wait for run",
                            (
                                f"Run `{_run_command('wait', execution_id)}` "
                                "to wait for the final result."
                            ),
                            "runs",
                            "wait",
                            execution_id,
                            "--json",
                        ),
                        _command_step(
                            "Show run",
                            (
                                f"Run `{_run_command('show', execution_id)}` "
                                "to inspect the current run record."
                            ),
                            "runs",
                            "show",
                            execution_id,
                            "--json",
                        ),
                        _command_step(
                            "Cancel run",
                            (
                                f"Run `{_run_command('cancel', execution_id)}` "
                                "to stop the background run."
                            ),
                            "runs",
                            "cancel",
                            execution_id,
                            "--json",
                        ),
                    ]
                if _exec_output_is_empty(data):
                    execution_id = _execution_id(data)
                    return [
                        _text_step(
                            "Run `agentnb vars --recent 5 --json` to inspect namespace changes."
                        ),
                        _text_step(
                            f"Run `agentnb history {execution_id} --json` to review this execution."
                        ),
                    ]
                return []

            if context.error_code == "INVALID_INPUT":
                return []

            if context.error_code == "TIMEOUT":
                steps = [
                    _text_step(
                        "Run `agentnb history @last-error --json` to review the latest failure."
                    )
                ]
                if data.get("interrupt_recommended"):
                    steps.append(
                        _text_step(
                            "Run `agentnb interrupt --json` if execution may still be stuck."
                        )
                    )
                steps.append(
                    _text_step("Run `agentnb reset --json` if the namespace needs a clean slate.")
                )
                return steps

            if context.error_name == "ModuleNotFoundError":
                module = _extract_module_name(context.error_value)
                if module:
                    package = _package_name_for_import(module)
                    session_python = _session_python(data)
                    if session_python:
                        return [
                            _shell_step(
                                "Repair live session",
                                (
                                    "Repair the live session: run "
                                    f"`uv pip install --python {session_python} {package}` "
                                    "in your shell."
                                ),
                                "uv",
                                "pip",
                                "install",
                                "--python",
                                session_python,
                                package,
                            ),
                            _shell_step(
                                "Add dependency",
                                (
                                    "For a durable project dependency, run "
                                    f"`uv add {package}` in your shell."
                                ),
                                "uv",
                                "add",
                                package,
                            ),
                            _text_step("Then retry the execution."),
                        ]
                    return [
                        _shell_step(
                            "Install dependency",
                            (
                                f"Install the missing module: run `uv add {package}` "
                                "in your shell (not inside the session)."
                            ),
                            "uv",
                            "add",
                            package,
                        ),
                        _text_step("Then retry the execution."),
                    ]

            if _missing_pip_in_called_process(context):
                package = _extract_pip_install_target(context.error_value)
                session_python = _session_python(data)
                if package and session_python:
                    install_hint = (
                        f"run `uv pip install --python {session_python} {package}` in your shell."
                    )
                    return [
                        _text_step(
                            "The selected interpreter does not provide pip inside the live session."
                        ),
                        _shell_step(
                            "Repair live session",
                            f"Install the dependency from this project with {install_hint}",
                            "uv",
                            "pip",
                            "install",
                            "--python",
                            session_python,
                            package,
                        ),
                    ]
                if session_python:
                    install_hint = (
                        f"use `uv pip install --python {session_python} PACKAGE` in your shell."
                    )
                elif package:
                    install_hint = f"run `uv add {package}` in your shell (not inside the session)."
                else:
                    install_hint = "use `uv add PACKAGE` in your shell (not inside the session)."
                steps = [
                    _text_step(
                        "The selected interpreter does not provide pip inside the live session."
                    )
                ]
                if package:
                    steps.append(
                        _shell_step(
                            "Install dependency",
                            f"Install the dependency from this project with {install_hint}",
                            "uv",
                            "add",
                            package,
                        )
                    )
                else:
                    steps.append(
                        _text_step(f"Install the dependency from this project with {install_hint}")
                    )
                return steps

            if context.error_name == "NameError" and context.session_id:
                session = context.session_id
                return [
                    _text_step(
                        f"Run `agentnb vars --session {session} --json` to inspect the namespace."
                    ),
                    _text_step("Run `agentnb sessions list --json` to see all live sessions."),
                    _text_step(
                        "Run `agentnb history @last-error --json` to review the latest failure."
                    ),
                ]

            return [
                _text_step(
                    "Run `agentnb history @last-error --json` to review the latest failure."
                ),
                _text_step("Run `agentnb interrupt --json` if execution may still be stuck."),
                _text_step("Run `agentnb reset --json` if the namespace needs a clean slate."),
            ]

        if command_name == "vars":
            if not data.get("vars"):
                return [_text_step('Run `agentnb "..." --json` to create some live state first.')]
            return []

        if command_name == "inspect":
            return []

        if command_name == "reload":
            stale_names = data.get("stale_names")
            if stale_names:
                return [
                    _text_step(
                        "Run `agentnb reset --json` if stale objects are still causing issues."
                    )
                ]
            reloaded = data.get("reloaded_modules")
            if isinstance(reloaded, list) and not reloaded:
                return [
                    _text_step("No project-local modules were found to reload."),
                    _text_step(
                        "To reload a specific module, run "
                        "`importlib.reload(module)` in the session."
                    ),
                ]
            return []

        if command_name == "history":
            if not data.get("entries"):
                return [
                    _text_step('Run `agentnb "..." --json` to record the first execution step.')
                ]
            return []

        if command_name == "interrupt":
            return [
                _text_step('Retry with `agentnb exec "..." --json` once the kernel is idle.'),
                _text_step(
                    "Run `agentnb reset --json` if interrupted code left partial state behind."
                ),
            ]

        if command_name == "reset":
            return [
                _command_step(
                    "Rebuild state",
                    'Run `agentnb exec "..." --json` to rebuild the state you need.',
                    "exec",
                    "...",
                    "--json",
                )
            ]

        if command_name == "stop":
            return []

        if command_name == "doctor":
            if data.get("ready"):
                if data.get("kernel_alive"):
                    return [_text_step("Kernel is already running.")]
                if data.get("session_exists"):
                    return [
                        _text_step("Session exists but kernel is not running."),
                        _text_step("Run `agentnb start --json` to restart the kernel."),
                    ]
                return [_text_step("Run `agentnb start --json` to start the kernel.")]
            return [
                _text_step(
                    "Run the install command shown by `agentnb doctor --json` in your shell."
                ),
                _text_step(
                    'Then restart with `agentnb --fresh "..." --json` '
                    "or run `agentnb start --json` again."
                ),
                _text_step(
                    "Run `agentnb start --python /path/to/python --json` "
                    "to try a specific interpreter."
                ),
            ]

        if command_name == "sessions-list":
            if not data.get("sessions"):
                return [
                    _text_step("Run `agentnb start --json` to start the default session."),
                    _text_step('Run `agentnb "..." --json` to start and execute in one step.'),
                ]
            return []

        if command_name == "sessions-delete":
            return []

        if command_name == "runs-list":
            if not data.get("runs"):
                return [
                    _text_step(
                        'Run `agentnb --background "..." --json` to create a persisted run record.'
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
                    _text_step(
                        f"Run `{_run_command('follow', execution_id)}` to stream new events."
                    ),
                    _text_step(
                        f"Run `{_run_command('wait', execution_id)}` "
                        "to wait for the final snapshot."
                    ),
                    _text_step(
                        f"Run `{_run_command('cancel', execution_id)}` to stop the background run."
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
                    _command_step(
                        "Wait for run",
                        (
                            f"Run `{_run_command('wait', execution_id)}` "
                            "to wait for the final snapshot."
                        ),
                        "runs",
                        "wait",
                        execution_id,
                        "--json",
                    ),
                    _command_step(
                        "Show run",
                        (
                            f"Run `{_run_command('show', execution_id)}` "
                            "to inspect the latest run snapshot."
                        ),
                        "runs",
                        "show",
                        execution_id,
                        "--json",
                    ),
                    _command_step(
                        "Cancel run",
                        f"Run `{_run_command('cancel', execution_id)}` to stop the background run.",
                        "runs",
                        "cancel",
                        execution_id,
                        "--json",
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
                        _text_step(
                            f"Run `{_run_command('show', execution_id)}` "
                            "to inspect the completed run."
                        ),
                        _text_step(
                            "Run `agentnb wait --session NAME --json` "
                            "to confirm the session is ready."
                        ),
                    ]
                if data.get("session_outcome") == "preserved":
                    session_id = data.get("session_id") or "default"
                    return [
                        _text_step(
                            f"Run `agentnb wait --session {session_id} --json` "
                            "to confirm the session is ready for more work."
                        ),
                        _text_step(
                            f"Run `{_run_command('show', execution_id)}` "
                            "to inspect the cancelled run record."
                        ),
                    ]
                if data.get("session_outcome") == "stopped":
                    return [
                        _text_step(
                            "Run `agentnb start --session NAME --json` "
                            "to start a fresh session explicitly."
                        ),
                        _text_step(
                            'Run `agentnb "..." --json` to restart and execute in one step.'
                        ),
                    ]
            return [
                _text_step(
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


def _package_name_for_import(module_name: str) -> str:
    return _IMPORT_PACKAGE_NAME_OVERRIDES.get(module_name, module_name)


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


def _file_exec_truncated(
    data: Mapping[str, object],
    *,
    session_id: str | None,
) -> bool:
    del session_id
    if data.get("source_kind") != "file":
        return False
    truncation_keys = ("stdout_truncated", "stderr_truncated", "result_truncated")
    return any(data.get(key) is True for key in truncation_keys)


def _file_exec_truncation_suggestions(
    data: Mapping[str, object],
    *,
    session_id: str | None,
) -> list[str]:
    return [step.text for step in _file_exec_truncation_steps(data, session_id=session_id)]


def _file_exec_truncation_actions(
    data: Mapping[str, object],
    *,
    session_id: str | None,
) -> list[SuggestionAction]:
    return [
        step.action
        for step in _file_exec_truncation_steps(data, session_id=session_id)
        if step.action is not None
    ]


def _file_exec_truncation_steps(
    data: Mapping[str, object],
    *,
    session_id: str | None,
) -> list[_AdviceStep]:
    return [
        _command_step(
            "Rerun without truncation",
            (
                f"Run `{_no_truncate_file_command(data, session_id=session_id)}` "
                "to rerun the file without truncation."
            ),
            *_no_truncate_file_args(data, session_id=session_id),
        ),
        _command_step(
            "Inspect recent vars",
            _vars_recent_command(session_id=session_id),
            *_vars_recent_args(session_id=session_id),
        ),
    ]


def _command_step(label: str, text: str, *args: str) -> _AdviceStep:
    return _AdviceStep(text=text, action=_agentnb_action(label, *args))


def _shell_step(label: str, text: str, command: str, *args: str) -> _AdviceStep:
    return _AdviceStep(text=text, action=_shell_action(label, command, *args))


def _text_step(text: str) -> _AdviceStep:
    return _AdviceStep(text=text)


def _agentnb_action(label: str, *args: str) -> SuggestionAction:
    return {
        "kind": "command",
        "label": label,
        "command": "agentnb",
        "args": list(args),
    }


def _shell_action(label: str, command: str, *args: str) -> SuggestionAction:
    return {
        "kind": "shell",
        "label": label,
        "command": command,
        "args": list(args),
    }


def _no_truncate_file_command(data: Mapping[str, object], *, session_id: str | None) -> str:
    args = _no_truncate_file_args(data, session_id=session_id)
    return "agentnb " + " ".join(args)


def _no_truncate_file_args(data: Mapping[str, object], *, session_id: str | None) -> list[str]:
    args = ["exec"]
    if session_id:
        args.extend(["--session", session_id])
    args.append("--no-truncate")
    source_path = data.get("source_path")
    if isinstance(source_path, str) and source_path:
        args.extend(["--file", source_path])
    return args


def _vars_recent_command(*, session_id: str | None) -> str:
    command = "agentnb " + " ".join(_vars_recent_args(session_id=session_id))
    return f"Run `{command}` to inspect the newest live variables."


def _vars_recent_args(*, session_id: str | None) -> list[str]:
    args = ["vars"]
    if session_id:
        args.extend(["--session", session_id])
    args.extend(["--recent", "5", "--json"])
    return args
