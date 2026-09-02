# Architecture Decision Records

## ADR-001 — Group settlement members by `settlement_id`

**Decision:** Aggregate signed transaction nets by the gateway-provided
`settlement_id`. Processing date is metadata, never membership evidence.

**Why:** The recon source already supplies the grouping key, and multiple
settlements may share a date. A subset solver would recreate information the
source already knows.

## ADR-002 — Keep models outside accounting decisions

**Decision:** Matching, exception reason codes, financial answers, and cash
position are deterministic. The model may select one allow-listed read-only
investigation tool; it never writes a financial fact or state transition.

**Why:** Every previously proposed model role had a bounded deterministic
acceptance predicate, so the strongest deterministic baseline could enumerate
the same accepted evidence. Natural-language question routing is useful model
work, while tool output remains verifiable. The final invariant is byte-identical
functional metrics with the model on or off.

## ADR-003 — Put exclusivity in SQLite

**Decision:** `match_members` uses plane-scoped uniqueness, all columns are
`NOT NULL`, every connection enables foreign keys, and a composite foreign key
binds child run/plane scope to its parent match.

**Why:** An application assertion is too late and can be bypassed by a second
write path. The ledger must physically reject double consumption.

## ADR-004 — Taint an entire batch after any rejected member

**Decision:** A fee violation, malformed value, duplicate source identifier, or
unsupported member type quarantines that member and abstains on its full batch.

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

## ADR-007 — Integrate LLMs by API contract, not matching behavior

**Decision:** The optional tool-selection layer supports OpenAI-compatible Chat
Completions, Anthropic Messages, and Gemini `generateContent`, plus an Ollama
shortcut. It uses one explicit user-owned configuration and no provider SDK.

**Why:** These three HTTP contracts cover the major hosted and local integration
families without coupling the accounting engine to a vendor package. Provider
output passes an exact tool-and-arguments schema; any configuration, transport,
shape, or content failure falls back or refuses. An unknown proprietary API
remains an adapter task, not a reason to make a false “every LLM” claim.

## ADR-008 — Separate benchmark accuracy from workload coverage

**Decision:** Report expected-match precision/recall and complete-workload
coverage as different metric families.

**Why:** A controller can be perfectly correct on the matches it attempts while
deliberately abstaining on part of the workload. Calling both “match rate” hides
operational residue and violates the Track 04 honesty requirement.

## ADR-009 — Bind truth to inputs and verify monetary evidence

**Decision:** The manifest stores exact input SHA-256 values and expected
amount/member facts. Evaluation requires one-to-one exception matches, monetary
agreement, complete member sets, and source/amount conservation.

**Why:** Pair identifiers alone can score a partial or monetarily wrong settlement
as correct. Ground truth that is not cryptographically bound to inputs can also
be reused after data changes. Both are unacceptable for a finance benchmark.
