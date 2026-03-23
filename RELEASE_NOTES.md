# v0.3.10 — Friction Closure And Hot-Path Trust Fixes

## Improvements

**Suggestions and recovery actions now come from one validated policy path** —
Human `Next:` guidance and machine-readable `suggestion_actions` now share one
internal builder. This removes drift between the two surfaces, fixes the bogus
post-`reset` `setup_code` suggestion, and makes suggestion regressions easier
to catch with focused tests.

**Same-session background admission is now fail-fast and deterministic** —
Background run startup now persists its `running` record and worker pid
immediately, so a same-session foreground follow-up sees the active run through
the durable run store instead of racing a partially persisted state. In live
CLI use, `exec` after `--background` now returns a consistent `SESSION_BUSY`
error with direct `runs wait/show` guidance.

**`runs follow` now follows from the current tail instead of replaying old
output** — The follow path now treats prior output as `runs show` territory and
streams only unseen events. Final follow snapshots omit replayed stdout/stderr/
result fields when history is intentionally skipped, so `runs show` followed by
`runs follow` no longer forces agents to re-parse the same output twice.

**Missing-module recovery is more literal and more correct** — Import-name to
package-name normalization now handles common mismatches such as
`sklearn -> scikit-learn`, both in human suggestions and structured shell
actions. Agents can now follow the suggested install command directly without a
second failure caused by the wrong package name.

**History and result previews are more scan-friendly in compact loops** —
Compact history labels now preserve multiline structure with bounded line-aware
previews instead of flattening everything into one whitespace-collapsed string.
Large structured exec results now prefer bounded shape/sample summaries in both
`--result-only` and default human output, reducing table-dump noise while still
showing enough shape information for the next decision.

**Missing-`ipykernel` smoke coverage is repo-owned and reproducible again** —
The smoke path no longer depends on an external drifting project state. A
fixture project now deterministically reports a selected interpreter with
missing `ipykernel`, so `doctor` and `start` exercise the documented manual
recovery path in both tests and targeted smoke runs.

# v0.3.9 — Targeting Truthfulness And Workflow-Scope Guidance

## Improvements

**Suggestions now preserve the workflow scope the user established** — Human
`Next:` guidance and machine-readable `suggestion_actions` now derive from one
scoped command builder instead of hardcoded strings. Cross-project follow-ups
now carry `--project`, explicit session-scoped workflows preserve `--session`
when omitting it would lose the intended target, and execution-id `runs`
commands remain project-scoped without leaking session flags into the wrong
surface.

**Session targeting cues are now more truthful in multi-session workflows** —
`sessions list` no longer presents the remembered session preference as
`(current)`, which overpromised omitted-command targeting behavior when
multiple live sessions existed. Human output now labels the remembered session
as `(preferred)`, while omitted session-bound commands continue to require
explicit `--session` selection when live targeting is ambiguous.

**Common file-execution misguesses recover at the CLI boundary** — `agentnb exec
path.py` no longer falls through to Python evaluation and a confusing
`NameError`. When the CLI can tell the argument is an existing Python file, it
now returns a targeted `INVALID_INPUT` response that points directly to
`exec --file PATH` and the top-level `agentnb PATH` hot path.

**Help text and matcher contracts now describe the real behavior** — The
`--session` help now explains the actual omitted-targeting rules instead of
implying a default live-session fallback that does not exist under ambiguity.
`vars --match` now explicitly documents case-insensitive substring semantics in
both option help and command help text.

**`--quiet` and `--no-suggestions` are now easier to distinguish in success
paths** — `--quiet` continues to suppress non-essential success chatter such as
switch notes, while `--no-suggestions` now keeps the normal human response body
and suppresses only the `Next:` block. This makes it clearer which flag to use
for lower-noise output versus suggestion suppression.

# v0.3.8 — Follow Semantics, Discoverability, And Cleanup Polish

## Improvements

**`runs follow` now behaves like a bounded observation tool instead of a
timeout trap** — `runs follow --timeout` now means "observe for up to this
long" rather than "fail unless the run finishes in time". When the window
elapses, the command returns `ok` with the latest persisted run snapshot plus
typed observation metadata such as `completion_reason`,
`replayed_event_count`, and `emitted_event_count`. Human `runs follow` output
now reuses the normal run snapshot renderer instead of falling back to raw
JSON-like output.

**Command discoverability is more deterministic and less surprising** —
Predictable mistypes such as `agentnb list` and `agentnb log` are now
classified before implicit exec and return concrete guidance toward
`sessions list`, `runs list`, or `history` instead of generic Click errors or
accidental code execution.

**Session targeting is quieter in the common single-live-session case** —
When agentnb falls back to the sole live session because the remembered
session is no longer live, it no longer re-announces the same implicit switch
on every command. Explicit targeting behavior remains unchanged.

**Session cleanup and exec truncation are more explicit at the surface** —
`sessions list` now reports when non-live session records are hidden from the
default live-only view and points directly to `sessions delete --stale`.
Exec-like payloads now expose explicit truncation booleans for stdout, stderr,
and result, so file execution can suggest `--no-truncate` and recent-variable
inspection without guessing from rendered text.

**Help text now explains the cleanup and output-shaping contracts directly** —
`--stdout-only`, `--stderr-only`, and `--result-only` now describe their
behavior more precisely, including the fact that large structured results may
still render as a compact preview. Root, `exec`, `reset`, and `stop` help now
share one cleanup-primitive explanation that makes the `reset` vs `--fresh`
vs `stop` distinction explicit, and `history --all` is documented more
clearly as a helper/provenance view rather than the default debugging path.

# v0.3.7 — Contract And Recovery Polish

## Improvements

**Run and help contracts are tighter and more consistent** — `runs show` and
`runs wait` now expose a top-level `data.status` alias for machine consumers
while preserving `data.run.status`, root help no longer overpromises `--session`
support for execution-id run commands, and `history --help` now documents
selector shortcuts such as `@latest`, `@last-error`, and `@last-success`.
