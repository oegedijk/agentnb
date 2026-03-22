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

### Follow-up cleanup shipped in the v0.3.6 release cut

- Exec-like payloads now derive bounded structured `result_preview` summaries through the shared execution-output / compact seam, while keeping `result` as the compatibility field.
- Error envelopes now normalize tracebacks once through the shared error contract, keeping `--json`, `--agent`, and human output aligned.
- Human wait output now explicitly says when the command waited, what it waited for, and the initial runtime state when known.
- Pip-less provisioning failures now surface one concrete manual recovery command, and `doctor --fix` reuses the same command that `start --auto-install` would suggest.

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

## v0.3.7 - Smoke-Test Contract And Recovery Polish

v0.3.7 should close the highest-value ergonomic issues that still showed up in the latest end-to-end smoke run. The focus is not feature expansion; it is removing the places where an agent still has to hesitate, guess, or special-case command behavior.

### Planned Issues

- Issue: JSON envelope field paths are still inconsistent across command families.
  Reproduce:
  - Run `uv run agentnb --json --session smoke-json "1 + 1"` and inspect `data.status`.
  - Start a background run, then run `uv run agentnb --json runs show @active` and inspect `data.run.status`.
  - Drive both through one parser that expects a stable status field path.
  Why this is a problem for ergonomic use:
  - Agents have to branch on command family just to find the same semantic fact.
  - This increases token cost and parser complexity in the middle of otherwise simple loops.

- Issue: Top-level grammar guidance still overpromises `--session` / `--project` placement for some `runs` commands.
  Reproduce:
  - Read the top-level `uv run agentnb --help` guidance about subcommand option placement.
  - Run `uv run agentnb runs show <execution_id> --session NAME`.
  - Observe that `runs show` rejects `--session` even though the top-level grammar guidance suggests that post-subcommand placement always works.
  Why this is a problem for ergonomic use:
  - Agents should not need a separate memorized grammar for one command family.
  - A wrong guess here breaks momentum and pushes the agent back into `--help` or trial-and-error.

- Issue: Dead-kernel auto-recovery is still too implicit about state loss.
  Reproduce:
  - Run `uv run agentnb --session smoke-crash "x = 1; x"`.
  - Run `uv run agentnb --session smoke-crash "import os; os._exit(1)"`.
  - Retry with `uv run agentnb --session smoke-crash "x"`.
  - Observe that recovery proceeds through a fresh kernel, but the user-facing signal is indirect state loss rather than an explicit restart notice.
  Why this is a problem for ergonomic use:
  - Agents may misclassify the follow-up `NameError` as an ordinary coding mistake instead of a session replacement event.
  - Hidden state loss is one of the most expensive footguns for a persistent-REPL product.

- Issue: Missing-module recovery guidance is still biased toward dependency management instead of live-session repair.
  Reproduce:
  - In a clean project venv, run `uv run agentnb --project /path/to/project --session smoke-missing "import humanize"`.
  - Observe the recovery suggestion pointing to `uv add humanize`.
  - In the same session, try `sys.executable -m pip install humanize` and observe that a fresh `uv venv` may not even have `pip`.
  Why this is a problem for ergonomic use:
  - The shortest path for a live exploratory loop is not always the same as the right long-term project dependency workflow.
  - Agents need a clear "recover now" path when they are already inside a running session.

- Issue: automatic dependency installation during startup/doctor is too brittle and too stateful for this CLI.
  Reproduce:
  - Trigger a missing-kernel-dependency path such as missing `ipykernel` in a fresh project environment.
  - Run `uv run agentnb start --auto-install` or `uv run agentnb doctor --fix`.
  - Observe agentnb trying to choose and run an installer itself across environment-management variants such as uv-managed envs, pip-less envs, and active live sessions.
  Why this is a problem for ergonomic use:
  - Installing packages is environment management, not durable REPL control; folding it into agentnb increases ambiguity around which interpreter/environment was changed.
  - Auto-install behavior is brittle across `pip` vs `uv` differences and makes recovery harder to reason about when startup still fails.
  - The cleaner contract is: detect the missing dependency, print one concrete install command, then tell the user/agent to restart with `--fresh` after installation.

- Issue: readiness wording is still ambiguous after `status --wait-idle`.
  Reproduce:
  - Start background work in a session.
  - Run `uv run agentnb status --wait-idle --session NAME`.
  - Observe output shaped like "Kernel is running ... after waiting ... for idle from busy."
  Why this is a problem for ergonomic use:
  - "Running" describes process liveness, not readiness to accept the next command.
  - Agents should be able to treat the command as a gate without reading between the lines.

- Issue: `inspect` previews for nested JSON-like structures still lose too much structure.
  Reproduce:
  - Load a nested public JSON API response into a variable.
  - Run `uv run agentnb inspect VAR --session NAME`.
  - Observe nested dict/list content degrading into stringified fragments.
  Why this is a problem for ergonomic use:
  - API exploration is a primary agent workflow.
  - If the preview is bounded but structurally unclear, the agent ends up falling back to raw printing and spending more context.

- Issue: `inspect` only accepts top-level variable names, not dotted or bracket access into live objects.
  Reproduce:
  - Run `uv run agentnb --session smoke-inspect --fresh "import pandas as pd; df = pd.DataFrame({'a':[1,2], 'b':[3,4]}); 'ready'"`.
  - Run `uv run agentnb inspect df --session smoke-inspect` and observe the expected DataFrame summary.
  - Run `uv run agentnb inspect "df.a" --session smoke-inspect` or `uv run agentnb inspect "df['a']" --session smoke-inspect`.
  - Observe that both fail as undefined variable lookups rather than inspecting a live sub-value.
  Why this is a problem for ergonomic use:
  - Agents naturally think in terms of inspecting the next interesting sub-object, especially DataFrame columns and object attributes.
  - Forcing a separate assignment step just to inspect an obvious sub-value adds ceremony to exploratory loops.

- Issue: `history --help` still does not advertise selector vocabulary even though selector-driven flows are first-class.
  Reproduce:
  - Run `uv run agentnb history --help`.
  - Observe that it shows `[REFERENCE]` but gives no examples or selector list.
  - Compare with `uv run agentnb runs show --help`, which explicitly documents selectors such as `@latest`, `@last-error`, `@last-success`, and `@active`.
  Why this is a problem for ergonomic use:
  - Agents have to guess whether selectors are supported on `history`, even though they are central to error recovery and orientation.
  - Help output should remove guesswork at exactly the moment an agent reaches for it.

- Issue: file execution remains too opaque when the executed file ends in assignments rather than a final expression.
  Reproduce:
  - Create a file that defines several useful variables but does not end in a final expression.
  - Run it with `uv run agentnb script.py`.
  - Observe the generic "Execution completed." response, then discover state only by explicitly calling `vars` or `history`.
  Why this is a problem for ergonomic use:
  - File-to-interactive is supposed to be smoother than an edit-and-rerun-only loop.
  - The agent should get a compact hint about what changed without needing an immediate follow-up probe.

- Issue: session cleanup still becomes awkward after many exploratory branches.
  Reproduce:
  - Run many scenarios with multiple named sessions.
  - Call `uv run agentnb sessions list`, then try a bare `uv run agentnb "1 + 1"` in a project with many leftover sessions.
  - Observe that ambiguity and cleanup pressure arrive before the actual work starts.
  - Then try to clean up only the no-longer-needed sessions efficiently.
  - Observe that the cleanup path is still noisy enough to discourage active session hygiene.
  Why this is a problem for ergonomic use:
  - Agents benefit from branching, but the system should not punish that behavior by making later targeting and cleanup harder.
  - Session clutter increases ambiguity pressure and drives unnecessary `sessions list` calls.
  - When targeting is unclear, session-scoped flags such as `--fresh` can appear unreliable simply because the agent is operating on a different session than expected.

- Issue: current-session preference after `stop` is still surprising enough to revive a previously stopped named session on a later bare exec.
  Reproduce:
  - In an isolated project, start two named sessions such as `alpha` and `beta`.
  - Stop both sessions with explicit `stop --session ...` commands.
  - Run a bare `uv run agentnb --project /tmp/project "'hi'"` with no `--session`.
  - Observe that agentnb may revive a remembered stopped session name instead of starting from a neutral default.
  Why this is a problem for ergonomic use:
  - After `stop`, an agent expects either a clean default or an explicit ambiguity, not silent reuse of a stopped workflow name.
  - Reviving the wrong session identity makes later missing-variable errors harder to interpret.

- Issue: human `runs show` output still collapses multi-line stdout into a single line.
  Reproduce:
  - Start a background run that prints multiple lines over time.
  - Wait for it to finish with `uv run agentnb runs wait <execution_id>`.
  - Run `uv run agentnb runs show <execution_id>`.
  - Observe `stdout:` rendered as `line-0 line-1 line-2` rather than preserving line breaks.
  Why this is a problem for ergonomic use:
  - Agents often inspect run output to understand progress and intermediate state.
  - Flattening line-oriented output makes logs harder to scan and removes structure that was already present.

- Issue: `--quiet` and `--no-suggestions` still have blurry, hard-to-predict semantics.
  Reproduce:
  - Run the same successful command with default output, `--quiet`, and `--no-suggestions`.
  - Observe that `--quiet` still keeps the session-targeting footer while `--no-suggestions` may remove it.
  - Run the same failing command with default output, `--quiet`, and `--no-suggestions`.
  - Observe that both flags suppress the `Next:` block, making the distinction hard to infer from behavior alone.
  Why this is a problem for ergonomic use:
  - Agents need to know which flag reduces chatter without hiding recovery guidance they still need.
  - Output-shaping flags should be predictable enough that an agent can choose them without experimentation.

### Release Goal

Make the latest smoke-run findings disappear by tightening the machine contract, clarifying recovery state, and reducing the number of follow-up probes an agent must issue just to regain confidence about what session it is talking to.

## v0.3.8 - Low-Noise Discoverability And Inspection Polish

v0.3.8 should pick up the remaining smoke frictions that are real but lower-severity than the contract and recovery issues in v0.3.7. The theme is reducing unnecessary guessing and output churn once the core command behavior is already correct.

### Re-opened Issues

- Re-opened: stale-session reconciliation makes `sessions delete --stale` effectively invisible in normal use.
  Previously marked done:
  - `v0.3.3` added `sessions delete --all` and `sessions delete --stale`.
  Reproduce:
  - Run `uv run agentnb --project /tmp/agentnb_validate_stale --session stale-a --fresh "import os; os.getpid()"`.
  - Kill the kernel pid externally.
  - Run `uv run agentnb sessions list --project /tmp/agentnb_validate_stale`.
  - Run `uv run agentnb sessions delete --stale --project /tmp/agentnb_validate_stale`.
  - Observe that listing already removed the stale session, and `delete --stale` reports `No sessions to delete.`
  Why this is a problem for ergonomic use:
  - The cleanup feature exists, but the user-facing model is unclear because stale sessions may disappear before the dedicated cleanup command can act.
  - Agents should be able to reason about whether stale cleanup is automatic, manual, or both.

- Re-opened: current-session preference after `stop` can still revive a previously stopped named session on a later bare exec.
  Previously marked done:
  - `v0.3.4` improved current-session visibility in multi-session workflows.
  - `v0.3.6` narrowed omitted-session targeting and session-preference mutation rules.
  Reproduce:
  - In an isolated project, run `uv run agentnb --project /tmp/agentnb_validate_stop --session alpha --fresh "'alpha'"`.
  - Run `uv run agentnb --project /tmp/agentnb_validate_stop --session beta --fresh "'beta'"`.
  - Stop both with `uv run agentnb stop --project /tmp/agentnb_validate_stop --session alpha` and `... --session beta`.
  - Run a bare `uv run agentnb --project /tmp/agentnb_validate_stop "import os; {'pid': os.getpid()}"`.
  - Run `uv run agentnb sessions list --project /tmp/agentnb_validate_stop` and observe that `alpha` was revived.
  Why this is a problem for ergonomic use:
  - After `stop`, an agent expects a neutral default or an explicit ambiguity, not reuse of an old stopped workflow name.
  - Wrong-session revival turns later missing-variable errors into confusing state-targeting failures.

- Re-opened: `history --help` still omits selector discoverability even though selector support is first-class.
  Previously marked done:
  - `v0.3.6` kept history selectors first-class and documented the canonical command surface.
  Reproduce:
  - Run `uv run agentnb history --help`.
  - Compare with `uv run agentnb runs show --help`.
  - Observe that `runs show --help` lists selectors, while `history --help` only shows `[REFERENCE]`.
  Why this is a problem for ergonomic use:
  - Agents reach for help specifically to avoid guessing.
  - If selectors are central to history recovery flows, they need to be discoverable from `history --help` itself.

- Re-opened: human `runs show` output still collapses multiline stdout into one line.
  Previously marked done:
  - No exact shipped item claimed this, but the surrounding run-snapshot polish in `v0.3.4` implied the human snapshot path was in good shape.
  Reproduce:
  - Start a background run that prints multiple lines.
  - Wait for completion with `uv run agentnb runs wait <execution_id>`.
  - Show the snapshot with `uv run agentnb runs show <execution_id>`.
  - Observe `stdout:` rendered as `line-0 line-1 line-2` instead of preserving line breaks.
  Why this is a problem for ergonomic use:
  - Agents often use run snapshots to understand progress and intermediate output.
  - Flattening line-oriented output removes structure that was already present in the run.

### Planned Issues

- Issue: `runs follow` is awkward for bounded, programmatic observation.
  Reproduce:
  - Start a long-running background run.
  - Run `uv run agentnb runs follow <execution_id>`.
  - Observe that the command is optimized for an open-ended terminal stream rather than "follow for a short window, then return control."
  Why this is a problem for ergonomic use:
  - Agents often want a brief observation window, not an unbounded blocking stream.
  - Without a bounded follow mode, the agent has to choose between hanging on the stream or falling back to repeated polling.

- Issue: timeout recovery suggestions are still more cautious than they need to be.
  Reproduce:
  - Run `uv run agentnb --session smoke-timeout --timeout 1 "import time; time.sleep(2)"`.
  - Observe that the error suggests `agentnb interrupt` even when the kernel may already be idle again by the time the response is rendered.
  Why this is a problem for ergonomic use:
  - Agents may spend extra commands on defensive cleanup that is no longer necessary.
  - Recovery suggestions are most useful when they are both safe and tightly matched to current runtime state.

- Issue: `--fresh`, `reset`, and `stop` still require too much comparative reading to choose correctly.
  Reproduce:
  - Read the help text for normal exec, `reset`, and `stop`.
  - Try to answer "which one clears variables, which one restarts the interpreter, and which one just shuts everything down?"
  - Observe that the distinctions are spread across separate help surfaces rather than compared directly.
  Why this is a problem for ergonomic use:
  - Agents should not need three separate help reads to select the right cleanup primitive.
  - Cleanup choices are common enough that a compact comparison belongs on the hot path.

- Issue: session-targeting notices are still noisier than they need to be in repeated human-mode loops.
  Reproduce:
  - Run several human-mode execs in the same session.
  - Observe repeated `(now targeting session: NAME)` output even when the session has not changed.
  Why this is a problem for ergonomic use:
  - The notice is valuable when targeting changes, but low-value repetition adds noise to iterative loops.
  - This is especially visible when an agent stays in human mode for compact interactive work.

- Issue: command typo recovery still falls back to generic Click errors without agent-oriented hints.
  Reproduce:
  - Run a common misguess such as `uv run agentnb list` or `uv run agentnb log`.
  - Observe the generic unknown-command error with no specific "did you mean?" guidance.
  Why this is a problem for ergonomic use:
  - Discoverability should help agents recover from plausible guesses without a full help re-read.
  - Common misguesses are predictable enough that the CLI can steer them cheaply.

- Issue: `inspect` truncation still hides too much about what was omitted.
  Reproduce:
  - Inspect a wide DataFrame or nested JSON-like structure.
  - Observe truncated `columns`, `dtypes`, `nulls`, or nested sample values without a clear indicator of how much was omitted.
  Why this is a problem for ergonomic use:
  - Agents can mistake a bounded preview for a complete view.
  - Bounded output works better when truncation is explicit rather than implicit.

- Issue: `--result-only` behavior on large values is still not clearly explained by the help text.
  Reproduce:
  - Run `uv run agentnb --result-only` against a large list or DataFrame-like value.
  - Observe that the output may be a bounded summary rather than the literal full repr.
  Why this is a problem for ergonomic use:
  - The flag name suggests "just the result," but the implementation sensibly returns a compact projection.
  - Agents need the documented expectation to match the actual output shape.

- Issue: file-exec truncation does not yet steer users toward the right escape hatch.
  Reproduce:
  - Run a file that produces analysis output large enough to truncate in default human mode.
  - Observe the truncated output without a targeted hint about `--no-truncate` or a better inspection path.
  Why this is a problem for ergonomic use:
  - Agents need a fast path from "this output was cut off" to "here is the correct next command."
  - File-driven workflows lose momentum if the first response hides results and does not name the escape hatch.

- Issue: stale-session reconciliation and stale-session deletion are still conceptually muddy.
  Reproduce:
  - Accumulate sessions, kill one externally, then run `uv run agentnb sessions list` and `uv run agentnb sessions delete --stale`.
  - Observe that stale cleanup may already have happened before deletion, making the feature hard to reason about.
  Why this is a problem for ergonomic use:
  - Agents should be able to tell whether stale sessions are auto-reconciled, manually cleaned, or both.
  - Cleanup commands feel more trustworthy when their scope is legible.

- Issue: session log retention is still unbounded enough to create `.agentnb` clutter over time.
  Reproduce:
  - Run many sessions and inspect `.agentnb/` after repeated smoke-style workflows.
  - Observe that old kernel logs accumulate even after session cleanup.
  Why this is a problem for ergonomic use:
  - Persistent clutter in the state directory undermines the feeling of clean lifecycle management.
  - Long-running agent workflows need basic retention hygiene even before richer artifact persistence exists.

- Issue: `history --all` remains weakly differentiated from normal `history` in common debugging flows.
  Reproduce:
  - Trigger a failure and compare `uv run agentnb history @last-error` with `uv run agentnb history --all --last 5`.
  - Observe that the extra internal entries may look like near-duplicates with unclear added value.
  Why this is a problem for ergonomic use:
  - Agents should know when `--all` is worth the extra output cost.
  - If the flag is useful only in narrow cases, that value should be surfaced more clearly.

### Release Goal

Make the human-mode and discoverability surfaces feel more intentional: less repetitive noise, clearer truncation, better typo recovery, and lower hesitation when choosing inspection and cleanup commands.

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
  - a clearer value proposition for `history --all` versus normal semantic history

- Selective recovery controls:
  - selective reset (`reset --keep df,weather`): current `reset` is all-or-nothing. The friction is in the rebuild cost after reset, which is exactly this milestone's theme. Design questions (keep by name? by type? by pattern?) should not be rushed.

- File execution improvements:
  - partial file execution (`exec --lines 17-20 script.py`): run specific lines from a file without re-executing the whole script. The workaround (copy-paste lines as inline code) is functional but breaks the file-to-interactive workflow.
  - richer file-to-interactive handoff summaries once the file-execution surface grows

- Session-local environment and shell affordances:
  - a clearer live-session dependency install path than ad hoc subprocess calls
  - optional shell escape / helper flow if it can be added without contaminating the core execution model

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
