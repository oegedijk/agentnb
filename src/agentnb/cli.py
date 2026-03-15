from __future__ import annotations

import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import click

from .app import (
    AgentNBApp,
    DoctorRequest,
    ExecRequest,
    HistoryRequest,
    InspectRequest,
    InterruptRequest,
    ReloadRequest,
    ResetRequest,
    RunLookupRequest,
    RunsCancelRequest,
    RunsFollowRequest,
    RunsListRequest,
    RunsWaitRequest,
    SessionsDeleteRequest,
    SessionsListRequest,
    StartRequest,
    StatusRequest,
    StopRequest,
    VarsRequest,
    suggestions_for_command,
)
from .contracts import (
    CommandResponse,
    ExecutionEvent,
    ExecutionSink,
    error_response,
)
from .errors import AgentNBException, InvalidInputError
from .execution import ExecutionService
from .ops import NotebookOps
from .output import RenderOptions, render_response
from .runtime import KernelRuntime
from .session import DEFAULT_SESSION_ID, resolve_project_root, validate_session_id

runtime = KernelRuntime()
ops = NotebookOps(runtime)
executions = ExecutionService(runtime)
application = AgentNBApp(runtime=runtime, executions=executions, ops=ops)
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

      1. agentnb exec --ensure-started "from myapp import thing" --json
      2. agentnb status --wait-idle --json
      3. agentnb exec --file analysis.py --json
      4. agentnb vars --recent 5 --json
      5. agentnb inspect thing --json
      6. agentnb reload myapp.module --json
      7. agentnb history --json
      8. agentnb runs list --json when you need durable execution records
      9. agentnb runs follow EXECUTION_ID --json for live background progress

    Use `--session NAME` on kernel-bound commands when working with more than
    one live session. `sessions list` shows live session names and metadata.
    `exec --background` returns an `execution_id`; use `runs show` for the
    latest persisted snapshot, `runs follow` for live progress, `runs wait`
    for the final snapshot, and `runs cancel` when you need to stop the run.

    For multiline code, prefer --file or stdin/heredoc over shell-escaped
    backslashes. `vars` includes type information by default. `history`
    shows semantic user-visible steps by default; pass --all to include
    internal helper executions. `runs` shows persisted exec/reset records by
    `execution_id`. Module reloading is explicit: use `reload` after editing
    project-local modules. agentnb does not auto-reload modules on every
    execution. Like a regular IPython notebook, the final expression in an
    exec block is returned as the result output while `print(...)` goes to
    stdout. In `--agent` mode, JSON payloads are compacted by default to
    reduce token usage.

    Prefer --json for agent integrations and machine-readable parsing. Use
    `status --wait-idle` when you need to know the session is safe for the
    next command, not just alive. If ipykernel is missing, use `start --auto-install`.
    Use `doctor --fix` for automatic repair. Top-level flags such as --agent
    and --json can be placed before or after the subcommand.
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


def project_option(func):
    return click.option(
        "--project",
        type=click.Path(path_type=Path, file_okay=False, dir_okay=True),
        default=None,
        help="Project root directory",
    )(func)


def json_option(func):
    return click.option("--json", "as_json", is_flag=True, help="Output as JSON")(func)


def session_option(func):
    return click.option(
        "--session",
        "session_id",
        default=None,
        callback=_session_option_callback,
        help="Session name. If omitted, agentnb uses the only live session or `default`.",
    )(func)


def python_option(func):
    return click.option(
        "--python",
        "python_executable",
        type=click.Path(path_type=Path, dir_okay=False),
        default=None,
        help="Python interpreter for the kernel",
    )(func)


def _session_option_callback(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> str | None:
    del ctx, param
    if value is None:
        return None
    try:
        return validate_session_id(value)
    except AgentNBException as exc:
        raise click.BadParameter(exc.message) from exc


def _emit(response: CommandResponse, *, as_json: bool) -> None:
    options = _current_render_options(local_as_json=as_json)
    if response.command == "exec" and response.data.get("selected_output") is not None:
        response = replace(response, suggestions=[])
    if options.quiet or not options.show_suggestions:
        response = replace(response, suggestions=[])
    rendered = render_response(response, options=options)
    if rendered:
        click.echo(rendered)
    if response.status == "error":
        raise click.exceptions.Exit(1)


class HumanExecutionStream(ExecutionSink):
    def __init__(self) -> None:
        self.execution_id: str | None = None
        self.session_id: str | None = None
        self.emitted_output = False

    def started(self, *, execution_id: str, session_id: str) -> None:
        self.execution_id = execution_id
        self.session_id = session_id

    def accept(self, event: ExecutionEvent) -> None:
        if event.kind == "stdout" and event.content:
            _echo_stream_text(event.content)
            self.emitted_output = True
            return
        if event.kind == "stderr" and event.content:
            _echo_stream_text(event.content, err=True)
            self.emitted_output = True
            return
        if event.kind in {"result", "display"} and event.content:
            _echo_stream_block(event.content)
            self.emitted_output = True


class JsonExecutionStream(ExecutionSink):
    def started(self, *, execution_id: str, session_id: str) -> None:
        _emit_json_stream_frame(
            {
                "type": "start",
                "execution_id": execution_id,
                "session_id": session_id,
            }
        )

    def accept(self, event: ExecutionEvent) -> None:
        _emit_json_stream_frame({"type": "event", "event": event.to_dict()})


def _echo_stream_text(text: str, *, err: bool = False) -> None:
    click.echo(text, nl=False, err=err)


def _echo_stream_block(text: str) -> None:
    if text.endswith("\n"):
        click.echo(text, nl=False)
        return
    click.echo(text)


def _emit_json_stream_frame(payload: dict[str, Any]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=True))


def _emit_stream_completion(
    response: CommandResponse,
    *,
    as_json: bool,
    stream: ExecutionSink | None = None,
) -> None:
    options = _current_render_options(local_as_json=as_json)
    if options.quiet or not options.show_suggestions:
        response = replace(response, suggestions=[])

    if options.as_json:
        _emit_json_stream_frame({"type": "final", "response": response.to_dict()})
    else:
        human_stream = stream if isinstance(stream, HumanExecutionStream) else None
        if response.status == "ok" and human_stream is not None and not human_stream.emitted_output:
            click.echo("Execution completed.")
        if response.status == "error":
            rendered = render_response(response, options=replace(options, as_json=False))
            if rendered:
                click.echo(rendered, err=True)
        elif response.suggestions:
            click.echo(_render_suggestions_block(response.suggestions))

    if response.status == "error":
        raise click.exceptions.Exit(1)


def _render_suggestions_block(suggestions: list[str]) -> str:
    lines = ["", "Next:"]
    lines.extend(f"- {suggestion}" for suggestion in suggestions)
    return "\n".join(lines)


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
    session_id: str | None,
    python_executable: Path | None,
    auto_install: bool,
    as_json: bool,
) -> None:
    """Start or reuse the project's persistent kernel.

    The interpreter is selected from --python, .venv, VIRTUAL_ENV, or the
    current Python executable. Without --auto-install, startup fails with the
    exact install command if ipykernel is missing.
    """

    request = StartRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        python_executable=python_executable,
        auto_install=auto_install,
    )
    _emit(application.start(request), as_json=as_json)


@main.command("exec")
@click.argument("code", required=False)
@click.option("-f", "--file", "filepath", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--timeout", default=30.0, show_default=True, type=float)
@click.option(
    "--ensure-started",
    is_flag=True,
    help="Start the target session first if it is not already running.",
)
@click.option(
    "--background",
    is_flag=True,
    help="Run the execution in the background and return an execution_id immediately.",
)
@click.option(
    "--stream",
    is_flag=True,
    help="Stream execution events in real time and finish with the final result payload.",
)
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
    ensure_started: bool,
    background: bool,
    stream: bool,
    output_selector: str | None,
    project: Path | None,
    session_id: str | None,
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
            session_id=session_id or DEFAULT_SESSION_ID,
            code=exc.code,
            message=exc.message,
            ename=exc.ename,
            evalue=exc.evalue,
            traceback=exc.traceback,
            suggestions=suggestions_for_command("exec", "error", {}),
        )
        if stream:
            _emit_stream_completion(response, as_json=as_json)
        else:
            _emit(response, as_json=as_json)
        return

    request = ExecRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        code=source,
        session_id=session_id,
        timeout_s=timeout,
        ensure_started=ensure_started,
        background=background,
        stream=stream,
        output_selector=output_selector,
    )

    if stream:
        _execute_streaming_exec(request=request, as_json=as_json)
        return

    _emit(application.exec(request), as_json=as_json)


def _execute_streaming_exec(
    *,
    request: ExecRequest,
    as_json: bool,
) -> None:
    options = _current_render_options(local_as_json=as_json)
    stream: ExecutionSink = JsonExecutionStream() if options.as_json else HumanExecutionStream()
    response = application.exec(request, event_sink=stream)
    _emit_stream_completion(response, as_json=as_json, stream=stream)


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
    session_id: str | None,
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

    request = VarsRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        include_types=include_types,
        match_text=match_text,
        recent=recent,
    )
    _emit(application.vars(request), as_json=as_json)


@main.command("inspect")
@click.argument("name")
@project_option
@session_option
@json_option
def inspect_cmd(name: str, project: Path | None, session_id: str | None, as_json: bool) -> None:
    """Inspect one variable in the kernel namespace.

    Dataframe-like values get a compact tabular preview. Lists, tuples, sets,
    and dicts get a compact structural preview instead of a generic repr.
    """

    request = InspectRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        name=name,
    )
    _emit(application.inspect(request), as_json=as_json)


@main.command("reload")
@click.argument("module", required=False)
@project_option
@session_option
@json_option
def reload_cmd(
    module: str | None, project: Path | None, session_id: str | None, as_json: bool
) -> None:
    """Reload project-local modules in the live kernel.

    Pass a module name to reload one imported project-local module. Omit the
    module to reload all currently imported project-local modules. The reload
    report includes rebound names and possible stale objects.
    """

    request = ReloadRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        module_name=module,
    )
    _emit(application.reload(request), as_json=as_json)


@main.command()
@click.option(
    "--wait",
    is_flag=True,
    help="Wait until the target session is ready instead of returning immediately.",
)
@click.option(
    "--wait-idle",
    is_flag=True,
    help="Wait until the target session is alive and not executing another command.",
)
@click.option(
    "--timeout",
    default=30.0,
    show_default=True,
    type=float,
    help="Maximum seconds to wait when --wait is used.",
)
@project_option
@session_option
@json_option
def status(
    wait: bool,
    wait_idle: bool,
    timeout: float,
    project: Path | None,
    session_id: str | None,
    as_json: bool,
) -> None:
    """Check whether the project's kernel is currently running."""

    if wait and wait_idle:
        raise click.UsageError("Use either --wait or --wait-idle, not both.")

    wait_for = "idle" if wait_idle else "ready" if wait else None
    request = StatusRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        wait_for=wait_for,
        timeout_s=timeout,
    )
    _emit(application.status(request), as_json=as_json)


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
    session_id: str | None,
    as_json: bool,
) -> None:
    """Show recent execution history recorded for the project.

    By default, this shows semantic user-visible steps such as exec, vars,
    inspect, reload, and reset. Pass --all to include internal helper
    executions. History entries are compact summaries by default.
    """

    request = HistoryRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        errors=errors,
        latest=latest,
        last=last,
        include_internal=include_internal,
    )
    _emit(application.history(request), as_json=as_json)


@main.command()
@project_option
@session_option
@json_option
def interrupt(project: Path | None, session_id: str | None, as_json: bool) -> None:
    """Interrupt the currently running execution without stopping the kernel."""

    request = InterruptRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
    )
    _emit(application.interrupt(request), as_json=as_json)


@main.command()
@click.option("--timeout", default=10.0, show_default=True, type=float)
@project_option
@session_option
@json_option
def reset(timeout: float, project: Path | None, session_id: str | None, as_json: bool) -> None:
    """Clear user state from the kernel while keeping the process alive."""

    request = ResetRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        timeout_s=timeout,
    )
    _emit(application.reset(request), as_json=as_json)


@main.command()
@project_option
@session_option
@json_option
def stop(project: Path | None, session_id: str | None, as_json: bool) -> None:
    """Shut down the project's kernel and clear the saved session metadata."""

    request = StopRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
    )
    _emit(application.stop(request), as_json=as_json)


@main.command()
@project_option
@session_option
@python_option
@click.option("--fix", is_flag=True, help="Attempt to auto-fix issues when possible")
@json_option
def doctor(
    project: Path | None,
    session_id: str | None,
    python_executable: Path | None,
    fix: bool,
    as_json: bool,
) -> None:
    """Check interpreter and kernel prerequisites for startup.

    Use this when start fails, the wrong interpreter is selected, or ipykernel
    is missing. Pass --fix to attempt automatic remediation when supported.
    """

    request = DoctorRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        python_executable=python_executable,
        auto_fix=fix,
    )
    _emit(application.doctor(request), as_json=as_json)


@main.group("sessions")
def sessions_group() -> None:
    """Inspect and manage named sessions for the current project."""


@sessions_group.command("list")
@project_option
@json_option
def sessions_list(project: Path | None, as_json: bool) -> None:
    """List live sessions recorded for the current project."""

    request = SessionsListRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project)
    )
    _emit(application.sessions_list(request), as_json=as_json)


@sessions_group.command("delete")
@click.argument("session_name", callback=_session_option_callback)
@project_option
@json_option
def sessions_delete(session_name: str, project: Path | None, as_json: bool) -> None:
    """Delete one named session and stop its kernel if it is still running."""

    request = SessionsDeleteRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_name=session_name,
    )
    _emit(application.sessions_delete(request), as_json=as_json)


@main.group("runs")
def runs_group() -> None:
    """Inspect persisted execution records for exec and reset commands."""


@runs_group.command("list")
@click.option("--errors", is_flag=True, help="Only show failed runs")
@click.option("--last", type=click.IntRange(min=1), default=None, help="Show the last N runs")
@project_option
@session_option
@json_option
def runs_list(
    errors: bool,
    last: int | None,
    project: Path | None,
    session_id: str | None,
    as_json: bool,
) -> None:
    """List persisted exec/reset runs for the current project."""

    request = RunsListRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        errors=errors,
        last=last,
    )
    _emit(application.runs_list(request), as_json=as_json)


@runs_group.command("show")
@click.argument("execution_id")
@project_option
@json_option
def runs_show(execution_id: str, project: Path | None, as_json: bool) -> None:
    """Show a persisted snapshot of one exec/reset run."""

    request = RunLookupRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        execution_id=execution_id,
    )
    _emit(application.runs_show(request), as_json=as_json)


@runs_group.command("wait")
@click.argument("execution_id")
@click.option("--timeout", default=30.0, show_default=True, type=float)
@project_option
@json_option
def runs_wait(execution_id: str, timeout: float, project: Path | None, as_json: bool) -> None:
    """Wait for one background run to finish and return its final snapshot."""

    request = RunsWaitRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        execution_id=execution_id,
        timeout_s=timeout,
    )
    _emit(application.runs_wait(request), as_json=as_json)


@runs_group.command("follow")
@click.argument("execution_id")
@click.option("--timeout", default=30.0, show_default=True, type=float)
@project_option
@json_option
def runs_follow(execution_id: str, timeout: float, project: Path | None, as_json: bool) -> None:
    """Follow one persisted run and stream newly recorded events until it finishes."""
    project_root = resolve_project_root(cwd=Path.cwd(), override=project)
    options = _current_render_options(local_as_json=as_json)
    stream: ExecutionSink = JsonExecutionStream() if options.as_json else HumanExecutionStream()
    request = RunsFollowRequest(
        project_root=project_root,
        execution_id=execution_id,
        timeout_s=timeout,
    )
    response = application.runs_follow(request, event_sink=stream)
    _emit_stream_completion(response, as_json=as_json, stream=stream)


@runs_group.command("cancel")
@click.argument("execution_id")
@project_option
@json_option
def runs_cancel(execution_id: str, project: Path | None, as_json: bool) -> None:
    """Cancel one running background run and report what happened to the session."""

    request = RunsCancelRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        execution_id=execution_id,
    )
    _emit(application.runs_cancel(request), as_json=as_json)


@main.command("_background-run", hidden=True)
@click.argument("execution_id")
@project_option
def background_run(execution_id: str, project: Path | None) -> None:
    """Internal helper to execute one persisted background run."""

    project_root = resolve_project_root(cwd=Path.cwd(), override=project)
    executions.complete_background_run(project_root=project_root, execution_id=execution_id)


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


if __name__ == "__main__":
    main()
