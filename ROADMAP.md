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

## v0.3.1 - Output Correctness And Ergonomic Fixes

### Goals

- Fix output-path bugs that break agent and human consumption.
- Make error responses, background dispatch, and run-control messages say what they mean.
- Improve context-awareness of suggestions and inspection so the agent wastes fewer tokens on wrong guesses.

### Planned Features

- Fix duplicate error output (highest impact):
  - execution errors print the full traceback and "Next:" suggestions twice — once as human text and once as the error block
  - confirmed across every error scenario including `--quiet` mode; the duplication is unconditional
  - root cause: errors are written to stderr and the rendered output to stdout; combined capture doubles the content
- Fix `reset` output message:
  - `reset` currently prints "Execution completed." which sounds like code was run, not that state was cleared
  - change to "Namespace cleared." or "User variables removed." to reflect what actually happened
- Context-aware suggestions (remaining):
  - after `SESSION_BUSY` (serialization lock), suggest `agentnb wait` as the primary recovery path, not `history @last-error` / `interrupt` / `reset`
  - after a dead-kernel error ("Kernel process is not running"), suggest `agentnb start` or `agentnb doctor`, not `interrupt` / `reset`
  - put this logic in `AdvicePolicy` rather than spreading special cases
- Fix `--session` and `--project` position consistency:
  - `agentnb --session X history` and `agentnb --project /path runs list` fail; both flags must go after the subcommand name for non-exec commands
  - documentation and help text corrected to reflect the actual constraint; the underlying fix is to make both flags work in prefix position for all commands
- Show session name in `status` output:
  - `agentnb status` reports "Kernel is running (pid N)" with no session name
  - when multiple sessions are live this gives the agent no basis for knowing which session it checked; include the session name
- Add stdout truncation notice:
  - when stdout is truncated (e.g., large `print()` calls), output ends with `...` but there is no explicit notice that truncation occurred or how many bytes were dropped
  - add a short note like `[stdout truncated at N chars]` so the agent knows the value is incomplete
- Fix `--auto-install` fallback suggestion for pip-less venvs:
  - `start --auto-install` falls back to `pip install ipykernel` which fails in fresh uv venvs that have no `pip`
  - the error message also suggests the `pip` command as the manual fix; for uv projects it should suggest `uv add ipykernel`

### Implementation Seams

Each item below maps a planned feature to the module that should own the change. No item should require touching more than one or two modules.

#### Output and rendering (`output.py`, `cli.py`)

- **Duplicate error output**: `_emit()` in `cli.py` (line 263) writes the rendered response to `err=True` (stderr) when `response.status == "error"`. A caller that captures both channels (e.g. `2>&1` or subprocess with `stderr=STDOUT`) sees the output twice — once from each channel. Fix: remove the `err=...` routing in `_emit()` and always write to stdout in non-JSON mode. The one-liner is changing line 263 from `click.echo(rendered, err=(response.status == "error" and not options.as_json))` to `click.echo(rendered)`. The `--agent` and `--json` paths are unaffected since they never reach this branch.

- **Reset output message**: `render_human()` in `output.py` (line 159) dispatches `reset` into `_render_exec_like()` alongside `exec`. `_render_exec_like()` (line 318-322) falls through to `"Execution completed."` when there is no stdout/stderr/result. Split the dispatch: keep `exec` going to `_render_exec_like()`, and for `reset` return `"Namespace cleared."` directly. One-line change at line 159: `elif command in {"exec", "reset"}:` → two separate branches.

- **Stdout truncation notice**: Truncation happens in `compact.py` (line 66) via `summarize_history_text(stdout, limit=_STDOUT_LIMIT)` where `_STDOUT_LIMIT = 200`. `summarize_history_text` appends `...` but does not indicate truncation explicitly. Fix at line 66-67: after computing the summary, check `if summary is not None and len(stdout) > _STDOUT_LIMIT` and append `f" [{len(stdout) - _STDOUT_LIMIT} chars truncated]"` to the summary. Apply the same guard to the `stderr` summary at line 71-73. This affects only the `--agent` projection; the human render in `output.py` `_render_exec_like()` emits stdout verbatim and does not truncate.

- **Status session name**: `render_human()` in `output.py` (lines 133-141) renders the `status` command using `status_data` only. `CommandResponse.session_id` is already available in `response`. At lines 137 and 138, include the session name: `f"Kernel is running (session: {response.session_id}, pid {status_data.get('pid')})."` Do the same at line 149 for the `wait` command which uses identical pid-only rendering.

#### Advice (`advice.py`)

- **SESSION_BUSY suggestion**: `SessionBusyError` in `errors.py` (line 43) raises with `code="SESSION_BUSY"`. `AdvicePolicy.suggestions()` has no branch for this code and falls through to the generic exec fallback (`history @last-error` / `interrupt` / `reset`) — all wrong when the session is merely locked. Add a top-level guard before the `command_name` dispatch (after the `AMBIGUOUS_EXECUTION` branch at line 36):
  ```python
  if context.error_code == "SESSION_BUSY":
      return ["Run `agentnb wait --json` to block until the session is idle, then retry."]
  ```

- **Dead kernel suggestion**: `BackendOperationError` in `errors.py` (line 111) raises with `code="BACKEND_ERROR"`. The relevant instance is `backend.py` line 275: `"Kernel process is not running"`. `NoKernelRunningError` (line 27) raises with `code="NO_KERNEL"`. Both fall through to the generic exec fallback in `AdvicePolicy`. Add alongside the `SESSION_BUSY` guard:
  ```python
  if context.error_code in {"NO_KERNEL", "BACKEND_ERROR"}:
      return [
          "Run `agentnb start --json` to start the kernel.",
          "Run `agentnb doctor --json` if startup has been failing.",
      ]
  ```

#### Invocation (`invocation.py`)

- **`--session` and `--project` position for non-exec commands**: The resolver (`invocation.py` lines 167-184) correctly moves `prefix_exec_tokens` (which includes `--session X` and `--project X`) after the command name when building the final argv. For top-level commands like `history` and `status` this works because those commands accept `--session` directly. The failure case is group commands: `agentnb --session X runs list` produces argv `["runs", "--session", "X", "list"]`, and Click rejects `--session` on the `runs` group because the group itself has no `--session` option (only the `list` subcommand does). Fix: in `_implicit_exec_intent()` (line 357), or in the `CommandIntent` argv assembly (line 174-183), detect when `command_candidate` is a known group name (`runs`, `sessions`) and move exec tokens after the first subcommand positional in `tail_tokens_without_root` rather than between the group name and its subcommand.

#### Startup (`kernel/provisioner.py`)

- **`--auto-install` fallback for pip-less venvs**: `ensure_ipykernel()` in `provisioner.py` (lines 114-130) always uses `install_cmd = [selected.executable, "-m", "pip", "install", IPYKERNEL_REQUIREMENT]`. This fails in fresh uv venvs where `pip` is absent. The error messages at lines 121-123 and 133-137 repeat the same pip command. Fix in two parts: (1) before running, probe whether `pip` is available with `_python_supports_module(Path(selected.executable), "pip")`; if not, substitute `["uv", "pip", "install", IPYKERNEL_REQUIREMENT]` (or `["uv", "add", "ipykernel"]` if `uv.lock` is detectable). (2) When the install fails, parse `result.stderr` for `"No module named pip"` and if found, emit a targeted message: `f"pip is not available in this environment. Try: uv add ipykernel"` instead of the generic retry-with-pip suggestion.

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

1. Implicit exec plus implicit ensure-started on the hot path
2. Compact `--agent` working mode distinct from full `--json`
3. Background/run-control ergonomics that always surface `execution_id` and minimize list-then-show flows
4. Sticky session defaults plus symbolic selectors such as `@latest`, `@active`, and `@last-error`
5. Help, suggestions, and output shaping rewrite around agent token efficiency
6. Recovery/debugging improvements that reduce session drops and extra probing
7. Verification workflows
8. Snapshots
9. Replay
10. Exports and artifacts
