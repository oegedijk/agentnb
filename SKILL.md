---
name: agentnb
description: Use this when working in a repo that has agentnb installed or under development and the task benefits from a persistent Python REPL instead of one-off Python commands. Covers starting the project-scoped kernel, executing code, inspecting live state, recovering from failures, and shutting the session down cleanly.
---

# agentnb

Use this skill when iterative Python work would benefit from preserved in-memory state across turns or commands.

Treat `agentnb` as a persistent REPL for agents, or an append-only notebook without a notebook UI. It keeps live state and execution history, but it is not a notebook editor.

Module reloading is explicit. Do not assume edited project files are reloaded
automatically on each execution.

`exec` follows normal IPython/Notebook behavior: if the final line of a code
snippet is an expression, its value is returned as the result.
`print(...)` goes to `stdout`; a bare final expression goes to `result`.

Drive one project session serially. Do not issue multiple `agentnb` commands
against the same live kernel at once; wait for one to finish before sending the next.

When you need low-noise machine-readable output, prefer `agentnb --agent ...`.
It returns compact JSON by default to reduce token usage.

Top-level flags such as `--agent` and `--json` can be placed before or after
the subcommand.

Typical cases:
- exploring a codebase or API incrementally
- keeping expensive imports, setup, or data loaded
- inspecting live variables instead of rebuilding state from scratch
- reloading a module after edits without restarting the whole Python process

Do not use `agentnb` for simple one-shot shell or Python tasks where state does not matter.

## Startup

Run from the target project root when possible.

Start or verify a kernel:

```bash
agentnb status --json
agentnb start --json
```

If `agentnb start` reports that `ipykernel` is missing, either rerun with
`--auto-install` or use `agentnb doctor --fix --json`. Startup no longer
installs dependencies unless asked.

If a specific interpreter is required:

```bash
agentnb start --python /path/to/python --json
```

If startup fails, inspect the environment before retrying:

```bash
agentnb doctor --json
agentnb doctor --fix --json
```

Notes:
- By default, `agentnb` resolves the project from the current directory upward until it finds `pyproject.toml`.
- `start` will reuse an already-alive kernel instead of spawning a duplicate.
- Prefer `--json` when you need machine-readable output.
- Use one command at a time per session.

## Core Loop

Use this order for normal work:

1. `agentnb start --json`
2. `agentnb exec ... --json` for short inline snippets
3. `agentnb exec --file ... --json` or pipe code through stdin for multiline work
4. `agentnb vars --json` or `agentnb inspect NAME --json`
5. `agentnb reload --json` after editing project-local source files
6. `agentnb reload myapp.models --json` when you want to target one imported module
7. `agentnb history --json` when you need to review prior steps

Examples:

```bash
agentnb exec "from myapp.models import User" --json
agentnb exec "u = User(name='test'); print(u)" --json
agentnb exec --file scripts/debug_snippet.py --json
agentnb exec --json <<'PY'
import pandas as pd
df = pd.read_csv("tips.csv")
df.head()
PY
agentnb vars --json
agentnb inspect u --json
agentnb reload --json
agentnb reload myapp.models --json
```

For multi-line code, prefer `--file` or stdin/heredoc over shell-escaped
backslashes. A literal multi-line shell argument is fine if your shell passes
it through, but `--file` and stdin are the reliable defaults.

When the namespace gets noisy, use:

```bash
agentnb vars --recent 5 --json
agentnb vars --match rows --json
```

## Recovery

If code hangs:

```bash
agentnb interrupt --json
```

If the namespace is polluted but the kernel should stay alive:

```bash
agentnb reset --json
```

If the kernel is dead or badly wedged:

```bash
agentnb stop --json
agentnb start --json
```

Use `history --errors --json` to inspect recent failures.

## Operating Rules

- Check `status` or `start` before assuming a live kernel exists.
- Prefer `exec` for real work and `vars` or `inspect` for observation.
- Prefer short inline `exec` for one-liners and stdin or `--file` for multiline code.
- Prefer a final expression over `print(...)` when you want a compact return value.
- Use `reload` after editing importable project modules instead of assuming live definitions updated automatically.
- Bare `reload` reloads all imported project-local modules. `reload MODULE` targets one imported project-local module.
- If reload reports stale objects, recreate them or run `reset` when the whole namespace has become unreliable.
- `vars` includes type information by default; pass `--no-types` only when you need less noise.
- `vars --recent N` and `vars --match TEXT` are the fastest way to clean up a noisy namespace view.
- `vars` hides imported helper routines and classes and summarizes common containers compactly.
- `history` shows semantic user-visible steps by default; use `history --all --json` only when debugging internals.
- `inspect` gives compact previews for pandas-like values and for common `list`/`dict` payloads.
- Keep snippets focused and incremental; avoid pasting large scripts unless the task truly needs that.
- Treat the kernel as project-scoped state. Stop it when the task is complete or when stale state could confuse later work.

## Limits

- `agentnb` is a persistent REPL interface, not a notebook editor.
- It behaves more like an append-only notebook transcript than a mutable notebook document.
- State is process-local and can drift from on-disk source until modules are reloaded or the kernel is restarted.
- Commands operate on the default session model in the current implementation.
