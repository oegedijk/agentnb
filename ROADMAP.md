# agentnb Roadmap

This roadmap is forward-looking. It is not a changelog.

`agentnb` is a persistent, project-scoped Python kernel for coding agents doing interactive work. The product wins when an agent can enter and stay in a productive loop with minimal token spend, minimal syntax overhead, minimal output parsing, and minimal recovery friction.

## Product Lens

The main optimization target is agent token efficiency:

- how little documentation an agent must read before it can use the tool correctly
- how few flags and subcommands it must remember for the hot path
- how rarely it has to self-correct after guessing the CLI shape
- how rarely it must call `--help`, `sessions list`, `runs list`, or `history` just to decide the next command
- how little output it must parse to recover the one fact needed for the next step

The primary usability target is coding agents; human ergonomics matter insofar as they reinforce the same low-friction path.

Human ergonomics still matter, but they follow this same direction: fewer steps, clearer defaults, quieter output, and better recovery guidance.

## Design Rules

Deterministic targeting and machine-readable recovery take priority over permissive convenience behavior.

1. Optimize the core interactive loop before adding reproducibility, export, or extensibility features.
2. Keep full `--json` as the exact machine contract, but do not treat it as the default working mode.
3. Prefer defaults, inference, selectors, and compact outputs over additional verbs.
4. Make the cheapest correct next action obvious from the current response.
5. Keep persisted provenance honest even when adding convenience syntax.
6. Keep new behavior behind deep modules rather than spreading policy across CLI handlers.

## Baseline Assumptions

The roadmap assumes the existing persistent-kernel baseline remains intact:

- project-scoped sessions
- explicit inspection and reload flows
- durable run records with `execution_id`
- background execution with follow/wait/cancel behavior
- stable machine-readable responses
- app, state, kernel, introspection, and run-control boundaries that can absorb new behavior without leaking low-level details upward

## v0.3 - Agent Loop Efficiency (shipped)

v0.3 shipped the core agent loop: implicit exec, auto-start, compact `--agent` mode, sticky sessions, symbolic selectors, lower-noise output, and the help/discoverability rewrite.

### Architecture Seams

These boundaries now own specific categories of complexity. New feature work should land in the appropriate seam rather than spreading policy across CLI handlers.

- `InvocationResolver`: hot-path syntax, argv/stdin/file-path inference, implicit exec routing.
- `ExecInvocationPolicy`: default execution ergonomics (startup, background, output selection).
- `ResponseProjector`: compact `--agent` vs full `--json` response shapes.
- Selector resolvers: `@latest`, `@active`, `@last-error`, `@last-success` expansion for runs and history.
- `StateRepository` + `KernelRuntime`: sticky session preferences and precedence rules.
- `AdvicePolicy`: next-step suggestions, success-path quieting, recovery guidance.

If a feature does not fit one of these seams cleanly, define or deepen the owning module first instead of adding a CLI-local special case.

## v0.3.1 - Output Correctness And Ergonomic Fixes (shipped)

v0.3.1 fixed the output-path bugs that most affected agent consumption and corrected the ergonomic rough edges identified in smoke testing.

### Shipped

- Fixed duplicate error output: errors now always go to stdout; the `err=True` routing in `_emit()` was the root cause of double output when stderr was captured alongside stdout.
- Fixed `reset` output message: `reset` now prints `"Namespace cleared."` instead of `"Execution completed."` to distinguish it from code execution.
- Context-aware suggestions in `AdvicePolicy`: `SESSION_BUSY` now suggests `agentnb wait` as the primary recovery path; `NO_KERNEL` and `BACKEND_ERROR` now suggest `agentnb start` / `agentnb doctor` instead of the generic exec fallback.
- Fixed `--session` and `--project` prefix position for group commands: `agentnb --session X runs list` and `agentnb --project /path runs list` now work by detecting group command names (`runs`, `sessions`) in `InvocationResolver` and moving prefix exec tokens after the first subcommand positional.
- Added session name to `status` and `wait` output: both commands now include `session: NAME` alongside the pid so the agent can identify which session was checked.
- Added stdout/stderr truncation notice in `--agent` mode: when output is truncated in `compact_execution_payload`, the summary now ends with `[N chars truncated]` so the agent knows the value is incomplete.
- Fixed `--auto-install` fallback for pip-less venvs: `ensure_ipykernel()` now probes pip availability before choosing an install command; falls back to `uv add ipykernel` (when `uv.lock` is present) or `uv pip install ipykernel>=6.0`; "No module named pip" in installer stderr triggers a targeted error message.

## v0.3.2 - Friction Fixes From Agent Smoke Testing (shipped)

v0.3.2 addressed friction points discovered by running all 17 smoke scenarios end-to-end as an agent. Each item maps to a concrete failure or confusion observed during that run.

### Shipped

- Staleness hint in `sessions list` human output: each session now shows relative last-activity age (e.g. "5m ago", "2d ago"). The `last_activity` timestamp was already present in JSON output; this surfaces it for human readers and makes stale sessions visible without probing each one.
- Fixed `ModuleNotFoundError` install suggestion for pip-less venvs: suggestion now recommends `uv add X` (run in your shell, not inside the session) and drops the bare `pip install` form that would silently install into the wrong environment.
- Added `importlib.reload()` hint when `reload` finds no project-local modules: when reload finds nothing to reload, the suggestion now points at `importlib.reload(module)` as a manual recovery path.
- Added kernel health check to `doctor`: `doctor` now probes whether the session kernel is alive and reports `kernel_alive`/`kernel_pid` in its payload. Human output shows `[OK] kernel` or `[WARN] kernel` alongside the prerequisite checks. This distinguishes "env is broken" from "kernel is dead" without requiring a separate `status` call.
- Truncated `inspect` head rows for wide DataFrames: `_safe_head_rows` now caps columns at 10 before converting to dict, consistent with how `dtypes` and `nulls` are already bounded. `compact_dataframe_preview` applies the same cap as a safety measure.
- Fixed `history @latest` suggestion after `exec`: the post-exec suggestion now uses the concrete `execution_id` (e.g. `history abc123`) instead of the relative `@latest` selector, so it remains correct even if the agent runs other commands before following the suggestion.

### Validated and removed from scope

- `--session`/`--project` prefix fix for plain subcommands: live testing confirmed the `InvocationResolver` else branch already handles all non-group subcommands correctly. The v0.3.1 group-command fix was the only gap; plain commands were never broken.
- Route `--agent`/`--json` error output to stdout: confirmed already fixed. `_emit()` routes all JSON errors to stdout; the only remaining `err=True` is for human-mode streaming, which is correct terminal behavior.

## v0.3.3 - Bugs And Ergonomic Friction From Second Smoke Run (shipped)

v0.3.3 fixed bugs and friction discovered by running all 17 smoke scenarios end-to-end a second time, after v0.3.2 shipped.

### Shipped

- Fixed duration always `0ms` in timeout, cancel, and background progress paths: `_ExecutionProgressSink` now tracks elapsed time, `ExecutionTimedOutError` carries `duration_ms`, and cancel/worker-exit paths compute wall-clock duration as fallback. Journal entries from `error_record` now also receive the computed duration.
- Fixed stdout swallowed on error in human mode: `_render_error()` now prepends stdout/stderr from `response.data` before the error block, matching the behavior of `--agent` and `--json` modes.
- Added `result_json` field to `--agent` exec responses: when the `result` repr string (or its inner content after stripping surrounding quotes) is valid JSON, a `result_json` field is included with the parsed value. The `result` field still contains the Python repr for backward compatibility.
- Fixed `--result-only` and `--stdout-only` leaking session targeting message: the `(now targeting session: ...)` notice is now suppressed when an output selector is active.
- Added `--no-truncate` flag to `exec`: skips stdout/stderr/result truncation in `--agent` mode. Threaded through `ExecInvocationPolicy` → `compact_execution_payload`.
- Improved `inspect` nested dict preview consistency: both kernel-side `_json_safe()` and client-side `_compact_jsonish()` now use `str()` instead of `repr()` for depth >= 2 leaves, and `_json_safe` checks depth before expanding nested mappings.
- Added bulk session cleanup: `sessions delete --all` and `sessions delete --stale` flags. `--stale` skips sessions with alive kernels.
- Added `--fresh` flag to `exec`: stops and restarts the target session before executing, ensuring a clean namespace.
- Added `history --full` flag: shows complete un-truncated code for each history entry instead of the compact summary.

### Deferred

- Silent serialization / `waited_ms` field: the `SESSION_BUSY` error fires correctly, but the response has no `waited_ms` field to distinguish lock-wait time from computation time. Deferred because it requires a behavior decision (wait-then-run vs fail-fast) and a contract extension to `ExecPayload`.

## v0.3.4 - Smoke-Driven CLI Consistency And Recovery Fixes (shipped)

v0.3.4 closed the consistency and recovery gaps found by the latest full smoke run. The emphasis stayed on making existing command paths behave reliably under real agent workflows rather than adding major new surface area.

### Shipped

- Fixed dead-kernel detection on hard exits: when the kernel dies during an exec (`os._exit(1)`, crash, worker disappearance), the triggering command now fails quickly with `KERNEL_DEAD` instead of hanging until an external timeout or later `status` call reveals the problem.
- Made runtime state visible in `status` / `wait`: machine payloads now expose `runtime_state` and `session_exists`, and human output distinguishes `starting` / `dead` instead of flattening them into "not running".
- Added a structured "session busy" contract: command-lock files now persist acquisition metadata, `RuntimeState` carries current lock facts, `status` exposes `lock_pid` / `lock_acquired_at` / `busy_for_ms`, and `SESSION_BUSY` exec/reset failures include `wait_behavior`, `waited_ms`, and current lock metadata.
- Made `history @last-error` prefer real execution failures over incidental control-plane errors: journal queries now bias toward kernel-side execution errors when available, while still falling back to the latest control-plane error if no execution failure exists.
- Fixed `doctor --fix` install targeting for uv-managed projects: automatic ipykernel repair now runs from the target project root, and the `uv pip` fallback binds installation to the selected interpreter before re-checking module availability.
- Completed startup-state handling for read-only helper commands: `vars`, `inspect`, and `reload` now return structured `KERNEL_NOT_READY` responses with `runtime_state=starting` while a same-session startup is still in flight, instead of collapsing into generic no-kernel behavior.
- Tightened `runs show` snapshot semantics: active run snapshots now carry an explicit `snapshot_stale` flag in machine output, matching the existing human warning that persisted state may lag live follow output.
- Clarified same-session foreground/background serialization on the full CLI path: overlapping `exec` / `reset` attempts now fail fast against active persisted run records before startup checks run, implicit session resolution no longer blocks on backend status probes first, `SESSION_BUSY` responses include the blocking `active_execution_id`, and recovery suggestions point directly at `runs wait/show` for that run.
- Improved current-session visibility in multi-session workflows: implicit session-bound commands now refresh the saved current-session preference and surface a switch notice when agentnb resolves to a different known session than the one previously targeted.

### Validated Historical Smoke Notes

These were plausible smoke findings at the time they were recorded, but representative current-path checks no longer reproduce them on the release tree.

- Root-position `--project` / `--session` placement for representative command families is already working through `CommandShape` + `InvocationResolver`: live smoke confirmed `agentnb --project PATH history`, `agentnb --project PATH runs show`, `agentnb --project PATH runs wait`, and `agentnb --session NAME --project PATH history/vars` all resolve correctly.
- Run-control help text and targeting are already aligned for execution-id based commands: `runs show/follow/wait/cancel --help` no longer advertises `--session`, and live smoke confirmed root-position `--project` works for those subcommands.
- Missing-module recovery guidance is already uv-aware: real CLI smoke confirmed `ModuleNotFoundError` suggestions now recommend `uv add PACKAGE` in the shell, not inside the live session.

### Deferred To v0.4

- Partial file rerun remains valuable, but it is new file-execution surface area rather than consistency polish. Keep it with the broader file execution work already planned in v0.4.

## v0.3.5 - Agent Correctness And Machine-Contract Frictions

v0.3.5 should close the remaining frictions that still cost agents extra probing, wrong-session risk, or response cleanup during real end-to-end use. The emphasis is deterministic targeting, predictable helper behavior, and machine-readable recovery.

### Frictions To Fix

- Read-only commands still fail too often under same-session contention:
  - friction: quick `vars`, `inspect`, mode-comparison, and follow-up checks still trip `SESSION_BUSY` when another same-session command is in flight, even when the agent is only trying to inspect state.
  - reproduce:
    - `uv run agentnb "import pandas as pd; big = pd.DataFrame({'i': range(200)})" --session large13`
    - `uv run agentnb vars --session large13`
    - immediately run `uv run agentnb inspect big --session large13`
    - observe: one of the read-only commands fails with `Another agentnb command is already using this session.`

- Session startup behavior is still inconsistent across command families:
  - friction: `exec` auto-starts a missing session, but `vars` / `inspect` on a fresh named session still fail with `No kernel running`, so the top-level mental model remains inconsistent.
  - reproduce:
    - `uv run agentnb vars --session disc10`
    - observe: `No kernel running. Start one with: agentnb start`
    - compare with `uv run agentnb "1 + 1" --session disc10`
    - observe: the exec path auto-starts and succeeds

- Implicit current-session routing remains too risky in multi-session workflows:
  - friction: once many sessions exist, an unqualified exec can silently run in the current session instead of forcing disambiguation or making the chosen target unmistakable.
  - reproduce:
    - start or reuse two sessions with distinct state:
      - `uv run agentnb "raw = {'name': 'a'}" --session compare6a`
      - `uv run agentnb "raw = {'name': 'b'}" --session compare6b`
    - make `compare6b` current by targeting it last
    - run `uv run agentnb "raw"`
    - observe: the command executes against the current session instead of erroring on ambiguity

- Output shaping still returns bulky repr strings for large values:
  - friction: `--agent`, `--json`, and `--result-only` still surface a flattened repr for large dataframes instead of steering the agent toward a bounded preview contract.
  - reproduce:
    - `uv run agentnb "import pandas as pd; big = pd.DataFrame({'i': range(200), 'text': ['x'*40 for _ in range(200)]}); big" --session large13`
    - `uv run agentnb exec --result-only "big" --session large13`
    - `uv run agentnb --agent "big" --session large13`
    - observe: large repr payloads are still emitted instead of a compact structured summary

- JSON mode is parseable but not fully machine-shaped:
  - friction: `suggestions` are still prose strings, and `runs show --json` can include ANSI-colored traceback fragments that require cleanup before downstream parsing.
  - reproduce:
    - `uv run agentnb --json "vals = [1, 2, 3]; vals[10]" --session json14`
    - `uv run agentnb runs show @latest --json`
    - observe: human prose in `suggestions` and ANSI escape sequences in traceback content

- `status --wait-idle` output remains too coarse:
  - friction: the command is operationally useful, but the human response still collapses to `Kernel is running` / `Kernel is idle` without exposing enough of the gating reason or elapsed wait to help an agent decide what just changed.
  - reproduce:
    - `uv run agentnb exec --stream "import time; [print(i, flush=True) or time.sleep(1) for i in range(5)]" --session stream11`
    - while it is running, execute `uv run agentnb status --session stream11 --wait-idle`
    - observe: the final response does not explain whether it waited, for how long, or what state transition occurred

- Bare `sessions` is still inconsistent with its documented shortcut behavior:
  - friction: docs and help treat bare `agentnb sessions` as a listing shortcut, but the group form still rejects `--project` and `--json` in that position instead of behaving like `sessions list`.
  - reproduce:
    - `uv run agentnb sessions --project /tmp/agentnb-review-empty`
    - observe: the command errors instead of behaving like `sessions list --project /tmp/agentnb-review-empty`

- In-session dependency installation still has too many recovery branches:
  - friction: `doctor --fix` / `start --auto-install` cover missing `ipykernel`, but installing a missing third-party package from inside a fresh session can still fail because the selected interpreter lacks `pip`.
  - reproduce:
    - create a fresh venv-backed project without pip-installed extras
    - `uv run agentnb start --project /tmp/agentnb-smoke17 --session dep17 --auto-install`
    - add a local module that imports an uninstalled dependency such as `pyjokes`
    - run `uv run agentnb "import needs_pyjokes; needs_pyjokes.tell()" --project /tmp/agentnb-smoke17 --session dep17`
    - then try `uv run agentnb "import subprocess, sys; subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'pyjokes'])" --project /tmp/agentnb-smoke17 --session dep17`
    - observe: the install path can fail with `No module named pip`

### Planned Fixes

- Make same-session read-only helpers cooperate better with active stateful workflows:
  - queue `vars` / `inspect` / `reload` behind the active same-session command with explicit waited-state reporting
  - make the chosen behavior visible in both human and machine responses

- Unify startup semantics for helper commands:
  - auto-start `vars` / `inspect` / `reload` only when session resolution is unambiguous
  - keep recovery suggestions aligned with the chosen policy when startup remains ambiguous or blocked

- Make ambiguous multi-session exec a hard error:
  - omitted-session `exec` and implicit top-level exec return `AMBIGUOUS_SESSION` when more than one live session exists
  - this is an error, not a warning

- Keep history selectors first-class:
  - `history @latest`, `history @last-error`, and `history @last-success` remain supported shortcut syntax alongside flag forms

- Add a compact large-value contract for output-shaped exec responses:
  - preserve the existing `result` field for compatibility
  - add an explicit bounded summary path for dataframe-like and container values so `--agent`, `--json`, and `--result-only` do not force repr parsing for large objects

- Make JSON recovery surfaces actually structured:
  - expose machine-readable suggestion actions alongside prose suggestions
  - strip ANSI formatting from tracebacks in JSON output or provide a parallel plain-text traceback field

- Improve wait-state visibility:
  - include whether `wait` / `status --wait-idle` actually waited, how long, and which state transition completed
  - keep the human output short, but give the agent enough information to reason about sequencing without extra probes

- Make bare `sessions` fully equivalent to `sessions list` for `0.3.5`:
  - accept the same option handling and output modes in that position
  - keep the docs accurate until the command surface cleanup lands in `0.3.6`

- Smooth the missing-dependency path inside fresh interpreters:
  - detect pip-less interpreters during dependency recovery and provide one exact supported path
  - consider a first-class helper for installing a missing import into the selected project interpreter without leaving the agent loop

### Acceptance Notes

- Smoke scenario for ambiguous omitted-session exec with multiple live sessions.
- Smoke scenario for helper auto-start on an unambiguous fresh session.
- Smoke scenario for helper waiting behind active same-session work.
- Smoke scenario for bounded large-value output in `--agent` and `--json`.
- Contract tests for machine-readable suggestion actions and ANSI-free JSON tracebacks.

### Implementation Seams

- Session ambiguity, wait-state reporting, and same-session busy policy:
  - start in `agentnb.runtime.KernelRuntime`
  - likely touch `resolve_session_id()`, `wait_for_ready()`, `wait_for_idle()`, `wait_for_usable()`, `execute()`, and `_session_busy_error()`

- Helper-command startup consistency and read-only command handling:
  - start in `agentnb.app.AgentNBApp`
  - likely touch `_handle_command()` plus the `vars()` / `inspect()` / `reload()` call paths
  - keep helper startup and blocking policy owned here rather than in the CLI layer

- `vars` / `inspect` / `reload` execution model:
  - start in `agentnb.introspection.KernelIntrospection` and `agentnb.ops.NotebookOps`
  - `_run_json_helper()` is the narrowest place to decide how helper reads wait and report their waited state

- Large-value output shaping and machine-facing exec payloads:
  - start in `agentnb.compact` and `agentnb.projection.ResponseProjector`
  - likely touch `compact_execution_payload()` and `_project_agent_data()`
  - `inspect` already has structured preview compaction; prefer deepening that pattern over inventing CLI-local truncation rules

- Structured recovery guidance and JSON-friendly suggestion contracts:
  - start in `agentnb.advice.AdvicePolicy`, then extend the response contract shape if needed
  - keep machine-readable suggestion actions in the contract layer, not the CLI renderer

- JSON traceback hygiene:
  - start in `agentnb.projection`, `agentnb.compact`, and the run-record projection path
  - if ANSI stripping should apply to full JSON as well as `--agent`, do it in projection/storage boundaries, not in terminal rendering code

- `sessions` shortcut consistency and option handling:
  - start in `agentnb.invocation.InvocationResolver` plus the `sessions` CLI group behavior
  - add docs/help parity tests so the shortcut behavior cannot drift again

- Interpreter and dependency-recovery workflow:
  - start in `agentnb.kernel.provisioner.KernelProvisioner`
  - `ensure_ipykernel()` already owns pip-vs-uv decisions; keep any broader missing-dependency install helper in the same provisioning boundary instead of scattering install logic across advice or CLI handlers

## v0.3.6 - Command Surface Simplification And Footgun Removal

v0.3.6 should remove the remaining agent-confusing behaviors without expanding major feature surface. The focus is a smaller public grammar, fewer hidden state changes, and clearer canonical command forms.

### Planned Simplifications

- Narrow current-session preference updates:
  - only explicit `--session`, `start`, and successful `exec` mutate remembered current session
  - implicitly resolved read/control commands no longer rewrite remembered session

- Make `wait` the primary documented blocking readiness command:
  - keep `status --wait` and `status --wait-idle` for compatibility in `0.3.x`
  - demote those `status` wait modes in help and README so `wait` is the obvious primary path

- Keep history selectors first-class and add `--successes`:
  - `history @latest`, `@last-error`, and `@last-success` stay
  - add `history --successes --latest` as the flag equivalent of `history @last-success`

- Normalize equivalent history selector/flag combinations:
  - accept redundant equivalent combinations and resolve them to the same query
  - reject only contradictory combinations

- Make `sessions list` the canonical documented form:
  - change bare `sessions` back to help-only behavior for consistency with other groups
  - keep `sessions list` as the one documented listing command

- Document one canonical CLI grammar:
  - `agentnb <command> [subcommand] [options]`
  - keep root output flags globally placeable, but stop teaching command-local option shuffling as part of the public model

- Reframe the Python import API:
  - describe the import-level helpers as low-level wrappers around runtime operations
  - do not present them as the primary ergonomic agent surface

### API / Contract Notes

- Read/control commands no longer rewrite remembered current session when they resolve implicitly.
- `wait` is the primary blocking command, while `status --wait` and `status --wait-idle` remain compatibility surface.
- `history @latest`, `@last-error`, and `@last-success` stay, and `--successes --latest` is the flag equivalent of `@last-success`.
- Bare `sessions` returns to help-only behavior, and `sessions list` is the canonical form.

### Acceptance Notes

- Tests proving implicit read/control commands do not mutate current-session preference.
- Tests for `history @last-success` parity with `history --successes --latest`.
- Docs/help parity tests for `sessions`, `wait`, and canonical command shapes.

### Implementation Seams

- `StateRepository` + `KernelRuntime`:
  - own current-session preference mutation policy and multi-session targeting rules that remain after `0.3.5`

- `AgentNBApp`:
  - own command-level blocking semantics and the demoted `status --wait*` documentation path

- Selector resolvers and history query validation:
  - own selector/flag equivalence, contradiction checks, and the `--successes` addition

- `InvocationResolver` plus CLI help and docs:
  - own canonical command grammar, root-flag placement guidance, and the return to help-only bare `sessions`

- Docs/help parity tests:
  - ensure README, `--help`, and agent skill examples stay aligned with the actual surface

## v0.4 - Recovery, Debugging, And Inspection Efficiency

### Goals

- Make failures cheaper to diagnose without dropping session state.
- Improve inspection and recovery so the agent can continue instead of restarting.
- Reduce the amount of output and follow-up probing needed to understand a bad state.

### Planned Features

- Better debugging:
  - traceback enrichment
  - frame and locals inspection commands

- Safer, more compact inspection:
  - bounded previews for large values
  - structured previews for common containers (`list`, `dict`, `tuple`, dataframe-like objects)
  - side-effect-aware inspection paths that avoid arbitrary `repr(...)` when possible

- Richer history metadata where it directly improves debugging:
  - execution mode
  - failure markers
  - replay and verify provenance once those features exist
  - optional tags if they add real value without bloating defaults

- Selective recovery controls:
  - selective reset (`reset --keep df,weather`): current `reset` is all-or-nothing. The friction is in the rebuild cost after reset, which is exactly this milestone's theme. Design questions (keep by name? by type? by pattern?) should not be rushed.

- File execution improvements:
  - partial file execution (`exec --lines 17-20 script.py`): run specific lines from a file without re-executing the whole script. The workaround (copy-paste lines as inline code) is functional but breaks the file-to-interactive workflow.

### API / Contract Notes

- Keep debug-oriented detail opt-in so the hot path stays compact.
- Grow history metadata in a backward-compatible way.
- Prioritize the smallest recovery-relevant facts first in error payloads and summaries.

## v0.5 - Verification And Reproducibility

### Goals

- Make clean verification a first-class workflow once the interactive loop is already efficient.
- Preserve honest provenance when replaying or verifying prior work.
- Help agents promote exploratory work into repeatable checks without paying the cost on every normal iteration.

### Planned Features

- Verification workflows first:
  - `agentnb verify` to restart a clean session and replay selected history or snapshot state
  - verification responses identify the first failed step and the source execution that produced it
- Session snapshots:
  - `agentnb snapshot create|list|restore`
- Replay workflows:
  - replay history to a new session
  - `agentnb replay --to-session <name>`
- Export follow-up:
  - export to `.ipynb`
  - export to markdown transcript

### Internal Design Constraints

- Keep replay and verify execution flows on the same run-control abstraction instead of creating separate orchestration paths.
- Keep public run semantics defined by the controller contract rather than by local subprocess behavior.
- Add a dedicated replay execution owner that translates semantic replay plans into executable work.
- Choose an honest replay persistence model:
  - either parent/child per-step run records
  - or a first-class composite replay record shape
- Preserve per-step provenance, source execution ids, code, outputs, and failure attribution across replay and verify flows.
- Extend history and journal metadata so replayed and verified steps remain distinguishable from original executions.

### API / Contract Notes

- Verification and replay responses must preserve source execution provenance clearly.
- Snapshot metadata remains tracked in `.agentnb/` with schema versioning.
- Reproducibility features should not distort the simpler runtime and run-control model built for the interactive loop.

## v0.6 - Rich Output, Artifacts, And Persistence Controls

### Goals

- Improve non-text outputs for data-heavy workflows after the core loop and reproducibility features are solid.
- Clarify which outputs are ephemeral versus intentionally persisted.

### Planned Features

- Structured artifacts:
  - tables, plots, HTML snippets, files
- Execution responses include `artifacts` in JSON mode.
- CLI helpers:
  - `agentnb artifacts list`
  - `agentnb artifacts open <id>`
- Output persistence controls:
  - recorded versus ephemeral execution modes
  - artifact retention policy and cleanup commands
  - optional promotion of prior execution results into saved artifacts

### Internal Design Constraints

- Separate persisted artifacts from transient execution outputs before artifact commands exist.
- Keep a first-class persisted artifact model with stable ids, metadata, and lifecycle state.

### API / Contract Notes

- Extend result schemas with backward-compatible artifact fields.
- Keep plain `stdout` / `result` contracts intact.
- Keep persisted artifact modeling behind the dedicated artifact domain boundary.

## v0.7 - Extensibility, Policy, And Alternate Control Surfaces

### Goals

- Turn internal seams into stable extension points once the core CLI is already efficient.
- Support richer integrations without contaminating the hot path.

### Planned Features

- Plugin interface:
  - custom operations and renderers
  - lifecycle hook registration
- Policy hooks:
  - pre/post execute checks
  - optional allow/deny rules
- Alternate control surfaces:
  - a uniform `call` or RPC-like shape over existing operations
  - stdin JSON request mode for tool wrappers and long-lived agent adapters

### Internal Design Constraints

- Give plugins, policy, and reliability hooks one deep home instead of growing ad hoc methods across runtime and CLI layers.
- Define typed execution lifecycle events and extension context objects before adding plugin loading.
- Keep extension APIs event/context-based rather than mirroring runtime internals.

### API / Contract Notes

- Version the plugin API surface explicitly.
- Policy violations return stable typed error codes.
- Alternate control surfaces should reuse existing app boundaries instead of inventing parallel behavior.

## v0.8+ - Runtime Backends And Collaboration

### Goals

- Decouple from local-only execution while keeping the CLI contract stable.
- Support headless and sharable workflows without regressing the single-agent local loop.

### Planned Features

- Alternate backends:
  - containerized local backend
  - remote backend connector
- Collaboration and CI modes:
  - headless CI run mode
  - import/export sharable session bundles

### Internal Design Constraints

- Grow the capability contract into the app, run-control, and extension boundary before adding non-local backends.
- Keep features branching on declared capabilities rather than backend type checks or local-only assumptions.

### API / Contract Notes

- Keep backend capability negotiation explicit (`supports_stream`, `supports_artifacts`, and similar capabilities).
- Preserve compatibility for local backend users.

## Cross-Cutting Work

- Documentation:
  - keep a tiny hot-path quickstart
  - keep deeper troubleshooting and integration docs available but off the critical path
  - maintain an agent-focused smoke-scenario catalog for deep iterative workflows
- Contract hardening:
  - schema regression tests
  - explicit deprecation policy for JSON fields
  - tests for compact `--agent` contracts once introduced
- Performance:
  - benchmark startup latency, round-trip execution latency, and memory overhead
  - measure token-oriented output size for common loops, not just runtime latency
- Output and noise control:
  - keep machine-oriented modes predictable during streaming and control-plane errors
  - optimize default responses for one-step-later decision-making
- Command-surface discipline:
  - prefer a small set of composable commands over feature-specific command growth
  - prefer defaults, selectors, and output shaping before adding new verbs
- State ownership:
  - keep session preferences, retention rules, and future sharable-bundle rules inside `StateRepository`

## Near-Term Priority Queue

Command-surface simplification is part of near-term recovery efficiency because agents pay for ambiguity with extra probing and wrong-session risk.

1. Recovery/debugging improvements that reduce session drops and extra probing
2. Verification workflows
3. Snapshots
4. Replay
5. Exports and artifacts
