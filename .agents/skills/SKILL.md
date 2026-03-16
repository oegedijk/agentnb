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

For multiline work, prefer stdin/heredoc or a file:

```bash
agentnb <<'PY'
import pandas as pd
df = pd.read_csv("tips.csv")
df.head()
PY

agentnb analysis.py
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
- `runs follow` streams live progress
- `runs wait` blocks until the run finishes
- `runs cancel` requests cancellation for an active run

Use explicit ids or selectors when you want exact lookup:

```bash
agentnb runs show run_123
agentnb runs show @last-error
agentnb runs show @last-success
```

Use `history` when you want the higher-level semantic transcript of what you asked `agentnb` to do:

```bash
agentnb history
agentnb history @last-error
agentnb history @last-success
```

Simple rule:
- if you care about one `execution_id`, use `runs`
- if you care about the recent flow of work, use `history`

## Output

Default output is plain terminal text.

Use `--json` when you want the full stable payload for scripting. Use `--agent` when you want a smaller JSON payload for agent/model consumption.

Examples:

```bash
agentnb --json "1 + 1"
agentnb --agent "1 + 1"
```

If you want only one `exec` stream:

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
agentnb doctor --fix
```

- `interrupt` for hanging code
- `reset` for polluted namespace with a healthy kernel
- `stop` and `start` for a dead or wedged kernel
- `doctor` when startup or interpreter detection fails

## Sessions

Use `--session NAME` when you want more than one live kernel for the same project:

```bash
agentnb start --session analysis
agentnb exec --session analysis "1 + 1"
agentnb sessions list
agentnb sessions delete analysis
```

When only one live session exists, commands can infer it. Once multiple live sessions exist, pass `--session` explicitly.

## Rules

- Prefer implicit top-level exec for the first command in a workflow.
- Prefer short inline snippets for one-liners and stdin or files for multiline work.
- Prefer a final expression over `print(...)` when you want a compact return value.
- Use `reload` after editing importable project modules instead of assuming live definitions updated automatically.
- Use `runs` for exact execution lookup and background control.
- Use `wait` for session readiness.
- Use `--agent` or `--json` only when machine-readable output is useful.
- Treat the kernel as project-scoped state. Stop it when the task is complete or stale state could confuse later work.
