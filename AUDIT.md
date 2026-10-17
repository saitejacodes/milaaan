# Milaan — Phase 1 Audit

Audited on 2026-10-10 against the `milaaan-main` zip (v1.3.0). Read-only: no source
file was changed. Every number below comes from commands run during the audit, on
macOS 26.6 arm64, Python 3.12.13 (test suite also run on Python 3.11).

---

## 1. What works

| Check | Command actually run | Result |
|---|---|---|
| Full test suite | `python -m pytest -q` (3.12 and 3.11) | **230 tests pass** on both |
| Coverage | `pytest --cov=milaan` | **88% line coverage** (4,157 statements, 496 missed) |
| `make ci` equivalent | every recipe line run by hand (clean ×1, mixed ×3, hard ×3, agent-eval, report, benchmark smoke, dashboard import, unittest) | pass, **12 s** |
| `make judge` | `python -m milaan.evalx.judge --out data/judge` | `RESULT: VERIFIED`, **1.0 s** |
| `make adversarial` | `python -m milaan.evalx.adversary` | **64 attacks, 0 failures** in all three families, 3 s |
| `make demo` | gen → run → eval → agent-eval → report | pass, ~1 s |
| `make benchmark` | full 5 sizes × 3 reps | pass, 8 s, all gates pass, 0 false matches |
| `make dashboard` | `streamlit run milaan/dashboard.py` | starts, health check 200, renders the run |

**README numbers that reproduce exactly** (seed 42, mixed): Plane A 1,154/1,154,
Plane B 21/21, exceptions 6/6, 0 false matches, coverage 1,154/1,155 (99.91%),
21/26 (80.77%), 21/28 (75.00%), conservation 2,467/2,467, buckets
2,056 / 366 / 45 / 0, routing 56/56, write refusals 18/18, hostile outputs 26/26
(16 refused, 9 fallback, 0 unsafe), 64 adversarial attacks, 26 agent-safety tests.
The cash figures in the README image (₹6,650,191.65 etc.) also match.

**Benchmark** — reproduces within normal machine noise, not exactly (it is a timing):

| Orders | README median rec/s | My run median rec/s | Diff |
|---:|---:|---:|---:|
| 50 | 5,000 | 4,357 | −13% |
| 200 | 13,182 | 12,056 | −9% |
| 1,200 | 29,687 | 28,651 | −3% |
| 5,000 | 37,792 | 37,102 | −2% |
| 10,000 | 39,248 | 37,899 | −3% |

Engineering strengths worth talking about in interviews: integer paise everywhere;
DB-enforced one-entity-one-match (`UNIQUE` + `CHECK`); identity-conflict
components found by graph traversal instead of resolved by sort order; tainted
batches held whole; evaluator that **rebuilds** truth from `run_meta.json` instead of
trusting the shipped answer key; a single `view.py` feeding both report and
dashboard; a pre-model authority boundary for write intent.

---

## 2. What is broken or unverified

| # | Finding | Evidence |
|---|---|---|
| B1 | **`pip install -e '.[dev,dashboard,preview]'` fails.** `preview` pins `pillow==12.3.0`, `streamlit==1.49.1` requires `pillow<12`. `make sample` installs `preview` *after* the dashboard, silently breaking the Streamlit install. | `ResolutionImpossible` on install; pip warning after installing `preview` |
| B2 | **Agent over-refuses harmless questions.** The write-intent regex fires on any of 30 verbs anywhere in the text. "How many orders did we **reconcile**?" and "Is it **clear** why cash is blocked?" are refused as write attempts. The safety suite only tests that bad prompts are refused; it never tests that good prompts containing those words get through, so the false-refusal rate is unmeasured. | probed with `ask_finance` on `data/judge` |
| B3 | **Exception-ID mis-routing.** "How many exceptions were raised in 2024?" routes to `get_exception(EXC-2024)` and returns "No verified exception exists". | same probe |
| B4 | **One question = one tool.** "Why is cash blocked and which exception is the biggest?" answers only the first half. No multi-step investigation exists. | same probe |
| B5 | **Bare bank-line questions not routed.** "What is the status of bank_00003?" is out of scope; only "why … bank_x" works. | same probe |
| U1 | **"40 tampering attacks"** in `test_truth_integrity.py` = 39 attacks + 1 untampered control (`test_00_untampered_copy_still_passes`). Say "39 attacks plus a control", or add one. | test file |
| U2 | **Reason codes listed but never emitted:** `NARRATION_UNPARSEABLE` (README list) and `UNSUPPORTED_MEMBER_IN_BATCH` (view headline). No engine path produces them. | grep across `milaan/` |
| U3 | CI on 3.13, the CI badge, and the commit SHAs in the README (`591da1eb…`) cannot be checked here: the zip has no git history. I tested 3.11 and 3.12 locally. | — |
| U4 | `make` is unusable on this Mac (Xcode command-line tools missing). Not a repo bug, but it shows the Makefile-only path is fragile for reviewers; the README already gives Python equivalents for Windows only. | `xcode-select: Failed to locate 'make'` |

Nothing that the README *claims as a measured number* failed to reproduce.

---

## 3. Code quality issues (most severe first)

1. **SQLite connections are never closed in the read path (resource leak).**
   `with connect(path) as conn:` in `agent/tools.py`, `agent/eval.py`, `router._finish`
   and `dashboard.py` only commits/rolls back — Python's `sqlite3` context manager
   does **not** close the connection. Harmless in a CLI, but in a long-running web demo
   every question leaks file handles. Fix: `contextlib.closing` or a small helper.
2. **The "read-only" agent writes to the database.** Each question inserts into
   `audit_log` (and `llm_calls` in live mode). Accounting tables are untouched, which the
   fingerprint proves, but the claim "AI can only read" should say "reads accounting
   state; appends its own audit row". For a public demo shared by many visitors this
   also means unbounded DB growth and write contention on one SQLite file.
3. **Write-intent boundary is keyword-based** (see B2). It fails safe, which is the right
   direction, but it has no negative test set and no measured false-refusal rate.
4. **Duplication.** `money()` is defined three times (`agent/tools.py`, `view.py`,
   `evalx/judge.py`) with two different minus signs (`-` vs `−`). `_git_sha()` exists in
   `engine/pipeline.py` and `evalx/judge.py`. The tier label table exists in both
   `view.py` and `judge.py`.
5. **Dead code.** `milaan/exceptions/codes.py` (0% coverage) is imported nowhere; every
   module uses string literals instead. Either use the constants or delete the file.
6. **Untested in-process.** `cli.py` 0%, `evalx/judge.py` 0% (both only run as
   subprocesses), `evalx/ai_probes.py` 63%, `agent/eval.py` 75%.
7. **Quadratic matching loops.** Plane B (`match_b0_b1`, `_detect_duplicates`,
   `collect_recovery_hits`) compares every batch with every bank line, normalising the
   narration again each time. Fine at 10k orders (≈500 batch×line pairs per day-window),
   but a bank statement with tens of thousands of lines would hit this first. Pre-index
   by amount and normalise narrations once.
8. **Small API smells.** `router._validated_selection` hard-codes an argument-schema
   dict that duplicates `FinanceTools.registry`; tool schemas could live next to the
   tools. `FinanceTools.__init__` raises `ValueError` for a failed gate, so a UI must
   special-case it. `ask_finance` re-opens the DB 2–3 times per question.
9. **Inconsistent dependency hygiene.** Runtime has zero dependencies (good), but
   extras pin exact versions that conflict (B1), and `setuptools==84.0.0` is pinned
   in the build system.
10. **Streamlit deprecations ahead.** `use_container_width=` is used throughout
    `dashboard.py`; newer Streamlit releases replace it with `width=`.

No secrets, no `eval`/`exec`, no shell injection found. The only f-string SQL
(`explain_why_unmatched`) uses a fixed table list — safe.

---

## 4. README / presentation issues (recruiter view)

- **No live demo.** The first call to action is `git clone … make judge`. A recruiter
  will not do this. This is the single largest gap.
- **The "Open the verified report" link opens raw HTML source** on GitHub, not the
  rendered page.
- **The first screen is about rules, not results.** It takes ~40 lines to reach a
  number. The 3-line "what it is / why it matters / proof" is missing.
- **Jargon before context:** "Track 04", "Plane A/B", "A0B", "UTR", "fail-closed"
  appear before a non-finance reader knows what reconciliation is.
- **README is 27 KB** — excellent as reference, too long as a landing page. Keep it,
  but put a short results table + screenshot + demo link above the fold.
- **No GitHub description or topics** (repo page shows "No description").
- **Dashboard cash tiles truncate** (`₹6,650,1…`) at normal widths.
- **No architecture diagram** — the ASCII flow is good, but a Mermaid diagram showing
  the AI boundary would render on GitHub.

---

## 5. Interview questions and model answers

1. **Why integer paise instead of `Decimal`?**
   Every amount is a whole number of paise, so `int` is exact, fast, and
   JSON/SQLite-safe. `fee_for` does half-up rounding with integers:
   `(gross * pct_bp + 5000) // 10000`. `money()` formats using `divmod`, never `/100`.

2. **How do you guarantee one record is never matched twice?**
   Two layers: in memory, matched/blocked sets; in the database,
   `UNIQUE(run_id, plane, entity_type, entity_id)` on `match_members`, plus a `CHECK`
   that only lets orders and transactions join plane A and only batches and bank lines
   join plane B. The adversary attacks both constraints directly.

3. **Walk me through Plane A tiers.**
   A0: the gateway's `order_ref` points at the order. A0B: the order's recorded
   `payment_id`. A1: same exact amount, forward business-day gap ≤ window, and
   *mutually* unique (the order has one candidate and that candidate has one order).
   Anything else becomes `AMBIGUOUS_TIE`.

4. **What is an identity conflict and why a graph?**
   If order X says payment P and payment Q says order X, picking one is arbitrary. The
   code builds an edge set from both directions, finds nodes with more than one
   edge, and runs a DFS to collect the whole connected component; everything in it
   is held under one `IDENTITY_CONFLICT`.

5. **Why hold an entire batch when one member is rejected?**
   The batch total is the signed sum of members. If one member is quarantined, the
   survivors can add up exactly to the bank credit and look perfect while money is
   missing. `aggregate_batches` marks it tainted, and Plane B skips tainted batches.

6. **Why not solve subset-sum for combined credits?**
   A sum that matches is not evidence that two batches were paid together; coincidental
   sums exist. `triage_plane_b` *detects* a unique two-batch pair and reports
   `AMBIGUOUS_COMBINED` (`"solver is intentionally not approved"`).

7. **How does B2 recovery stay safe?**
   It never relaxes amount equality. It allows only: the full UTR after removing
   spaces and dashes; a unique suffix of ≥ 6 characters owned by exactly one batch; or
   exactly one confusable substitution (0/O, 1/I, 1/L). Lines with any recovery hit are
   deferred so B1 (amount/date) can't steal them, and mutual uniqueness is still
   required.

8. **Why should we trust 100% precision?**
   Mostly you shouldn't treat it as real-world accuracy: the data is synthetic.
   What *is* trustworthy is the measurement: the evaluator regenerates truth from
   seed/profile/config hashes, checks inputs byte-for-byte, binds every DB row to the
   canonical row, and refuses to publish metrics if any check fails. 39 tampering
   tests prove that.

9. **Precision vs. coverage — why report both?**
   Precision = were the matches made correct. Coverage = how much of the work was
   finished. An engine can reach 100% precision by matching nothing. Seed 42:
   100% precision, but only 80.77% of settlement batches banked.

10. **How is the LLM prevented from writing?**
    (a) No write tool exists in the registry. (b) `authority_boundary` refuses write
    and override phrasing *before* any model call. (c) Model output must be a JSON
    object with exactly `{tool, arguments}`, an allow-listed tool, the exact argument
    names, and identifier shapes matching regexes. (d) The answer text comes from the
    deterministic tool, never from model prose.

11. **What happens if the model returns garbage?**
    Transport or parse failures (`ProviderError`, `ValueError`…) fall back to the
    deterministic router. A *well-formed but unapproved* selection is refused, not
    downgraded, so the refusal is visible. 26 hostile bodies are tested, with a
    before/after fingerprint of the accounting tables.

12. **Weakness of the keyword write-intent filter?**
    False refusals: "how many orders did we reconcile?" is blocked. It fails safe, but I
    never measured the false-refusal rate. The fix is a benign test set containing the
    trigger words plus verb-in-imperative detection, measured and reported.

13. **Is the "routing 56/56" an AI score?**
    No. Offline it measures deterministic code. The README says so. A live-model number
    is not published because it depends on a model and key a reviewer can't reproduce.

14. **Where would this break at real scale?**
    Plane B is O(batches × bank lines) with repeated narration normalisation; SQLite is
    single-writer; one bank narration family is modelled. I'd index bank lines by
    amount and date, normalise once, and add per-bank adapters.

15. **How do report and dashboard avoid showing different numbers?**
    Both read one view model (`view.py`), which reads only the evaluator's published
    `functional_metrics.json` and the verified DB. `test_surface_parity.py` enforces it,
    and `build_view` raises `GateNotPassed` for a run that failed its gate.

---

## 6. Ranked improvement list

| Rank | Improvement | Impact | Effort | Phase |
|---:|---|---|---|---|
| 1 | Public zero-config web demo (run, explore exceptions, ask agent, "try to break it") | Very high | Medium | 3 |
| 2 | README top rewrite: pitch, demo link, screenshot/GIF, results table, Mermaid diagram | Very high | Low | 4 |
| 3 | Multi-step read-only investigation with visible step trace + measured multi-step eval | High | Medium | 2 |
| 4 | Fix over-refusal (B2) and add a benign-question set; publish false-refusal rate | High | Low | 2 |
| 5 | Fix dependency conflict (B1) | High (install breaks) | Very low | 2 |
| 6 | Close SQLite connections; isolate demo writes per session | High for demo | Low | 2 |
| 7 | GitHub description + topics | Medium | Very low | 2 |
| 8 | Fix EXC-year mis-routing (B3) and bare bank-line routing (B5) | Medium | Low | 2 |
| 9 | Correct "40 attacks" wording; drop never-emitted codes or implement them (U1, U2) | Medium (honesty) | Very low | 2 |
| 10 | In-process tests for `cli.py`, `judge.py`, `ai_probes.py` | Medium | Low | 2 |
| 11 | De-duplicate `money()`, `_git_sha()`, tier labels; delete or use `codes.py` | Low–medium | Low | 2 |
| 12 | Pre-index Plane B by amount; normalise narrations once | Low (not needed at demo scale) | Medium | later |

---

## 7. Status after phases 2–4 (2026-10-17)

| Finding | Status |
|---|---|
| B1 extras conflict | Fixed: `pillow==11.3.0`; scorecard image re-renders byte-identical |
| B2 over-refusal | Fixed: write verbs refused only as commands; false refusals 7/16 → 0/16, all 30 write probes still refused |
| B3 `EXC-2024` misroute | Fixed |
| B4 one question = one tool | Fixed: `investigate` runs bounded multi-step read-only chains; 16/16 offline cases |
| B5 bare `bank_` ids | Fixed |
| U1 "40 attacks" | README now says 39 plus a control |
| U2 never-emitted codes | `NARRATION_UNPARSEABLE` removed from README; `codes.py` deleted |
| U4 no `make` | `scripts/verify.sh` runs the same checks |
| Code 1 connection leak | Fixed: `db.reading()` on every read path; leak test added |
| Code 2 "read-only" wording | README states the agent appends only its own audit rows |
| Code 4–5 duplication / dead code | `money.py` shared; `codes.py` removed |
| Code 6 untested entry points | `cli.py` 0→91%, `judge.py` 0→93%, `ai_probes.py` 63→92% |
| New bug found | `agent-eval` crashed on runs with no exceptions; fixed and tested |
| No live demo | Streamlit web app + Dockerfile + `DEPLOY.md`; deploy pending (needs the owner's HF account) |
| README first screen | Pitch, demo link, GIF, results table bound by tests, Mermaid AI-boundary diagram |
| Not done | Code 7 (Plane B pre-indexing) and Code 10 (Streamlit `use_container_width` in the operator dashboard), both low impact |
