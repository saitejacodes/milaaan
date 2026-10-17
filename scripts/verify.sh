#!/usr/bin/env bash
# The same checks as `make ci` followed by `make judge`, for machines without make.
# Usage: scripts/verify.sh [python]   (defaults to python3)
set -euo pipefail
PY="${1:-python3}"
CLI=("$PY" -m milaan.cli)
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT

echo "== test suite"
"$PY" -m unittest discover -s tests 2>&1 | tail -3

echo "== clean profile"
"${CLI[@]}" gen --records 200 --seed 1 --profile clean --out "$T/clean"
"${CLI[@]}" run --data "$T/clean" --db "$T/clean/m.db" --llm mock
"${CLI[@]}" eval --run "$T/clean" --db "$T/clean/m.db" --out-dir "$T/clean" --gate clean
"${CLI[@]}" agent-eval --run "$T/clean" --db "$T/clean/m.db" --out "$T/clean/agent.json" --llm mock

for profile in mixed hard; do
  for seed in 1 2 3; do
    echo "== $profile seed $seed"
    d="$T/$profile$seed"
    "${CLI[@]}" gen --records 1200 --seed "$seed" --profile "$profile" --out "$d"
    "${CLI[@]}" run --data "$d" --db "$d/m.db" --llm mock
    "${CLI[@]}" eval --run "$d" --db "$d/m.db" --out-dir "$d" --gate "$profile"
  done
done

echo "== agent suite and report"
"${CLI[@]}" agent-eval --run "$T/mixed1" --db "$T/mixed1/m.db" --out "$T/mixed1/agent.json" --llm mock
"${CLI[@]}" report --run "$T/mixed1" --db "$T/mixed1/m.db" --out "$T/mixed1/report.html"

echo "== adversarial"
"$PY" -m milaan.evalx.adversary | tail -4

echo "== benchmark smoke"
"${CLI[@]}" benchmark --out "$T/benchmark_smoke.json" --sizes 50,200,1200 --repetitions 3

echo "== dashboard import"
"$PY" -c "import milaan.dashboard as d; assert callable(d.main); print('dashboard import OK')"

echo "== judge"
"$PY" -m milaan.evalx.judge --out "$T/judge" | tail -3
echo "ALL CHECKS PASSED"
