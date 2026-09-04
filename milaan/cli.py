"""Command-line interface for Milaan."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="milaan")
    parser.add_argument("--version", action="version", version="milaan 1.3.0")
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("gen", help="generate a deterministic synthetic run")
    gen.add_argument("--records", type=int, default=1200)
    gen.add_argument("--seed", type=int, default=42)
    gen.add_argument("--profile", choices=("clean", "mixed", "hard"), default="mixed")
    gen.add_argument("--out", type=Path, required=True)

    run = sub.add_parser("run", help="run reconciliation")
    run.add_argument("--data", type=Path, required=True)
    run.add_argument("--db", type=Path, required=True)
    run.add_argument("--llm", choices=("mock", "live"), default="mock")

    evaluate = sub.add_parser("eval", help="score a completed run")
    evaluate.add_argument("--run", dest="run_dir", type=Path, required=True)
    evaluate.add_argument("--db", type=Path, required=True)
    evaluate.add_argument("--out-dir", type=Path, required=True)
    evaluate.add_argument("--gate", choices=("clean", "mixed", "hard"), required=True)

    report = sub.add_parser("report", help="render a self-contained HTML report")
    report.add_argument("--run", dest="run_dir", type=Path, required=True)
    report.add_argument("--db", type=Path, required=True)
    report.add_argument("--out", type=Path, required=True)

    ask = sub.add_parser("ask", help="ask the bounded read-only finance agent")
    ask.add_argument("--run", dest="run_dir", type=Path, required=True)
    ask.add_argument("--db", type=Path, required=True)
    ask.add_argument("--question", required=True)
    ask.add_argument("--llm", choices=("mock", "live"), default="mock")

    agent_eval = sub.add_parser("agent-eval", help="evaluate finance-agent routing and grounding")
    agent_eval.add_argument("--run", dest="run_dir", type=Path, required=True)
    agent_eval.add_argument("--db", type=Path, required=True)
    agent_eval.add_argument("--out", type=Path, required=True)
    agent_eval.add_argument("--llm", choices=("mock", "live"), default="mock")

    benchmark = sub.add_parser("benchmark", help="run a reproducible throughput sweep")
    benchmark.add_argument("--out", type=Path, required=True)
    benchmark.add_argument("--sizes", default="50,200,1200,5000,10000")
    benchmark.add_argument("--repetitions", type=int, default=3)
    benchmark.add_argument("--profile", choices=("clean", "mixed", "hard"), default="mixed")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "gen":
        from milaan.generator.emit import generate_to_directory

        result = generate_to_directory(args.records, args.seed, args.profile, args.out)
    elif args.command == "run":
        from milaan.engine.pipeline import run_pipeline

        result = run_pipeline(args.data, args.db, args.llm)
    elif args.command == "eval":
        from milaan.evalx.harness import evaluate_run

        result = evaluate_run(args.run_dir, args.db, args.out_dir, args.gate)
    elif args.command == "report":
        from milaan.report.render import render_report

        result = render_report(args.run_dir, args.db, args.out)
    elif args.command == "ask":
        import json

        from milaan.agent.router import ask_finance

        result = json.dumps(
            ask_finance(args.run_dir, args.db, args.question, args.llm),
            indent=2, sort_keys=True, ensure_ascii=False,
        )
    elif args.command == "agent-eval":
        from milaan.agent.eval import evaluate_agent

        result = evaluate_agent(args.run_dir, args.db, args.out, args.llm)
    else:
        from milaan.evalx.benchmark import run_benchmark

        sizes = tuple(int(value.strip()) for value in args.sizes.split(",") if value.strip())
        result = run_benchmark(args.out, sizes, args.repetitions, args.profile)
    if result:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
