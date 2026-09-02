# Milaan v1.3.0 — finance-control hardening

## Correctness

- Taint a settlement after any rejected member, including fee and parse failures.
- Block duplicate/conflicting order-payment identity graphs.
- Emit an exception for every residual gateway payment.
- Enforce forward-only order → payment → settlement → bank chronology.
- Add end-to-end regression coverage for the partial-batch exploit.

## Evaluation integrity

- Bind manifests to exact source-file SHA-256 values.
- Score expected monetary amounts and settlement member sets, not IDs alone.
- Match expected and actual exceptions one-to-one with exact scope.
- Enforce match precision, exception precision, source-record conservation, and
  settlement amount conservation in clean, mixed, and hard gates.
- Report expected-match accuracy separately from complete-workload coverage.

## Cash and AI

- Add deterministic banked, expected-unbanked, blocked, and unexplained cash buckets.
- Add seven read-only finance investigation tools.
- Restrict live model output to one allow-listed tool and typed arguments.
- Add 50-question routing/grounding/refusal evaluation and an operator dashboard.
- Make exception-language replacement exact and fail closed against semantic reversal.

## Evidence and scale

- Add source records/second to runtime telemetry.
- Remove a quadratic set-union path in Plane A.
- Add five-size, three-repetition benchmark through 10,000 orders.
- Expand CI to clean, mixed, and hard profiles on Python 3.11–3.13.
- Regenerate the named sample, metrics, report, and build log.
