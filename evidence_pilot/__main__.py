"""Command line entry point: python3 -m evidence_pilot."""

import argparse
from pathlib import Path

from .common import read_json, write_json
from .design import make_plan, messages_for, schema_for
from .runner import execute, run_lock, write_summary


def main():
    parser = argparse.ArgumentParser(description="Pilot of hypothetical elaboration and evidence use.")
    sub = parser.add_subparsers(dest="command", required=True)
    plan_parser = sub.add_parser("plan", help="Export a fixed schedule and prompts without model calls")
    plan_parser.add_argument("--config", default="configs/smoke.json")
    plan_parser.add_argument("--out", required=True)
    run_parser = sub.add_parser("run", help="Run offline fixtures or explicitly select paid API calls")
    run_parser.add_argument("--config", default="configs/smoke.json")
    run_parser.add_argument("--backend", choices=["scripted", "openai"], default="scripted")
    run_parser.add_argument("--scripted-policy", choices=["correct", "contaminated"], default="correct")
    run_parser.add_argument("--out", required=True)
    run_parser.add_argument("--budget-usd", type=float)
    run_parser.add_argument("--max-calls", type=int)
    run_parser.add_argument("--resume", action="store_true")
    analyze_parser = sub.add_parser("analyze", help="Recompute saved-run summaries without model calls")
    analyze_parser.add_argument("run_directory")
    args = parser.parse_args()
    try:
        if args.command == "plan":
            plan = make_plan(read_json(args.config))
            output = Path(args.out)
            if output.exists() and (not output.is_dir() or any(output.iterdir())):
                raise ValueError("Plan output directory must be new or empty")
            write_json(output / "plan.json", plan)
            cases = {case["id"]: case for case in plan["cases"]}
            prompts = []
            for node in plan["nodes"]:
                case = cases[node["case_id"]]
                note = "[GENERATED NOTE INSERTED ONLY AFTER DEPENDENCY COMPLETES]" if node["depends_on"] else None
                prompts.append({"node": node, "schema": schema_for(case, node),
                                "messages": messages_for(case, node, note=note)})
            write_json(output / "prompts.json", prompts)
            print(f"{plan['planned_calls']} planned requests; {len(plan['cases'])} task instances. No model calls made.")
            print(f"Review {output / 'prompts.json'}")
        elif args.command == "analyze":
            with run_lock(Path(args.run_directory)):
                write_summary(args.run_directory)
            print(f"Updated {Path(args.run_directory) / 'summary.md'}")
        else:
            summary = execute(read_json(args.config), args.out, args.backend, args.scripted_policy,
                              args.budget_usd, args.max_calls, args.resume, progress=True)
            print(f"Saved {Path(args.out) / 'summary.md'}")
            valid = summary["statuses"].get("valid", 0)
            print(f"Valid requests: {valid}/{summary['planned_calls']}")
            if valid != summary["planned_calls"]:
                parser.exit(1, "Run is incomplete or contains invalid responses. Inspect the summary before interpreting results.\n")
    except KeyboardInterrupt:
        parser.exit(130, "Interrupted. Saved requests will not be replayed; inspect the run summary before resuming.\n")
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(2, f"Error: {error}\n")


if __name__ == "__main__":
    main()
