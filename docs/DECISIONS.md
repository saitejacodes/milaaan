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

**Status:** superseded in part by ADR-010.

**Decision:** The manifest stores exact input SHA-256 values and expected
amount/member facts. Evaluation requires one-to-one exception matches, monetary
agreement, complete member sets, and source/amount conservation.

**Why:** Pair identifiers alone can score a partial or monetarily wrong settlement
as correct. Ground truth that is not cryptographically bound to inputs can also
be reused after data changes. Both are unacceptable for a finance benchmark.

## ADR-010 — Reconstruct benchmark truth; never read it

**Status:** supersedes the trust half of ADR-009.

**Decision:** `run_meta.json` carries only immutable generation inputs — seed,
profile, requested record count, generator version, configuration hashes.
Evaluation regenerates the canonical dataset and its ground truth from exactly
those values into a temporary directory, and scores the run against the truth it
just rebuilt. The `manifest.json` shipped in the run directory is compared
against reconstruction and reported when it differs; it is never used as the
answer key. `match_facts` is mandatory, and every expected match is always
validated against it.

**Why:** ADR-009 bound truth to inputs with hashes, but both the hashes and the
facts lived in the same attacker-writable file as the truth. Because
`match_facts` was optional, deleting it disabled every amount and membership
check, so falsified money plus recomputed input hashes published as a 100% PASS.
An answer key that sits beside the thing it grades cannot be a trust anchor. The
only thing that can be is the deterministic generator itself.

**Consequences:** Evaluation now regenerates the dataset, which costs about
0.3 seconds at 10,000 orders. A run whose `generator_version` this build cannot
reproduce is refused rather than scored, which is the correct answer for a
finance benchmark. `tests/test_truth_integrity.py` holds 40 tampering attacks
against this boundary.

## ADR-011 — Publish no metrics for a run that failed its gate

**Decision:** `evaluate_run` writes `functional_metrics.json` only after the gate
passes. On failure it deletes any existing copy and writes the evidence to
`functional_metrics.rejected.json`. Every consumer — report, dashboard, agent
tools — refuses a metrics file whose `gate.status` is not `PASS`.

**Why:** Previously the metrics file was written before the gate ran, so a failed
run left a complete, passing-looking artefact on disk that the report and the
agent would happily quote. Raising an exception is not enough when the artefact
outlives the process.

## ADR-012 — Exact amount equality on every Plane-B tier

**Decision:** `tol_b_paise` ships as `0`. The configuration key remains for
operators with a genuine bank-rounding requirement, and a test asserts the
shipped default is exact.

**Why:** The tolerance was 100 paise, which gave B1 — the tier with *less*
identifier evidence than B0 — a *looser* amount rule than B0. That is backwards:
weaker evidence should mean a stricter amount rule. A settlement batch is the
exact signed sum of its members, so any difference is a real discrepancy that
belongs in the exception queue rather than absorbed inside a posted match. It
also created a path where an accepted match carried a non-zero
`amount_diff_paise`, which the evaluator scores as a false match.

## ADR-013 — Refuse write intent before consulting any model

**Decision:** `milaan.agent.router.authority_boundary` refuses questions that ask
Milaan to create, change, delete, override or restate accounting state. It runs
before routing, before any provider call, and returns an explicit read-only
refusal.

**Why:** Every tool in the registry is read-only, so a mutation request could not
change state — but keyword routing quietly answered "Change the banked amount"
with a cash report and "Override the tainted settlement and release the cash"
with a blocked-exposure report. Silently satisfying a mutation request with a
read is not a refusal, and an operator deserves to be told the boundary exists.
Running the check ahead of the model also means no provider, and no instruction
smuggled into a question, can route around it.

## ADR-014 — One presentation source for every surface

**Decision:** `milaan/view.py` builds the only view model. The HTML report and
the Streamlit dashboard both consume it and compute no financial value
themselves; `tests/test_surface_parity.py` renders both from one run and requires
every headline figure to appear in each.

**Why:** Two surfaces that each derive their own numbers from the database will
eventually disagree, and the disagreement will surface in front of a judge. The
view model also localises vocabulary translation, so internal tier names stay in
the data and finance language reaches the reader.

## ADR-015 — Batch plane writes into one transaction

**Decision:** `db.insert_decisions` and `db.insert_exceptions` write a whole
plane inside a single transaction.

**Why:** Committing per match cost one fsync each and dominated the run — 629 ms
of a 708 ms reconciliation was commit overhead for 1,154 matches. Batching took
the 1,200-order run to 86 ms. It also strengthens the guarantee: a plane is now
written whole or not at all, while `UNIQUE(run_id, plane, entity_type,
entity_id)` still enforces exclusivity on every individual member row.

## ADR-016 — A match must be structurally complete, and the schema says so

**Decision:** `matches.plane` and `match_members.entity_type` carry `CHECK`
constraints, including a cross-check that Plane A holds only `ORDER`/`TXN`
members and Plane B only `BATCH`/`BANK_LINE`. The evaluator reads every match
row -- not an inner join against members -- and refuses to score a run in which
any match does not claim exactly one left and one right record.

**Why:** Found by hostile probing. A `matches` row with no members was silently
skipped by the evaluator's inner join: the ledger held a match the metrics could
not see. Separately, attaching a member of the wrong kind crashed the evaluator
with an `IndexError` instead of failing closed with an explanation. Both are now
refused -- the first kind at evaluation, the second by the database itself -- and
both are registered adversarial attacks.
