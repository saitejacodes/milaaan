# MILAAN v1.2.1 CHANGELOG — round-3 disposition
**21 Aug 2026 · TECH_EXECUTION v1.2.1 is the sole spec. All prior versions and amendment files are historical. Standalone status is now *verified*, not claimed: `grep -c "v1.1"` on the spec returns 0, and every model, DDL statement, config file, tier, code, metric, and prompt is defined inline.**

## Round-3 blockers → resolutions (all ACCEPTED)

| # | Blocker | Resolution |
|---|---|---|
| 1 | Not standalone (24 refs, ~18 normative inheritances) | Full inline rewrite; zero external references (grep-verified). The architecture patch was small; the document is now complete |
| 2 | **B2 ablation invalid — bounded allow-list is enumerable (≤1+2n≈33 variants), so a fair mock recovers everything; live model can't own the CORRUPTED column** | Conceded as a theorem, not a bug, and codified as the spec's **design law**: *if acceptance is decidable by bounded enumeration, the capability belongs to code.* B2 is now a deterministic fuzzy-recovery tier (`engine/recovery.py`: FULL / SUFFIX≥6 / one-confusable-substitution sliding-window scan over `(settlement_id, utr)` candidates). The ablation command is **deleted** — the capability it measured no longer exists. The LLM's role is language only: exception narratives + investigation guidance, template-canonical, content-checked |
| 3 | Cache lived in the run DB, which `run` recreates | Persistent separate file `data/.llm_cache.sqlite` (env `MILAAN_CACHE_PATH`, gitignored); run DB keeps only `llm_calls` telemetry |
| 4 | Quarantining transfers corrupts batch totals | Taint-and-abstain: unknown-type row quarantined AND its batch marked `tainted`, excluded from Plane B, terminated as new code `UNSUPPORTED_MEMBER_IN_BATCH` with full member evidence; unit test feeds a hand-written transfer row |
| 5 | Exclusivity not airtight (nullable members pass UNIQUE; child run_id/plane unconstrained) | `NOT NULL` on all member columns; `PRAGMA foreign_keys=ON` per connection; `matches` gains `UNIQUE(match_id, run_id, plane)` as composite-FK target; `match_members` FK `(match_id, run_id, plane)`; dedicated `test_db` adversarial cases |
| 6 | demo-twice only reran `run` | CI second pass is a full `run → eval --gate mixed → report` cycle on the same data dir |
| + | Code must derive `kind`; candidates must be `(settlement_id, utr)` | Both absorbed: B2 is entirely code, so code derives everything; candidates are id-paired, keeping duplicate-UTR fixtures unambiguous |
| + | Omit CP-SAT unless separately approved | T20 marked **NOT APPROVED**, unscheduled, and requires a signed-off written design before any code |

## New invariant replacing the ablation (stronger, and pitch-leading)

`tests/test_llm_mode_invariance.py` + CI: `functional_metrics.json` must be **byte-identical** between `--llm mock` and `--llm live` (cassette/secret in CI). The model cannot move a single number — enforced by a field firewall, a content check on polished text (ids/amounts must be a subset of the evidence; violation ⇒ template kept, `polish_rejected` audited), and the byte-equality gate.

## Reviewer verifications acknowledged with thanks

TOML parse, prompt offsets, null-tolerant aggregation, and the recon-sample field semantics were independently verified by the reviewer before this round; the enumeration bound (7 variants on the worked example, ≤33 general) was checked and is exact.

## Downstream patches implied (PRD/pitch — for the owner, not the coding agent)

- PRD: FR-X6 (ablation) deleted; FR-M7/V1/V2 collapse into the language-layer firewall + invariance gate; NFR "LLM ≤10% of records" is obsolete (matching uses zero model calls; LLM cost ≈ exception count × polish).
- Pitch "where's the AI" answer, final form: *"Nowhere near the money — by proof, not by preference. Three hostile review rounds each found my latest model-in-the-loop design was deterministically reachable, so the final system lets the model touch only language, and CI verifies the books are byte-identical with the model on or off. Every hostile review removed another place where the system could pretend certainty."*
- `docs/BUILD_LOG.md` should record all three rounds as they happened — it is the panel interview's best material.

— end of changelog —
