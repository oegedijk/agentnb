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

Human ergonomics matter too, but they follow this same direction: fewer steps, clearer defaults, quieter output, and better recovery guidance.

## Design Rules

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

## v0.3.4 - Smoke-Driven CLI Consistency And Recovery Fixes

v0.3.4 should focus on the concrete usability gaps found by running all smoke scenarios end-to-end again. The goal is not new surface area; it is making the existing surface behave consistently enough that an agent can stay in flow without guessing.

Two prep refactors are now in place and should be treated as the primary seams for the remaining work:

- `RuntimeState` in `agentnb.runtime` centralizes lifecycle facts such as `missing`, `starting`, `ready`, `busy`, `dead`, and `stale`.
- `CommandShape` in `agentnb.invocation` centralizes command-family grammar and prefix-option placement.

The remaining fixes below should extend those seams instead of reintroducing timing inference in app/CLI code or token-shuffling heuristics in the parser.

### Shipped So Far

- Fixed dead-kernel detection on hard exits: when the kernel dies during an exec (`os._exit(1)`, crash, worker disappearance), the triggering command now fails quickly with `KERNEL_DEAD` instead of hanging until an external timeout or later `status` call reveals the problem.
- Made runtime state visible in `status` / `wait`: machine payloads now expose `runtime_state` and `session_exists`, and human output distinguishes `starting` / `dead` instead of flattening them into "not running".
- Added a structured "session busy" contract: command-lock files now persist acquisition metadata, `RuntimeState` carries current lock facts, `status` exposes `lock_pid` / `lock_acquired_at` / `busy_for_ms`, and `SESSION_BUSY` exec/reset failures include `wait_behavior`, `waited_ms`, and current lock metadata.
- Made `history @last-error` prefer real execution failures over incidental control-plane errors: journal queries now bias toward kernel-side execution errors when available, while still falling back to the latest control-plane error if no execution failure exists.
- Fixed `doctor --fix` install targeting for uv-managed projects: automatic ipykernel repair now runs from the target project root, and the `uv pip` fallback binds installation to the selected interpreter before re-checking module availability.
- Completed startup-state handling for read-only helper commands: `vars`, `inspect`, and `reload` now return structured `KERNEL_NOT_READY` responses with `runtime_state=starting` while a same-session startup is still in flight, instead of collapsing into generic no-kernel behavior.
- Tightened `runs show` snapshot semantics: active run snapshots now carry an explicit `snapshot_stale` flag in machine output, matching the existing human warning that persisted state may lag live follow output.
- Clarified same-session foreground/background serialization: overlapping `exec` / `reset` attempts now fail fast against active persisted run records before startup checks run, `SESSION_BUSY` responses include the blocking `active_execution_id`, and recovery suggestions point directly at `runs wait/show` for that run.
- Improved current-session visibility in multi-session workflows: implicit session-bound commands now refresh the saved current-session preference and surface a switch notice when agentnb resolves to a different known session than the one previously targeted.

### Planned

- Unify `--session` and `--project` option placement across commands: top-level and subcommand-position forms should both work consistently for `history`, `runs`, lifecycle commands, and any other command that advertises those options. Extend `CommandShape` metadata and canonicalization instead of adding parser exceptions.
- Fix run-control command targeting docs and behavior: if `runs show/follow/wait/cancel` are intentionally session-independent once an `execution_id` is known, remove misleading help text that suggests `--session` works there; otherwise, make the grammar and declarations agree through `CommandShape` and `cli.py`.
- Add a first-class partial file rerun path: support rerunning only the changed tail of a file-backed workflow without manual copy-paste back into inline exec.
- Improve in-session dependency recovery guidance: when a module import fails, suggestions should acknowledge uv-managed environments, pip-less venvs, and the difference between installing into the project environment versus the caller's environment.
- Make cross-project driving uniform: `--project /other/path` should work the same way for `exec`, `vars`, `history`, `runs`, and lifecycle commands, with no command-specific flag placement surprises. This should be expressed through `CommandShape` plus matching Click declarations.

### Owning Seams

The smoke failures cluster into a small number of existing modules. v0.3.4 should deepen those modules instead of spreading fixes across Click handlers.

- `CommandShape` + `InvocationResolver` in `agentnb.invocation` own command-family grammar, prefix/suffix flag placement, and implicit-exec inference. The `--project` / `--session` consistency fixes should land by extending that grammar first, not as command-local parser exceptions in `cli.py`.
- `AgentNBApp` in `agentnb.app` owns command-level session resolution and response shaping. Startup-race behavior for read-only commands, multi-session current-session visibility, and any "wait briefly vs fail fast" policy should be decided here and then delegated downward.
- `RuntimeState` + `KernelRuntime` in `agentnb.runtime` own kernel liveness, startup, busy/idle waiting, interrupt, and doctor integration. Hard-exit detection, `starting` versus `missing`, and the distinction between dead kernel, stale session record, and active startup all belong here.
- `SessionStateFiles` / `StateRepository` in `agentnb.state` own lock files and persisted runtime markers. If v0.3.4 needs richer startup or contention metadata than the current `RuntimeState` can infer, that metadata should be modeled here rather than inferred ad hoc from CLI timing.
- `LocalRunManager`, `ExecutionStore`, and `RunSelectorResolver` in `agentnb.runs` and `agentnb.selectors` own background-run semantics, follow/show/wait/cancel behavior, selector resolution, and snapshot freshness. The `runs show` staleness fix and same-session fail-fast serialization policy belong in this layer.
- `CommandJournal` / `HistorySelectorResolver` in `agentnb.journal` and `agentnb.selectors` own how history is selected and filtered. The `@last-error` ambiguity between execution failures and incidental control-plane failures should be resolved here, not in the renderer.
- `KernelProvisioner` in `agentnb.kernel.provisioner` owns interpreter choice, ipykernel installation, and doctor/fix behavior. The broken `doctor --fix` path and uv/pip-less environment handling belong entirely inside this seam.
- `AdvicePolicy` in `agentnb.advice` owns next-step recovery guidance. Missing-module suggestions, uv-aware install hints, and clearer post-error recovery advice should be fixed here after the runtime/provisioner behavior is corrected.
- `RenderOptions` / response rendering in `agentnb.output` and `ResponseProjector` in `agentnb.projection` own how transient state is presented. If v0.3.4 adds `starting`, stale snapshot warnings, or richer busy metadata, those payloads should be rendered here without changing command handlers.
- File-to-interactive rerun support should not be bolted onto `cli.py`. It likely needs a dedicated execution-planning seam near `ExecInvocationPolicy` / `ExecutionService` so file slicing, provenance, and history labels remain consistent with normal exec.

### Prep Refactors (shipped)

The two abstraction-deepening steps that v0.3.4 depended on are now in place. Remaining fixes in this milestone should build on them rather than bypassing them.

#### 1. Runtime / Session State (shipped foundation)

Shipped foundation:

- `agentnb.runtime` now has an explicit `RuntimeState` model instead of treating lifecycle as a loose combination of session-file presence, backend `alive`, and command-lock status.
- States such as `missing`, `starting`, `ready`, `busy`, `dead`, and `stale` are now first-class runtime facts.
- `KernelRuntime.start/status/wait_for_usable/doctor` now project through that model while preserving compatibility at the `KernelStatus` edge.

Use it next for:

- hard-exit detection during exec
- startup-race policy for read-only commands
- richer session-busy metadata and contention reporting
- any future persisted startup/lock metadata that `RuntimeState` needs but cannot infer cleanly today

#### 2. Invocation / CLI Grammar (shipped foundation)

Shipped foundation:

- `agentnb.invocation` now has a `CommandShape` model that owns command-family option-placement rules.
- `InvocationResolver` canonicalization now derives from that grammar instead of hard-coded group-command token shuffling heuristics.
- The parser boundary now has a deeper, more extensible source of truth for accepted prefix placement.

Use it next for:

- remaining `--project` / `--session` consistency work
- run-control command targeting semantics and help text
- cross-project flag placement cleanup
- future command-family growth without reviving parser exceptions

### Change Map

- Dead-kernel detection:
  `RuntimeState`, `KernelRuntime.execute`, backend status checks, and possibly `LocalIPythonBackend.execute` need a fast path for "connection died during exec" so the original command fails with a typed dead-kernel error.
- Startup race clarity:
  `RuntimeState`, `KernelRuntime.status`, `wait_for_usable`, and `AgentNBApp._handle_command` need a shared notion of `session is starting` rather than treating missing session state and in-flight startup as the same condition.
- Busy/serialization clarity:
  `RuntimeState`, `SessionBusyError`, `ExecPayload`/error payload contracts, and `LocalRunManager` now carry the fail-fast same-session contract, including blocking-run metadata (`active_execution_id`) and non-blocking implicit session resolution.
- Background follow/show semantics:
  `LocalRunManager.follow_run`, `wait_for_run`, `get_run`, and the stored `ExecutionRecord` shape are where snapshot freshness and queued-command semantics should be clarified.
- `history @last-error` behavior:
  `HistorySelectorResolver.resolve_query`, `CommandJournal.select`, and the journal classification/provenance model are the right seams if "last execution error" becomes distinct from "last command error".
- Cross-project consistency:
  `CommandShape`, `InvocationResolver`, and the Click option declarations in `cli.py` need to agree on which options are root-positionable. The app layer is already project-parameterized; the parser layer is the inconsistent part.
- Doctor/install fixes:
  `KernelProvisioner.ensure_ipykernel`, `_ipykernel_install_cmd`, and `doctor(auto_fix=True)` need to use the target project's environment deterministically and verify installation in that same environment.
- Missing dependency guidance:
  the behavior split is "runtime/provisioner decides what works" and `AdvicePolicy` tells the user what to do next. Those two modules should be updated together so suggestions match the real environment model.
- Partial file rerun:
  `CommandShape`, `InvocationResolver.resolve_exec_source`, `ExecInvocationPolicy`, and `ExecutionService` are the likely seams for supporting line-ranged or tail-only file execution while preserving execution history honestly.

### Deferred

- Decide whether read-only commands should wait during startup/busy windows or always fail fast with structured state. The smoke run showed the current hybrid behavior is confusing, but the fix should be a deliberate policy choice, not an accidental timeout tweak.

## v0.4 - Recovery, Debugging, And Inspection Efficiency

### Goals

- Make failures cheaper to diagnose without dropping session state.
- Improve inspection and recovery so the agent can continue instead of restarting.
- Reduce the amount of output and follow-up probing needed to understand a bad state.

### Planned Features

- Better debugging:
  - traceback enrichment
  - frame and locals inspection commands
  - optional profiling (`cProfile`) paths where useful
- Safer, more compact inspection:
  - bounded previews for large values
  - structured previews for common containers (`list`, `dict`, `tuple`, dataframe-like objects)
  - side-effect-aware inspection paths that avoid arbitrary `repr(...)` when possible
- Richer history metadata where it directly improves debugging:
  - execution mode
  - failure markers
  - replay and verify provenance once those features exist
  - optional tags if they add real value without bloating defaults
- Recovery-oriented control-plane improvements:
  - health checks and structured diagnostics
  - improved cleanup for stale state
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

1. Recovery/debugging improvements that reduce session drops and extra probing
2. Verification workflows
3. Snapshots
4. Replay
5. Exports and artifacts
