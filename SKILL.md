---
name: agentnb
description: Use this when working in a repo that has agentnb installed or under development and the task benefits from a persistent Python REPL instead of one-off Python commands. Covers starting the project-scoped kernel, executing code, inspecting live state, recovering from failures, and shutting the session down cleanly.
---

# agentnb

Use this skill when iterative Python work would benefit from preserved in-memory state across turns or commands.

Treat `agentnb` as a persistent REPL for agents, or an append-only notebook without a notebook UI. It keeps live state and execution history, but it is not a notebook editor.

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

## Core Loop

Use this order for normal work:

1. `agentnb start --json`
2. `agentnb exec ... --json`
3. `agentnb vars --json` or `agentnb inspect NAME --json`
4. `agentnb reload MODULE --json` after editing source files
5. `agentnb history --json` when you need to review prior steps

Examples:

```bash
agentnb exec "from myapp.models import User" --json
agentnb exec "u = User(name='test'); print(u)" --json
agentnb vars --json
agentnb inspect u --json
agentnb reload myapp.models --json
```

For multi-line code, prefer `--file` or stdin over fragile shell quoting:

```bash
agentnb exec --file scripts/debug_snippet.py --json
printf '%s\n' 'x = 1' 'x + 1' | agentnb exec --json
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
- Use `reload` after editing importable modules instead of assuming live definitions updated automatically.
- Keep snippets focused and incremental; avoid pasting large scripts unless the task truly needs that.
- Treat the kernel as project-scoped state. Stop it when the task is complete or when stale state could confuse later work.

## Limits

- `agentnb` is a persistent REPL interface, not a notebook editor.
- It behaves more like an append-only notebook transcript than a mutable notebook document.
- State is process-local and can drift from on-disk source until modules are reloaded or the kernel is restarted.
- Commands operate on the default session model in the current implementation.
