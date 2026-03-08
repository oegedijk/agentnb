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


@click.group()
def main() -> None:
    """agentnb - A persistent Python notebook for AI agents."""


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
        )

    _emit(response, as_json=as_json)


@main.command()
@project_option
@python_option
@click.option("--auto-install/--no-auto-install", default=True, show_default=True)
@json_option
def start(
    project: Path | None,
    python_executable: Path | None,
    auto_install: bool,
    as_json: bool,
) -> None:
    """Start a kernel for the project."""

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
    """Execute code in the running kernel."""
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
    """List variables in the kernel namespace."""

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
    """Inspect a variable in the kernel namespace."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        payload = ops.inspect_var(project_root=project_root, session_id=session_id, name=name)
        return {"inspect": payload}

    _execute_command("inspect", project, as_json, handler)


@main.command("reload")
@click.argument("module")
@project_option
@json_option
def reload_cmd(module: str, project: Path | None, as_json: bool) -> None:
    """Reload a module (importlib.reload)."""

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
    """Check if the kernel is running."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        return runtime.status(project_root=project_root, session_id=session_id).to_dict()

    _execute_command("status", project, as_json, handler)


@main.command()
@click.option("--errors", is_flag=True, help="Only show failed executions")
@project_option
@json_option
def history(errors: bool, project: Path | None, as_json: bool) -> None:
    """Show execution history."""

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
    """Interrupt the currently running code."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        runtime.interrupt(project_root=project_root, session_id=session_id)
        return {"interrupted": True}

    _execute_command("interrupt", project, as_json, handler)


@main.command()
@click.option("--timeout", default=10.0, show_default=True, type=float)
@project_option
@json_option
def reset(timeout: float, project: Path | None, as_json: bool) -> None:
    """Clear the namespace but keep the kernel alive."""

    def handler(project_root: Path, session_id: str) -> dict[str, object]:
        result = runtime.reset(project_root=project_root, session_id=session_id, timeout_s=timeout)
        return result.to_dict()

    _execute_command("reset", project, as_json, handler)


@main.command()
@project_option
@json_option
def stop(project: Path | None, as_json: bool) -> None:
    """Shut down the kernel."""

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
    """Run environment diagnostics for kernel startup."""

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
