# v0.3.7 — Contract And Recovery Polish

## Improvements

**Run and help contracts are tighter and more consistent** — `runs show` and
`runs wait` now expose a top-level `data.status` alias for machine consumers
while preserving `data.run.status`, root help no longer overpromises `--session`
support for execution-id run commands, and `history --help` now documents
selector shortcuts such as `@latest`, `@last-error`, and `@last-success`.

**Recovery paths are more explicit and less stateful** — Foreground exec now
reports when it had to replace a dead or stale kernel and makes the resulting
state loss explicit in both human and JSON output. Stopping the current session
also clears the remembered-session preference so later bare exec does not
silently revive a stopped named session.

**Dependency repair is now shell-first instead of session-mutation-first** —
Missing live-session modules now point first to `uv pip install --python ...`
for the selected interpreter, startup and `doctor` no longer try to install
`ipykernel` automatically, and missing-startup guidance now tells the user to
install it explicitly and restart with `--fresh`.

**Inspection and exec output are more informative** — `inspect` now supports
safe dotted and constant-subscript references such as `df.a` and
`payload['items'][0]`, nested previews preserve JSON-like structure more
faithfully, `runs show` and `runs wait` preserve multiline stdout and stderr in
human output, and file execution now reports compact namespace changes when a
script completes without stdout, stderr, or a final expression result.

**Quiet-mode behavior is clearer** — `--quiet` now suppresses non-essential
success-path chatter without hiding primary output or failure recovery guidance,
while `--no-suggestions` suppresses only the `Next:` block.

# v0.3.6 — Command Surface Simplification And Release Cut

## Improvements

**Structured exec summaries now flow through one bounded seam** — Exec-like
payloads now consistently preserve `result_preview` for sequence-like,
mapping-like, and dataframe-like values when a bounded summary can be derived.
This keeps `result` as the compatibility field while making container-shaped
results more decision-useful in both full JSON and compact agent projections.

**Error envelopes now normalize through one shared contract** — Traceback
cleanup now happens once in the shared error contract instead of being decided
ad hoc in app and projection layers. Human output, full `--json`, and
`--agent` all now see the same sanitized traceback and top-level error facts.

**Wait-state messaging is clearer in human mode** — `wait`,
`status --wait`, and `status --wait-idle` now make it explicit when the
command waited, what condition it waited for, and the starting runtime state
when that context is available.

**Pip-less recovery is aligned across `start` and `doctor`** — Provisioning
errors now carry one concrete manual recovery command, and `doctor --fix`
reuses the same command that `start --auto-install` would surface for the
same interpreter state.

**Release metadata now matches the shipped 0.3.6 surface** — Versioned
artifacts, roadmap notes, and release notes now agree on `0.3.6`.

# v0.3.5 — Helper Access And Agent Contract Fixes

## Bug fixes

**Read-only helpers now cooperate with active same-session work** — `vars`,
`inspect`, and `reload` no longer fail fast so often behind active same-session
execution. The helper path now waits through the run manager, records whether
it waited, and surfaces the blocking execution id when that context matters.

**Helper startup behavior is now consistent with `exec`** — On an
unambiguously targeted missing session, `vars`, `inspect`, and `reload` now
auto-start the session instead of falling back to generic no-kernel behavior.
Structured helper responses include whether a new session was started.

**Helper error metadata no longer disappears on exception paths** — Helper
failures raised directly during execution or busy handling now preserve the new
access metadata instead of dropping it during exception rewriting.

**Bare `sessions` no longer drifts from its shortcut behavior** — The CLI now
accepts `agentnb sessions --project ... --json` as a true shortcut for
`sessions list`, and the repo docs/examples now match that behavior.

## Improvements

**Typed helper-access contract** — Helper read commands now use a shared
`HelperAccessMetadata` contract instead of ad hoc payload mutation. The access
fields flow through introspection, ops, app shaping, and the local run manager
as typed state.

**Hard ambiguity error for omitted-session exec** — When more than one live
session exists, omitted-session `exec` and implicit top-level exec now return
`AMBIGUOUS_SESSION` instead of silently following the remembered current
session.

**Structured recovery actions in `--agent` responses** — Compact agent-mode
responses now preserve `suggestion_actions`, so ambiguity and recovery flows
carry machine-readable next steps instead of only prose suggestions.

# v0.3.4 — Smoke-Driven Consistency And Recovery Fixes

## Bug fixes

**Dead-kernel detection on hard exits** — When the kernel died during an
`exec` (for example via `os._exit(1)` or a backend crash), the triggering
command could hang until a later timeout or until a follow-up `status` call
made the problem visible. The execution path now fails quickly with
`KERNEL_DEAD`.

**`history @last-error` favored incidental control-plane errors** — History
selection could return an unrelated control-plane failure instead of the most
recent kernel execution failure the agent actually needed to inspect. Journal
selection now prefers real execution failures and only falls back to the
latest control-plane error when no execution failure exists.

**`doctor --fix` could repair the wrong environment** — In uv-managed
projects, automatic ipykernel repair could run outside the target project or
against the wrong interpreter. The provisioner now runs from the target
project root and binds the `uv pip` fallback to the selected interpreter
before re-checking availability.

**Read-only helper commands during startup were ambiguous** — `vars`,
`inspect`, and `reload` could collapse into generic no-kernel behavior while a
same-session startup was still in flight. These commands now return structured
`KERNEL_NOT_READY` responses with `runtime_state=starting`.

**`runs show` active snapshots were underspecified** — Persisted active run
snapshots could be mistaken for live state. Active machine payloads now carry
an explicit `snapshot_stale` flag to match the human warning that persisted
state may lag live follow output.

**Same-session overlap handling was inconsistent on the full CLI path** —
Sending a second `exec` or `reset` after a background run could still block on
startup or session-resolution probes before surfacing a busy error. Overlaps
now fail fast against active persisted run records before startup checks run,
and implicit session resolution no longer blocks on backend status probes
first.

## Improvements

**Visible runtime state in `status` and `wait`** — Machine payloads now expose
`runtime_state` and `session_exists`, and human output distinguishes
`starting` and `dead` states instead of flattening them into "not running".

**Structured session-busy contract** — `SESSION_BUSY` payloads now include
`wait_behavior`, `waited_ms`, and current lock metadata (`lock_pid`,
`lock_acquired_at`, `busy_for_ms`). When a same-session overlap is rejected at
the run layer, the payload also includes the blocking `active_execution_id`.

**Current-session visibility in multi-session workflows** — Implicit
session-bound commands now refresh the saved current-session preference and
surface a switch notice when agentnb resolves to a different known session
than the one previously targeted.

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
