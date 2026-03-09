from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

import click

from .contracts import CommandResponse, error_response, success_response
from .errors import AgentNBException, InvalidInputError
from .ops import NotebookOps
from .output import render_response
from .runtime import KernelRuntime
from .session import DEFAULT_SESSION_ID, resolve_project_root

runtime = KernelRuntime()
ops = NotebookOps(runtime)


@click.group(
    invoke_without_command=True,
    context_settings={"help_option_names": ["--help", "-h"]},
)
@click.pass_context
def main(ctx: click.Context) -> None:
    """Persistent project-scoped Python state for agent workflows.

    Start a long-running kernel for the current project, execute code against it,
    inspect live variables, and recover without losing all state on every step.

    Recommended loop:

      1. agentnb start --json
      2. agentnb exec "from myapp import thing" --json
      3. agentnb vars --json
      4. agentnb inspect thing --json
      5. agentnb reload myapp.module --json
      6. agentnb history --json

    Prefer --json for agent integrations and machine-readable parsing.
    Startup does not install ipykernel unless you pass --auto-install or use
    agentnb doctor --fix.
    """
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


def python_option(func: Callable[..., object]) -> Callable[..., object]:
    return click.option(
        "--python",
        "python_executable",
        type=click.Path(path_type=Path, dir_okay=False),
        default=None,
        help="Python interpreter for the kernel",
    )(func)


def _emit(response: CommandResponse, *, as_json: bool) -> None:
    click.echo(render_response(response, as_json=as_json))
    if response.status == "error":
        raise click.exceptions.Exit(1)


def _suggestions(command_name: str, response_status: str, data: dict[str, object]) -> list[str]:
    if command_name == "start":
        return [
            'Run `agentnb exec "..." --json` to execute code in the live kernel.',
            "Run `agentnb vars --json` to inspect the current namespace.",
            "Run `agentnb status --json` to confirm the kernel is still alive.",
        ]
    if command_name == "status":
        if data.get("alive"):
            return [
                'Run `agentnb exec "..." --json` to execute code.',
                "Run `agentnb vars --json` to inspect current variables.",
                "Run `agentnb stop --json` when the session is no longer needed.",
            ]
        return [
            "Run `agentnb start --json` to start a project-scoped kernel.",
            "Run `agentnb doctor --json` if startup has been failing.",
        ]
    if command_name == "exec":
        if response_status == "ok":
            return [
                "Run `agentnb vars --json` to inspect the updated namespace.",
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
            'Run `agentnb exec "..." --json` to add or modify live state.',
        ]
    if command_name == "inspect":
        return [
            "Run `agentnb vars --json` to inspect more of the namespace.",
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
    return []


def _execute_command(
    command_name: str,
    project: Path | None,
    as_json: bool,
    handler: Callable[[Path, str], dict[str, object]],
) -> None:
    project_root = resolve_project_root(cwd=Path.cwd(), override=project)
    session_id = DEFAULT_SESSION_ID

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
            traceback=exc.traceback,
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
@python_option
@click.option(
    "--auto-install",
    is_flag=True,
    help="Install ipykernel into the selected interpreter if it is missing.",
)
@json_option
def start(
    project: Path | None,
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

    _execute_command("start", project, as_json, handler)


@main.command("exec")
@click.argument("code", required=False)
@click.option("-f", "--file", "filepath", type=click.Path(path_type=Path, dir_okay=False))
@click.option("--timeout", default=30.0, show_default=True, type=float)
@project_option
@json_option
def exec_cmd(
    code: str | None,
    filepath: Path | None,
    timeout: float,
    project: Path | None,
    as_json: bool,
) -> None:
    """Execute code in the live kernel.

    Provide code as an argument, with --file, or through stdin. The kernel must
    already be running for the target project.
    """
    try:
        source = _resolve_code_input(code=code, filepath=filepath)
    except AgentNBException as exc:
        project_root = resolve_project_root(cwd=Path.cwd(), override=project)
        response = error_response(
            command="exec",
            project=str(project_root),
            session_id=DEFAULT_SESSION_ID,
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
        result = runtime.execute(
            project_root=project_root,
            session_id=session_id,
            code=source,
            timeout_s=timeout,
        )
        return result.to_dict()

    _execute_command("exec", project, as_json, handler)


@main.command("vars")
@click.option("--types", "include_types", is_flag=True, help="Include type information")
@project_option
@json_option
def vars_cmd(project: Path | None, as_json: bool, include_types: bool) -> None:
    """List user variables currently defined in the kernel namespace."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        values = ops.list_vars(project_root=project_root, session_id=session_id)
        if not include_types:
            values = [{"name": item["name"], "repr": item["repr"]} for item in values]
        return {"vars": values}

    _execute_command("vars", project, as_json, handler)


@main.command("inspect")
@click.argument("name")
@project_option
@json_option
def inspect_cmd(name: str, project: Path | None, as_json: bool) -> None:
    """Inspect one variable in the kernel namespace."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        payload = ops.inspect_var(project_root=project_root, session_id=session_id, name=name)
        return {"inspect": payload}

    _execute_command("inspect", project, as_json, handler)


@main.command("reload")
@click.argument("module")
@project_option
@json_option
def reload_cmd(module: str, project: Path | None, as_json: bool) -> None:
    """Reload an imported module with importlib.reload."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        payload = ops.reload_module(
            project_root=project_root, session_id=session_id, module_name=module
        )
        return payload

    _execute_command("reload", project, as_json, handler)


@main.command()
@project_option
@json_option
def status(project: Path | None, as_json: bool) -> None:
    """Check whether the project's kernel is currently running."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        return runtime.status(project_root=project_root, session_id=session_id).to_dict()

    _execute_command("status", project, as_json, handler)


@main.command()
@click.option("--errors", is_flag=True, help="Only show failed executions")
@project_option
@json_option
def history(errors: bool, project: Path | None, as_json: bool) -> None:
    """Show recent execution history recorded for the project."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        entries = runtime.history(
            project_root=project_root, session_id=session_id, errors_only=errors
        )
        return {"entries": entries}

    _execute_command("history", project, as_json, handler)


@main.command()
@project_option
@json_option
def interrupt(project: Path | None, as_json: bool) -> None:
    """Interrupt the currently running execution without stopping the kernel."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        runtime.interrupt(project_root=project_root, session_id=session_id)
        return {"interrupted": True}

    _execute_command("interrupt", project, as_json, handler)


@main.command()
@click.option("--timeout", default=10.0, show_default=True, type=float)
@project_option
@json_option
def reset(timeout: float, project: Path | None, as_json: bool) -> None:
    """Clear user state from the kernel while keeping the process alive."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        result = runtime.reset(project_root=project_root, session_id=session_id, timeout_s=timeout)
        return result.to_dict()

    _execute_command("reset", project, as_json, handler)


@main.command()
@project_option
@json_option
def stop(project: Path | None, as_json: bool) -> None:
    """Shut down the project's kernel and clear the saved session metadata."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        runtime.stop(project_root=project_root, session_id=session_id)
        return {"stopped": True}

    _execute_command("stop", project, as_json, handler)


@main.command()
@project_option
@python_option
@click.option("--fix", is_flag=True, help="Attempt to auto-fix issues when possible")
@json_option
def doctor(
    project: Path | None,
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

    _execute_command("doctor", project, as_json, handler)


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
