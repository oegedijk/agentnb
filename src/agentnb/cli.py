from __future__ import annotations

import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

import click

from . import __version__
from .advice import AdviceContext
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
    SessionsDeleteBulkRequest,
    SessionsDeleteRequest,
    SessionsListRequest,
    StartRequest,
    StatusRequest,
    StopRequest,
    VarsRequest,
    WaitRequest,
)
from .contracts import (
    CommandResponse,
    ExecutionEvent,
    ExecutionSink,
    error_response,
)
from .errors import AgentNBException
from .execution import ExecutionService
from .execution_invocation import ExecInvocationPolicy, OutputSelector, StartupPolicy
from .invocation import ROOT_OPTION_SPECS, InvocationResolver
from .ops import NotebookOps
from .output import RenderOptions, projector, render_response
from .runtime import KernelRuntime
from .selectors import (
    HistoryReference,
    RunReference,
    parse_history_reference,
    parse_run_reference,
)
from .session import DEFAULT_SESSION_ID, resolve_project_root, validate_session_id

runtime = KernelRuntime()
executions = ExecutionService(runtime)
ops = NotebookOps(runtime, executions=executions)
application = AgentNBApp(runtime=runtime, executions=executions, ops=ops)
invocations = InvocationResolver()

HELP_COMMAND_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Run Code", ("exec",)),
    ("Read And Inspect", ("vars", "inspect", "reload", "history")),
    ("Control Session", ("wait", "status", "interrupt", "reset", "start", "stop", "doctor")),
    ("Background Runs", ("runs",)),
    ("Sessions", ("sessions",)),
)


class AgentGroup(click.Group):
    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if args and args[0] == "help":
            args = ["--help", *args[1:]]
        intent = invocations.resolve_invocation_intent(
            args,
            known_commands=self.list_commands(ctx),
            cwd=Path.cwd(),
            stdin=sys.stdin,
        )
        return super().parse_args(ctx, list(intent.argv))

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        command_map = {name: self.get_command(ctx, name) for name in self.list_commands(ctx)}
        rendered: set[str] = set()

        for section_title, command_names in HELP_COMMAND_GROUPS:
            rows: list[tuple[str, str]] = []
            for name in command_names:
                command = command_map.get(name)
                if command is None or command.hidden:
                    continue
                rendered.add(name)
                rows.append((name, command.get_short_help_str()))
            if not rows:
                continue
            with formatter.section(section_title):
                formatter.write_dl(rows)

        remaining_rows: list[tuple[str, str]] = []
        for name in self.list_commands(ctx):
            if name in rendered:
                continue
            command = command_map.get(name)
            if command is None or command.hidden:
                continue
            remaining_rows.append((name, command.get_short_help_str()))
        if remaining_rows:
            with formatter.section("Other Commands"):
                formatter.write_dl(remaining_rows)


def root_options(func):
    for spec in reversed(ROOT_OPTION_SPECS):
        func = click.option(*spec.param_decls, is_flag=True, help=spec.help_text)(func)
    return func


@click.group(
    cls=AgentGroup,
    invoke_without_command=True,
    context_settings={"help_option_names": ["--help", "-h"]},
)
@click.version_option(version=__version__, prog_name="agentnb")
@root_options
@click.pass_context
def main(
    ctx: click.Context,
    root_as_json: bool,
    agent: bool,
    quiet: bool,
    no_suggestions: bool,
) -> None:
    """Persistent project-scoped Python REPL for agent workflows.

    \b
    Hot path:
      agentnb "import json"
      agentnb "payload.keys()"
      agentnb analysis.py
      agentnb wait
      agentnb --background "long_task()"
      agentnb --session myenv "df.head()"
      agentnb --timeout 120 "train_model()"
      agentnb --stream "train_model(epochs=10)"

    \b
    Multiline code (braces, quotes, special chars):
      agentnb <<'PY'
      import pandas as pd
      df = pd.read_csv("tips.csv")
      PY

    \b
    Inspect and recover:
      agentnb vars                  agentnb inspect df
      agentnb inspect "payload['items'][0]"
      agentnb history --last 5      agentnb history @last-error
      agentnb runs show @latest     agentnb runs list --last 10
      agentnb wait                  agentnb sessions list
      agentnb reset                 agentnb reload

    \b
    Output control:
      agentnb --result-only "1+1"   agentnb --stdout-only "print('hi')"
      agentnb --agent "1+1"         agentnb --json "1+1"

    `exec` auto-starts the target session by default. Read-only commands such
    as `vars`, `inspect`, and `reload` also auto-start when session targeting
    is unambiguous, and they wait behind active same-session work.
    Drive one session serially: only one command at a time can use a session,
    including quick reads such as `vars` and `inspect`. If a session is busy,
    use `agentnb wait`, `agentnb status --wait-idle`, or `agentnb runs
    wait/show` when you want explicit control over sequencing.

    Canonical grammar: `agentnb <command> [subcommand] [options]`. For
    subcommands, put `--project` after the command name. Session-scoped
    subcommands also accept `--session` there, but execution-id `runs`
    subcommands (`show`, `wait`, `follow`, `cancel`) are project-scoped and
    intentionally do not accept `--session`. `wait` is the primary blocking
    command. `status --wait` and `status --wait-idle` remain compatibility
    forms. When code contains braces or quotes, prefer heredoc or --file over
    inline strings. Do not use `\\n` to embed newlines in an inline string;
    use heredoc instead.

    `--agent` returns compact JSON. `--json` returns the full stable envelope.
    The `result` field is the Python repr of the return value; when valid
    JSON can be extracted, a `result_json` field is also included with the
    parsed value. `--quiet` trims non-essential success-path chatter, while
    `--no-suggestions` suppresses the `Next:` block. `history` and `runs
    list` accept `--last N` and `--errors` to limit output. `start` and
    `doctor` never install packages for you; if `ipykernel` is missing, they
    print one explicit shell command and tell you to restart with `--fresh`.
    """
    ctx.obj = RenderOptions.resolve(
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


def _run_reference_callback(
    ctx: click.Context,
    param: click.Parameter,
    value: str | None,
) -> RunReference | None:
    del ctx, param
    return parse_run_reference(value)


def _history_reference_callback(
    ctx: click.Context,
    param: click.Parameter,
    value: str | None,
) -> HistoryReference | None:
    del ctx, param
    return parse_history_reference(value)


def _emit(response: CommandResponse, *, as_json: bool) -> None:
    options = _current_render_options(local_as_json=as_json)
    if response.command == "exec" and response.data.get("selected_output") is not None:
        response = replace(response, suggestions=[])
    if not options.show_suggestions:
        response = replace(response, suggestions=[])
    elif not options.as_json:
        response = replace(response, suggestions=_strip_json_suffix(response.suggestions))
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
    if not options.show_suggestions:
        response = replace(response, suggestions=[])
    elif not options.as_json:
        response = replace(response, suggestions=_strip_json_suffix(response.suggestions))

    if options.as_json:
        _emit_json_stream_frame(
            {
                "type": "final",
                "response": projector.project(response, profile=options.profile.value),
            }
        )
    else:
        human_stream = stream if isinstance(stream, HumanExecutionStream) else None
        if response.status == "ok" and human_stream is not None and not human_stream.emitted_output:
            click.echo("Execution completed.")
        if response.status == "error":
            rendered = render_response(response, options=options)
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
@json_option
def start(
    project: Path | None,
    session_id: str | None,
    python_executable: Path | None,
    as_json: bool,
) -> None:
    """Start or reuse the project's persistent kernel.

    The interpreter is selected from --python, .venv, VIRTUAL_ENV, or the
    current Python executable. If ipykernel is missing, startup fails with the
    exact install command and tells you to restart cleanly afterward; `start`
    does not modify the environment itself.
    """

    request = StartRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        python_executable=python_executable,
    )
    _emit(application.start(request), as_json=as_json)


@main.command("exec")
@click.argument("code", required=False)
@click.option("-f", "--file", "filepath", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--timeout", default=30.0, show_default=True, type=float)
@click.option(
    "--ensure-started",
    "startup_policy",
    flag_value="always",
    default=None,
    help="Start the target session first if it is not already running.",
)
@click.option(
    "--no-ensure-started",
    "startup_policy",
    flag_value="never",
    help="Do not start the session automatically before execution.",
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
@click.option(
    "--fresh",
    is_flag=True,
    help="Stop and restart the target session before executing.",
)
@click.option("--stdout-only", "output_selector", flag_value="stdout", default=None)
@click.option("--stderr-only", "output_selector", flag_value="stderr")
@click.option("--result-only", "output_selector", flag_value="result")
@click.option(
    "--no-truncate",
    is_flag=True,
    help="Do not truncate stdout, stderr, or result in output.",
)
@project_option
@session_option
@json_option
def exec_cmd(
    code: str | None,
    filepath: Path | None,
    timeout: float,
    startup_policy: StartupPolicy | None,
    background: bool,
    stream: bool,
    fresh: bool,
    output_selector: OutputSelector | None,
    no_truncate: bool,
    project: Path | None,
    session_id: str | None,
    as_json: bool,
) -> None:
    """Execute code in the live kernel.

    Provide code as an argument, with --file, or through stdin. The target
    session starts automatically unless you pass --no-ensure-started, which
    makes missing-session startup fail fast instead. Like a notebook cell, the
    final expression is returned as the execution result, while `print(...)`
    writes to stdout. Quiet file execution can return a compact namespace
    change summary when a script ends in assignments instead of a final
    expression. `--fresh` restarts the whole session process before executing;
    use `reset` when you only want to clear user state in the existing
    process.

    Examples:

      agentnb "1 + 1" --json
      agentnb analysis.py --json
      agentnb --background "long_task()" --json
      agentnb exec --no-ensure-started "1 + 1" --json
      agentnb exec --json <<'PY'
      import pandas as pd
      df = pd.read_csv("tips.csv")
      df.head()
      PY
    """
    if fresh:
        import contextlib

        project_root = resolve_project_root(cwd=Path.cwd(), override=project)
        target_session = session_id or DEFAULT_SESSION_ID
        with contextlib.suppress(Exception):
            runtime.stop(project_root=project_root, session_id=target_session)

    try:
        source = invocations.resolve_exec_source(code=code, filepath=filepath, stdin=sys.stdin)
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
            suggestions=application.advisor.suggestions(
                AdviceContext(
                    command_name="exec",
                    response_status="error",
                    data={},
                    error_code=exc.code,
                    error_name=exc.ename,
                    error_value=exc.evalue,
                )
            ),
        )
        if stream:
            _emit_stream_completion(response, as_json=as_json)
        else:
            _emit(response, as_json=as_json)
        return

    request = ExecRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        code=source.code,
        session_id=session_id,
        timeout_s=timeout,
        source_kind=cast(Any, source.source_kind),
        source_path=source.path,
        invocation=ExecInvocationPolicy.from_cli(
            startup_policy=startup_policy,
            background=background,
            stream=stream,
            output_selector=output_selector,
            no_truncate=no_truncate,
        ),
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
    the namespace gets noisy. This command auto-starts a missing session when
    targeting is unambiguous and waits behind active same-session work.
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
    Dotted and constant-index references such as `df.a` and `payload['items'][0]`
    are supported. This command auto-starts a missing session when targeting
    is unambiguous and waits behind active same-session work.
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
    report includes rebound names and possible stale objects. Only imported
    project-local modules are eligible. Installing a new package does not
    require `reload`, but editing a local module does. This command auto-starts
    a missing session when targeting is unambiguous and waits behind active
    same-session work.
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
    help="Compatibility: wait until the target session is ready. Prefer `agentnb wait`.",
)
@click.option(
    "--wait-idle",
    is_flag=True,
    help="Compatibility: wait until the target session is idle. Prefer `agentnb wait`.",
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
    """Inspect the current kernel state.

    Use `agentnb wait` for the primary blocking readiness path. `status --wait`
    and `status --wait-idle` remain compatibility modes.
    """

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
@click.option(
    "--timeout",
    default=30.0,
    show_default=True,
    type=float,
    help="Maximum seconds to wait for the session to become usable.",
)
@project_option
@session_option
@json_option
def wait(
    timeout: float,
    project: Path | None,
    session_id: str | None,
    as_json: bool,
) -> None:
    """Wait until the target session is usable for the next command.

    If the session is starting, wait until it is ready. If it is busy, wait
    until it is idle. If a background run is still active for the session,
    wait for that run to finish too. If it is already usable, return
    immediately with the current status payload.
    """

    request = WaitRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        timeout_s=timeout,
    )
    _emit(application.wait(request), as_json=as_json)


@main.command()
@click.argument("reference", required=False, callback=_history_reference_callback)
@click.option("--errors", is_flag=True, help="Only show failed executions")
@click.option("--successes", is_flag=True, help="Only show successful executions")
@click.option("--latest", is_flag=True, help="Show only the most recent history entry")
@click.option("--last", type=click.IntRange(min=1), default=None, help="Show the last N entries")
@click.option("--all", "include_internal", is_flag=True, help="Include internal kernel executions")
@click.option("--full", is_flag=True, help="Show full un-truncated code and output for each entry")
@project_option
@session_option
@json_option
def history(
    reference: HistoryReference | None,
    errors: bool,
    successes: bool,
    latest: bool,
    last: int | None,
    include_internal: bool,
    full: bool,
    project: Path | None,
    session_id: str | None,
    as_json: bool,
) -> None:
    """Show recent execution history recorded for the project.

    By default, this shows semantic user-visible steps such as exec, vars,
    inspect, reload, and reset. Pass --all to include internal helper
    executions such as the helper calls behind `vars`, `inspect`, and
    `reload`. Selectors such as `@latest`, `@last-error`, and
    `@last-success` are supported for REFERENCE. History entries are compact
    summaries by default; use --full to see complete stored code and output.
    """

    request = HistoryRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        reference=reference,
        errors=errors,
        successes=successes,
        latest=latest,
        last=last,
        include_internal=include_internal,
        full=full,
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
@json_option
def doctor(
    project: Path | None,
    session_id: str | None,
    python_executable: Path | None,
    as_json: bool,
) -> None:
    """Check interpreter and kernel prerequisites for startup.

    Use this when start fails, the wrong interpreter is selected, or ipykernel
    is missing. Doctor reports one explicit install command when a dependency
    is missing; run that command in your shell, then restart with `--fresh`.
    Doctor does not install packages on your behalf.
    """

    request = DoctorRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        python_executable=python_executable,
    )
    _emit(application.doctor(request), as_json=as_json)


@main.group("sessions", invoke_without_command=True)
@project_option
@json_option
@click.pass_context
def sessions_group(ctx: click.Context, project: Path | None, as_json: bool) -> None:
    """Inspect and manage named sessions for the current project.

    Use `agentnb sessions list` as the canonical listing form. Bare
    `agentnb sessions` remains a supported alias.
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(sessions_list, project=project, as_json=as_json)


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
@click.argument("session_name", required=False, callback=_session_option_callback)
@click.option("--all", "delete_all", is_flag=True, help="Delete all sessions.")
@click.option(
    "--stale",
    "delete_stale",
    is_flag=True,
    help="Delete sessions whose kernel is no longer running.",
)
@project_option
@json_option
def sessions_delete(
    session_name: str | None,
    delete_all: bool,
    delete_stale: bool,
    project: Path | None,
    as_json: bool,
) -> None:
    """Delete one or more sessions and stop their kernels if still running.

    Provide a SESSION_NAME to delete one session, or use --all to delete every
    session, or --stale to delete only sessions whose kernel is dead. `--all`
    stops live kernels before deleting their session records.
    """
    project_root = resolve_project_root(cwd=Path.cwd(), override=project)

    if sum([session_name is not None, delete_all, delete_stale]) != 1:
        raise click.UsageError("Provide exactly one of: SESSION_NAME, --all, or --stale.")

    if session_name is not None:
        request = SessionsDeleteRequest(
            project_root=project_root,
            session_name=session_name,
        )
        _emit(application.sessions_delete(request), as_json=as_json)
        return

    bulk_request = SessionsDeleteBulkRequest(
        project_root=project_root,
        stale_only=delete_stale,
    )
    _emit(application.sessions_delete_bulk(bulk_request), as_json=as_json)


@main.group("runs", invoke_without_command=True)
@click.pass_context
def runs_group(ctx: click.Context) -> None:
    """Inspect persisted execution records for exec and reset commands.

    Bare `agentnb runs` lists recent runs (same as `runs list`).
    """
    if ctx.invoked_subcommand is None:
        ctx.invoke(runs_list)


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

    options = _current_render_options(local_as_json=as_json)
    if last is None and not options.as_json:
        last = 20
    request = RunsListRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        session_id=session_id,
        errors=errors,
        last=last,
    )
    _emit(application.runs_list(request), as_json=as_json)


@runs_group.command("show")
@click.argument("run_reference", required=False, callback=_run_reference_callback)
@project_option
@json_option
def runs_show(run_reference: RunReference | None, project: Path | None, as_json: bool) -> None:
    """Show a persisted snapshot of one exec/reset run.

    Omit RUN_REFERENCE to inspect the latest relevant run. Selectors such as
    @latest, @last-error, @last-success, and @active are also supported. When
    omitted, the default prefers the current session's latest relevant run,
    then falls back to the project latest.
    """

    request = RunLookupRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        run_reference=run_reference,
    )
    _emit(application.runs_show(request), as_json=as_json)


@runs_group.command("wait")
@click.argument("run_reference", required=False, callback=_run_reference_callback)
@click.option("--timeout", default=30.0, show_default=True, type=float)
@project_option
@json_option
def runs_wait(
    run_reference: RunReference | None,
    timeout: float,
    project: Path | None,
    as_json: bool,
) -> None:
    """Wait for one background run to finish and return its final snapshot.

    Omit RUN_REFERENCE to wait for the active relevant run when there is a safe
    default.
    """

    request = RunsWaitRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        run_reference=run_reference,
        timeout_s=timeout,
    )
    _emit(application.runs_wait(request), as_json=as_json)


@runs_group.command("follow")
@click.argument("run_reference", required=False, callback=_run_reference_callback)
@click.option("--timeout", default=30.0, show_default=True, type=float)
@click.option("--tail", is_flag=True, help="Skip historical events and only stream new ones.")
@project_option
@json_option
def runs_follow(
    run_reference: RunReference | None,
    timeout: float,
    tail: bool,
    project: Path | None,
    as_json: bool,
) -> None:
    """Replay and stream events for one persisted run until it finishes.

    Omit RUN_REFERENCE to follow the active relevant run when there is a safe
    default. Use --tail to skip historical events and only stream new ones.
    """
    project_root = resolve_project_root(cwd=Path.cwd(), override=project)
    options = _current_render_options(local_as_json=as_json)
    stream: ExecutionSink = JsonExecutionStream() if options.as_json else HumanExecutionStream()
    request = RunsFollowRequest(
        project_root=project_root,
        run_reference=run_reference,
        timeout_s=timeout,
        tail=tail,
    )
    response = application.runs_follow(request, event_sink=stream)
    if response.error is not None and response.error.code == "TIMEOUT":
        if options.as_json:
            _emit_json_stream_frame(
                {
                    "type": "final",
                    "response": projector.project(response, profile=options.profile.value),
                }
            )
        else:
            click.echo("Following stopped (timeout).")
        raise click.exceptions.Exit(2)
    _emit_stream_completion(response, as_json=as_json, stream=stream)


@runs_group.command("cancel")
@click.argument("run_reference", required=False, callback=_run_reference_callback)
@project_option
@json_option
def runs_cancel(run_reference: RunReference | None, project: Path | None, as_json: bool) -> None:
    """Cancel one running background run and report what happened to the session.

    Omit RUN_REFERENCE to cancel the active relevant run when there is a safe
    default.
    """

    request = RunsCancelRequest(
        project_root=resolve_project_root(cwd=Path.cwd(), override=project),
        run_reference=run_reference,
    )
    _emit(application.runs_cancel(request), as_json=as_json)


@main.command("_background-run", hidden=True)
@click.argument("execution_id")
@project_option
def background_run(execution_id: str, project: Path | None) -> None:
    """Internal helper to execute one persisted background run."""

    project_root = resolve_project_root(cwd=Path.cwd(), override=project)
    executions.complete_background_run(project_root=project_root, execution_id=execution_id)


def _strip_json_suffix(suggestions: list[str]) -> list[str]:
    return [s.replace(" --json", "").replace("--json ", "") for s in suggestions]


def _current_render_options(*, local_as_json: bool) -> RenderOptions:
    ctx = click.get_current_context(silent=True)
    root_options = ctx.find_root().obj if ctx is not None else None
    options = root_options if isinstance(root_options, RenderOptions) else RenderOptions()
    if local_as_json:
        options = options.with_local_json()
    return options


if __name__ == "__main__":
    main()
