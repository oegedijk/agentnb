# v0.3.1 — Output Correctness And Ergonomic Fixes

## Bug fixes

**Duplicate error output** — In default (human) mode, execution errors were
written to both stderr and stdout. Any caller that captured both channels
together (e.g. `2>&1`, subprocess with `stderr=STDOUT`) saw the traceback and
"Next:" suggestions twice. Errors are now always written to stdout only.

**`reset` printed "Execution completed."** — `agentnb reset` printed the same
message as a successful `exec` with no output, making it impossible to tell
from the output alone that state was cleared rather than code run. It now
prints `"Namespace cleared."`.

**`--session` and `--project` prefix rejected for `runs` and `sessions`
subcommands** — `agentnb --session X runs list` and
`agentnb --project /path runs list` failed with "No such option". The
`InvocationResolver` now correctly moves prefix flags past the subcommand
name for group commands. Prefix position works for inline exec and most
subcommands; after the subcommand always works.

**`--auto-install` failed in pip-less venvs** — `agentnb start --auto-install`
always tried `python -m pip install ipykernel`, which fails in fresh `uv`
environments where `pip` is not present. The provisioner now probes pip
availability first and falls back to `uv add ipykernel` (when `uv.lock` is
detected) or `uv pip install ipykernel>=6.0`. When the installer itself
reports `"No module named pip"`, the error message now suggests the correct
`uv` command instead of repeating the failing `pip` invocation.

## Improvements

**Session name in `status` and `wait` output** — `agentnb status` and
`agentnb wait` now include the session name alongside the pid:

```
Kernel is running (session: default, pid 12345).
Kernel is idle (session: default, pid 12345).
```

This makes it unambiguous which session was checked when multiple sessions are
live.

**Stdout/stderr truncation notice in `--agent` mode** — When output is
truncated in compact `--agent` payloads, the `stdout` and `stderr` fields now
end with `[N chars truncated]` so the agent knows the value is incomplete
rather than inferring it from a trailing `...`.

**Context-aware recovery suggestions** — `AdvicePolicy` now returns targeted
suggestions for two previously unhandled error codes:

- `SESSION_BUSY` (serialization lock): suggests `agentnb wait --json` to
  block until idle, instead of the generic `history @last-error` / `interrupt`
  / `reset` fallback.
- `NO_KERNEL` and `BACKEND_ERROR` (dead or missing kernel): suggests
  `agentnb start --json` and `agentnb doctor --json`, instead of the generic
  exec fallback.
