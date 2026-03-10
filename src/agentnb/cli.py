from __future__ import annotations

import os
import sys
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import click

from .compact import (
    compact_execution_payload,
    compact_history_entry,
    compact_inspect_payload,
    compact_traceback,
)
from .contracts import CommandResponse, ExecutionResult, error_response, success_response
from .errors import AgentNBException, InvalidInputError, KernelNotReadyError, NoKernelRunningError
from .history import HistoryStore, kernel_execution_record, user_command_record
from .ops import NotebookOps
from .output import RenderOptions, render_response
from .runtime import KernelRuntime
from .session import DEFAULT_SESSION_ID, resolve_project_root, validate_session_id

runtime = KernelRuntime()
ops = NotebookOps(runtime)
_ROOT_FLAG_NAMES = {"--json", "--agent", "--quiet", "--no-suggestions"}


class AgentGroup(click.Group):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        normalized = _normalize_root_flags(args)
        return super().parse_args(ctx, normalized)


@click.group(
    cls=AgentGroup,
    invoke_without_command=True,
    context_settings={"help_option_names": ["--help", "-h"]},
)
@click.option("--json", "root_as_json", is_flag=True, help="Output all commands as JSON")
@click.option(
    "--agent",
    is_flag=True,
    help="Agent preset: JSON output with deterministic, low-noise defaults.",
)
@click.option("--quiet", is_flag=True, help="Reduce non-essential human output")
@click.option("--no-suggestions", is_flag=True, help="Suppress next-step suggestions")
@click.pass_context
def main(
    ctx: click.Context,
    root_as_json: bool,
    agent: bool,
    quiet: bool,
    no_suggestions: bool,
) -> None:
    """Persistent project-scoped Python REPL for agent workflows.

    Start a long-running kernel for the current project, execute code against it,
    inspect live variables, and recover without losing all state on every step.

    Think of agentnb as an agent REPL, or an append-only notebook without a
    notebook editor. It preserves execution state and history, but does not
    edit notebook cells or manage .ipynb files.

    One project session should be driven serially. Wait for each command to
    finish before sending the next one to the same kernel.

    Recommended loop:

      1. agentnb start --json
      2. agentnb exec "from myapp import thing" --json
      3. agentnb exec --file analysis.py --json
      4. agentnb vars --recent 5 --json
      5. agentnb inspect thing --json
      6. agentnb reload myapp.module --json
      7. agentnb history --json

    For multiline code, prefer --file or stdin/heredoc over shell-escaped
    backslashes. `vars` includes type information by default. `history`
    shows semantic user-visible steps by default; pass --all to include
    internal helper executions. Module reloading is explicit: use `reload`
    after editing project-local modules. agentnb does not auto-reload modules
    on every execution. Like a regular IPython notebook, the final expression
    in an exec block is returned as the result output while `print(...)` goes
    to stdout. In `--agent` mode, JSON payloads are compacted by default to
    reduce token usage.

    Prefer --json for agent integrations and machine-readable parsing. Startup
    does not install ipykernel unless you pass --auto-install or use agentnb
    doctor --fix. Top-level flags such as --agent and --json can be placed
    before or after the subcommand.
    """
    ctx.obj = _resolve_render_options(
        root_as_json=root_as_json,
        agent=agent,
        quiet=quiet,
        no_suggestions=no_suggestions,
    )
    if ctx.invoked_subcommand is None:
        click.echo(
            "No command provided. Run `agentnb --help` to see the full workflow "
            "and command guide.\n"
        )
        click.echo(ctx.get_help())
        ctx.exit(0)


def project_option(func: Callable[..., object]) -> Callable[..., object]:
    return click.option(
        "--project",
        type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
        default=None,
        help="Project root directory",
    )(func)


def json_option(func: Callable[..., object]) -> Callable[..., object]:
    return click.option("--json", "as_json", is_flag=True, help="Output as JSON")(func)


def session_option(func: Callable[..., object]) -> Callable[..., object]:
    return click.option(
        "--session",
        "session_id",
        default=DEFAULT_SESSION_ID,
        show_default=True,
        callback=_session_option_callback,
        help="Session name",
    )(func)


def python_option(func: Callable[..., object]) -> Callable[..., object]:
    return click.option(
        "--python",
        "python_executable",
        type=click.Path(path_type=Path, dir_okay=False),
        default=None,
        help="Python interpreter for the kernel",
    )(func)


def _session_option_callback(ctx: click.Context, param: click.Parameter, value: str) -> str:
    del ctx, param
    try:
        return validate_session_id(value)
    except AgentNBException as exc:
        raise click.BadParameter(exc.message) from exc


def _emit(response: CommandResponse, *, as_json: bool) -> None:
    options = _current_render_options(local_as_json=as_json)
    if response.command == "exec" and response.data.get("selected_output") is not None:
        response = replace(response, suggestions=[])
    if not options.show_suggestions:
        response = replace(response, suggestions=[])
    rendered = render_response(response, options=options)
    if rendered:
        click.echo(rendered)
    if response.status == "error":
        raise click.exceptions.Exit(1)


def _suggestions(command_name: str, response_status: str, data: dict[str, object]) -> list[str]:
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
        if response_status == "ok":
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
            "Run `agentnb start --python /path/to/python --json` to try a specific interpreter.",
        ]
    if command_name == "sessions-list":
        return [
            "Run `agentnb start --session NAME --json` to start another named session.",
            "Run `agentnb status --session NAME --json` to inspect one session.",
        ]
    if command_name == "sessions-delete":
        return [
            "Run `agentnb sessions list --json` to confirm the remaining sessions.",
        ]
    return []


def _execute_command(
    command_name: str,
    project: Path | None,
    as_json: bool,
    session_id: str,
    handler: Callable[[Path, str], dict[str, object]],
) -> None:
    project_root = resolve_project_root(cwd=Path.cwd(), override=project)

    try:
        data = handler(project_root, session_id)
        response = success_response(
            command=command_name,
            project=str(project_root),
            session_id=session_id,
            data=data,
            suggestions=_suggestions(command_name, "ok", data),
        )
    except AgentNBException as exc:
        response = error_response(
            command=command_name,
            project=str(project_root),
            session_id=session_id,
            code=exc.code,
            message=exc.message,
            ename=exc.ename,
            evalue=exc.evalue,
            traceback=compact_traceback(exc.traceback),
            data=exc.data,
            suggestions=_suggestions(command_name, "error", {}),
        )
    except Exception as exc:
        response = error_response(
            command=command_name,
            project=str(project_root),
            session_id=session_id,
            code="INTERNAL_ERROR",
            message=str(exc),
            ename=type(exc).__name__,
            evalue=str(exc),
            suggestions=_suggestions(command_name, "error", {}),
        )
    _emit(response, as_json=as_json)


@main.command()
@project_option
@session_option
@python_option
@click.option(
    "--auto-install",
    is_flag=True,
    help="Install ipykernel into the selected interpreter if it is missing.",
)
@json_option
def start(
    project: Path | None,
    session_id: str,
    python_executable: Path | None,
    auto_install: bool,
    as_json: bool,
) -> None:
    """Start or reuse the project's persistent kernel.

    The interpreter is selected from --python, .venv, VIRTUAL_ENV, or the
    current Python executable. Without --auto-install, startup fails with the
    exact install command if ipykernel is missing.
    """

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        status, started_new = runtime.start(
            project_root=project_root,
            session_id=session_id,
            python_executable=python_executable,
            auto_install=auto_install,
        )
        payload = status.to_dict()
        payload["started_new"] = started_new
        payload["auto_install"] = auto_install
        return payload

    _execute_command("start", project, as_json, session_id, handler)


@main.command("exec")
@click.argument("code", required=False)
@click.option("-f", "--file", "filepath", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--timeout", default=30.0, show_default=True, type=float)
@click.option("--stdout-only", "output_selector", flag_value="stdout", default=None)
@click.option("--stderr-only", "output_selector", flag_value="stderr")
@click.option("--result-only", "output_selector", flag_value="result")
@project_option
@session_option
@json_option
def exec_cmd(
    code: str | None,
    filepath: Path | None,
    timeout: float,
    output_selector: str | None,
    project: Path | None,
    session_id: str,
    as_json: bool,
) -> None:
    """Execute code in the live kernel.

    Provide code as an argument, with --file, or through stdin. For multiline
    code, prefer --file or a stdin heredoc. The kernel must already be running
    for the target project. Like a notebook cell, the final expression is
    returned as the execution result, while `print(...)` writes to stdout.
    Drive one project session serially: wait for each command to finish before
    sending the next one to the same kernel.

    Examples:

      agentnb exec "1 + 1" --json
      agentnb exec --file analysis.py --json
      agentnb exec --json <<'PY'
      import pandas as pd
      df = pd.read_csv("tips.csv")
      df.head()
      PY
    """
    try:
        source = _resolve_code_input(code=code, filepath=filepath)
    except AgentNBException as exc:
        project_root = resolve_project_root(cwd=Path.cwd(), override=project)
        response = error_response(
            command="exec",
            project=str(project_root),
            session_id=session_id,
            code=exc.code,
            message=exc.message,
            ename=exc.ename,
            evalue=exc.evalue,
            traceback=exc.traceback,
            suggestions=_suggestions("exec", "error", {}),
        )
        _emit(response, as_json=as_json)
        return

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        history_store = HistoryStore(project_root=project_root, session_id=session_id)
        try:
            result = runtime.execute(
                project_root=project_root,
                session_id=session_id,
                code=source,
                timeout_s=timeout,
            )
        except Exception as exc:
            _record_exec_history(
                history_store=history_store,
                session_id=session_id,
                code=source,
                error=exc,
            )
            raise

        _record_exec_history(
            history_store=history_store,
            session_id=session_id,
            code=source,
            execution=result,
        )
        payload = compact_execution_payload(result.to_dict())
        if output_selector is not None:
            payload["selected_output"] = output_selector
            payload["selected_text"] = _select_exec_output(payload, output_selector)
        if result.status == "error":
            raise AgentNBException(
                code="EXECUTION_ERROR",
                message="Execution failed",
                ename=result.ename,
                evalue=result.evalue,
                traceback=result.traceback,
                data=payload,
            )
        return payload

    _execute_command("exec", project, as_json, session_id, handler)


@main.command("vars")
@click.option("--types/--no-types", "include_types", default=True, help="Show type information")
@click.option("--match", "match_text", default=None, help="Only show variables whose names match")
@click.option(
    "--recent",
    type=click.IntRange(min=1),
    default=None,
    help="Show only the most recently created matching variables",
)
@project_option
@session_option
@json_option
def vars_cmd(
    project: Path | None,
    session_id: str,
    as_json: bool,
    include_types: bool,
    match_text: str | None,
    recent: int | None,
) -> None:
    """List user variables currently defined in the kernel namespace.

    Type information is included by default. Pass --no-types to hide it.
    Imported helper routines and classes are omitted, and common dataframe or
    container values are summarized compactly. Use --recent or --match when
    the namespace gets noisy.
    """

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        values = ops.list_vars(project_root=project_root, session_id=session_id)
        if match_text:
            match_lower = match_text.lower()
            values = [item for item in values if match_lower in str(item["name"]).lower()]
        if recent is not None:
            values = values[-recent:]
        if not include_types:
            values = [{"name": item["name"], "repr": item["repr"]} for item in values]
        return {"vars": values}

    _execute_command("vars", project, as_json, session_id, handler)


@main.command("inspect")
@click.argument("name")
@project_option
@session_option
@json_option
def inspect_cmd(name: str, project: Path | None, session_id: str, as_json: bool) -> None:
    """Inspect one variable in the kernel namespace.

    Dataframe-like values get a compact tabular preview. Lists, tuples, sets,
    and dicts get a compact structural preview instead of a generic repr.
    """

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        payload = ops.inspect_var(project_root=project_root, session_id=session_id, name=name)
        return {"inspect": compact_inspect_payload(payload)}

    _execute_command("inspect", project, as_json, session_id, handler)


@main.command("reload")
@click.argument("module", required=False)
@project_option
@session_option
@json_option
def reload_cmd(module: str | None, project: Path | None, session_id: str, as_json: bool) -> None:
    """Reload project-local modules in the live kernel.

    Pass a module name to reload one imported project-local module. Omit the
    module to reload all currently imported project-local modules. The reload
    report includes rebound names and possible stale objects.
    """

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        payload = ops.reload_module(
            project_root=project_root, session_id=session_id, module_name=module
        )
        return payload

    _execute_command("reload", project, as_json, session_id, handler)


@main.command()
@project_option
@session_option
@json_option
def status(project: Path | None, session_id: str, as_json: bool) -> None:
    """Check whether the project's kernel is currently running."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        return runtime.status(project_root=project_root, session_id=session_id).to_dict()

    _execute_command("status", project, as_json, session_id, handler)


@main.command()
@click.option("--errors", is_flag=True, help="Only show failed executions")
@click.option("--latest", is_flag=True, help="Show only the most recent history entry")
@click.option("--last", type=click.IntRange(min=1), default=None, help="Show the last N entries")
@click.option("--all", "include_internal", is_flag=True, help="Include internal kernel executions")
@project_option
@session_option
@json_option
def history(
    errors: bool,
    latest: bool,
    last: int | None,
    include_internal: bool,
    project: Path | None,
    session_id: str,
    as_json: bool,
) -> None:
    """Show recent execution history recorded for the project.

    By default, this shows semantic user-visible steps such as exec, vars,
    inspect, reload, and reset. Pass --all to include internal helper
    executions. History entries are compact summaries by default.
    """

    if latest and last is not None:
        raise click.UsageError("Use either --latest or --last, not both.")

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        entries = runtime.history(
            project_root=project_root,
            session_id=session_id,
            errors_only=errors,
            include_internal=include_internal,
        )
        entries = [compact_history_entry(entry) for entry in entries]
        if latest:
            entries = entries[-1:]
        elif last is not None:
            entries = entries[-last:]
        return {"entries": entries}

    _execute_command("history", project, as_json, session_id, handler)


@main.command()
@project_option
@session_option
@json_option
def interrupt(project: Path | None, session_id: str, as_json: bool) -> None:
    """Interrupt the currently running execution without stopping the kernel."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        runtime.interrupt(project_root=project_root, session_id=session_id)
        return {"interrupted": True}

    _execute_command("interrupt", project, as_json, session_id, handler)


@main.command()
@click.option("--timeout", default=10.0, show_default=True, type=float)
@project_option
@session_option
@json_option
def reset(timeout: float, project: Path | None, session_id: str, as_json: bool) -> None:
    """Clear user state from the kernel while keeping the process alive."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        history_store = HistoryStore(project_root=project_root, session_id=session_id)
        try:
            result = runtime.reset(
                project_root=project_root,
                session_id=session_id,
                timeout_s=timeout,
            )
        except Exception as exc:
            _record_reset_history(
                history_store=history_store,
                session_id=session_id,
                error=exc,
            )
            raise

        _record_reset_history(
            history_store=history_store,
            session_id=session_id,
            execution=result,
        )
        payload = compact_execution_payload(result.to_dict())
        if result.status == "error":
            raise AgentNBException(
                code="EXECUTION_ERROR",
                message="Reset failed",
                ename=result.ename,
                evalue=result.evalue,
                traceback=result.traceback,
                data=payload,
            )
        return payload

    _execute_command("reset", project, as_json, session_id, handler)


@main.command()
@project_option
@session_option
@json_option
def stop(project: Path | None, session_id: str, as_json: bool) -> None:
    """Shut down the project's kernel and clear the saved session metadata."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        runtime.stop(project_root=project_root, session_id=session_id)
        return {"stopped": True}

    _execute_command("stop", project, as_json, session_id, handler)


@main.command()
@project_option
@session_option
@python_option
@click.option("--fix", is_flag=True, help="Attempt to auto-fix issues when possible")
@json_option
def doctor(
    project: Path | None,
    session_id: str,
    python_executable: Path | None,
    fix: bool,
    as_json: bool,
) -> None:
    """Check interpreter and kernel prerequisites for startup.

    Use this when start fails, the wrong interpreter is selected, or ipykernel
    is missing. Pass --fix to attempt automatic remediation when supported.
    """

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        return runtime.doctor(
            project_root=project_root,
            session_id=session_id,
            python_executable=python_executable,
            auto_fix=fix,
        )

    _execute_command("doctor", project, as_json, session_id, handler)


@main.group("sessions")
def sessions_group() -> None:
    """Inspect and manage named sessions for the current project."""


@sessions_group.command("list")
@project_option
@json_option
def sessions_list(project: Path | None, as_json: bool) -> None:
    """List live sessions recorded for the current project."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        del session_id
        return {"sessions": runtime.list_sessions(project_root=project_root)}

    _execute_command("sessions-list", project, as_json, DEFAULT_SESSION_ID, handler)


@sessions_group.command("delete")
@click.argument("session_name", callback=_session_option_callback)
@project_option
@json_option
def sessions_delete(session_name: str, project: Path | None, as_json: bool) -> None:
    """Delete one named session and stop its kernel if it is still running."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        del session_id
        return runtime.delete_session(project_root=project_root, session_id=session_name)

    _execute_command("sessions-delete", project, as_json, session_name, handler)


def _record_exec_history(
    *,
    history_store: HistoryStore,
    session_id: str,
    code: str,
    execution: ExecutionResult | None = None,
    error: Exception | None = None,
) -> None:
    if _should_skip_history(error):
        return
    history_store.append(
        kernel_execution_record(
            session_id=session_id,
            command_type="exec",
            label="exec kernel execution",
            code=code,
            origin="cli",
            execution=execution,
            error=error,
        )
    )
    history_store.append(
        user_command_record(
            session_id=session_id,
            command_type="exec",
            label="exec",
            input_text=code,
            code=code,
            origin="cli",
            execution=execution,
            error=error,
        )
    )


def _record_reset_history(
    *,
    history_store: HistoryStore,
    session_id: str,
    execution: ExecutionResult | None = None,
    error: Exception | None = None,
) -> None:
    if _should_skip_history(error):
        return
    history_store.append(
        kernel_execution_record(
            session_id=session_id,
            command_type="reset",
            label="reset kernel state",
            code=None,
            origin="cli",
            execution=execution,
            error=error,
        )
    )
    history_store.append(
        user_command_record(
            session_id=session_id,
            command_type="reset",
            label="reset",
            origin="cli",
            execution=execution,
            error=error,
        )
    )


def _should_skip_history(error: Exception | None) -> bool:
    return isinstance(error, (NoKernelRunningError, KernelNotReadyError))


def _resolve_code_input(code: str | None, filepath: Path | None) -> str:
    if filepath is not None:
        if not filepath.exists():
            raise InvalidInputError(f"File not found: {filepath}")
        return filepath.read_text(encoding="utf-8")

    if code is not None:
        return code

    if not sys.stdin.isatty():
        stdin_data = sys.stdin.read()
        if stdin_data.strip():
            return stdin_data
        raise InvalidInputError("No code provided through stdin")

    raise InvalidInputError("No code provided. Use code argument, --file, or pipe via stdin")


def _resolve_render_options(
    *,
    root_as_json: bool,
    agent: bool,
    quiet: bool,
    no_suggestions: bool,
) -> RenderOptions:
    env_mode = os.getenv("AGENTNB_FORMAT", "").strip().lower()
    env_as_json = env_mode in {"json", "agent"}
    env_quiet = _env_flag("AGENTNB_QUIET")
    env_no_suggestions = _env_flag("AGENTNB_NO_SUGGESTIONS")

    options = RenderOptions(
        as_json=root_as_json or env_as_json,
        show_suggestions=not (no_suggestions or env_no_suggestions),
        quiet=quiet or env_quiet,
    )
    if agent or env_mode == "agent":
        options.as_json = True
        options.show_suggestions = False
        options.quiet = True
    return options


def _current_render_options(*, local_as_json: bool) -> RenderOptions:
    ctx = click.get_current_context(silent=True)
    root_options = ctx.find_root().obj if ctx is not None else None
    options = root_options if isinstance(root_options, RenderOptions) else RenderOptions()
    if local_as_json:
        options = replace(options, as_json=True)
    return options


def _env_flag(name: str) -> bool:
    value = os.getenv(name, "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _normalize_root_flags(args: list[str]) -> list[str]:
    if not args:
        return args

    boundary = args.index("--") if "--" in args else len(args)
    command_index: int | None = None
    for index, token in enumerate(args[:boundary]):
        if not token.startswith("-"):
            command_index = index
            break

    if command_index is None:
        return args

    leading = args[:command_index]
    command = args[command_index]
    tail = args[command_index + 1 : boundary]
    suffix = args[boundary:]
    moved_flags: list[str] = []
    remaining_tail: list[str] = []
    for token in tail:
        if token in _ROOT_FLAG_NAMES:
            moved_flags.append(token)
        else:
            remaining_tail.append(token)

    if not moved_flags:
        return args
    return [*leading, *moved_flags, command, *remaining_tail, *suffix]


def _select_exec_output(payload: dict[str, object], selector: str) -> str:
    if selector == "result":
        result = payload.get("result")
        return "" if result is None else str(result)
    value = payload.get(selector)
    return "" if value is None else str(value)
