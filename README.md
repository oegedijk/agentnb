# agentnb

A persistent project-scoped Python REPL for coding agents, exposed through a simple CLI.

> Status: alpha. Expect rough edges and breaking changes. Use in local
> development workflows at your own risk.

## Why

Agents can run shell commands, but they lose state when using one-off `python -c` and script invocations. `agentnb` gives agents a long-running IPython kernel they can drive with CLI commands, so they can explore incrementally, keep expensive setup in memory, inspect live variables, and recover without restarting from scratch on every step.

The right mental model is a persistent REPL for agents, or an append-only notebook without a notebook UI. `agentnb` keeps execution state and history, but it does not edit notebook cells or manage `.ipynb` files.

Module reloading is explicit. `agentnb` does not auto-reload edited modules on
every execution; use `agentnb reload` after changing project-local code.

`exec` follows normal IPython/Notebook semantics for output: if the final line
of a snippet is an expression, its value is returned as the execution result.
`print(...)` goes to `stdout`; a bare final expression goes to `result`.

One session should be driven serially. Do not send multiple commands to the
same project kernel at once; wait for one command to finish before sending the
next.

## Install

```bash
uv add agentnb
# or
pip install agentnb
```

## Quick Start

```bash
agentnb exec --project /path/to/project --ensure-started --json "from myapp.models import User"
agentnb exec --file analysis.py --json
agentnb vars --json
agentnb inspect User --json
agentnb sessions list --json
agentnb stop --json
```

For multiline code, prefer `--file` or stdin/heredoc:

```bash
agentnb exec --json <<'PY'
import pandas as pd
df = pd.read_csv("tips.csv")
df.head()
PY
```

That applies to background work too. Avoid passing literal `\n` escapes inside a
single shell argument for multiline snippets; use `--file` or stdin/heredoc.

For lower-noise agent integrations, you can set defaults once per shell:

```bash
export AGENTNB_FORMAT=agent
```

That enables JSON output and suppresses suggestions across commands. You can also use
top-level flags such as `agentnb --agent ...`, `agentnb --json ...`,
`agentnb --no-suggestions ...`, and `agentnb --quiet ...`.

In `--agent` mode, default payloads are compacted to reduce token usage:
trimmed error tracebacks, compact history entries, compact dataframe previews,
and structural summaries for common containers such as `list` and `dict`.

You can place top-level flags such as `--agent` and `--json` before or after
the subcommand, for example `agentnb --agent status` or `agentnb status --agent`.

## Recommended Workflow

The normal agent loop is:

1. `agentnb exec --ensure-started "..." --json` for short snippets
2. `agentnb status --wait-idle --json` when you need to know the session is safe for the next command
3. `agentnb exec --file analysis.py --json` or pipe code through stdin for multiline work
4. `agentnb vars --json`
5. `agentnb inspect NAME --json`
6. `agentnb reload --json` after editing project-local modules
7. `agentnb reload myapp.module --json` to target one imported module
8. `agentnb history --json`
9. `agentnb runs list --json` when you need durable execution records
10. `agentnb runs follow EXECUTION_ID --json` when you need live background progress

Important:
- Drive one session serially: wait for each command to finish before sending the next.
- Prefer a final expression over `print(...)` when you want a compact `result` payload.
- Use `vars --recent N` or `vars --match TEXT` once the namespace gets noisy.
- Once more than one live session exists, pass `--session NAME` on kernel-bound commands.

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
- top-level flags: `agentnb [--json] [--agent] [--quiet] [--no-suggestions] <command>`
- `agentnb status [--project PATH] [--session NAME] [--wait|--wait-idle] [--timeout SECONDS]`
- `agentnb exec [CODE] [-f FILE] [--timeout SECONDS] [--ensure-started] [--background|--stream] [--stdout-only|--stderr-only|--result-only] [--project PATH] [--session NAME] [--json]`
- `agentnb vars [--project PATH] [--session NAME] [--json] [--types|--no-types] [--match TEXT] [--recent N]`
- `agentnb inspect NAME [--project PATH] [--session NAME] [--json]`
- `agentnb reload [MODULE] [--project PATH] [--session NAME] [--json]`
- `agentnb history [--project PATH] [--session NAME] [--errors] [--latest|--last N] [--all] [--json]`
- `agentnb runs list [--project PATH] [--session NAME] [--errors] [--json]`
- `agentnb runs show EXECUTION_ID [--project PATH] [--json]`
- `agentnb runs follow EXECUTION_ID [--project PATH] [--timeout SECONDS] [--json]`
- `agentnb runs wait EXECUTION_ID [--project PATH] [--timeout SECONDS] [--json]`
- `agentnb runs cancel EXECUTION_ID [--project PATH] [--json]`
- `agentnb sessions list [--project PATH] [--json]`
- `agentnb sessions delete NAME [--project PATH] [--json]`
- `agentnb interrupt [--project PATH] [--session NAME] [--json]`
- `agentnb reset [--project PATH] [--session NAME] [--json]`
- `agentnb stop [--project PATH] [--session NAME] [--json]`
- `agentnb doctor [--project PATH] [--python PATH] [--fix] [--json]`

Notes:
- `vars` includes type information by default.
- `vars --recent N` shows the newest matching variables; `vars --match TEXT` filters by name.
- `vars` hides imported helper routines and classes, and summarizes common containers compactly.
- `history` shows semantic user-visible steps by default such as `exec`, `vars`, `inspect`, `reload`, and `reset`.
- Use `history --all` to include internal helper executions sent to the kernel.
- `runs` exposes durable execution records keyed by `execution_id`; use it for background work and exact run lookup.
- `exec --background` returns immediately with an `execution_id`; use `runs show` for the latest persisted snapshot, `runs follow` for live progress, `runs wait` for the final snapshot, and `runs cancel` to stop the run.
- When multiple live sessions exist, kernel-bound commands require `--session NAME` unless there is only one live session to infer.
- Module reloading is explicit. `reload MODULE` reloads one imported project-local module.
- Bare `reload` reloads all currently imported project-local modules and reports rebound names and possible stale objects.
- If reload reports stale objects, recreate them or run `agentnb reset` when stale state is widespread.
- `inspect` gives compact previews for pandas-like dataframes and for common `list`/`dict` API payloads.

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

Command-level success and execution success now align: if `exec` or `reset`
fails in the kernel, the top-level response has `"status": "error"` and the
command exits non-zero. The execution payload is still included in `data`.
Default JSON is intentionally compact for agent use: large event lists are
omitted, tracebacks are trimmed, and inspection/history payloads prefer short
previews over raw internal detail.

For agents, the usual pattern is:
- short `exec`
- inspect the returned `result`
- narrow further with another short `exec`
- use `vars --recent` or `inspect NAME` only when needed

If you want that behavior by default, set `AGENTNB_FORMAT=json` or `AGENTNB_FORMAT=agent`.
`agent` also suppresses suggestions and enables quiet mode.

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
    "execution_id": "run_123",
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

- `SessionStore`: project/session metadata and stale cleanup
- `ExecutionStore`: append-only JSONL run records keyed by `execution_id`
- `ExecutionService`: foreground/background execution lifecycle and run queries
- `CommandJournal`: unified read path over semantic history and persisted execution records
- `HistoryStore`: typed JSONL history records for semantic and internal execution history
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

## 0.1.1 Ergonomics

- top-level `--agent`, `--json`, `--quiet`, and `--no-suggestions` flags
- `AGENTNB_FORMAT`, `AGENTNB_NO_SUGGESTIONS`, and `AGENTNB_QUIET` environment defaults
- `exec --stdout-only`, `--stderr-only`, and `--result-only` for script-friendly capture
- `history --latest` and `history --last N` shortcuts

## Current Ergonomics

- multi-session targeting with `--session`, plus `sessions list` and `sessions delete`
- `exec --ensure-started`, `status --wait`, and `status --wait-idle` for first-use/startup and session-idleness flows
- persisted run records with `execution_id`
- `runs list`, `runs show`, `runs follow`, `runs wait`, and `runs cancel` for durable execution control
- `exec` accepts short inline code, `--file`, or stdin/heredoc for multiline snippets
- `exec --stream` for foreground live event delivery on the same execution model
- `vars` includes type information by default
- `vars` hides imported helper routines and classes and summarizes common containers compactly
- `inspect` gives compact previews for pandas-like objects and common `list`/`dict` payloads
- `history` defaults to semantic user-visible steps, with `--all` for internal kernel executions
- `--agent` returns compact JSON by default to reduce token usage during iterative workflows

## License

MIT
