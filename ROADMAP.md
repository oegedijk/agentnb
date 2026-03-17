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

- Fix `inspect` crash on MultiIndex DataFrames:
  - `TypeError: keys must be str, int, float, bool or None, not tuple` when inspecting a DataFrame with tuple column keys from `groupby().agg()`
  - MultiIndex columns are common in pandas workflows and should serialize cleanly
- Fix background dispatch message:
  - `--background` currently prints "Execution completed" when the execution is only dispatched
  - change to "Background execution started" or similar to reflect the actual state
- Fix `runs cancel` wording for already-finished runs:
  - currently says "Run is already ok" which is confusing
  - change to "Run already completed" or "Run already finished"
- Fix `runs follow` timeout behavior:
  - timing out while tailing a still-running background job exits with code 1 and an error message
  - this is normal flow, not an error; use exit code 0 or a distinct non-error exit
- Fix `runs follow` replay semantics:
  - `follow` currently replays all output from the beginning
  - the name implies `tail -f` semantics; either stream only new events or document the replay behavior clearly
- Context-aware suggestions:
  - after `ModuleNotFoundError`, suggest how to install the missing module rather than `history @last-error` / `interrupt` / `reset`
  - after `NameError` with multiple sessions running, mention which session was targeted and suggest `sessions list`
  - after `doctor`, do not suggest `agentnb start` when sessions are already running
  - put this logic in `AdvicePolicy` rather than spreading special cases
- Fix `--session` position consistency:
  - the main help says `--session` works "before or after the subcommand" but `agentnb --session X history` fails
  - either make it work in both positions or correct the documentation
- Add a default limit to `runs list`:
  - currently dumps all historical runs with no limit, producing a wall of text
  - add a sensible default (e.g., last 20) or at least suggest `--last N` when output is large
- Add `help` as a command alias:
  - `agentnb help` currently fails with "No such command"
  - alias it to `--help` since it is a natural first thing to type
- Reduce noise in `inspect` for scalars:
  - inspecting an `int` shows method lists like `as_integer_ratio`, `bit_count`, etc.
  - for primitive types, show just the value; reserve member listing for complex objects
- Make implicit session switching visible:
  - using `--session X` silently makes X the "current" session with no indication
  - surface the switch in human output so the agent knows which session subsequent commands will target

### Preparatory Refactor: Deepen AdviceContext

Three of the planned features (ModuleNotFoundError suggestion, NameError session context, doctor awareness) need information that `AdviceContext` does not carry today. Patching each one individually would mean three separate ad-hoc additions and three corresponding changes to every `AdviceContext(...)` construction site in `_handle_command()`.

Deepen `AdviceContext` once before the feature work:

```python
@dataclass(slots=True, frozen=True)
class AdviceContext:
    command_name: str
    response_status: str
    data: Mapping[str, object]
    error_code: str | None = None
    # execution error identity (currently buried in data dict or lost on generic except path)
    error_name: str | None = None
    error_value: str | None = None
    # session context (currently available in _handle_command but not forwarded)
    session_id: str | None = None
    live_session_count: int = 0
```

Why this is the right refactor:

- `error_name` / `error_value`: Today the exec error branch (line ~74) would have to reach into `data.get("ename")`, which only works when the `AgentNBException` carried error details. The generic `except Exception` path (line ~779) passes `data={}`, losing the ename/evalue entirely. Promoting these to first-class fields ensures they are always available regardless of which exception path constructed the context.
- `session_id` / `live_session_count`: `_handle_command()` has `resolved_session_id` (line ~737) and can cheaply query the session count, but neither flows to `AdvicePolicy` today. The NameError and doctor suggestions both need this context. Passing it through `AdviceContext` keeps the advice module decoupled from runtime — it never needs to query session state itself.
- Testability: Advice logic becomes testable by constructing `AdviceContext` directly with typed fields, rather than building fake `data` dicts with undocumented keys.

Populate the new fields in the two `AdviceContext(...)` construction sites inside `_handle_command()` (success at line ~752, error at line ~770) and the generic exception handler (line ~788). No other call sites exist.

### Implementation Seams

Each item below maps a planned feature to the module that should own the change. No item should require touching more than one or two modules.

#### Introspection (`introspection.py`)

- **MultiIndex inspect crash**: The kernel-side helper `_inspect_helper()` calls `_dtype_summary()` (line ~601) and `_null_counts()` (line ~617), both of which call `.to_dict()` on a Series with tuple keys from MultiIndex columns. The resulting dict has tuple keys that `json.dumps()` cannot serialize. Fix both helpers to coerce all dict keys to `str` before returning, matching the `{str(key): ...}` pattern already used at line ~607 but applied too late. The same coercion should guard the `columns` list in `_dataframe_preview()` since MultiIndex columns are tuples there too.

- **Scalar member noise**: The kernel-side helper `_inspect_helper()` (line ~754) populates `_members` via `dir(_value)` whenever `_preview is None`, which includes all primitives. Add a guard: skip `dir()` for types in `{int, float, str, bool, bytes, complex, type(None)}`. For these types `repr` alone is sufficient; the method list adds no useful information.

#### Output and rendering (`output.py`)

- **Background dispatch message**: `_render_exec_like()` (line ~313) falls through to `"Execution completed."` when there is no stdout/stderr/result. For background dispatch, the response data includes `background: True` and an `execution_id`. Add a branch: when `data.get("background")` is truthy, render `"Background execution started (execution_id)."` instead of the generic message.

- **Cancel wording**: `render_human()` for `runs-cancel` (line ~286) renders `f"Run {id} is already {status}."` when `cancel_requested` is false. Change to `f"Run {id} already {status}."` — dropping "is" — or use an explicit mapping: `{"ok": "already finished", "error": "already failed", "cancelled": "already cancelled"}` for clarity.

#### Run control (`runs/local_manager.py`, `cli.py`)

- **Follow timeout exit code**: `follow_run()` (line ~129) raises `RunWaitTimedOutError` on timeout, which propagates to `cli.py` → `_emit_stream_completion()` → `Exit(1)`. The timeout is not a failure of the run itself. Either catch the timeout in `runs_follow` in `cli.py` and exit cleanly (code 0) with a note that following stopped, or introduce a distinct exit code (e.g., 2) that agents can distinguish from execution failure.

- **Follow replay semantics**: `follow_run()` (line ~123) emits `record.events[emitted_events:]` starting from `emitted_events = 0`, so the first iteration replays all historical events. Two options: (a) accept this as the documented behavior and rename the docstring/help from "stream newly recorded events" to "replay and then stream"; or (b) add an `--offset` or `--skip-history` flag that sets `emitted_events` to `len(record.events)` before entering the poll loop so only new events stream.

#### Advice (`advice.py`)

These items depend on the `AdviceContext` deepening described above.

- **ModuleNotFoundError suggestion**: After the refactor, `context.error_name` is available directly. Add a branch in the `exec` error handler (line ~74): when `context.error_name == "ModuleNotFoundError"`, extract the module name from `context.error_value` and suggest the install command (e.g., `pip install {module}` or `uv add {module}`). No `data` dict inspection needed.

- **NameError with session context**: With `context.session_id` and `context.live_session_count` available, add a branch: when `context.error_name == "NameError"` and `context.live_session_count > 1`, include the targeted session name in the suggestion and mention `sessions list`. No runtime queries from inside `AdvicePolicy`.

- **Doctor suggestion awareness**: The `doctor` handler (line ~103) always suggests `agentnb start` when `data.get("ready")` is true. Check `data.get("session_exists")` (already present in `DoctorPayload`). When true, suppress the `start` suggestion or change it to `"Kernel is already running."`. This one does not need the new `AdviceContext` fields — it uses `data` which already carries the doctor payload.

#### Invocation (`invocation.py`, `cli.py`)

- **`--session` position for non-exec commands**: `InvocationResolver._scan_args()` classifies `--session` as `kind="exec"` (line ~54), so it is recognized in both prefix and tail positions. The issue is that for known subcommands like `history`, the prefix exec tokens are forwarded correctly, but Click's own parsing of `agentnb --session X history` may consume `--session` as a root-level unknown option before `AgentGroup.parse_args` runs. Verify whether the `InvocationResolver` is actually invoked before Click's root option parsing. If the option is consumed too early, either promote `--session` to a root option (like `--json`) or ensure the resolver runs first.

- **`help` command alias**: Add a hidden Click command `help` to the `AgentGroup` that prints the same output as `--help`. Alternatively, handle it in `AgentGroup.parse_args` by rewriting `["help"]` to `["--help"]` before passing to `super().parse_args()`.

#### Query defaults (`app.py`)

- **`runs list` default limit**: `_runs_list_payload()` (line ~626) only slices when `request.last` is not None. Add a default: when `request.last` is None and not in JSON/agent mode, default to 20. Keep the unlimited behavior for `--json` and `--agent` so programmatic consumers can still get everything. Alternatively, add the default in the CLI layer (`runs_list` command in `cli.py`) so the app layer stays mode-unaware.

#### Session visibility (`app.py`, `output.py`)

- **Implicit session switch**: `_handle_command()` (line ~738) calls `remember_current_session()` when `--session` is provided but produces no output about the switch. Add a field to `CommandResponse.data` (e.g., `switched_session: str | None`) when the current session changes. In `render_human()`, append a short note like `"(now targeting session: X)"` when this field is set. Keep it out of `--agent` and `--json` modes to avoid breaking contracts — or add it as an optional field that agents can ignore.

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
  - more actionable `SESSION_BUSY` and `AMBIGUOUS_SESSION` responses
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
