# MILAAN — TECHNICAL EXECUTION SPEC v1.2.1 (FULLY STANDALONE)
### Zero external references. If it is not in this file, it is not in scope.

**Version 1.2.1 · 21 Aug 2026 · Status: candidate for GO — round-3 blockers 1–6 incorporated; T20 (solver) NOT APPROVED and excluded from the build unless separately signed off.**

**What Milaan is:** an AI finance controller for three-way settlement reconciliation. It ingests a merchant's orders, the gateway's settlement-recon transactions, and a bank statement; proves every match it can through deterministic rules; abstains into evidence-rich exceptions when it cannot; and reports per-plane, per-difficulty metrics scored against a ground-truth manifest the matcher never sees. **The matching engine contains zero model calls by design.** The LLM's only role is language: exception narratives and investigation guidance.

**Design law (state in README and interviews):** *If acceptance of a piece of evidence is decidable by bounded enumeration, the capability belongs to deterministic code.* A model can add matching capability only where verification is possible but search is intractable for code, or where verification requires information code lacks. In this system's scope that set is empty — proven across three review rounds (greedy solver → tie "adjudication" → allow-listed corruption recovery, each shown deterministically reachable). Corollary: **functional results must be byte-identical under `--llm mock` and `--llm live`** (CI-checked, §11.4).

---

## 0. WORKING AGREEMENT (the coding agent's rules)

1. **Loop per task (§12):** read task → write the named test first where marked TDD → implement → `make test` → run the phase gate → paste real output *with denominators* into `docs/BUILD_LOG.md` → one conventional commit per task.
2. **Never fabricate data to pass a check.** Gates run the real pipeline over a real generated batch.
3. **Green gates or stop.** Never weaken an assertion; ask the owner with a recommended default.
4. **Scope = this file.** Cut list §13 is the only descope path. Hard walls: no FX, no real bank APIs, no money movement, no web framework, no auth, no Q&A agent, no solver (T20 unapproved).
5. **Money = `int` paise, signed.** Explicit `random.Random(seed)` instances threaded through. No `datetime.now()` in generator or engine; wall-clock only in run metadata → telemetry.
6. **Candidate-driven verification, never regex-gated.** Identifier evidence counts only if it resolves to a known unmatched `(settlement_id, utr)` candidate under §7.3's bounded rules. Regexes exist solely as generator shape descriptors and test helpers.
7. **One database per run.** `milaan run` deletes and recreates the file at `--db`. The LLM cache is a *separate persistent file* (§8.2) precisely so it survives this.
8. **The LLM never touches matching.** No code path may let model output reach `matches`, `exceptions`' reason codes, amounts, ids, or any functional metric. Model output lands only in `narrative` / `guidance` text fields, gated by the invariance test (§8.4).
9. **Exclusivity is the database's job.** `PRAGMA foreign_keys=ON` on every connection; `match_members` NOT NULL + plane-scoped UNIQUE + composite FK to `matches` (§3). A constraint violation is a bug: raise, never except-and-continue.
10. **No new dependencies** beyond §1.1.

---

## 1. ENVIRONMENT

### 1.1 `pyproject.toml`

```toml
[project]
name = "milaan"
version = "1.2.1"
requires-python = ">=3.11"
dependencies = ["typer>=0.12", "jinja2>=3.1", "httpx>=0.27"]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-cov"]
# NOTE: no solver extra until T20 is approved.

[project.scripts]
milaan = "milaan.cli:app"
```

Stdlib `tomllib` for config, stdlib `csv` for all IO, stdlib `sqlite3`. pandas and pyyaml are banned.

### 1.2 `Makefile`

```make
.PHONY: test gen run run-live eval report demo demo-live ci
SEED ?= 42
DATA ?= data/run$(SEED)

test: ; pytest -q
gen:  ; milaan gen --records 1200 --seed $(SEED) --profile mixed --out $(DATA)
run:  ; milaan run --data $(DATA) --db $(DATA)/milaan.db --llm mock
run-live: ; milaan run --data $(DATA) --db $(DATA)/milaan.db --llm live
eval: ; milaan eval --run $(DATA) --db $(DATA)/milaan.db --out-dir $(DATA) --gate mixed
report: ; milaan report --run $(DATA) --db $(DATA)/milaan.db --out $(DATA)/report.html
demo: gen run eval report
demo-live: gen run-live eval report

ci: test
	set -e; T=$$(mktemp -d); \
	milaan gen --records 200 --seed 1 --profile clean --out $$T/clean || exit 1; \
	milaan run  --data $$T/clean --db $$T/clean/m.db --llm mock || exit 1; \
	milaan eval --run $$T/clean --db $$T/clean/m.db --out-dir $$T/clean --gate clean || exit 1; \
	for s in 1 2 3; do \
	  milaan gen  --records 1200 --seed $$s --profile mixed --out $$T/m$$s || exit 1; \
	  milaan run  --data $$T/m$$s --db $$T/m$$s/m.db --llm mock || exit 1; \
	  milaan eval --run $$T/m$$s --db $$T/m$$s/m.db --out-dir $$T/m$$s --gate mixed || exit 1; \
	done; \
	# demo-twice: FULL second cycle on the same data dir (run recreates DB; eval+report must pass again)
	milaan run    --data $$T/m1 --db $$T/m1/m.db --llm mock || exit 1; \
	milaan eval   --run $$T/m1 --db $$T/m1/m.db --out-dir $$T/m1 --gate mixed || exit 1; \
	milaan report --run $$T/m1 --db $$T/m1/m.db --out $$T/m1/report.html || exit 1
	pytest -q tests/test_golden_metrics.py tests/test_llm_mode_invariance.py
```

**Gate bundles** (in `eval`; exit 1 on any failure):
- `--gate clean`: Plane A and Plane B auto-match both 100% · zero exceptions · zero false matches.
- `--gate mixed`: Plane A ≥ 99.5% · Plane B deterministic (B0+B1+B2) ≥ 95% · false-match count 0 on both planes · expected-exception recall 100% · completeness 100% (every entity named in `expectations` lands in exactly one of {matches, exceptions}; abstain-everything fails on match rate, match-everything fails on false/recall).

### 1.3 Repository layout

```
milaan/
├── README.md  LICENSE(MIT)  Makefile  pyproject.toml  .gitignore(data/.llm_cache.sqlite)
├── config/fees.toml  config/timing.toml
├── milaan/
│   ├── cli.py  config.py  db.py  audit.py  models.py
│   ├── generator/ (calendar.py narration.py world.py inject.py manifest.py emit.py)
│   ├── ingest/    (readers.py normalize.py aggregate.py quarantine.py)
│   ├── engine/    (plane_a.py plane_b.py recovery.py rules.py confidence.py pipeline.py)
│   ├── llm/       (client.py live.py mock.py cache.py prompts/)
│   ├── exceptions/(codes.py triage.py actions.py)
│   ├── evalx/     (metrics.py harness.py golden/)
│   └── report/    (render.py templates/report.html.j2)
├── tests/   docs/(BUILD_LOG.md ARCHITECTURE.md DECISIONS.md)   data/samples/run42/
└── .github/workflows/ci.yml   (runs `make ci`, ubuntu-latest, py3.11, badge in README)
```

---

## 2. DOMAIN MODEL (`milaan/models.py`) — complete

```python
from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

class Channel(StrEnum): UPI="UPI"; CARD="CARD"; NETBANKING="NETBANKING"
class OrderStatus(StrEnum): PAID="PAID"; REFUNDED="REFUNDED"; PARTIAL_REFUND="PARTIAL_REFUND"; FAILED="FAILED"
class TxnType(StrEnum): PAYMENT="PAYMENT"; REFUND="REFUND"; CHARGEBACK="CHARGEBACK"; ADJUSTMENT="ADJUSTMENT"
class MatchKind(StrEnum): ORDER_TXN="ORDER_TXN"; BATCH_BANK="BATCH_BANK"
class Plane(StrEnum): A="A"; B="B"
class MatchTier(StrEnum): A0="A0"; A0B="A0B"; A1="A1"; B0="B0"; B1="B1"; B2="B2"

@dataclass(frozen=True)
class Order:
    order_id: str; created_at: datetime; amount_paise: int
    status: OrderStatus; channel: Channel
    payment_id: str | None            # merchant-recorded gateway payment id; None ⇒ A1 path
    source_row_id: str

@dataclass(frozen=True)
class GatewayTxn:
    txn_id: str                       # normalized from recon `entity_id` (pay_/rfnd_/adj_…)
    txn_type: TxnType
    payment_id: str | None            # canonical: PAYMENT ⇒ = txn_id (raw recon field is null there)
    original_payment_id: str | None   # recon `payment_id` on REFUND/CHARGEBACK rows
    refund_id: str | None
    order_ref: str | None             # recon `order_id`; present on most PAYMENT rows
    channel: Channel | None           # PAYMENT only; None otherwise
    gross_paise: int; fee_paise: int; tax_paise: int      # tax = GST on fee (recon naming)
    net_paise: int                    # SIGNED: + PAYMENT · − REFUND/CHARGEBACK · ± ADJUSTMENT
    captured_at: datetime
    settlement_id: str | None; settlement_utr: str | None
    settlement_processed_at: datetime | None
    source_row_id: str

@dataclass(frozen=True)
class SettlementBatch:
    settlement_id: str
    settlement_utr: str | None        # null-tolerant aggregation (§6.1); None is legitimate
    amount_paise: int                 # Σ signed member nets
    processed_at: datetime
    member_txn_ids: tuple[str, ...]
    tainted: bool = False             # §6.1: contains unsupported member rows

@dataclass(frozen=True)
class BankLine:
    line_id: str; txn_date: date; value_date: date; narration: str
    credit_paise: int; debit_paise: int; balance_paise: int
    ref_no: str; source_row_id: str

@dataclass(frozen=True)
class Decision:
    plane: Plane; kind: MatchKind
    left_ids: tuple[str, ...]; right_id: str
    tier: MatchTier; amount_diff_paise: int; date_gap_bd: int
    confidence: float; evidence: dict          # includes recovery details for B2

@dataclass(frozen=True)
class ExceptionItem:
    scope_ids: tuple[str, ...]; reason: str; confidence: float
    evidence: dict
    narrative: str = ""; guidance: str = ""; suggested_action: str = ""
```

**Signed rules (normative):** PAYMENT: `net = gross − fee − tax`, all components ≥ 0, `channel` required. REFUND/CHARGEBACK: `gross < 0`, `fee = tax = 0`, `net = gross`, `original_payment_id` required. ADJUSTMENT: `fee = tax = 0`, `net = gross`, either sign, `settlement_utr` may be null.

**Recon normalization (normative):** PAYMENT row → `txn_id = entity_id`, `payment_id = entity_id`. REFUND/CHARGEBACK → `original_payment_id =` recon `payment_id`. **Unknown `type` values (e.g. `transfer`): the row is quarantined *and its whole batch is tainted*** (§6.1) — never silently dropped, never partially summed.

---

## 3. DATABASE (`milaan/db.py`) — complete DDL

Every connection opens with `PRAGMA foreign_keys=ON` (SQLite defaults OFF).

```sql
CREATE TABLE runs(
  run_id TEXT PRIMARY KEY, seed INTEGER NOT NULL, profile TEXT NOT NULL,
  llm_mode TEXT NOT NULL, started_at TEXT NOT NULL,
  orders_sha256 TEXT NOT NULL, txns_sha256 TEXT NOT NULL, bank_sha256 TEXT NOT NULL,
  fees_config_sha256 TEXT NOT NULL, timing_config_sha256 TEXT NOT NULL, git_sha TEXT);

CREATE TABLE raw_orders(
  order_id TEXT PRIMARY KEY, created_at TEXT NOT NULL, amount_paise INTEGER NOT NULL,
  status TEXT NOT NULL, channel TEXT NOT NULL, payment_id TEXT,
  source_row_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id));

CREATE TABLE raw_txns(
  txn_id TEXT PRIMARY KEY, txn_type TEXT NOT NULL, payment_id TEXT,
  original_payment_id TEXT, refund_id TEXT, order_ref TEXT, channel TEXT,
  gross_paise INTEGER NOT NULL, fee_paise INTEGER NOT NULL, tax_paise INTEGER NOT NULL,
  net_paise INTEGER NOT NULL, captured_at TEXT NOT NULL,
  settlement_id TEXT, settlement_utr TEXT, settlement_processed_at TEXT,
  source_row_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id));

CREATE TABLE raw_bank(
  line_id TEXT PRIMARY KEY, txn_date TEXT NOT NULL, value_date TEXT NOT NULL,
  narration TEXT NOT NULL, credit_paise INTEGER NOT NULL, debit_paise INTEGER NOT NULL,
  balance_paise INTEGER NOT NULL, ref_no TEXT NOT NULL,
  source_row_id TEXT NOT NULL, run_id TEXT NOT NULL REFERENCES runs(run_id));

CREATE TABLE settlement_batches(
  settlement_id TEXT PRIMARY KEY, settlement_utr TEXT,
  amount_paise INTEGER NOT NULL, processed_at TEXT NOT NULL,
  member_count INTEGER NOT NULL, tainted INTEGER NOT NULL DEFAULT 0,
  run_id TEXT NOT NULL REFERENCES runs(run_id));

CREATE TABLE matches(
  match_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, plane TEXT NOT NULL,
  kind TEXT NOT NULL, tier TEXT NOT NULL, right_id TEXT NOT NULL,
  amount_diff_paise INTEGER NOT NULL, date_gap_bd INTEGER NOT NULL,
  confidence REAL NOT NULL, evidence TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(match_id, run_id, plane));                 -- composite FK target

CREATE TABLE match_members(
  match_id INTEGER NOT NULL, run_id TEXT NOT NULL, plane TEXT NOT NULL,
  entity_type TEXT NOT NULL, entity_id TEXT NOT NULL,
  UNIQUE(run_id, plane, entity_type, entity_id),
  FOREIGN KEY(match_id, run_id, plane) REFERENCES matches(match_id, run_id, plane));

CREATE TABLE exceptions(
  id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, scope_ids TEXT NOT NULL,
  reason_code TEXT NOT NULL, confidence REAL NOT NULL, evidence TEXT NOT NULL,
  narrative TEXT NOT NULL DEFAULT '', guidance TEXT NOT NULL DEFAULT '',
  suggested_action TEXT NOT NULL, created_at TEXT NOT NULL);

CREATE TABLE audit_log(
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, run_id TEXT NOT NULL,
  actor TEXT NOT NULL, action TEXT NOT NULL, payload TEXT NOT NULL);

CREATE TABLE llm_calls(              -- telemetry only; caching lives in the cache file (§8.2)
  id INTEGER PRIMARY KEY, ts TEXT NOT NULL, run_id TEXT NOT NULL,
  purpose TEXT NOT NULL, model TEXT NOT NULL, tokens_in INTEGER, tokens_out INTEGER,
  cost_paise INTEGER, latency_ms INTEGER, prompt_hash TEXT NOT NULL, cache_hit INTEGER NOT NULL);
```

**Membership (normative):** Plane A members = `{(ORDER, order_id), (TXN, txn_id)}`. Plane B members = `{(BATCH, settlement_id), (BANK_LINE, line_id)}` — never the batch's member txns (that linkage is `raw_txns.settlement_id`). One transaction per decision: matches + members + audit, commit; any constraint violation aborts and **raises** (bug, not exception record). Tests: duplicate member insert fails; NULL member column fails; child `run_id`/`plane` mismatching the parent match fails (FK).

**Run semantics:** `milaan run` deletes the file at `--db`, creates fresh, `run_id = f"{seed}-{profile}-{llm_mode}"` on every row. JSON columns (`evidence`, `payload`, `scope_ids`) serialized with sorted keys.

---

## 4. CONFIG

### 4.1 `config/fees.toml` (valid TOML, integers only)

```toml
gst_rate_bp = 1800

[channels.UPI]
pct_bp = 0
flat_paise = 0

[channels.CARD]
pct_bp = 200
flat_paise = 0

[channels.NETBANKING]
pct_bp = 100
flat_paise = 1200
```

```python
def fee_for(gross_paise: int, ch: Channel, cfg) -> tuple[int, int]:   # (fee, tax)
    c = cfg.channels[ch]
    fee = c.flat_paise + (gross_paise * c.pct_bp + 5000) // 10000     # half-up bp
    tax = (fee * cfg.gst_rate_bp + 5000) // 10000                     # GST on fee
    return fee, tax
```

### 4.2 `config/timing.toml`

```toml
settlement_cycle_bd = 2       # T+N business days FROM CAPTURE DATE
bank_lag_bd_choices = [0, 1]  # value_date lag after processed date (T2 widens use of 1)
window_b_bd = 3               # Plane-B candidate window (business days)
window_a_bd = 1               # Plane-A A1 window
tol_b_paise = 100             # Plane-B amount tolerance for injected rounding artifacts
```

### 4.3 `generator/calendar.py`

Synthetic 2026 bank-holiday set (approximate on purpose; internal consistency is the requirement; generator and engine share it): `{Jan 1, Jan 26, Mar 3, Apr 3, May 1, Aug 15, Oct 2, Oct 20, Nov 8, Dec 25}`. Functions (TDD): `is_business_day(d)` = weekday<5 and not holiday; `add_business_days(d, n)`; `business_gap(a, b)` order-agnostic, holiday-aware.

### 4.4 Runtime env

`MILAAN_LLM_PROVIDER` (`mock` default) · `MILAAN_LLM_MODEL` · `MILAAN_LLM_API_KEY` · `MILAAN_LLM_BASE_URL` · `MILAAN_LLM_PRICE_IN_PAISE_PER_1K` / `_OUT_` · `MILAAN_CACHE_PATH` (default `data/.llm_cache.sqlite`, gitignored). Ship `.env.example`; never a real key in the repo.

---

## 5. GENERATOR (complete)

### 5.1 World construction (`world.py`) — single `rng = random.Random(seed)` threaded everywhere

1. **Orders:** N over a 30-day window ending 2026-08-14; channels UPI 55 / CARD 30 / NETBANKING 15; amounts ₹150–₹25,000 in paise, skewed small; statuses mostly PAID, some FAILED / (PARTIAL_)REFUND.
2. **PAYMENT txns** for PAID orders via `fee_for`; ids `pay_` + 14 alnum. **REFUND txns** for refunded orders (`rfnd_` ids, `original_payment_id` set, negative net, later `captured_at`). Occasional `adj_` ADJUSTMENT rows (± small amounts, null UTR allowed).
3. **Batching — by settlement_id, never by date:** each txn's `processed_date = add_business_days(captured_at.date(), settlement_cycle_bd)`; txns sharing a processed date share one `settlement_id` (`setl_` + 14 alnum) — **except ~10% of days are multi-settlement**: that day's txns split across two `settlement_id`s with two UTRs, two credits, distinct amounts by construction (structural, labeled `multi_settlement_day`).

   > The baseline models one settlement slot per processing day for a single balance account. **This is a generator simplification, not a Razorpay invariant** — real schedules vary, partial settlements exist, balance accounts settle separately. The multi-settlement days exist to prove Milaan groups by `settlement_id`.
4. **Bank credits:** one per batch; `value_date = processed_date + choice(bank_lag_bd_choices)` business days; `credit_paise = batch amount`; clean narration embedding the batch UTR.
5. **Plane-A structural variety:** ~90% of PAYMENT txns carry `order_ref`; independently ~90% of PAID orders carry `payment_id`; the gaps exercise A0b/A1 without being anomalies.

### 5.2 Difficulty tiers (`inject.py`) — targets disjoint by construction (partition before sampling; assertion fails generation on overlap; `tier_labels` stays single-valued)

| Tier | Injection | Intended outcome |
|---|---|---|
| T0 | none (clean slice) | A0/B0 |
| T1 | structural fees/GST only | A0/B0 |
| T2 | force `bank_lag_bd = 1`; land some value_dates past weekends/holidays | B0 with gap; window logic exercised |
| T3 | structural batches of 5–40 members | B0 one-batch-one-credit |
| T4 | in-batch REFUND members; a few PARTIAL_REFUND orders | refund linkage; B unchanged |
| T5 | twin-credit tie trap (two batches, equal amounts, same value_date, UTRs dropped from both narrations) · duplicate one UTR onto a second unrelated credit · clone one bank line verbatim | `AMBIGUOUS_TIE` (never guessed) · `DUPLICATE_UTR` · `DUPLICATE_BANK_LINE` |
| T6 | narration damage: truncate UTR to suffix ≥6 · strip all spaces · counterparty typo · prefix noise (`MB:`, `INB `, `TRF/`) · separator inserted inside the UTR · **exactly one confusable substitution inside the UTR** (`0↔O`, `1↔I`, `1↔l`) · drop UTR entirely (~5% of T6) — operator recorded per line in the manifest | B2 deterministic recovery; unrecoverable → `NARRATION_UNPARSEABLE` or B1 |
| T7 | delete a bank credit · insert an unexplained credit (plausible narration, odd amount) · delete all gateway txns for one PAID order | `MISSING_IN_BANK` · `UNKNOWN_BANK_CREDIT` · `PAID_ORDER_MISSING_FROM_GATEWAY` |

Profiles: `clean` (T0–T4 structural only; must reconcile 100%) · `mixed` (default; T5 5% · T6 7% · T7 3% of applicable entities) · `hard` (T5–T7 heavy; also the only profile containing combined-credit cases, which — with the solver unapproved — terminate as `AMBIGUOUS_COMBINED` exceptions, honestly listed).

### 5.3 `narration.py` — UTR families and templates

Families (weights 45/45/10), matching shapes Razorpay's own docs exhibit: **F1 bank-style** 4 uppercase letters + 12 alnum (`KKBKH14156891582`) · **F2 gateway-style** 10 digits + 6 lowercase alnum (`1568176960vxp0rj`) · **F3 RRN** 12 digits. `SHAPE_RES` exist only as generator descriptors/test helpers — **no verification gate may use them**.

Clean templates (UTR slotted in): `NEFT-{utr}-RAZORPAY SOFTWARE PVT LTD-SETL` · `IMPS/P2A/{utr}/RAZORPAYSOF/UTIB0000123` · `BY TRANSFER-NEFT*HDFC0000001*{utr}*RAZORPAY SOFT`. Mangling operators per §5.2 T6. TDD: each operator produces its documented effect; families produce their shapes.

### 5.4 `manifest.py` — schema (read ONLY under `evalx/`; lint test greps it out of engine/ingest)

```json
{ "seed": 42, "profile": "mixed", "generator_version": "1.2.1",
  "expectations": {
    "plane_a_matches": [["txn_id","order_id"], ...],
    "plane_b_matches": [["settlement_id","bank_line_id"], ...],
    "exceptions": [ {"ids":["setl_x"], "allowed_codes":["MISSING_IN_BANK"], "tier":"T7"},
                    {"ids":["bank_7","bank_9"], "allowed_codes":["AMBIGUOUS_TIE"], "tier":"T5"} ] },
  "tier_labels": {"entity_id":"T5"},
  "structural": {"multi_settlement_days":["2026-08-04", ...]},
  "injected": [{"tier":"T6","ids":["bank_31"],"detail":{"op":"CONFUSABLE_SUB","pos":11}}] }
```

### 5.5 `emit.py` — stdlib `csv`, fixed column order, rows sorted by id, `\n` endings; **byte-identity per seed is a test**.

---

## 6. MATCHING ENGINE (complete; zero model calls)

### 6.1 Ingest & aggregation (`ingest/`)

Readers: `csv.DictReader`; monetary fields read as `str`, converted with explicit `int()`; strict ISO dates. Normalization checks: enums; PAYMENT `net == gross − fee − tax` with `(fee,tax) == fee_for(gross, channel)`; REFUND/CHARGEBACK `fee == tax == 0 ∧ net == gross < 0`; ADJUSTMENT `fee == tax == 0 ∧ net == gross`. Violations → `FEE_MODEL_VIOLATION` (row-scoped). Malformed rows → `INGEST_REJECT` with raw row preserved. **Unknown txn types (e.g. `transfer`): quarantine the row AND taint the batch** — `settlement_batches.tainted = 1`; a tainted batch is excluded from Plane-B matching and terminates as `UNSUPPORTED_MEMBER_IN_BATCH` with all member evidence (partial sums would silently corrupt arithmetic; abstention is the only honest move). Unit test feeds a hand-written transfer row and asserts the taint path.

**Null-tolerant batch-UTR aggregation (normative):**
```python
non_null = {r.settlement_utr for r in members if r.settlement_utr}
if   len(non_null) == 1: batch_utr = next(iter(non_null))
elif len(non_null) == 0: batch_utr = None
else: exception(UTR_CONFLICT_IN_BATCH, members)      # ≥2 DISTINCT non-null only
```

### 6.2 Plane A — orders ↔ gateway PAYMENT txns

- **A0** order-ref join: `txn.order_ref == order.order_id ∧ txn.gross == order.amount ∧ order.status != FAILED`. Valid ref with amount disagreement → `AMOUNT_MISMATCH_BEYOND_TOL`. Confidence 1.0.
- **A0b** payment-id join: `order.payment_id == txn.txn_id`, same validation. Confidence 1.0.
- **A1** mutual uniqueness: remaining orders ↔ remaining PAYMENT txns, exact amount, within `window_a_bd`; match iff exactly one candidate on **both** sides; ≥2 on either → `AMBIGUOUS_TIE`. Confidence 0.9.
- PAID order with no surviving txn → `PAID_ORDER_MISSING_FROM_GATEWAY`. REFUND rows attach via `original_payment_id` (bookkeeping, unscored); a refund whose parent payment is absent → `ORPHAN_REFUND`.

### 6.3 Plane B — settlement batches ↔ bank credits (untainted batches only)

Candidate window: `business_gap(processed_date, value_date) ≤ window_b_bd`, both sides unmatched. `normalize(s) = casefold(s)` with spaces/hyphens stripped. **Candidates are `(settlement_id, utr)` pairs** — duplicate UTR strings across batches stay distinguishable, and every recovery resolves to a settlement_id, not a string.

- **B0** UTR + exact amount: for each unmatched batch with `batch_utr`, search `normalize(narration)` for `normalize(batch_utr)` verbatim; require `credit == amount` exactly. One UTR found on ≥2 credits → `DUPLICATE_UTR` (all involved). Confidence 1.0. `batch_utr is None` ⇒ skip to B1/B2.
- **B1** mutual-uniqueness amount/date: `|credit − amount| ≤ tol_b_paise` within window; match iff exactly one candidate on both sides; ≥2 either side → `AMBIGUOUS_TIE` with every candidate in evidence. Confidence `0.95 − 0.01·gap_bd − (0.03 if diff > 0)`.
- **B2** deterministic fuzzy identifier recovery (`engine/recovery.py`) — **no model involved**:
  ```
  for each unmatched credit c, for each candidate (sid, utr):
    U = normalize(utr); H = normalize(c.narration)
    FULL:   U in H                                   → hit(kind=FULL)
    SUFFIX: some suffix of U, len ≥ 6, in H AND that suffix is unique
            among candidates                         → hit(kind=SUFFIX)
    CORRUPTED: some substring of H of len(U) is within one confusable
            substitution of U (pairs: 0↔O, 1↔I, 1↔L/l)→ hit(kind=CORRUPTED)
  a hit matches iff: exact amount ∧ window ∧ the hit resolves to exactly ONE
  settlement_id ∧ mutual uniqueness (that credit is the only credit hitting
  that sid, and vice versa); otherwise → AMBIGUOUS_TIE with hits in evidence.
  ```
  Confidences: FULL 0.9 · SUFFIX 0.8 · CORRUPTED 0.75. Complexity: candidates × narration length, trivially fast at this scale; the CORRUPTED scan is a sliding-window compare with an early-exit two-mismatch break. Evidence records the matched span offsets and, for CORRUPTED, the substituted position — the report highlights it exactly as the old design intended, now with zero model risk. Adversarial tests: two-substitution corruption rejected · suffix < 6 rejected · suffix hitting two sids → tie · hit on an already-consumed sid blocked by DB exclusivity.
- Residue triage: unmatched batch → `MISSING_IN_BANK`; unmatched credit → `UNKNOWN_BANK_CREDIT`, or `NARRATION_UNPARSEABLE` when no recovery hit of any kind existed.

### 6.4 `rules.py` and `confidence.py`

Every named threshold (windows, tolerances, suffix minimum, confusable pairs, confidence constants) lives in `rules.py` with a docstring saying *why that number*. `confidence.py` is a pure table-tested function of (tier, gap, diff, kind).

### 6.5 Pipeline (`engine/pipeline.py`) — the one true sequence

```
ingest → quarantine/taint → aggregate → persist raws+batches (txn) → audit
Plane A: A0 → A0b → A1                → persist (one txn per decision) → audit
Plane B: B0 → B1 → B2                 → persist → audit
triage residue → exceptions (deterministic actions §9; language layer §8 fills
    narrative/guidance text only) → persist → audit
emit RunStats: functional counters (deterministic) + telemetry (wall/latency)
```

---

## 7. WHERE THE AI IS — AND ISN'T (normative product stance)

The matching engine is 100% deterministic — three review rounds proved every proposed model role (solver-tie adjudication, span-verified extraction, allow-listed corruption recovery) was deterministically reachable, and the design law in the header now forbids reintroducing one. The LLM is the **finance-controller's voice**: it turns structured exceptions into two-sentence narratives and a short "what to check first" guidance list, and nothing else. `--llm live` vs `--llm mock` may change *prose*, never *results* — enforced by the invariance gate (§8.4). The README states this in one honest paragraph and the pitch leads with it: *"The books are code. The model only explains them."*

---

## 8. LLM LAYER (language only)

### 8.1 Client

```python
class LLMClient(Protocol):
    def complete_json(self, purpose: str, prompt: str) -> dict: ...
```
`live.py`: httpx; env per §4.4; temperature 0; JSON-only instruction + `json.loads` with one repair retry; tokens/cost/latency logged to `llm_calls` (telemetry). `mock.py`: returns deterministic template output (below) — tests and offline demo use only this.

### 8.2 Persistent cache (`llm/cache.py`) — survives run-DB recreation

Separate SQLite file at `MILAAN_CACHE_PATH` (default `data/.llm_cache.sqlite`, gitignored), opened independently of the run DB:

```sql
CREATE TABLE IF NOT EXISTS llm_cache(
  cache_key TEXT PRIMARY KEY,   -- sha256(provider|model|prompt_version|purpose|prompt)
  provider TEXT NOT NULL, model TEXT NOT NULL, prompt_version TEXT NOT NULL,
  response_json TEXT NOT NULL, created_at TEXT NOT NULL);
```
Lookup before any network call; hits recorded in the run DB's `llm_calls` with `cache_hit=1`. This is what makes video retakes free.

### 8.3 Prompts (`llm/prompts/`, each with a `PROMPT_VERSION` constant)

**`exception_narrate.txt` v3.0** — input: reason_code + evidence JSON (ids, amounts, dates, candidate lists, recovery spans). Output JSON: `{"narrative": "exactly two sentences", "guidance": ["≤3 short imperative checks"], "confidence": 0..1}`. Rules: use only ids/amounts present in the evidence; no speculation; no invented entities. The deterministic **canonical** text comes from `exceptions/actions.py` templates; the model may only rewrite for clarity.

**Mock behavior:** returns the template text unchanged (`guidance` from the per-code template map). Live behavior: polished text, **then the invariance filter**:

### 8.4 Invariance enforcement (two layers, both mandatory)

1. **Field firewall:** pipeline code writes model output only into `narrative`/`guidance`. Reason codes, ids, amounts, matches, metrics have no code path from model output (§0.8).
2. **Content check:** ids and amounts regex-extracted from the model's text must be a subset of those in the evidence; violation ⇒ discard polish, keep template, log `polish_rejected` audit event.
3. **CI gate (`tests/test_llm_mode_invariance.py`):** run the pipeline twice on the same generated batch, `--llm mock` and `--llm live` (live leg skipped without a key locally, enforced in CI-with-secret or via a recorded-cassette client): `functional_metrics.json` must be **byte-identical**. This is the replacement for the deleted ablation and a stronger claim: the model cannot move a single number.

---

## 9. EXCEPTIONS (complete)

Codes: `MISSING_IN_BANK · UNKNOWN_BANK_CREDIT · PAID_ORDER_MISSING_FROM_GATEWAY · AMOUNT_MISMATCH_BEYOND_TOL · DATE_OUT_OF_WINDOW · AMBIGUOUS_TIE · AMBIGUOUS_COMBINED · DUPLICATE_UTR · DUPLICATE_BANK_LINE · UTR_CONFLICT_IN_BATCH · UNSUPPORTED_MEMBER_IN_BATCH · NARRATION_UNPARSEABLE · FEE_MODEL_VIOLATION · ORPHAN_REFUND · INGEST_REJECT`.

Precedence (most specific wins): unsupported-member/taint > duplicates/conflicts > ambiguity > amount > date > narration > missing/unknown. Every exception carries: scope_ids · code · confidence · evidence (all candidates considered with diffs; recovery hits with spans) · deterministic `suggested_action` from `actions.py` (per-code template, e.g. `MISSING_IN_BANK` → "Raise with bank: settlement {sid}, UTR {utr}, expected ₹{amt} in window {d1}–{d2}") · `narrative`/`guidance` text per §8. Exceptions are scored (§10): recall against `expectations.exceptions` with `allowed_codes`, precision against the full flagged set.

---

## 10. METRICS (`evalx/`, complete)

**Units:** Plane A unit = (PAYMENT txn ↔ order) pair. Plane B unit = (settlement batch ↔ bank credit) pair — a 23-member batch matched to its credit is **one** unit; member linkage is data, never scored.

Per plane, per difficulty tier, and overall:
`auto_match_rate = |found ∩ expected| / |expected|` · `false_match_count`, `false_match_rate = |found − expected| / max(1,|found|)` (a right credit with the wrong batch is **false**, not partial — say so in the README) · tier coverage A0/A0b/A1/B0/B1/B2(+kind) · `exception_recall` (every planted anomaly flagged with an allowed code) and `exception_precision` · completeness (every expected entity in exactly one bucket).

**Outputs:** `functional_metrics.json` — all of the above + seed/profile/config hashes; deterministic; golden-tested (`evalx/golden/functional_metrics_seed42_mock.json`, exact equality). `runtime_telemetry.json` — wall time, per-stage latency p50/p95, LLM tokens/cost, model, git SHA, timestamps; never golden-tested. **Claims wording (README/form/pitch):** always "zero false matches **on the named synthetic benchmark** (seed N, profile P, generator 1.2.1)" — on real data the guarantees are the invariants (DB exclusivity, abstention, taint-and-abstain) plus sampled audit, never a universal promise.

Gate bundles: §1.2, implemented here; `--gate` failures exit 1 with the failing metric named.

---

## 11. REPORT (`report/`, complete)

`milaan report --run DIR --db PATH --out PATH` — reads both JSON files from the run dir plus the DB; renders one self-contained HTML file (inline CSS, no external assets, printable):

run header (seed · profile · git SHA · **functional and telemetry in visually separate panels**) → per-plane summary cards → per-tier tables → **drill-down 1:** a batch↔credit match showing member signed nets, Σ, credit, Δ → **drill-down 2:** a B2 CORRUPTED recovery with the narration span highlighted and the substituted character marked → **multi-settlement-day card** (two settlements, one date, both matched — proof of settlement_id grouping) → exception table (`<details>` per item: evidence, narrative, guidance, action) → telemetry panel. A generated sample + screenshot committed under `data/samples/run42/`.

---

## 12. TESTS & TASK ORDER

### 12.1 Test map (all offline; live-LLM paths via recorded cassette or CI secret only)

| File | Asserts |
|---|---|
| test_calendar | business-day math incl. holiday rollovers |
| test_narration | families produce shapes; each mangling op produces its documented effect |
| test_generator | byte-identical per seed; disjoint tier targets asserted; expectations complete; multi-settlement days present |
| test_ingest | str→int money; identity checks; quarantine; **transfer row ⇒ batch tainted ⇒ UNSUPPORTED_MEMBER_IN_BATCH**; null-tolerant UTR aggregation (adj-null no conflict; two distinct non-null ⇒ conflict) |
| test_plane_a | A0/A0b joins; A1 mutual uniqueness; ambiguity → tie; PAID_ORDER_MISSING_FROM_GATEWAY |
| test_plane_b | B0 candidate-driven; B1 tie-trap returns AMBIGUOUS_TIE never a match; duplicate-UTR path |
| test_recovery | FULL/SUFFIX/CORRUPTED accept cases; two-substitution reject; suffix<6 reject; suffix→two sids ⇒ tie; consumed sid blocked by DB |
| test_db | NOT NULL enforced; duplicate member fails; composite-FK child/parent mismatch fails; foreign_keys pragma on |
| test_pipeline_clean | property: clean profile, seeds 1–5 ⇒ both planes 100%, 0 exceptions, false 0 |
| test_pipeline_mixed | seed 42 ⇒ gate-mixed thresholds; every injected anomaly ends with an allowed code |
| test_metrics | formula units incl. wrong-batch-counts-as-false; completeness |
| test_golden_metrics | seed-42 mock functional metrics == golden |
| test_llm_mode_invariance | functional metrics byte-identical mock vs live/cassette; polish content-check rejects id/amount drift |
| test_audit | every decision has an audit row; llm_calls rows for polish calls |

### 12.2 Task order (dates unchanged; bold = gate)

```
P0 Aug 21–22  T01 scaffold+CI(.PHONY, fail-fast, mktemp, badge)
              T02 models+db(+pragma, NOT NULL, composite FK)+audit+fresh-db-per-run
P1 Aug 23–24  T03 calendar(TDD)  T04 narration families+ops(TDD)
              T05 world.py (capture clock, settlement_id batching, multi-settlement
              days, A-structural gaps)  T06 inject.py disjoint T2/T5/T6/T7
              T07 manifest+emit+determinism
              [**gen gate: byte-identical; disjointness asserted; expectations complete**]
P2 Aug 25–26  T08 ingest incl. taint + null-tolerant UTR(TDD)  T09 aggregate
              T10 Plane A(TDD)  T11 Plane B B0+B1(TDD)
              T12 metrics+gates+functional/telemetry split+golden
              [**--gate clean passes end-to-end**]
P3 Aug 27–28  T13 B2 deterministic recovery(TDD, adversarial)  T14 triage+actions+
              all codes+mixed hardening
              [**--gate mixed passes: A≥99.5 · B0+B1+B2≥95 · false 0 · exc-recall 100 · complete 100**]
P4 Aug 29–30  T15 llm client+persistent cache+templates+polish+content-check
              T16 invariance test (mock vs cassette/live)
              [**functional metrics byte-identical across llm modes; gates still pass**]
P5 Aug 31     T17 report(+--run, both drill-downs, multi-settlement card, telemetry)
P6 Sep 1      T18 README(+design law, Known Failures, benchmark-scoped claims)
              T19 ADRs + sample run + stranger-test        [**fresh clone ≤ 5 min**]
P7 Sep 2–3    video + form (real numbers)        P8 Sep 4  submit
T20 SOLVER: NOT APPROVED. Not scheduled. Requires separate owner+reviewer sign-off
    of a written design (component-global, lexicographic, whole-component abstention,
    set-valued matches) before any code exists.
```

## 13. CUT ORDER & ANTI-PATTERNS

Cut in order if behind: ① live-LLM polish (templates stand; invariance test runs mock-vs-mock as a tautology and says so honestly) ② T6 breadth (keep suffix + drop-UTR + one confusable op) ③ multi-settlement-day card in the report (keep the data + metric). **Never cut:** manifest expectations · per-plane metrics + gates · false-match CI gate · deterministic-only matching · DB exclusivity (NOT NULL + FK + pragma) · taint-and-abstain · Known Failures.

Anti-patterns (instant review-reject): float on money · pandas/pyyaml · `datetime.now()` in generator/engine · **any model output influencing a match, code, id, amount, or metric** · regex- or shape-gating candidate verification · partial batch sums after dropping a member row · nullable `match_members` columns or a connection without `foreign_keys=ON` · two matches sharing a bank line · date-based batching assumptions · treating a null member UTR as a conflict · cache stored inside the run DB · weakening the mock or any gate · a README/form number not reproduced by `make demo` · "zero false matches" without the benchmark qualifier.

— end of spec v1.2.1 —
