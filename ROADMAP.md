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

These refactors should happen before or alongside the first 0.3 user-facing work.

Apply an Ousterhout lens here:

- prefer deep modules with shallow interfaces
- hide syntax inference, selector lookup, output policy, and sticky-default complexity behind a few owning boundaries
- avoid spreading special cases across `cli.py`, app handlers, runtime methods, and render helpers
- define each refactor by the complexity it should absorb, not by the number of files it touches

- Invocation-resolution boundary:
  - purpose:
    - absorb hot-path syntax inference into one deep module so implicit execution does not turn Click handling into a pile of special cases
  - owns:
    - mapping raw argv, stdin presence, cwd context, and file-path detection into typed invocation intents
    - rules for implicit `exec`, file execution, stdin execution, and default `ensure-started`
  - does not own:
    - execution semantics
    - rendering
    - session resolution after an intent has been produced
  - initial shape:
    - a typed intent layer such as `ExecIntent`, `CommandIntent`, and related parse results
    - a small interface from `cli.py` into that resolver
  - first migration step:
    - move code-input shape resolution and hot-path inference behind the resolver while keeping existing app request types stable
  - done when:
    - Click remains thin
    - new hot-path syntax can be added without editing many subcommands
    - implicit `agentnb "code"` does not require ad hoc command branching throughout `cli.py`
  - tests:
    - direct resolver tests for argv, stdin, and file inference behavior
    - a few CLI contract tests for the surfaced syntax
- Output-profile boundary:
  - purpose:
    - separate exact machine contracts from cheap working output without letting mode checks spread through the CLI and renderer
  - owns:
    - output-profile selection
    - compact versus full rendering policy
    - suggestion visibility defaults by mode
  - does not own:
    - command business logic
    - run/session lookup
  - initial shape:
    - explicit profiles rather than booleans, at least:
      - full stable JSON contract
      - compact agent working mode
      - compact human mode
  - first migration step:
    - replace boolean render flags with a profile object or enum while preserving current behavior
  - done when:
    - `--agent` is a first-class compact working mode rather than quiet full JSON
    - adding a new output mode does not require scattered conditionals
  - tests:
    - renderer tests at the profile boundary
    - contract tests for full `--json`
    - compact-mode tests for minimal success-path payloads
- Selector-resolution boundary:
  - purpose:
    - make convenient selectors possible without teaching every command how to guess ids and target objects
  - owns:
    - typed references for run, history, and session targets
    - selector resolution for values such as `@latest`, `@active`, `@last-error`, and `@current`
  - does not own:
    - the underlying run-control or history storage model
    - rendering of lookup results
  - initial shape:
    - typed reference values plus a resolver that returns concrete ids or target records before lower layers execute
  - first migration step:
    - introduce typed references alongside explicit ids, then teach `runs` and `history` commands to accept them
  - done when:
    - commands no longer need ad hoc list-then-show logic in the CLI
    - selector precedence and ambiguity rules live in one place
  - tests:
    - direct resolver tests for active/latest/error cases
    - owning-boundary tests around ambiguous or missing targets
- Session-preferences state boundary:
  - purpose:
    - keep sticky current-session behavior as explicit project state rather than hidden CLI memory or runtime heuristics
  - owns:
    - lightweight per-project session preferences such as current session
    - persistence and retrieval of those preferences through `StateRepository`
  - does not own:
    - session runtime state
    - kernel lifecycle
  - initial shape:
    - a small persisted preference record under `.agentnb/`, owned by `StateRepository`
  - first migration step:
    - persist and read a current-session preference without changing broader session runtime files
  - done when:
    - sticky session defaults work without scattering state across CLI and runtime
    - session-selection rules remain explicit and testable
  - tests:
    - direct repository tests for reading and writing session preferences
    - runtime/app tests for precedence between explicit, sticky, sole-live, and default sessions
- Advice-policy boundary:
  - purpose:
    - make next-step guidance cheap, mode-aware, and concrete instead of keeping a large hard-coded suggestion switch
  - owns:
    - whether to emit advice
    - how much advice to emit
    - interpolation of real ids and session names
    - mode-aware suppression of routine success chatter
  - does not own:
    - command execution
    - rendering format beyond returning advice content
  - initial shape:
    - an advisor that consumes command/result/error context plus output profile
  - first migration step:
    - move the current suggestion branching behind the advisor while preserving current user-visible behavior
  - done when:
    - routine success responses stay quiet
    - recovery guidance is concrete and specific
    - non-JSON modes are not constantly pushed back toward `--json`
  - tests:
    - advisor tests for success, error, ambiguous-session, and busy-session cases
- Execution-invocation policy cleanup:
  - purpose:
    - keep execution semantics stable while allowing hot-path ergonomics to evolve cleanly
  - owns:
    - execution-default policy such as implicit startup, background defaults, and output-selection defaults
  - does not own:
    - the meaning of execution itself
    - lower-level runtime behavior
  - initial shape:
    - a policy/profile object distinct from the semantic execution request
  - first migration step:
    - stop encoding all invocation ergonomics as booleans directly on `ExecRequest`
  - done when:
    - hot-path default changes do not bloat or destabilize semantic request types
    - execution semantics remain understandable independent of CLI convenience rules
  - tests:
    - app-level tests that separate semantic execution behavior from invocation-default policy

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
