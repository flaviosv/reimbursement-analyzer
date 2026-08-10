# PR #14 Comment-Triage Plan

All 14 threads are automated `code-review`/`tests-code-review` findings with no
additional user comment layered on top — classified **auto-fix** per
ship-spec's rule ("no user comment on the thread → fix it directly, no
exceptions, no pushback"). Every finding was independently re-verified before
fixing (e.g. Thread 2's FK-constraint claim confirmed directly against
`packages/api/src/api/migrations/0002.create-human-review.sql:6`). None were
found invalid; all 14 get a fix.

Given the volume and the tight coupling between several findings (Threads 2/6
both resolve via the same design-doc + STATE.md correction; Threads 10/14
both resolve via the same `FakePool` extension), fixes are grouped into
cohesive, file-scoped commits rather than 14 mechanically separate ones —
every thread still gets its own fix and its own GraphQL reply/resolve.

## Sequential (grouped by shared file / coupled reasoning)

| # | Thread | Severity | File | Fix |
| - | ------ | -------- | ---- | --- |
| 1 | 1 | High | `publish_pending.py` | Widen the compensation try-block to cover `ReimbursementEnvelope` construction, not just `publish()` |
| 2 | 2, 6 | Medium | `publish_pending.py`, `.specs/STATE.md` (AD-037), `design.md` | Wrap `delete_pending`'s call in its own `conn.transaction()` too (matching insert's treatment); correct AD-036's rationale via a new AD-037 (append-only log); fix design.md's Architecture Overview + mermaid diagram to show both narrow transactions |
| 3 | 3 | Low | `publish_pending.py` | Fix the dotted-name docstring reference |
| 4 | 4 | Low | `publish_pending.py` | Avoid eager `json.dumps` via an `isEnabledFor` guard |
| 5 | 5 | Low | `docs/RISKS.md` | State R-001's updated severity directly instead of a dangling cross-reference |
| 6 | 7 | Medium | `spec.md` | Document the new transient read-visibility window as an explicit edge case |
| 7 | 8, 9, 11 | Medium, High, Low | `test_publish_pending.py` | Strengthen noop/failure-record assertions to exact-match; fix helper type hints |
| 8 | 12 | Low | `test_publish_pending.py` | Extract repeated call-site kwargs into a local `_publish` helper |
| 9 | 10, 14 | Medium | `fakes.py`, `test_processing.py` | Track uuid→inserted mapping in `FakePool` so a compensating delete actually removes the entry; add a `delete_error` knob; new test proving `ItemOutcome.REQUEUED` when the compensating delete raises |
| 10 | 13 | High | `test_processing.py` | New `RealPool`-backed test proving `envelope.retry` (not a hardcoded value) reaches the compensating-delete log through the real `_insert_and_publish` wiring |

No `## Parallel` bucket — every group touches a file another group also
touches (`publish_pending.py`: 1,2,3,4; `test_publish_pending.py`: 7,8;
`test_processing.py`: 9,10), so all groups process sequentially, in the
order above, each its own commit.
