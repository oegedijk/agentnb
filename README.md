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

## Running Code

Run from the target project root when possible. The cheapest path is the
implicit top-level execution form:

```bash
agentnb "from myapp.models import User"
agentnb "User.query.limit(5)"
```

For multiline code or code containing braces, quotes, or special shell
characters, prefer stdin/heredoc over inline strings:

```bash
agentnb <<'PY'
import pandas as pd
df = pd.read_csv("tips.csv")
df.describe()
PY
```

You can also run a script directly, and then keep the state in session so you can poke around:

```bash
agentnb analysis.py
agentnb "print(final_result)"
```

If a file ends in assignments instead of a final expression, `agentnb` reports
a compact namespace-change summary so you can see what became available
without an immediate follow-up `vars`.

Canonical grammar is:

```bash
agentnb <command> [subcommand] [options]
```

For subcommands, put `--project` after the command name. Session-scoped
subcommands also accept `--session` there, but execution-id `runs`
subcommands (`show`, `wait`, `follow`, `cancel`) are project-scoped and do
not accept `--session`:

```bash
agentnb --session myenv "df.head()"     # prefix works for inline exec
agentnb --background "long_task()"
agentnb history --session myenv
agentnb runs list --session myenv
agentnb vars --project /path/to/project
agentnb runs show RUN_ID --project /path/to/project
```

The default execution timeout is 30 seconds. Use `--timeout` for long-running
code:

```bash
agentnb --timeout 120 "train_model()"
```

Use `--stream` to see execution output in real time:

```bash
agentnb --stream "train_model(epochs=10)"
```

Use `--fresh` to stop and restart the session before executing, ensuring a
clean slate:

```bash
agentnb --fresh "from myapp import run; run()"
```

Use `--no-truncate` to get full stdout/stderr/result output in `--agent` mode
instead of the default compact truncation:

```bash
agentnb --agent --no-truncate "print(large_output)"
```

## Reading Results And Inspecting State

`agentnb` follows normal IPython/Notebook behavior: a final expression becomes the
execution `result`, while `print(...)` goes to `stdout`.


Human-oriented output:

```text
$ agentnb "x = 40 + 2; x"
42

$ agentnb "print('hello')"
hello
```

Use `vars` to see the current namespace and `inspect` to drill into one value:

```bash
agentnb vars
agentnb vars --recent 5
agentnb vars --match rows
agentnb inspect df
agentnb inspect "payload['items'][0]"
```

## Reloading Local Imports

`agentnb` does not auto-reload edited modules by default. But after changing
project-local code on disk, you can reload explicitly:

```bash
agentnb reload
agentnb reload myapp.models
```

Bare `reload` reloads imported project-local modules and reports rebound names
and possible stale objects. `reload MODULE` targets one imported module. This allows you to 
try out local functions as you improve them and reload them. 


## Background Runs And History

Use `wait` as the primary blocking readiness command:

```bash
agentnb wait
```

`status --wait` and `status --wait-idle` remain available as compatibility
forms:

```bash
agentnb status --wait         # wait until the session is ready
agentnb status --wait-idle    # compatibility form; prefer `agentnb wait`
```

Use `--background` when you want to start work and come back to it later:

```bash
agentnb --background "long_task()"
```

That command returns quickly with an `execution_id`. `agentnb` also records a
durable run record for that execution, so you can look it up again even after
the original command has finished printing output. After background launch,
prefer explicit `execution_id` follow-up:

```bash
agentnb runs wait RUN_ID
agentnb runs show RUN_ID
agentnb runs follow RUN_ID
agentnb runs cancel RUN_ID
```

Use `runs` when the question is about one specific execution:
- "What is the latest stored state of this run?"
- "Show me live progress from the active run."
- "Wait until that run finishes."
- "Cancel the active run."

The common `runs` commands are:

```bash
agentnb runs show
agentnb runs follow
agentnb runs wait
agentnb runs cancel @active
```

How to read them:
- `runs show` returns the latest persisted snapshot for a run
- `runs follow` replays all output so far, then streams new events until done; use `--tail` to skip history and only stream new events
- `runs wait` blocks until a run finishes and returns its final state
- `runs cancel` requests cancellation for an active run

Filter the runs list with `--last N` and `--errors`:

```bash
agentnb runs list --last 5
agentnb runs list --errors
```

By default, omitted run references mean "use the obvious next run":
- `runs show` falls back to the current session's latest run, then the project latest
- `runs follow`, `runs wait`, and `runs cancel` only auto-target an active run

Use an explicit id or selector when you want an exact lookup instead of the
cheap default:

```bash
agentnb runs show run_123
agentnb runs wait @latest
agentnb runs show @last-success
agentnb runs show @last-error
```

Selectors supported by `runs` are `@latest`, `@active`, `@last-error`, and
`@last-success`.

`history` is related, but it answers a different question.

Use `history` when you want the semantic transcript of what you asked `agentnb`
to do, not the low-level run record for one execution. It includes user-visible
steps such as `exec`, `vars`, `inspect`, `reload`, and `reset`:

```bash
agentnb history
agentnb history --last 5
agentnb history --errors
agentnb history --successes --latest
agentnb history --latest
agentnb history @last-error
agentnb history @last-success
agentnb history --all
agentnb history --full                # full un-truncated code and output
```

The difference is:
- `runs` is for exact execution records and background-run control
- `history` is for the notebook-like semantic transcript of your workflow

A simple rule:
- If you care about one `execution_id`, use `runs`
- If you care about the recent flow of work in the session, use `history`

## Output Modes

Default output is plain terminal text, not JSON.

What that means in practice:
- for `exec`, you usually just see the produced output: `stdout`, `stderr`, and a final `result` if there is one
- for status-style commands such as `start`, `status`, `wait`, `stop`, and `interrupt`, you get a short sentence
- for commands such as `vars`, `history`, and `runs`, you get a compact text listing

Examples:

```text
$ agentnb "1 + 1"
2

$ agentnb "print('hello')"
hello

$ agentnb wait
Kernel is idle (session: default, pid 91098).
```

Use `--json` when you want the full stable payload for scripting:

```bash
agentnb --json "1 + 1"
agentnb runs show @latest --json
```

Example:

```json
{
  "schema_version": "1.0",
  "status": "ok",
  "command": "exec",
  "project": "/path/to/project",
  "session_id": "default",
  "timestamp": "2026-03-16T20:26:15.207123+00:00",
  "data": {
    "duration_ms": 16,
    "status": "ok",
    "execution_id": "919910f9e8e1",
    "execution_count": 12,
    "result": "2",
    "ensured_started": true,
    "started_new_session": false
  },
  "suggestions": [],
  "error": null
}
```

Use `--agent` when you still want JSON, but a smaller payload:

```bash
agentnb --agent "1 + 1"
agentnb runs follow --agent
```

Example:

```json
{
  "ok": true,
  "command": "exec",
  "session_id": "default",
  "data": {
    "status": "ok",
    "execution_id": "31690003f3c0",
    "duration_ms": 50,
    "ensured_started": true,
    "started_new_session": false,
    "result": "2",
    "result_json": 2
  }
}
```

If you truly want only one stream from `exec`, use the output selectors
instead:

```bash
agentnb --result-only "1 + 1"
agentnb "print('hello')" --stdout-only
agentnb --stderr-only "raise RuntimeError('boom')"
```

Typical scripting patterns:

```bash
agentnb --agent "1 + 1" | jq .
agentnb --json "1 + 1" | python -c 'import json,sys; print(json.load(sys.stdin)["data"]["result"])'
```

If you want a default mode per shell:

```bash
export AGENTNB_FORMAT=agent
```

`AGENTNB_FORMAT=agent` enables compact JSON output and suppresses routine
suggestions. `AGENTNB_FORMAT=json` selects the full stable envelope.

Use `--quiet` to suppress non-essential human output, or `--no-suggestions` to
hide next-step suggestions:

```bash
agentnb --quiet "1 + 1"
agentnb --no-suggestions "1 + 1"
```

Top-level flags such as `--agent`, `--json`, `--quiet`, and `--no-suggestions`
can appear before or after the subcommand.

Use `--agent` or `--json` when consuming output programmatically to get a
stable, parseable single JSON object on stdout:

## Recovery And Lifecycle

Use the lifecycle commands based on the failure mode:

```bash
agentnb start
agentnb status
agentnb wait
agentnb interrupt
agentnb reset
agentnb stop
agentnb doctor
```

Use:
- `interrupt` when code hangs but the kernel should survive
- `reset` when the namespace is polluted but the process is still healthy
- `stop` and then `start` when the kernel is dead or badly wedged
- `doctor` when startup or environment detection fails

On `agentnb start`, the runtime selects an interpreter in this order:

1. `--python`
2. `<project>/.venv`
3. active `VIRTUAL_ENV`
4. current Python executable

If `ipykernel` is missing for the selected interpreter, `agentnb start` fails
with the exact install command and a `--fresh` restart hint. `agentnb doctor`
reports the same command without modifying the environment. Run the command in
your shell, then restart cleanly with `agentnb --fresh "..."` or rerun
`agentnb start`.

## Projects And Sessions

If you are already in the target project root, you usually do not need
`--project`.

Use `--project` when you are driving another repo from this checkout or from
some other working directory:

```bash
uv run agentnb --project /path/to/project "from myapp.models import User"
uv run agentnb runs follow --project /path/to/project --agent RUN_ID
```

Use `--session` when you want more than one live kernel for the same project:

```bash
agentnb --session analysis "1 + 1"
agentnb start --session analysis
agentnb sessions list
agentnb sessions             # supported alias for `sessions list`
agentnb sessions delete analysis
agentnb sessions delete --stale      # delete sessions with dead kernels
agentnb sessions delete --all        # delete all sessions
```

When only one live session exists, kernel-bound commands can infer it. Once
multiple live sessions exist, pass `--session NAME` explicitly.

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
uv run ty check src tests
uv run pytest
```

## License

MIT
