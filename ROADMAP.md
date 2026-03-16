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

## Pre-v0.3 Preparatory Refactors

These refactors are complete and now define the main seams for 0.3 feature work.

Apply an Ousterhout lens here:

- prefer deep modules with shallow interfaces
- hide syntax inference, selector lookup, output policy, and sticky-default complexity behind a few owning boundaries
- avoid spreading special cases across `cli.py`, app handlers, runtime methods, and render helpers
- define each refactor by the complexity it should absorb, not by the number of files it touches

- Invocation-resolution boundary: completed; raw argv/stdin/file-path inference and implicit exec routing now live behind a typed resolver instead of in `cli.py`.
- Output-profile boundary: completed; output mode selection is centralized and compact/full JSON policy no longer spreads through CLI handlers.
- Selector-resolution boundary: completed; typed run and history references plus selector-to-query/id resolution now own `@latest` and `@last-error` style targeting.
- Session-preferences state boundary: completed; sticky current-session behavior is persisted in project state and resolved by runtime precedence rules.
- Advice-policy boundary: completed; next-step guidance is centralized in a mode-aware advisor instead of a large command switch.
- Execution-invocation policy cleanup: completed; invocation ergonomics now live in policy objects rather than bloating semantic execution requests.

## v0.3 - Agent Loop Efficiency

### Goals

- Make the cheapest useful command also be the easiest command.
- Reduce the number of tokens needed to start, continue, recover, and inspect.
- Reduce wrong guesses and follow-up discovery commands.
- Separate compact working output from exact machine-contract output.

### Planned Features

- Implicit hot-path execution:
  - `agentnb "code"` behaves like `agentnb exec --ensure-started "code"`
  - `agentnb path/to/script.py` behaves like `agentnb exec --ensure-started --file path/to/script.py`
  - `agentnb` with stdin executes stdin through the same path
  - explicit `exec` remains available for clarity and scripting
- Default startup on execution:
  - make `ensure-started` the default behavior for the main execution path
  - keep an explicit opt-out only if needed for strict scripts/tests
- Compact agent working mode:
  - define `--agent` as a compact iterative mode, not just quiet full JSON
  - return only the minimum fields needed for the next step during normal success cases
  - keep full `--json` as the exact stable envelope
- Lower-noise default output:
  - reduce success-path suggestions by default in agent mode
  - only emit suggestions when the next action is genuinely ambiguous or recovery-oriented
  - interpolate concrete ids and session names instead of placeholder text like `EXECUTION_ID`
- Stronger run-control ergonomics:
  - always surface `execution_id` clearly for background execution in every output mode
  - allow `runs show|follow|wait|cancel` to default to the active or latest relevant run where safe
- Sticky session defaults:
  - remember the current session per project once a session is selected explicitly
  - make ambiguity rarer without hiding multi-session state
  - show the current session clearly in `sessions list`
- History and query shortcuts:
  - direct selectors for the latest failed or successful history/run entries
  - clearer failed-only flows without requiring extra list-then-show commands
  - optional flat machine-friendly query output where it reduces parsing overhead
- Help and discoverability rewrite:
  - replace the long first-contact workflow with a short hot-path guide
  - prioritize examples like `agentnb "import json"` over verbose control-plane examples
  - keep extended help for deeper workflows, but move it off the critical path
- Human-mode follow-up:
  - keep human output self-sufficient and compact
  - prefer short summaries over repeated routine success guidance

### Implementation Seams

- Put hot-path syntax and shorthand behavior in `InvocationResolver`; keep `cli.py` as an adapter from argv to typed intents and app requests.
- Put default execution ergonomics such as implicit startup or future opt-outs in `ExecInvocationPolicy`, not on `ExecRequest` itself.
- Put compact versus full JSON response shape decisions in `ResponseProjector`; keep full `--json` stable and treat `--agent` as a separate compact contract.
- Put run/history symbolic defaults and selector expansion in the selector resolvers; lower layers should receive typed references or resolved queries, not ad hoc CLI guesses.
- Put sticky session behavior in `StateRepository` plus `KernelRuntime` precedence rules; do not reintroduce CLI-side session memory.
- Put recovery guidance and success-path quieting in `AdvicePolicy`; avoid command-specific suggestion branching in render helpers or handlers.
- Keep feature work additive at the app boundary: shorthand syntax may change, but semantic request/response shapes and persisted provenance should stay clear and stable.
- If a 0.3 feature does not fit one of these seams cleanly, define or deepen the owning module first instead of adding another CLI-local special case.

### Example Target Workflows

- `agentnb "import json"`
- `agentnb "payload.keys()"`
- `agentnb analysis.py`
- `agentnb --background "long_task()"`
- `agentnb runs follow`
- `agentnb history @last-error`

### API / Contract Notes

- Keep full `--json` as the stable machine contract with predictable fields such as `session_id`, `execution_id`, `duration_ms`, and typed error codes.
- Define a separate compact `--agent` contract for working-loop efficiency rather than assuming the full envelope is the right default for every step.
- Keep shorthand syntax and symbolic selectors as CLI affordances over stable underlying request/response shapes.
- Do not let convenience defaults obscure persisted provenance or session identity.

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
