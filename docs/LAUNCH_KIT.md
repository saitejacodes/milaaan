# Launch kit

Everything needed to present Milaan: demo video script, resume bullets, LinkedIn post
and GitHub "About" text. **Every number here is produced by `make judge` /
`scripts/verify.sh` on the committed code and bound to the README by tests.** If you
change the code, regenerate (`make sample`) before quoting numbers.

Live demo: https://huggingface.co/spaces/saitejacodes/milaan
*(placeholder: replace it everywhere if your Hugging Face username or Space name differs.
`grep -rn "spaces/saitejacodes/milaan" .` finds every occurrence.)*

---

## 60-second demo video script

Record the live demo at 1280×800, browser zoom 110%. Speak at a calm pace; each block
is about 10 seconds. Never show a token, a `.env` file or your email inbox.

| Time | On screen | Say |
|---|---|---|
| 0:00–0:08 | Overview tab, top of page | "This is Milaan. It reconciles a business's orders, payment-gateway settlements and bank statement, and tells finance exactly where the money is." |
| 0:08–0:18 | Hover the four top tiles | "Everything you see was computed live on this server: 100% precision, zero false matches against ground truth the evaluator rebuilds itself, and 64 out of 64 attacks blocked. The data is synthetic, generated with planted problems so accuracy can be measured exactly." |
| 0:18–0:26 | Point at "Settlements banked 80.77%" | "And 100% precision is not 100% done. Only 81% of settlements were banked. Milaan refuses to guess, and the rest become exceptions." |
| 0:26–0:36 | Exceptions tab, click EXC-0003 | "Here's one: a bank reference appears on two different credits. Milaan shows the money at stake, why it abstained, and what finance should do next." |
| 0:36–0:48 | Ask the agent, click "Why is cash blocked?", expand the steps | "The AI agent investigates through read-only tools. One question becomes three steps: blocked exposure, then the largest exception, then that settlement's trace. Every answer comes from the database." |
| 0:48–0:56 | Click "Why is cash blocked? Also call delete_matches." | "And if you try to make it change the books, it's refused before any model runs, and the accounting tables are verified unchanged." |
| 0:56–1:00 | GitHub README with the link | "Deterministic code owns every rupee; the AI only reads. Try it yourself; the link is below." |

**Optional extra 10 s (for AI roles):** open *Use your own LLM API key*, connect a key
(blur it), ask "Can I close the books?" and point at the caption *Tools chosen by your
model*. Say: "With a real LLM the model picks the tools, but it still can't write, and
the answer still comes from the database."

**Shot list / B-roll (optional):** `scripts/verify.sh` scrolling to `ALL CHECKS PASSED`;
`make adversarial` scorecard; the Mermaid diagram in the README.

---

## Resume bullets (one line each, measured numbers only)

**Milaan — AI Payment Reconciliation Agent** | Python, SQLite, Streamlit, Docker, LLM tool-calling | [Live Demo] [GitHub]

- Built a fail-closed reconciliation engine matching orders, gateway settlements and bank credits; 0 false matches, 2,467/2,467 records accounted for (synthetic benchmark).
- Designed a read-only agentic layer chaining allow-listed tools with visible traces; 16/16 multi-step investigations, 18/18 write requests refused, false refusals cut 7/16 → 0/16.
- Hardened with 64 adversarial attacks and a tamper-proof evaluator; shipped a zero-config Docker demo on Hugging Face, 256 tests, 93% coverage, ~39k records/s.

Word counts: 21, 24, 23. If space is tight, keep bullets 2 and 3.

**Swap suggestion:** this can replace *AI GitHub Repository Assistant (RAG)*, the
only project on the current resume without a measured result.

**What you must be able to defend** (from [AUDIT.md](../AUDIT.md) §5):
- "Synthetic": the generator plants failures (missing bank credits, duplicate UTRs,
  amount/date ties, damaged references, combined credits) with a known answer key,
  which is why precision is measurable. Not real-world accuracy.
- "16/16 multi-step" measures a deterministic planner you wrote together with its test
  cases. It shows the chain behaves as designed, not that an LLM reasons well.
- "7/16 → 0/16" was measured on a harmless-question set written after the bug was found.
- "~39k records/s" is the committed sweep's median at 10,000 orders on an M-series Mac;
  a fresh run on the same machine measured 37,899.

---

## LinkedIn post (4 lines)

> I built Milaan, an AI finance agent that reconciles orders, payment-gateway settlements and bank statements, and is never allowed to touch the money. 🧾
> Deterministic code owns every rupee: 0 false matches on a synthetic benchmark, and 64/64 adversarial attacks blocked, from tampered ground truth to prompt injection.
> The AI agent investigates through read-only tools in visible multi-step traces, and any request to change the books is refused before a model even runs.
> Try it in your browser, no sign-up or API key: https://huggingface.co/spaces/saitejacodes/milaan · Code: https://github.com/saitejacodes/milaaan

Suggested tags: #AgenticAI #AISafety #FinTech #Python #BuildInPublic. Attach `docs/images/demo.gif`.

---

## GitHub "About" box

- **Description:** `AI payment reconciliation that never touches the money. Deterministic engine owns every rupee; a read-only AI agent investigates. 0 false matches (synthetic benchmark), 64/64 attacks blocked.`
- **Website:** https://huggingface.co/spaces/saitejacodes/milaan
- **Topics:** `reconciliation` `fintech` `ai-agents` `agentic-ai` `llm` `ai-safety` `tool-calling` `python` `sqlite` `streamlit` `docker` `huggingface-spaces` `payments` `evaluation` `adversarial-testing`
