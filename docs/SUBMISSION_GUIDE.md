# Submission Guide

## One-line pitch

Milaan is a deterministic three-way settlement controller that proves matches,
abstains with exact evidence, and uses AI only to explain exceptions—never to
move a number.

## Five-minute demo

1. **0:00–0:35 — State the loop.** “Milaan reconciles merchant orders to
   gateway settlement rows to bank credits, then explains every abstention.”
   Show the Track 04 requirement table at the top of the README.
2. **0:35–1:10 — State the trust boundary.** Show the architecture diagram and
   say: “The books are code. The model only explains them.” Name grouping by
   `settlement_id`, database exclusivity, and taint-and-abstain.
3. **1:10–1:35 — Reproduce it.** Run `make demo`. Point out that it uses 1,200
   records, requires no API key, and prints real numerators and denominators.
4. **1:35–2:20 — Show the scorecard.** Open `data/run42/report.html`. Show Plane
   A 1,154/1,154, Plane B 21/21, exception recall 6/6, completeness
   2,363/2,363, and zero false matches on this named synthetic benchmark.
5. **2:20–3:10 — Show proof, not just a card.** Show the signed-member drill-down:
   payment/refund/adjustment nets sum to one batch and equal one bank credit.
   Then show two batches on the same date remaining separate by `settlement_id`.
6. **3:10–3:50 — Show difficult evidence.** Show deterministic B2 recovery and
   its highlighted narration span. Explain that bounded evidence recovery is
   code because enumeration can decide it.
7. **3:50–4:25 — Show honesty.** Open `AMBIGUOUS_TIE` and show the candidates.
   Say that choosing either would be plausible but unprovable, so Milaan abstains.
8. **4:25–5:00 — Close on AI and portability.** Show the BYO-LLM table in the
   README: OpenAI-compatible, Ollama, Anthropic, and Gemini. Explain that the
   model changes only wording, the firewall rejects new facts, and CI requires
   byte-identical functional metrics with live mode on or off.

## Reproducible numbers to use

Always name the benchmark: seed 42, mixed profile, generator 1.2.1, 1,200
orders.

- Plane A: 1,154/1,154 correct auto-matches.
- Plane B: 21/21 correct auto-matches.
- Expected exception recall and precision: 6/6 each.
- Completeness: 2,363/2,363 entities.
- False matches: 0 on this named synthetic benchmark.
- Offline automated tests: 49/49.
- Fresh-clone `make demo`: 3 seconds in the verification environment.

## “Where is the AI?” answer

> Nowhere near the money—by proof, not preference. Three hostile review rounds
> showed that each proposed model-in-the-loop role was deterministically
> reachable, so the final system lets the model touch only exception language.
> CI verifies that the books are byte-identical with the model on or off. Every
> hostile review removed another place where the system could pretend certainty.

## Before uploading

- Create the GitHub repository and push the existing commit history.
- Replace the README CI badge target if the repository slug differs.
- Run `make ci` once on the upload machine.
- Confirm `data/samples/run42/report.html` opens locally.
- Never commit `.env`, API keys, `data/.llm_cache.sqlite`, or run databases.
- Do not claim production accuracy or universal zero false matches.
- Do not add CP-SAT or a model-based matching path before submission.
