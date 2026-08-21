# Build Log

All metrics in this file are copied from executable gates with real denominators.

## P1 generation gate — PASS

- Command: `milaan gen --records 1200 --seed 42 --profile mixed`
- Output: 1,200 orders; 1,239 gateway recon rows; 28 bank lines.
- Byte identity: PASS across two independent output directories.
- Disjoint injected targets: PASS.
- Expectation completeness: PASS.
- SHA-256: orders `57aaa6fccfae45bd10b8c0d2aec35239714ed3e48390635f423895470093b066`; recon `733ff0055daf4ed600ec7f7a96b70f53c2ce26fa83a8fc640b1cb319263d64f8`; bank `d782f031e9cebbfd59fac6ccae23368104cffb8c6b2828d434211ff3e722398f`; manifest `5ada335b94c7d3a20582b43642a6b64c7421fbd588d1896b0722871d9597dee0`.

## P2 clean gate — PASS

- Benchmark: seed 1, clean profile, 200 orders.
- Plane A: 189/189 correct matches (100.00%).
- Plane B: 23/23 correct matches (100.00%).
- Exceptions: 0/0; false matches: 0.

## P3 mixed gate — PASS

- Benchmark: seed 42, mixed profile, generator 1.2.1, 1,200 orders.
- Plane A: 1,154/1,154 correct matches (100.00%).
- Plane B: 21/21 correct matches (100.00%); tiers B0=19 and deterministic B2=2.
- Exceptions: recall 6/6 (100.00%); precision 6/6 (100.00%).
- Completeness: 2,363/2,363 entities (100.00%).
- False matches: 0 on this named synthetic benchmark.

## P4 language-layer invariance — PASS

- Mock-mode and live-mode/offline-fallback `functional_metrics.json`: byte-identical.
- Model-writable fields: `narrative` and `guidance` only.
- Invented identifier test: rejected; canonical template retained.
- Invented monetary amount test: rejected; canonical template retained.
- Persistent cache reopen test: PASS outside the recreated run database.

## P5 report gate — PASS

- Self-contained HTML: 12,011 bytes; no external scripts or assets.
- Contains signed-member batch proof, bank-credit delta, deterministic B2 span,
  multi-settlement-day proof, exception evidence, and separate telemetry.
- Generated preview: `data/samples/run42/report_preview.png`.

## Full CI — PASS

- Offline tests: 40/40 passed.
- Clean seed 1: Plane A 189/189; Plane B 23/23; exceptions 0; false matches 0.
- Mixed seed 1: Plane A 1,139/1,139; Plane B 19/19; exceptions 6/6; false 0.
- Mixed seed 2: Plane A 1,148/1,148; Plane B 20/20; exceptions 6/6; false 0.
- Mixed seed 3: Plane A 1,145/1,145; Plane B 19/19; exceptions 6/6; false 0.
- Full second `run → eval → report` cycle on seed 1: PASS; report 12,007 bytes.
- Exact seed-42 golden metrics: PASS.
- Mock/live functional byte equality: PASS.
