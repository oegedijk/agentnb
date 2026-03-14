---
name: full-review
description: Use this when the user asks for a thorough code review, full review, nitpicky review, architecture review, or test quality review. Produces a detailed review that checks clean code, clean typing, John Ousterhout style architecture, and test quality including pytest fixtures, parameterization, pytest-mock usage, behavior-vs-implementation testing, and useless/no-op tests.
---

# full-review

Use this skill for review requests where breadth and rigor matter more than brevity. The output may be nitpicky and should mention all meaningful issues found, not just the top few.

## Review Standard

Review against all of these lenses:

- Correctness and regression risk
- Clean code and local clarity
- Clean types and type boundary hygiene
- Clean architecture with John Ousterhout style abstractions
- Test quality and test design

Do not stop at bugs. Call out awkward seams, shallow abstractions, leaky boundaries, naming that obscures responsibility, and unnecessary complexity.

## Architecture Lens

Prefer deep modules with shallow interfaces.

Flag code when you see:

- pass-through abstraction layers that do not hide complexity
- timing, subprocess, protocol, storage-layout, or framework details leaking across boundaries
- public behavior depending on incidental implementation details or race timing
- special cases that should be explicit state or metadata instead
- ad hoc branching spread across layers instead of owned by one boundary

Ask: does this module make the rest of the system simpler, or does it just move code around?

## Type Lens

Flag:

- weakly typed boundaries where stable payloads or domain objects should exist
- raw dict plumbing across architectural seams
- unnecessary casts, partial typing, or unvalidated shape assumptions
- type aliases or protocols that do not buy real clarity

## Test Lens

Expect tests to be comprehensive and idiomatic pytest.

Prefer:

- fixtures for reusable setup
- parametrization for repeated case matrices
- `pytest-mock` for targeted patching
- behavior-focused tests at the owning boundary

Flag:

- tests that assert implementation structure instead of contract or behavior
- tests that patch deep internals when a higher-level contract test is possible
- no-op asserts, trivial asserts, duplicate tests, or tests with little failure value
- setup noise that should be a fixture
- repeated tests that should be parametrized
- brittle mocks that couple tests to refactors without protecting behavior

## Output

Default to a findings-first review.

Order findings by severity, but include lower-severity issues too when they are real. Be explicit about:

- what is wrong
- why it matters
- where it lives
- what a cleaner direction would be

If the review is clean, say so explicitly, then note any residual risks or coverage gaps.
