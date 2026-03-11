# agentnb Smoke Scenarios

Intention-driven smoke scenarios for agents using `agentnb`.

Use only `uv run agentnb ...` commands. The point is to exercise workflows
where persistent session state, inspection, history, replay of context, and
incremental iteration should be materially better than rewriting and rerunning
scripts from scratch.

Each scenario should be run as an actual exploratory loop, not as a single
execution. Prefer scenarios where the agent has to:
- build state over multiple steps
- inspect intermediate values
- correct mistakes without restarting from zero
- use history or run records to understand what happened
- keep work in memory and continue from there

## Scenario 1: Exploratory Data Analysis Loop

Intent:
- load a public dataset into memory
- derive a few intermediate tables over several iterations
- inspect suspicious or surprising intermediate values before deciding the next step
- make at least one mistake in the analysis and recover without starting over
- end with a short final summary based on live in-memory state

Good signs:
- live state inspection is faster than rerunning the whole flow
- the agent naturally uses session memory instead of rebuilding everything

## Scenario 2: API Exploration to Analysis

Intent:
- fetch data from a public JSON API
- use repeated calls and inspection to figure out how the API actually works
- inspect nested structures and intermediate objects while deciding what to keep
- normalize or restructure it incrementally once the shape is understood
- turn the useful subset into a dataframe-like or otherwise structured object
- do a follow-up analysis using the already-loaded data instead of refetching

Good signs:
- the agent can learn the API surface through iterative calls inside one live session
- the agent can move from raw API payloads to structured analysis without leaving the session
- intermediate inspection meaningfully reduces guesswork

## Scenario 3: Debugging a Local Module in a Live Session

Intent:
- create or use a small local module with a bug
- import it into a live session and hit the bug
- inspect state around the failure
- edit the module on disk
- reload it and verify the fix without losing the surrounding session context
- continue working with the already-built in-memory objects after the fix

Good signs:
- reload is easier than rebuilding the whole environment
- history and inspection help narrow down the bug quickly

## Scenario 4: Long-Running Analysis with Observation and Cancellation

Intent:
- kick off a background run that produces useful intermediate output
- inspect the persisted run while it is active
- follow it live for a while
- decide whether to wait for it or cancel it based on what it is doing
- after cancellation or completion, continue using the session rather than throwing it away immediately

Good signs:
- run observation is understandable during real work, not just as a toy example
- cancel/wait/follow/show form a coherent control loop

## Scenario 5: Failure, Recovery, and Continued Iteration

Intent:
- build meaningful live state
- trigger an execution failure or timeout in the middle of the workflow
- use history, inspection, status, or other CLI surfaces to understand what failed
- recover in the same session
- continue the workflow from the existing in-memory state instead of rebuilding it

Good signs:
- failures are debuggable without dropping the session
- the recovery path keeps momentum instead of forcing a restart

## Scenario 6: Multi-Session Comparative Workflow

Intent:
- use two sessions for related but different work
- keep different assumptions, transformations, or debugging branches alive in each
- hit ambiguity at least once and resolve it
- compare results across sessions without collapsing them into one shared state

Good signs:
- session targeting feels like a real productivity feature, not bookkeeping
- ambiguity handling nudges the agent toward the right recovery path

## Scenario 7: Reset Versus Continue

Intent:
- build up a session with enough state that restarting would be annoying
- decide partway through that the namespace is messy or misleading
- determine whether to reset, continue, or open a second session
- verify that the chosen path is less painful than rebuilding from scratch

Good signs:
- the tool makes the tradeoff between reset and continuity legible
- reset is useful without being confused with stop

## Scenario 8: File-to-Interactive Workflow

Intent:
- start from a script on disk
- run it through the CLI
- inspect and iterate on the state it creates interactively
- edit the file and rerun only the parts that need rerunning
- use the live session to avoid full script reruns when only the tail of the workflow changed

Good signs:
- file execution and live iteration work together naturally
- agentnb feels better than an edit-and-rerun-only loop

## Scenario 9: Output Shaping in Real Work

Intent:
- perform a real iterative workflow that produces mixed stdout, stderr, and result values
- decide when full output is useful and when a narrowed output channel is better
- use low-noise output modes in a way that helps the workflow rather than as an isolated feature demo

Good signs:
- output shaping helps with control and readability during real iteration
- low-noise modes do not hide information the agent immediately needs

## Scenario 10: Discoverability Stress Test

Intent:
- start from one of the scenarios above without preselecting commands in advance
- rely on CLI help, suggestions, and command behavior to discover the path
- note where the agent guesses wrong, hesitates, or reaches for an implementation detail

Good signs:
- the CLI surface suggests the next useful step at the right moments
- the agent can recover from wrong guesses without much friction
