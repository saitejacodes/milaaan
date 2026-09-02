# Milaan v1.3 verified build log

All claims below came from executable local gates on 2 September 2026. Runtime
figures are environment observations, not universal performance guarantees.

## Named dataset — PASS

- Command: `milaan gen --records 1200 --seed 42 --profile mixed`.
- Output: 1,200 orders; 1,239 gateway rows; 28 bank lines; 2,467 physical
  source records.
- Generator: 1.3.0; deterministic byte identity: PASS.
- Orders SHA-256: `57aaa6fccfae45bd10b8c0d2aec35239714ed3e48390635f423895470093b066`.
- Gateway SHA-256: `733ff0055daf4ed600ec7f7a96b70f53c2ce26fa83a8fc640b1cb319263d64f8`.
- Bank SHA-256: `210c4f005da1e1333065221984278fac63e611268e83f7ee1bafd8279f8948ee`.
- Manifest SHA-256: `095b2ce5c3517653ed41b4d69629620809befacb0c20c5634bb0e95b5659a498`.
- Manifest-to-input hash binding: PASS.

## Correctness and honesty gate — PASS

- Plane A expected-match recall: 1,154/1,154; precision: 1,154/1,154.
- Plane B expected-match recall: 21/21; precision: 21/21.
- Exception recall: 6/6; exception precision: 6/6 using one-to-one exact-scope matching.
- False matches: 0 on the named synthetic benchmark.
- Source-record conservation: 2,467/2,467.
- Settlement amount conservation: ₹8,782,752.25 source net and batch total;
  delta 0 paise.
- Evaluator verifies IDs, expected amounts, bank amounts, and exact settlement
  member sets. Pair identifiers alone are insufficient.

## Workload coverage — REPORTED SEPARATELY

- Eligible orders: 1,154/1,155 (99.91%).
- Gateway payments: 1,154/1,154 (100.00%).
- Settlement batches: 21/26 (80.77%).
- Bank lines: 21/28 (75.00%).

The gap between labelled-match accuracy and workload coverage is intentional
abstention and is visible in the report.

## Cash position — PASS

- Verified banked: ₹6,650,191.65.
- Expected but unbanked: ₹870,532.12.
- Blocked settlement evidence: ₹1,262,028.48.
- Unexplained bank credits: ₹1,898,941.06.
- Gross evidence under attention: ₹4,031,501.66. This may contain both sides of
  an ambiguity and is not presented as a net loss estimate.

## Adversarial controls — PASS

- Rejected member plus tempting partial bank credit: full settlement tainted;
  no partial match posted.
- Duplicate order/payment identity claims: `IDENTITY_CONFLICT`; no sorted winner.
- Payment before order: blocked.
- Bank credit before settlement processing: blocked.
- Modified input with original manifest: evaluation fails hash gate.
- Correct pair IDs with wrong amount/member truth: counted as a false match.
- Unexpected exception: fails exception-precision gate.
- Semantic reversal of `MISSING_IN_BANK`: language firewall rejects it.
- Invalid AI tool name or write-shaped output: refused.

## Finance-agent gate — PASS

- Offline named set: 50 questions.
- Tool selection: 50/50.
- Grounded answer or correct refusal: 50/50.
- Allowed surface: cash, metrics, exposure, exception lookup, order trace,
  settlement trace, and throughput.
- Model scope: tool and typed-argument selection only; deterministic code returns
  the financial answer and evidence IDs.
- This is an offline router gate, not a real-provider accuracy claim.

## Throughput sweep — PASS

Three mixed-profile repetitions per size; generation and evaluation excluded
from end-to-end reconciliation wall time.

| Orders | Median source records | Median records/s |
|---:|---:|---:|
| 50 | 125 | 9,769 |
| 200 | 435 | 16,148 |
| 1,200 | 2,464 | 20,364 |
| 5,000 | 10,159 | 22,959 |
| 10,000 | 20,291 | 20,876 |

## Full CI — PASS

- Offline tests: 64/64.
- Clean seed 1: 189/189 Plane A; 23/23 Plane B; zero exceptions and false matches.
- Mixed seeds 1–3: every correctness, exception, integrity, and conservation gate passed.
- Hard seeds 1–3: every correctness, exception, integrity, and conservation gate passed.
- Second full run/eval/agent/report cycle: PASS.
- Exact seed-42 golden metrics: PASS.
- Mock/live-offline functional byte equality: PASS.
- GitHub Actions matrix: Python 3.11, 3.12, and 3.13; remote result must be
  checked after this branch is pushed.

## Reviewer artifacts

- Self-contained report: `data/samples/run42/report.html` (15,975 bytes).
- Functional metrics SHA-256:
  `b7e958ef4ad19ff3967c78e6db87030ac110caf5faad7da32921f4e95f638558`.
- Agent metrics SHA-256:
  `252f215ce6e81d69f0dd717662f67d234b29c0cff5a1d3dd0cd3876e5f3278e8`.
- Throughput artifact: `data/benchmark.json`.
