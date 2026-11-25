"""Command line: run / status / stop / export / hub."""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .config import load_goal
from .hub import SkillHub
from .store import Store
from .supervisor import Supervisor, slug

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="colony", description="Persistent multi-agent coding team")
    ap.add_argument("--runs", type=Path, default=ROOT / "runs", help="where run state lives")
    ap.add_argument("--hub", type=Path, default=ROOT / "hub",
                    help="skill hub directory; point several colonies at one hub to share it")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start or resume a run")
    r.add_argument("goal", type=Path)
    r.add_argument("--seed-skills", type=Path, default=ROOT / "skills",
                   help="starter skills copied into an empty hub")
    for name, help_ in (("status", "show progress"), ("stop", "stop a run gracefully"),
                        ("export", "export the episode log as JSONL training data")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("goal", type=Path)
    h = sub.add_parser("hub", help="inspect the skill hub")
    h.add_argument("what", choices=["skills", "lessons", "log"])
    tr = sub.add_parser("train-export", help="export all runs as an SFT dataset (JSONL)")
    tr.add_argument("--out", type=Path, default=ROOT / "data" / "sft.jsonl")
    tr.add_argument("--all", action="store_true",
                    help="include unsuccessful sessions (labelled in meta.success)")
    tr.add_argument("--no-reflection", action="store_true")
    b = sub.add_parser("bench", help="run or compare the benchmark suite")
    bsub = b.add_subparsers(dest="bench_cmd", required=True)
    br = bsub.add_parser("run", help="run a team on the benchmark tasks")
    br.add_argument("--team", type=Path, required=True)
    br.add_argument("--tasks", default="", help="comma-separated task names (default: all)")
    br.add_argument("--repeats", type=int, default=1)
    br.add_argument("--bench-dir", type=Path, default=ROOT / "benchmarks")
    br.add_argument("--out", type=Path, required=True)
    bc = bsub.add_parser("compare", help="gate a candidate against a baseline")
    bc.add_argument("baseline", type=Path)
    bc.add_argument("candidate", type=Path)
    bc.add_argument("--max-success-drop", type=float, default=0.0)
    bc.add_argument("--max-cost-increase", type=float, default=0.25)
    args = ap.parse_args(argv)

    if args.cmd == "bench":
        return bench_cmd(args)
    if args.cmd == "hub":
        return hub_cmd(SkillHub(args.hub, seed=ROOT / "skills"), args.what)
    if args.cmd == "train-export":
        from .training.export import TERMS_NOTE, export_runs

        summary = export_runs(args.runs, args.out, only_success=not args.all,
                              include_reflection=not args.no_reflection)
        print(json.dumps(summary, indent=1))
        print(TERMS_NOTE)
        return 0

    goal = load_goal(args.goal)
    run_dir = args.runs / slug(goal.name)

    if args.cmd == "run":
        from .backend import RouterBackend

        sup = Supervisor(goal, RouterBackend(), args.runs, args.hub, args.seed_skills)
        print(f"run dir: {sup.run_dir}\nhub: {args.hub}\n"
              f"stop with: python -m colony stop {args.goal}")
        reason = sup.run()
        print(f"\n{reason}\nreport: {sup.run_dir / 'REPORT.md'}")
        return 0

    if not (run_dir / "colony.db").exists():
        print(f"no run found at {run_dir}", file=sys.stderr)
        return 1
    store = Store(run_dir / "colony.db")

    if args.cmd == "stop":
        (run_dir / "STOP").touch()
        print("stop requested; agents finish their current turn and exit")
    elif args.cmd == "status":
        print(f"goal done: {bool(store.get_meta('goal_done'))}  winner: {store.get_meta('winner')}")
        print(f"cost so far: ${store.total_cost():.2f} of ${goal.budget_usd:.2f}")
        print("\ntasks:")
        for t in store.tasks():
            print(f"  #{t['id']} [{t['status']}] {t['owner'] or '-'}: {t['title']}")
        print("\nrecent blackboard:")
        for p in store.board(10):
            print(f"  [{p['kind']}] {p['author']}: {p['content'][:200]}")
        esc = run_dir / "ESCALATIONS.md"
        if esc.exists():
            print(f"\nneeds a human:\n{esc.read_text()}")
    elif args.cmd == "export":
        out = run_dir / "episodes.jsonl"
        with out.open("w") as f:
            for e in store.events():
                f.write(json.dumps(e) + "\n")
        print(f"wrote {out} (full per-session transcripts are in {run_dir / 'transcripts'})")
    return 0


def hub_cmd(hub: SkillHub, what: str) -> int:
    if what == "skills":
        print(hub.index())
    elif what == "lessons":
        for l in hub.lessons():
            print(f"#{l['id']} [{l['kind']}] {l['topic']} ({l['author']}, {l['run']}): "
                  f"{l['text']}")
    elif what == "log":
        for e in hub.log():
            ts = time.strftime("%Y-%m-%d %H:%M", time.localtime(e["ts"]))
            print(f"{ts} {e['event']:<16} {e['name']:<24} {e['author']}: {e['detail'][:150]}")
    return 0


def bench_cmd(args) -> int:
    from . import bench

    if args.bench_cmd == "compare":
        ok, lines = bench.compare(json.loads(args.baseline.read_text()),
                                  json.loads(args.candidate.read_text()),
                                  args.max_success_drop, args.max_cost_increase)
        print("\n".join(lines))
        print("GATE PASSED" if ok else "GATE FAILED")
        return 0 if ok else 1
    from .backend import RouterBackend

    tasks = bench.load_tasks(args.bench_dir, [t for t in args.tasks.split(",") if t] or None)
    team = bench.load_team(args.team)
    results = bench.run_bench(tasks, team, RouterBackend(), args.hub, ROOT / "skills",
                              args.repeats)
    bench.save(results, args.out)
    print(json.dumps(results["summary"], indent=1))
    print(f"saved {args.out}")
    return 0
