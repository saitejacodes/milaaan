# Instructions for AI coding agents working on Milaan

You are working on **Milaan**, a payment-reconciliation engine with a read-only AI
agent and a public web demo. Read this whole file before changing anything.

## The one rule that overrides everything

**Deterministic code owns every rupee. The AI can only read, never write.**
Never add a code path where a language model (or any agent tool) can create, change
or delete a match, an exception, a settlement, a bank line or any accounting table.

## Always do

1. **Run the checks before and after every change.** All must pass:
   ```bash
   scripts/verify.sh .venv/bin/python   # = make ci + make judge, works without make
   ```
   Quick loop while editing: `.venv/bin/python -m pytest -q`.
2. **Work on a new git branch** per task, with small commits and clear messages.
3. **Get every number by running code.** Any number in the README, UI, docs or a
   resume must come from `make judge`, `make benchmark` or `agent-eval` output.
   After changing behaviour, run `make sample` (or
   `python -m milaan.evalx.judge --out data/samples/run42 --skip-attacks` then
   `python scripts/render_report_preview.py`) so the committed artefacts match.
   `tests/test_published_claims.py` will fail if the README and artefacts disagree;
   fix the cause, never the test.
4. **Keep the word "synthetic"** next to accuracy claims. The benchmark data and its
   answer key come from Milaan's own generator.
5. **Money is integer paise.** Use `int` and `milaan.money.format_paise`. Never use
   `float` for money, not even for display.
6. **Add a test** for every bug fix and feature. A safety change also needs a
   "this attack is still refused" test.
7. **Write to the run database only through the engine** (`milaan/engine/pipeline.py`,
   `milaan/db.py`). Read paths use `db.reading(path)` so connections close.
8. **Match the surrounding style:** type hints, small functions, docstrings that say
   *why*, and the existing comment density.
9. **Ask the human first** before adding a dependency, a paid service, a new external
   network call, or before deleting a file you didn't create.

## Never do

1. **Never give the agent a write tool.** `milaan/agent/tools.py` is read-only by
   design; every tool runs `SELECT` only. New tools must be read-only and added to
   both `FinanceTools.registry` and the argument check in
   `router._validated_selection`.
2. **Never weaken the authority boundary** (`router.authority_boundary`,
   `WRITE_INTENT_RE`, `AUTHORITY_OVERRIDE_RE`). It runs before any model. If a
   harmless question is refused, add it to `_benign_cases()` and make the pattern
   more precise. Never delete a refusal case to make a test pass.
3. **Never let model text become an answer or a fact.** The model may only return
   `{"tool": ..., "arguments": ...}`. Answers come from the tools.
4. **Never trust `manifest.json` in the evaluator.** Truth is rebuilt from
   `run_meta.json`. Never let the engine read the manifest.
5. **Never relax a matching rule** to raise coverage: no amount tolerance, no
   subset-sum solver, no tie-breaking by sort order. Abstaining is correct behaviour.
6. **Never edit tests, golden files or committed metrics to make a failure go away.**
   If a test is wrong, explain why to the human first.
7. **Never commit secrets.** No API keys in code, `.env`, README, tests, screenshots
   or commit messages. `.env` is gitignored; keep it that way.
8. **Never put a visitor's API key into `os.environ`**, a file, the response cache,
   the run database, a log, an exception message or a `repr`. Bring-your-own-key
   clients use `LLMSettings` + `LiveLLM(settings, use_cache=False)`.
   `tests/test_byok.py` must keep passing.
9. **Never let the demo call a URL the visitor typed** without
   `demo.check_public_https_url` (https only, public addresses only).
10. **Never add a server-side API key to the public demo.** It must work with no key.
11. **Never claim a result you didn't measure**, and never call the offline
    routing or multi-step scores "AI accuracy". They measure deterministic code.
12. **Never run destructive git** (`push --force` to `main`, `reset --hard` on shared
    branches, history rewrites) without the human's explicit approval.

## Map of the code

| Path | What it is |
|---|---|
| `milaan/engine/` | Matching: Plane A (order→payment), Plane B (settlement→bank), recovery |
| `milaan/ingest/` | CSV parsing, quarantine, settlement batch sums |
| `milaan/exceptions/` | Exception wording and residual triage |
| `milaan/evalx/` | Truth rebuild, metrics, gates, 64-attack adversary, benchmark, judge |
| `milaan/agent/` | Read-only tools, router + authority boundary, multi-step `investigate`, agent eval |
| `milaan/llm/` | Provider adapters (`LiveLLM`, `LLMSettings`), mock, cache, exception-text firewall |
| `milaan/demo.py` / `webapp.py` | Public web demo backend / Streamlit UI |
| `milaan/view.py` | Shared view model for the report and dashboard |
| `tests/` | 270 tests, including safety, tampering, claims, BYOK and web UI |

## When you finish a task, report

- What changed, file by file, and why.
- The exact command output of `scripts/verify.sh` (pass or fail; never hide a failure).
- Any number that changed, with the command that produced it.
- Anything you skipped or couldn't verify.
