# Submission Guide

## One-line pitch

Milaan is a deterministic three-way settlement controller that proves matches,
abstains with exact evidence, and uses AI only to explain exceptions—never to
move a number.

## Three-minute demo

1. Run `make demo` and show the four commands completing.
2. Open `data/run42/report.html` and show the five functional cards.
3. Open the signed-net drill-down: transaction members sum to one settlement
   batch, which equals one bank credit.
4. Show deterministic B2 recovery and its highlighted narration span.
5. Show the multi-settlement-day card: two batches share a date but remain
   separate because grouping uses `settlement_id`.
6. Open one `AMBIGUOUS_TIE` exception and point out that Milaan abstained instead
   of choosing a plausible candidate.
7. Finish with the language-layer invariant: mock and live modes produce
   byte-identical functional metrics.

## Reproducible numbers to use

Always name the benchmark: seed 42, mixed profile, generator 1.2.1, 1,200
orders.

- Plane A: 1,154/1,154 correct auto-matches.
- Plane B: 21/21 correct auto-matches.
- Expected exception recall and precision: 6/6 each.
- Completeness: 2,363/2,363 entities.
- False matches: 0 on this named synthetic benchmark.
- Offline automated tests: 40/40.
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
