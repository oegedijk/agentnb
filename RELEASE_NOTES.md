# v0.3.3 — Bug Fixes And Ergonomic Improvements From Second Smoke Run

## Bug fixes

**Duration always `0ms` on timeout, cancel, and background progress** —
Multiple code paths lost the elapsed time: the `_ExecutionProgressSink` always
passed `duration_ms=0`, timeout exceptions discarded the partial timing, and
cancel/worker-exit paths propagated the initial zero. The progress sink now
tracks `time.monotonic()` from creation, `ExecutionTimedOutError` carries
`duration_ms`, and cancel/worker-exit paths fall back to wall-clock computation
from the run's ISO timestamp. Journal entries from `error_record` also receive
the computed duration so `history` shows correct values.

**Stdout swallowed on error in human mode** — When code printed to stdout
before erroring (e.g. `print('x'); 1/0`), the stdout output was lost in
default human mode. `--agent` and `--json` included it correctly. The human
error renderer now prepends stdout and stderr from the response data before
the error block.

**`--result-only` and `--stdout-only` leaked session targeting message** —
`(now targeting session: ...)` appeared even with output selectors, breaking
the "only the selected output" contract. The session targeting message is now
suppressed when an output selector is active.

## New features

**`result_json` field in `--agent` exec responses** — The `result` field
still contains the Python repr of the return value. When valid JSON can be
extracted from the repr string (plain JSON literals like `42`, `[1,2,3]`,
`true`, or repr-quoted strings from `json.dumps()` output), a `result_json`
field is now included with the parsed value. This eliminates the need to
shell out to `json.dumps()` inside the kernel for structured data that is
already JSON-serializable.

**`--no-truncate` flag on `exec`** — Skips stdout/stderr/result truncation
in `--agent` mode. Useful when output is large and the truncation notice
(`[N chars truncated]`) indicates lost data. `--json` mode already bypasses
truncation; this flag brings the same behavior to `--agent`.

```bash
agentnb --agent --no-truncate "print(large_dataframe)"
```

**`--fresh` flag on `exec`** — Stops and restarts the target session before
executing, ensuring a clean namespace. This replaces the manual
`stop` + `exec` two-step when reconnecting to a session with stale state.

```bash
agentnb --session analysis --fresh "from myapp import run; run()"
```

**`history --full`** — Shows complete un-truncated code for each history
entry instead of the compact summary. Useful when history labels are too
short to tell what actually ran.

```bash
agentnb history --full --last 5
```

**`sessions delete --all` and `sessions delete --stale`** — Bulk session
cleanup. `--all` deletes every session; `--stale` deletes only sessions
whose kernel is no longer running. Replaces the tedious one-at-a-time
deletion that accumulated 20+ zombie sessions during test runs.

```bash
agentnb sessions delete --stale
agentnb sessions delete --all
```

## Improvements

**Inspect nested dict preview consistency** — Both the kernel-side
`_json_safe()` and client-side `_compact_jsonish()` now use `str()` instead
of `repr()` for depth >= 2 leaves. `_json_safe` also checks depth before
expanding nested mappings, so deeply nested structures are consistently
truncated rather than partially expanded with mixed quoting styles.

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
