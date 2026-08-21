# Architecture Decision Records

## ADR-001 — Group settlement members by `settlement_id`

**Decision:** Aggregate signed transaction nets by the gateway-provided
`settlement_id`. Processing date is metadata, never membership evidence.

**Why:** The recon source already supplies the grouping key, and multiple
settlements may share a date. A subset solver would recreate information the
source already knows.

## ADR-002 — Keep models outside accounting decisions

**Decision:** Matching and exception reason codes are deterministic. The model
may polish only language.

**Why:** Every previously proposed model role had a bounded deterministic
acceptance predicate, so the strongest deterministic baseline could enumerate
the same accepted evidence. The final invariant is byte-identical functional
metrics with the model on or off.

## ADR-003 — Put exclusivity in SQLite

**Decision:** `match_members` uses plane-scoped uniqueness, all columns are
`NOT NULL`, every connection enables foreign keys, and a composite foreign key
binds child run/plane scope to its parent match.

**Why:** An application assertion is too late and can be bypassed by a second
write path. The ledger must physically reject double consumption.

## ADR-004 — Taint an entire unsupported batch

**Decision:** Quarantine an unsupported member row and abstain on its full
settlement batch.

**Why:** Dropping one debit or credit silently changes the batch sum. A partial
sum is more dangerous than no answer.

## ADR-005 — Do not ship CP-SAT

**Decision:** Combined-credit residue is an explicit exception. No OR-Tools
dependency or solver module exists.

**Why:** The baseline task is grouping and verification. A solver expands scope
and creates difficult uniqueness semantics without improving the named core
benchmark.

## ADR-006 — Separate functional truth from runtime telemetry

**Decision:** Golden-test `functional_metrics.json`; never golden-test wall time,
timestamps, token usage, cost, or latency.

**Why:** Functional correctness must be reproducible. Runtime observations are
valuable but inherently environment-dependent.
