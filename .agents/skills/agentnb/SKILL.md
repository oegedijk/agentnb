---
name: agentnb
description: Use this when working in a repo that has agentnb installed or under development and the task benefits from a persistent Python REPL instead of one-off Python commands. Covers starting the project-scoped kernel, executing code, inspecting live state, recovering from failures, and shutting the session down cleanly.
---

# agentnb

Use this skill when Python work benefits from preserved in-memory state across commands.

Treat `agentnb` as a persistent REPL for agents, or an append-only notebook without a notebook UI. It keeps live state and execution history, but it is not a notebook editor.

Use it for:
- exploring a codebase or API incrementally
- keeping expensive imports, setup, or data loaded
- inspecting live variables instead of rebuilding state from scratch
- reloading local modules after edits without restarting Python

Do not use it for one-shot shell or Python tasks where state does not matter.

## Mental Model

- `agentnb "..."` is the cheap hot path.
- A final expression becomes the execution result; `print(...)` goes to `stdout`.
- Reloading is explicit. Edited project files do not auto-reload.
- Drive one session serially. Wait for one command to finish before sending the next.

## Running Code

Run from the target project root when possible.

Use the implicit top-level form first:

```bash
agentnb "from myapp.models import User"
agentnb "User.query.limit(5)"
```

For multiline code or code with braces, quotes, or special characters, use
stdin/heredoc or a file:

```bash
agentnb <<'PY'
import pandas as pd
df = pd.read_csv("tips.csv")
df.describe()
PY

agentnb analysis.py
```

If a file finishes with assignments instead of a final expression, `agentnb`
reports a compact namespace-change summary so you can see what changed
without immediately calling `vars`.

`--session` and `--background` work in prefix position for inline code, file
execution, and most subcommands. Put `--project` after the command name.
Session-scoped subcommands also accept `--session` there, but execution-id
`runs` subcommands (`show`, `wait`, `follow`, `cancel`) are project-scoped and
do not accept `--session`:

```bash
agentnb --session myenv "df.head()"     # prefix works for inline exec
agentnb --background "long_task()"
agentnb history --session myenv
agentnb runs list --session myenv
agentnb runs show RUN_ID --project /path/to/project
```

The default execution timeout is 30 seconds. Use `--timeout` for long-running
code, and `--stream` to see output in real time:

```bash
agentnb --timeout 120 "train_model()"
agentnb --stream "train_model(epochs=10)"
```

Use `--fresh` to stop and restart the session before executing:

```bash
agentnb --fresh "from myapp import run; run()"
```

Use `--no-truncate` to get full output in `--agent` mode:

```bash
agentnb --agent --no-truncate "print(large_output)"
```

The session auto-starts for normal execution. Use strict startup failure only when needed:

```bash
agentnb exec --no-ensure-started "1 + 1"
```

If you are running from this repo checkout instead of the target project, pass `--project`:

```bash
uv run agentnb --project /path/to/project "1 + 1"
```

## Reading State

Use `vars` for the namespace and `inspect` for one value:

```bash
agentnb vars
agentnb vars --recent 5
agentnb vars --match rows
agentnb inspect df
agentnb inspect "payload['items'][0]"
```

Use `wait` when the question is "can I safely send the next command yet?":

```bash
agentnb wait
```

## Reloading

After editing project-local code on disk, reload explicitly:

```bash
agentnb reload
agentnb reload myapp.models
```

Bare `reload` reloads imported project-local modules. `reload MODULE` targets one imported module.

## Background Runs And History

Use `--background` when you want to start work and come back later:

```bash
agentnb --background "long_task()"
```

That returns an `execution_id` and writes a durable run record.

Use `runs` when you care about one specific execution:

```bash
agentnb runs show
agentnb runs follow
agentnb runs wait
agentnb runs cancel @active
```

- `runs show` reads the latest stored snapshot
- `runs follow` streams new events from the current tail; use `runs show` to inspect the stored snapshot first, and use `--timeout T` to bound the observation window without turning an active run into an error
- `runs wait` blocks until the run finishes
- `runs cancel` requests cancellation for an active run

Filter the runs list with `--last N` and `--errors`:

```bash
agentnb runs list --last 5
agentnb runs list --errors
```

Use explicit ids or selectors when you want exact lookup:

```bash
agentnb runs show run_123
agentnb runs show @last-error
agentnb runs show @last-success
```

Use `history` when you want the higher-level semantic transcript of what you asked `agentnb` to do:

```bash
agentnb history
agentnb history --last 5
agentnb history --errors
agentnb history --latest
agentnb history --all                # include helper/provenance entries
agentnb history --full               # full un-truncated code and output
agentnb history @last-error
agentnb history @last-success
```

Simple rule:
- if you care about one `execution_id`, use `runs`
- if you care about the recent flow of work, use `history`

## Output

Default output is plain terminal text on stdout. Use `--agent` or `--json` to
get a single JSON object on stdout — this is the preferred output mode for
agent-driven workflows.

Use `--json` when you want the full stable payload for scripting. Use `--agent` when you want a smaller JSON payload for agent/model consumption.

```bash
agentnb --json "1 + 1"
agentnb --agent "1 + 1"
```

Set `AGENTNB_FORMAT=agent` to make `--agent` mode the default for all commands
in a shell session.

Use `--quiet` to suppress non-essential output, or `--no-suggestions` to hide
next-step suggestions:

```bash
agentnb --quiet "1 + 1"
agentnb --no-suggestions "1 + 1"
```

If you want only one `exec` stream, use the output selectors; `--result-only`
still uses bounded result rendering, so large structured values may show a
compact preview instead of the full repr:

```bash
agentnb --result-only "1 + 1"
agentnb "print('hello')" --stdout-only
agentnb --stderr-only "raise RuntimeError('boom')"
```

## Recovery

Use:

```bash
agentnb interrupt
agentnb reset
agentnb stop
agentnb start
agentnb doctor
```

- `interrupt` for hanging code
- `reset` for polluted namespace with a healthy kernel
- `stop` and `start` for a dead or wedged kernel
- `doctor` when startup or interpreter detection fails

`reset` clears user variables in the current process, `exec --fresh` restarts
then executes, and `stop` shuts the session down without executing anything.

If `ipykernel` is missing, `start` and `doctor` print one explicit install
command. Run that command in your shell, then restart cleanly with
`agentnb --fresh "..."` or rerun `agentnb start`.

Check session readiness with wait modes:

```bash
agentnb wait
agentnb status --wait
agentnb status --wait-idle
```

## Sessions

Use `--session NAME` when you want more than one live kernel for the same project:

```bash
agentnb --session analysis "1 + 1"
agentnb start --session analysis
agentnb sessions             # same as `agentnb sessions list`
agentnb sessions delete analysis
agentnb sessions delete --stale      # delete non-live session records
agentnb sessions delete --all        # delete all sessions
```

When only one live session exists, commands can infer it. Once multiple live
sessions exist, pass `--session` explicitly. `agentnb sessions list` shows
live sessions only and notes when older non-live records are hidden behind
`agentnb sessions delete --stale`.

## Rules

- Prefer implicit top-level exec for the first command in a workflow.
- Prefer short inline snippets for one-liners and stdin or files for multiline work. Do not use `\n` to embed newlines in an inline code string — use heredoc or `--file` instead.
- Prefer a final expression over `print(...)` when you want a compact return value.
- Use `reload` after editing importable project modules instead of assuming live definitions updated automatically.
- Use `runs` for exact execution lookup and background control.
- Use `wait` for session readiness, not `status --wait-idle` (same semantics, shorter).
- Use `--agent` or `--json` when consuming output programmatically for a stable, parseable single JSON object. The `result` field contains the Python `repr()` of the return value. When valid JSON can be extracted from the repr, a `result_json` field is also included with the parsed value. For structured data, prefer reading `result_json` when present; otherwise use `import json; json.dumps(obj)` inside the kernel and read `result_json` from the response.
- Use `vars` and `inspect` to check live state rather than `print()` — they produce bounded output regardless of object size.
- Treat the kernel as project-scoped state. Stop it when the task is complete or stale state could confuse later work. `agentnb sessions list` shows live sessions only and will note when non-live records are hidden; use `agentnb sessions delete --stale` or `agentnb sessions delete --all` to clean them up.
