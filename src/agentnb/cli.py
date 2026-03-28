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
from .errors import AgentNBException, ErrorContext
from .execution import ExecutionService
from .execution_invocation import ExecInvocationPolicy, OutputSelector, StartupPolicy
from .introspection import KernelIntrospection
from .invocation import ROOT_OPTION_SPECS, InvocationResolver
from .output import RenderOptions, projector, render_response, render_stream_completion
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
introspection = KernelIntrospection(runtime, session_access=executions)
application = AgentNBApp(
    runtime=runtime,
    executions=executions,
    introspection=introspection,
)
invocations = InvocationResolver()

HELP_COMMAND_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Run Code", ("exec",)),
    ("Read And Inspect", ("vars", "inspect", "reload", "history")),
    ("Control Session", ("wait", "status", "interrupt", "reset", "start", "stop", "doctor")),
    ("Background Runs", ("runs",)),
    ("Sessions", ("sessions",)),
)


class AgentGroup(click.Group):
    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        command = super().get_command(ctx, cmd_name)
        if command is not None:
            return command
        advice = invocations.unknown_command_advice(cmd_name)
        if advice is not None:
            raise click.UsageError(advice.message(cmd_name))
        return None

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
    context_settings={"help_option_names": ["--help", "-h"], "max_content_width": 100},
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


def _request_kwargs(project: Path | None) -> dict[str, Path]:
    return {
        "project_root": resolve_project_root(cwd=Path.cwd(), override=project),
        **({"project_override": project} if project is not None else {}),
    }


def session_option(func):
    return click.option(
        "--session",
        "session_id",
        default=None,
        callback=_session_option_callback,
        help=(
            "Session name. If omitted, read commands use the only live session when "
            "unambiguous; otherwise commands fall back to `default` or require "
            "explicit selection."
        ),
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
    response = _prepare_response_for_rendering(response, options=options)
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
    response = _prepare_response_for_rendering(response, options=options)

    if options.as_json:
        _emit_json_stream_frame(
            {
                "type": "final",
                "response": projector.project(response, profile=options.profile.value),
            }
        )
    else:
        human_stream = stream if isinstance(stream, HumanExecutionStream) else None
        rendered = render_stream_completion(
            response,
            options=options,
            output_emitted=bool(human_stream and human_stream.emitted_output),
        )
        if rendered:
            click.echo(rendered, err=response.status == "error")

    if response.status == "error":
        raise click.exceptions.Exit(1)


def _prepare_response_for_rendering(
    response: CommandResponse,
    *,
    options: RenderOptions,
) -> CommandResponse:
    if response.command == "exec" and response.data.get("selected_output") is not None:
        response = replace(response, suggestions=[])
    if not options.show_suggestions:
        return replace(response, suggestions=[])
    if not options.as_json:
        return replace(response, suggestions=_strip_json_suffix(response.suggestions))
    return response


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
        **_request_kwargs(project),
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
@click.option(
    "--stdout-only",
    "output_selector",
    flag_value="stdout",
    default=None,
    help="Show only stdout from the execution.",
)
@click.option(
    "--stderr-only",
    "output_selector",
    flag_value="stderr",
    help="Show only stderr from the execution.",
)
@click.option(
    "--result-only",
    "output_selector",
    flag_value="result",
    help=(
        "Show only the result channel. Large structured values may still render as "
        "a compact preview rather than the full repr."
    ),
)
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

    \b
    Provide code as an argument, with --file, or through stdin.
    The target session starts automatically unless you pass --no-ensure-started.
    Like a notebook cell, the final expression is returned as the execution result.
    `print(...)` writes to stdout.
    Quiet file execution can return a compact namespace change summary.
    `--fresh` restarts the whole session process before executing.
    Use `reset` when you only want to clear user state in the existing process.

    \b
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
        if filepath is None and code is not None:
            candidate = Path(code)
            resolved_candidate = candidate if candidate.is_absolute() else Path.cwd() / candidate
            if (
                "\n" not in code
                and resolved_candidate.is_file()
                and resolved_candidate.suffix == ".py"
            ):
                raise AgentNBException(
                    code="INVALID_INPUT",
                    message=(
                        "It looks like you passed a Python file path as inline code. "
                        "Use `exec --file PATH` or the top-level `agentnb PATH` form instead."
                    ),
                    error_context=ErrorContext(
                        input_shape="exec_file_path",
                        source_path=str(resolved_candidate.resolve()),
                    ),
                )
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
            data=exc.command_data if exc.command_data is not None else exc.data,
            suggestions=application.advisor.suggestions(
                AdviceContext(
                    command_name="exec",
                    response_status="error",
                    error_context=exc.error_context,
                    error_code=exc.code,
                    error_name=exc.ename,
                    error_value=exc.evalue,
                    command_data=cast(Any, exc.command_data),
                    session_id=session_id,
                    project_override=project_root if project is not None else None,
                )
            ),
            suggestion_actions=application.advisor.suggestion_actions(
                AdviceContext(
                    command_name="exec",
                    response_status="error",
                    error_context=exc.error_context,
                    error_code=exc.code,
                    error_name=exc.ename,
                    error_value=exc.evalue,
                    command_data=cast(Any, exc.command_data),
                    session_id=session_id,
                    project_override=project_root if project is not None else None,
                )
            ),
        )
        if stream:
            _emit_stream_completion(response, as_json=as_json)
        else:
            _emit(response, as_json=as_json)
        return

    request = ExecRequest(
        **_request_kwargs(project),
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
@click.option(
    "--match",
    "match_text",
    default=None,
    help="Only show variables whose names contain this substring",
)
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
    container values are summarized compactly. `--match` uses case-insensitive
    substring matching. Use --recent or --match when the namespace gets noisy.
    This command auto-starts a missing session when targeting is unambiguous
    and waits behind active same-session work.
    """

    request = VarsRequest(
        **_request_kwargs(project),
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
        **_request_kwargs(project),
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
        **_request_kwargs(project),
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
        **_request_kwargs(project),
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
        **_request_kwargs(project),
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

    \b
    By default, this shows semantic user-visible steps such as exec, vars, inspect, reload,
    and reset.
    Pass --all to include internal helper executions such as the helper calls behind `vars`,
    `inspect`, and `reload`.

    \b
    Selectors for REFERENCE: `@latest`, `@last-error`, `@last-success`

    \b
    History entries are compact summaries by default.
    Use --full to see complete stored code and output.
    """

    request = HistoryRequest(
        **_request_kwargs(project),
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
        **_request_kwargs(project),
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
        **_request_kwargs(project),
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
        **_request_kwargs(project),
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
        **_request_kwargs(project),
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

    request = SessionsListRequest(**_request_kwargs(project))
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
            **({"project_override": project} if project is not None else {}),
            session_name=session_name,
        )
        _emit(application.sessions_delete(request), as_json=as_json)
        return

    bulk_request = SessionsDeleteBulkRequest(
        project_root=project_root,
        **({"project_override": project} if project is not None else {}),
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
        **_request_kwargs(project),
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
        **_request_kwargs(project),
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
        **_request_kwargs(project),
        run_reference=run_reference,
        timeout_s=timeout,
    )
    _emit(application.runs_wait(request), as_json=as_json)


@runs_group.command("follow")
@click.argument("run_reference", required=False, callback=_run_reference_callback)
@click.option("--timeout", default=30.0, show_default=True, type=float)
@click.option(
    "--tail",
    is_flag=True,
    help="Compatibility alias. `runs follow` already streams only new events.",
)
@project_option
@json_option
def runs_follow(
    run_reference: RunReference | None,
    timeout: float,
    tail: bool,
    project: Path | None,
    as_json: bool,
) -> None:
    """Stream new events for one persisted run until it finishes.

    Omit RUN_REFERENCE to follow the active relevant run when there is a safe
    default. Historical output is available from `runs show`; `runs follow`
    streams only unseen events. `--tail` remains as a compatibility alias.
    """
    project_root = resolve_project_root(cwd=Path.cwd(), override=project)
    options = _current_render_options(local_as_json=as_json)
    stream: ExecutionSink = JsonExecutionStream() if options.as_json else HumanExecutionStream()
    request = RunsFollowRequest(
        project_root=project_root,
        **({"project_override": project} if project is not None else {}),
        run_reference=run_reference,
        timeout_s=timeout,
        tail=tail,
    )
    response = application.runs_follow(request, event_sink=stream)
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
        **_request_kwargs(project),
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


def _append_help_text(command: click.Command, extra: str) -> None:
    base = (command.help or "").rstrip()
    command.help = f"{base}\n\n{extra}".strip()


_CLEANUP_PRIMITIVE_COMPARISON = (
    "\b\n"
    "Cleanup primitives:\n"
    "  reset: clear user variables in the current process.\n"
    "  exec --fresh: stop and restart the session, then execute code.\n"
    "  stop: shut the session down without executing anything."
)

_HISTORY_ALL_GUIDE = (
    "\b\n"
    "`history` shows semantic user-visible steps by default.\n"
    "Use `--all` when you need helper/provenance entries such as the internal calls\n"
    "behind `vars`, `inspect`, or `reload`."
)

_SESSIONS_VISIBILITY_GUIDE = (
    "Normal listing shows live sessions only. Non-live session records remain on disk "
    "until you remove them explicitly with `agentnb sessions delete --stale`."
)

_RUNS_FOLLOW_WINDOW_GUIDE = (
    "\b\n"
    "`--timeout` bounds the observation window for `runs follow`.\n"
    "If the window ends before the run finishes, agentnb returns the latest snapshot\n"
    "instead of failing with a timeout error."
)

_append_help_text(main, _CLEANUP_PRIMITIVE_COMPARISON)
_append_help_text(exec_cmd, _CLEANUP_PRIMITIVE_COMPARISON)
_append_help_text(reset, _CLEANUP_PRIMITIVE_COMPARISON)
_append_help_text(stop, _CLEANUP_PRIMITIVE_COMPARISON)
_append_help_text(history, _HISTORY_ALL_GUIDE)
_append_help_text(sessions_list, _SESSIONS_VISIBILITY_GUIDE)
_append_help_text(sessions_delete, _SESSIONS_VISIBILITY_GUIDE)
_append_help_text(runs_follow, _RUNS_FOLLOW_WINDOW_GUIDE)


if __name__ == "__main__":
    main()
