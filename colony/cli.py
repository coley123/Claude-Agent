"""Command line: run / status / stop / export."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import load_goal
from .store import Store
from .supervisor import Supervisor, slug

ROOT = Path(__file__).resolve().parent.parent


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="colony", description="Persistent multi-agent coding team")
    ap.add_argument("--runs", type=Path, default=ROOT / "runs", help="where run state lives")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="start or resume a run")
    r.add_argument("goal", type=Path)
    r.add_argument("--skills", type=Path, default=ROOT / "skills")
    for name, help_ in (("status", "show progress"), ("stop", "stop a run gracefully"),
                        ("export", "export the episode log as JSONL training data")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("goal", type=Path)
    args = ap.parse_args(argv)

    goal = load_goal(args.goal)
    run_dir = args.runs / slug(goal.name)

    if args.cmd == "run":
        from .backend import AnthropicBackend

        sup = Supervisor(goal, AnthropicBackend(), args.runs, args.skills)
        print(f"run dir: {sup.run_dir}\nstop with: python -m colony stop {args.goal}")
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
