# agentnb Roadmap

This roadmap is forward-looking. It is not a changelog or release-notes file.

`agentnb` is a persistent, project-scoped Python kernel for coding agents doing
interactive work. The product wins when an agent can enter and stay in a
productive loop with minimal token spend, minimal syntax overhead, minimal
output parsing, and minimal recovery friction.

## Product Lens

The main optimization target is agent token efficiency:

- how little documentation an agent must read before it can use the tool correctly
- how few flags and subcommands it must remember for the hot path
- how rarely it has to self-correct after guessing the CLI shape
- how rarely it must call `--help`, `sessions list`, `runs list`, or `history` just to decide the next command
- how little output it must parse to recover the one fact needed for the next step

The primary usability target is coding agents; human ergonomics matter insofar
as they reinforce the same low-friction path.

Human ergonomics still matter, but they follow this same direction: fewer
steps, clearer defaults, quieter output, and better recovery guidance.

## Design Rules

Deterministic targeting and machine-readable recovery take priority over
permissive convenience behavior.

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

## Current Focus

The current priority is not adding major new surface area. It is tightening the
remaining places where the CLI still creates avoidable retries for agents:

- targeting should be truthful and unambiguous
- suggestions should preserve the scope the user established
- invocation-shape mistakes should recover to the right command quickly
- help text should describe actual matcher and selector behavior, not an approximation

## v0.3.9 - Targeting Truthfulness And Workflow-Scope Hints

v0.3.9 should close the remaining smoke-tested gaps where the CLI surface says
or implies one thing while the actual targeting or invocation behavior does
another.

### Planned Issues

- Issue: multi-session help text and the `(current)` marker overpromise default targeting.
  Reproduce:
  - Start at least two named sessions, for example `curA` and `curB`.
  - Run `uv run agentnb sessions list` and observe `curA (current)`.
  - Run `uv run agentnb vars` and observe `Error: Multiple live sessions exist; pass --session to select one explicitly.`
  - Run `uv run agentnb vars --help` and observe `Session name. If omitted, agentnb uses the only live session or \`default\`.`
  Why this is a problem for practical agentnb workflows:
  - Agents trust the CLI surface. A `(current)` marker plus help text that sounds like a default-resolution rule encourages the next omitted-session command, which then fails.
  - This adds hesitation to every branching workflow because the agent cannot tell whether `current` is real targeting state or just a display hint.
  - Targeting truthfulness matters more than preserving a vague notion of "current" in human output.

- Issue: cross-project `Next:` suggestions lose the `--project` context.
  Reproduce:
  - Create or pick another project and run `uv run agentnb --project /tmp/other --session sugg15 "answer = 42"`.
  - Observe the success response.
  - Observe that `Next:` suggests `agentnb vars --recent 5` and `agentnb history ...` without carrying `--project /tmp/other`.
  Why this is a problem for practical agentnb workflows:
  - Agents follow the suggested next command literally. Dropping `--project` points the very next action back at the caller project and silently breaks the workflow boundary the user just established.
  - This is especially costly in cross-project debugging because the wrong suggestion can inspect or mutate the wrong repo with no syntax error to warn the user.

- Issue: `exec path.py` is still a high-probability file-execution footgun.
  Reproduce:
  - Write a small script, for example `/tmp/agentnb-invoke19.py`.
  - Run `uv run agentnb exec --session inv19 /tmp/agentnb-invoke19.py`.
  - Observe that `agentnb` treats the path as Python code and fails with a `NameError` instead of executing the file.
  - Then run either `uv run agentnb exec --session inv19 --file /tmp/agentnb-invoke19.py` or `uv run agentnb --session inv19 /tmp/agentnb-invoke19.py` and observe that both work.
  Why this is a problem for practical agentnb workflows:
  - Agents naturally unify "exec code" and "exec file" into one command family, so `exec script.py` is a predictable first guess.
  - Falling into a Python parse/eval traceback instead of a targeted "did you mean `--file`?" recovery path wastes a full correction turn on a very common workflow.
  - File-to-interactive use is one of the core value paths for `agentnb`, so its main footgun deserves direct CLI guidance.

- Issue: `vars --match` semantics are under-documented and easy to misguess.
  Reproduce:
  - Create variables such as `by_day` and `by_user`.
  - Run `uv run agentnb vars --session matchval --match "by_*"` and observe `No user variables found.`
  - Run `uv run agentnb vars --session matchval --match "by"` and observe both variables listed.
  Why this is a problem for practical agentnb workflows:
  - Agents are likely to assume glob or regex-like matching from the flag name.
  - A failed `--match "prefix_*"` query looks like missing state rather than a matcher-semantics mistake, which can send the agent down the wrong recovery path.
  - This does not necessarily require new matching power, but it does require a truthful contract about whether `--match` is substring, glob, or regex.

- Issue: explicit session-targeting notices are still too repetitive in human-mode loops.
  Reproduce:
  - Run several human-mode commands with an explicit `--session`, for example `uv run agentnb --session curA "1"` followed by more commands against the same session.
  - Observe repeated `(now targeting session: curA)` output even though the agent already specified the session and nothing about targeting changed.
  Why this is a problem for practical agentnb workflows:
  - The notice is valuable when targeting changes or is inferred, but low-value repetition adds noise to compact human-mode loops.
  - Agents already pay attention to session targeting; repeating the same notice every turn makes the real signal harder to spot.

- Issue: `--quiet` and `--no-suggestions` are still weakly differentiated in common success paths.
  Reproduce:
  - Run a simple successful expression with `uv run agentnb --quiet ...`.
  - Run the same expression with `uv run agentnb --no-suggestions ...`.
  - Observe that the outputs are often nearly identical in practice.
  Why this is a problem for practical agentnb workflows:
  - Agents should be able to predict which flag to use when they want less chatter versus when they want to suppress only the `Next:` block.
  - If the distinction is too subtle, the surface area costs more than the choice it provides.

- Issue: file and invocation-shape guidance is still spread across help surfaces instead of converging on the cheapest correct choice.
  Reproduce:
  - Compare the top-level hot path (`agentnb script.py`), heredoc/stdin, and `exec --file` flows on code that contains braces and quotes.
  - Observe that the CLI does work, but the agent still has to infer when it should abandon inline form and when `exec --file` is required.
  - Observe that the targeted recovery hint for the `exec path.py` misguess is currently missing.
  Why this is a problem for practical agentnb workflows:
  - Invocation-shape mistakes are common in exploratory work because the same analysis naturally moves between inline snippets, heredocs, and files.
  - The tool already has the right escape hatches; the missing piece is tighter guidance at the moment the agent guesses wrong.

### Release Goal

Make targeting and invocation guidance fully truthful under real smoke-tested
agent workflows: no misleading current-session cues, no cross-project
suggestions that lose scope, and no common file-execution misguess that falls
through to a Python traceback when the CLI could have redirected the agent
immediately.

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
  - selective reset (`reset --keep df,weather`)

- File execution improvements:
  - partial file execution (`exec --lines 17-20 script.py`)
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
