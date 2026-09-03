# Milaan v1.3 submission guide

## One-line pitch

Milaan is a fail-closed AI finance controller: deterministic code owns every
rupee, while AI investigates verified cash exposure and exceptions through
read-only tools.

## Five-minute demo

1. **0:00–0:30 — The finance loop.** “Milaan reconciles orders to gateway
   transactions to settlement batches to bank credits, then reports the cash
   position and every unresolved exception.”
2. **0:30–1:00 — The trust boundary.** Show `docs/ARCHITECTURE.md`. State that
   models cannot match, post, calculate money, or write accounting state.
3. **1:00–1:30 — Reproduce it.** Run `make demo`. Point out 1,200 orders,
   2,467 physical source records, exact hashes, and no required API key.
4. **1:30–2:10 — Honest measurements.** Open the report. First show expected-pair
   precision/recall, then the separate workload coverage: 99.91% of eligible
   orders and 80.77% of settlement batches. Explain that abstentions are not
   hidden inside a 100% headline.
5. **2:10–2:45 — Cash position.** Show verified banked, expected-unbanked,
   blocked, and unexplained bank amounts. Open one amount to its evidence IDs.
6. **2:45–3:20 — Ask Milaan.** Ask “Why is cash blocked?” The model selects an
   allow-listed read-only tool; code returns the amount and cited settlements.
   Then ask “Delete all exceptions” and show the refusal.
7. **3:20–4:10 — Failure recovery.** Corrupt one settlement member fee and
   reduce the bank credit to the tempting partial sum. Show that the entire batch
   is tainted, no match is posted, and the exception names the quarantined row.
8. **4:10–4:40 — Proof and scale.** Show signed-member batch proof, input hash
   binding, 2,467/2,467 source conservation, and the five-size benchmark.
9. **4:40–5:00 — Close.** “Milaan does not maximize the number of matches. It
   maximizes the number of matches a finance team can safely defend.”

## Exact named results

Always name the benchmark: **seed 42, mixed profile, generator 1.3.0, 1,200
orders, 2,467 physical source records**.

- Plane A expected-match recall and precision: 1,154/1,154 each.
- Plane B expected-match recall and precision: 21/21 each.
- Workload coverage: 1,154/1,155 eligible orders; 21/26 settlement batches;
  21/28 bank lines.
- Exception recall and precision: 6/6 each.
- Source-record conservation: 2,467/2,467.
- Settlement amount conservation delta: 0 paise.
- False matches: 0 on this named synthetic benchmark.
- Offline finance-agent gate: 50/50 tool selections and 50/50 grounded/refused
  outputs. This is not a real-model benchmark.
- Three-run median throughput: 20,364 source records/s at 1,200 orders and
  20,876 source records/s at 10,000 orders in the verification environment.

## “Where is the AI?” answer

> The AI converts a natural-language finance question into one approved,
> read-only investigation tool call. Deterministic code returns the monetary
> answer and evidence IDs. The model is useful for operator intent, but it has
> no authority to invent a fact, approve a match, or post money.

If a real provider is shown, name its provider/model and separately report its
result. Never present the offline deterministic-router score as model accuracy.

## Before uploading

- Run `make ci` and confirm all clean, mixed, and hard gates pass.
- Run `make benchmark`; copy only the newly measured numbers.
- Open `data/samples/run42/report.html` and verify the cash and AI sections.
- Start `make dashboard` and test one trace, one finance question, and one refusal.
- Put the demo video and any live dashboard URL at the top of the README.
- Confirm GitHub Actions passes on Python 3.11, 3.12, and 3.13.
- Never commit `.env`, API keys, model cache, or run databases.
- Do not claim production accuracy, real-world validation, or universal zero
  false matches.
- Do not add model-based matching or money movement before submission.
