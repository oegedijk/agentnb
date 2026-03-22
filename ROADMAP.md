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

## v0.3.5 - Agent Correctness And Machine-Contract Frictions (shipped)

v0.3.5 closed the highest-friction agent issues around helper reads, session ambiguity, and machine-readable recovery.

### Shipped

- Read-only helpers now auto-start unambiguous sessions, wait behind same-session work, and report `started_new_session`, `waited`, `waited_for`, `waited_ms`, `initial_runtime_state`, and `blocking_execution_id`.
- Omitted-session `exec` and implicit top-level exec now fail with hard `AMBIGUOUS_SESSION` errors when multiple live sessions exist.
- Compact `--agent` responses now preserve structured `suggestion_actions` for ambiguity and recovery flows.
- Bare `sessions` now behaves like `sessions list`, including `--project` and `--json`, and the docs/examples now match.
- Helper access metadata now lives in a typed contract and flows through introspection, ops, app shaping, and run-control boundaries instead of ad hoc payload mutation.

### Remaining

- Large-value exec output still leans on repr strings; bounded structured exec summaries remain future work.
  - problem:
    - `exec` still mostly surfaces the backend result as a compacted string repr
    - this is acceptable for scalars but weak for lists, dicts, dataframe-like values, and nested structures where agents need a quick shape summary rather than a long opaque repr
  - owning seams:
    - [execution_output.py](/Users/oege/projects/agentnb/src/agentnb/execution_output.py) for stable output modeling
    - [compact.py](/Users/oege/projects/agentnb/src/agentnb/compact.py) for bounded summary logic
    - [app.py](/Users/oege/projects/agentnb/src/agentnb/app.py), [projection.py](/Users/oege/projects/agentnb/src/agentnb/projection.py), and [output.py](/Users/oege/projects/agentnb/src/agentnb/output.py) only as consumers of the summary
  - minimal implementation guidance:
    - keep `result` as the compatibility field
    - add a bounded structured summary for common container-like results instead of pushing more repr truncation into each caller
    - prefer a narrow exec-summary seam over a broad response refactor
  - validation:
    - unit tests around compact summaries for sequence-like, mapping-like, and dataframe-like exec results
    - CLI smoke with `uv run agentnb exec --json` on a large list/dict/dataframe-like value to confirm the response stays compact and decision-useful

- JSON cleanup beyond structured suggestion actions, including traceback hygiene, remains future work.
  - problem:
    - error details are cleaner than before, but `exec` and agent-mode responses still mix compact payload fields, top-level error envelopes, and traceback cleanup in more than one place
    - this makes the machine contract harder to reason about and encourages ad hoc shaping
  - owning seams:
    - [compact.py](/Users/oege/projects/agentnb/src/agentnb/compact.py) for traceback cleanup and bounded error text
    - [projection.py](/Users/oege/projects/agentnb/src/agentnb/projection.py) for compact agent projection
    - [contracts.py](/Users/oege/projects/agentnb/src/agentnb/contracts.py) as the stable envelope boundary
  - minimal implementation guidance:
    - keep one canonical traceback-cleaning path
    - keep machine-oriented detail opt-in and compact by default
    - do not add more command-local shaping branches in `app.py`; prefer cleaning once and projecting many times
  - validation:
    - contract-focused tests for `--json` and `--agent` parity on representative execution failures
    - smoke with `uv run agentnb exec --json "1/0"` and `uv run agentnb exec --agent "1/0"` to verify stable top-level error fields and compact tracebacks

- Wait-state visibility for `status --wait-idle` can still get clearer in human output.
  - problem:
    - `status --wait-idle` is now a more reliable readiness gate, but human output is still terse and does not clearly explain that it waited, or what condition it satisfied before returning
  - owning seams:
    - [app.py](/Users/oege/projects/agentnb/src/agentnb/app.py) for the wait payload facts already emitted (`waited`, `waited_for`)
    - [output.py](/Users/oege/projects/agentnb/src/agentnb/output.py) for the actual user-facing clarity improvement
  - minimal implementation guidance:
    - treat this as a presentation problem, not another runtime/race fix
    - make human output explicitly mention that the command waited for the session to become idle when that happened
    - only add new payload fields if the existing `waited` / `waited_for` facts are insufficient
  - validation:
    - renderer tests proving waited status is visible in human mode
    - smoke with a short background run followed immediately by `uv run agentnb status --wait-idle`

- Missing-dependency recovery inside fresh pip-less interpreters remains future work.
  - problem:
    - the interpreter selection and install path in `doctor` / `start` is better than before, but pip-less environments still need clearer recovery and cleaner fallback behavior
    - the rough edge is not just install failure; it is making the next working command obvious when `pip` is absent
  - owning seams:
    - [kernel/provisioner.py](/Users/oege/projects/agentnb/src/agentnb/kernel/provisioner.py) for interpreter selection, install fallback, and doctor reporting
    - advice/help call sites only for messaging consistency, not provisioning logic
  - minimal implementation guidance:
    - make `ProvisioningError` messages explicit about the exact manual recovery command in pip-less environments
    - keep `doctor` and `start` aligned on the same recovery wording
    - degrade cleanly when `pip` is unavailable rather than implying the normal `python -m pip install ...` path will work
  - validation:
    - unit tests for pip-missing interpreter selection/install failure branches
    - smoke against a controlled pip-less interpreter or a stubbed provisioner path to verify `doctor` / `start` suggestions are actionable

## v0.3.6 - Command Surface Simplification And Footgun Removal (shipped)

v0.3.6 shipped the command-surface simplification pass for the remaining
agent-confusing behaviors. The release kept the major feature surface stable
while making the public grammar smaller, the canonical forms clearer, and the
default recovery paths more reliable.

### Shipped

- Narrowed omitted-session command targeting: bare session-bound commands now raise `AMBIGUOUS_SESSION` when multiple live sessions exist, and implicitly resolved read/control commands no longer rewrite remembered current-session preference.

- Made `wait` the primary documented blocking readiness command: `status --wait` and `status --wait-idle` remain supported compatibility forms, but help, README, and recovery wording now steer agents toward `wait`.

- Kept history selectors first-class and added `--successes`: `history --successes --latest` now matches `history @last-success`, and equivalent selector/flag combinations normalize while contradictory combinations fail.

- Made `sessions list` the canonical documented form while keeping bare `sessions` as a supported alias.

- Documented one canonical CLI grammar for subcommands and added standard top-level affordances including root `--version`.

- Fixed human-mode ambiguity for read helpers in multi-session contexts by including stable session identity in `vars` and `inspect` output.

- Fixed `status --wait-idle` as a readiness gate for background work: it now waits for both runtime idleness and any active persisted run on the target session before reporting ready.

- Deepened `SessionTargetingPolicy` as the owning seam for command-target provenance, explicit-preference persistence, run-scope preference reads, and switch-notice decisions.

### Remaining

- No additional `0.3.6` implementation work remains.

### API / Contract Notes

- Implicitly resolved read/control commands do not rewrite remembered current-session preference.
- `wait` is the primary blocking command, while `status --wait` and `status --wait-idle` remain compatibility surface.
- `history @latest`, `@last-error`, and `@last-success` stay, and `--successes --latest` is the flag equivalent of `@last-success`.
- Bare `sessions` remains a supported alias, while `sessions list` is the canonical documented form.

### Acceptance Notes

- Tests proving implicit read/control commands do not mutate current-session preference.
- Tests for `history @last-success` parity with `history --successes --latest`.
- Docs/help parity tests for `sessions`, `wait`, canonical command shapes, and root `--version`.

### Implementation Seams

- `SessionTargetingPolicy`:
  - owns command-target provenance, explicit-preference persistence, run-scope preference reads, and switch-notice decisions

- `StateRepository` + `KernelRuntime`:
  - own persisted preference storage, low-level session resolution facts, and actual runtime busy/idle truth

- `AgentNBApp`:
  - owns command-level blocking semantics, background follow-up guidance, and the demoted `status --wait*` documentation path

- Selector resolvers and history query validation:
  - own selector/flag equivalence, contradiction checks, and the `--successes` addition

- `InvocationResolver` plus CLI help and docs:
  - own canonical command grammar, root-flag placement guidance, support for compatibility aliases, and the top-level `--version` / help wording cleanup

- `ResponseProjector` / output rendering:
  - include `session_id` in human-mode output headers when more than one session is alive for the project

- Docs/help parity tests:
  - ensure README, `--help`, and agent skill examples stay aligned with the actual surface and the intended primary command paths

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
