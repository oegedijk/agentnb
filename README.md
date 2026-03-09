# agentnb

A persistent project-scoped Python REPL for coding agents, exposed through a simple CLI.

## Why

Agents can run shell commands, but they lose state when using one-off `python -c` and script invocations. `agentnb` gives agents a long-running IPython kernel they can drive with CLI commands, so they can explore incrementally, keep expensive setup in memory, inspect live variables, and recover without restarting from scratch on every step.

The right mental model is a persistent REPL for agents, or an append-only notebook without a notebook UI. `agentnb` keeps execution state and history, but it does not edit notebook cells or manage `.ipynb` files.

## Install

```bash
uv add agentnb
# or
pip install agentnb
```

## Quick Start

```bash
agentnb start --project /path/to/project --json
agentnb exec "from myapp.models import User" --json
agentnb exec "u = User(name='test'); print(u)" --json
agentnb vars --json
agentnb stop --json
```

## Recommended Workflow

The normal agent loop is:

1. `agentnb start --json`
2. `agentnb exec "..." --json`
3. `agentnb vars --json`
4. `agentnb inspect NAME --json`
5. `agentnb reload MODULE --json` after source edits
6. `agentnb history --json`

Use `agentnb doctor --json` if startup fails, `agentnb interrupt --json` if execution hangs, and `agentnb reset --json` if the namespace needs a clean slate.

If startup reports that `ipykernel` is missing, rerun `agentnb start` with
`--auto-install` or use `agentnb doctor --fix --json`.

Running `agentnb` with no arguments, or `agentnb --help`, prints an agent-oriented command guide and workflow summary.

## Positioning

`agentnb` is optimized for stateful agent iteration inside a project:
- a persistent REPL the agent can keep using across steps
- a lightweight append-only notebook model backed by execution history
- module reload and variable inspection without a notebook editor

It is not a notebook editing tool:
- it does not edit cells
- it does not write `.ipynb` files
- it does not synchronize with JupyterLab

## Commands

- `agentnb start [--project PATH] [--python PATH] [--auto-install]`
- `agentnb status [--project PATH]`
- `agentnb exec [CODE] [-f FILE] [--timeout SECONDS] [--project PATH] [--json]`
- `agentnb vars [--project PATH] [--json] [--types]`
- `agentnb inspect NAME [--project PATH] [--json]`
- `agentnb reload MODULE [--project PATH] [--json]`
- `agentnb history [--project PATH] [--errors] [--json]`
- `agentnb interrupt [--project PATH] [--json]`
- `agentnb reset [--project PATH] [--json]`
- `agentnb stop [--project PATH] [--json]`
- `agentnb doctor [--project PATH] [--python PATH] [--fix] [--json]`

## Out-of-the-box startup

On `agentnb start`, the runtime selects an interpreter in this order:

1. `--python` interpreter
2. `<project>/.venv` interpreter
3. active `VIRTUAL_ENV` interpreter
4. current Python executable

If `ipykernel` is missing for the selected interpreter, `agentnb start` fails
with the exact install command. Pass `--auto-install` to let `agentnb` install
it for you, or use `agentnb doctor --fix --json`.

## JSON Mode

Pass `--json` to emit a stable machine-readable envelope. This is the preferred mode for agent integrations.

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "command": "exec",
  "project": "/path/to/project",
  "session_id": "default",
  "timestamp": "2026-03-08T21:00:00+00:00",
  "data": {
    "status": "ok",
    "stdout": "",
    "stderr": "",
    "result": "42",
    "execution_count": 1,
    "duration_ms": 12
  },
  "suggestions": [
    "Run `agentnb vars --json` to inspect the updated namespace.",
    "Run `agentnb inspect NAME --json` to inspect a specific variable.",
    "Run `agentnb history --json` to review prior executions."
  ],
  "error": null
}
```

## How It Works

`agentnb` starts an IPython kernel process and stores connection/session metadata under `.agentnb/` in the target project. CLI commands connect via Jupyter messaging protocol.

## Architecture

- `SessionStore`: project/session metadata, stale cleanup, history
- `KernelRuntime`: lifecycle + execution API
- `RuntimeBackend`: backend interface, with local IPython backend for v0.1
- `NotebookOps`: vars/inspect/reload semantic operations
- `OutputContract`: human + JSON output from one response envelope
- `Hooks`: no-op extension points for future policy/plugins/telemetry

## Development

```bash
uv sync --extra dev
uv run ruff check src tests
uv run ruff format --check src tests
uv run ty check src
uv run pytest
```

## License

MIT
