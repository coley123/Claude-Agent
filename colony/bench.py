"""Benchmark suite: a fixed set of tasks that a team, model or hub must not regress on.

A task is a folder in `benchmarks/`:

    benchmarks/<task>/task.toml   description, verify_command, optional mode / max_hours
    benchmarks/<task>/repo/       the starting code (copied into a fresh git repo per attempt)

A *team file* says who attacks the tasks: `[[agents]]` entries exactly like a goal file,
plus optional `[guardrails]` and `mode`. Swap `model = "local:my-candidate"` to benchmark a
fine-tuned model, or pass a different `--hub` to benchmark a hub snapshot.

Every attempt runs on a throwaway copy of the hub, so benchmarking never changes it.
"""
from __future__ import annotations

import json
import shutil
import statistics
import subprocess
import tempfile
import time
import tomllib
from dataclasses import dataclass, fields
from pathlib import Path

from .config import AgentSpec, Goal
from .supervisor import Supervisor
from .training.metrics import RewardWeights, metrics_from_store, reward


@dataclass
class BenchTask:
    name: str
    root: Path
    description: str
    verify_command: str
    mode: str | None = None
    max_hours: float = 0.5


@dataclass
class Team:
    name: str
    agents: list[AgentSpec]
    mode: str | None
    guardrails: dict


def load_tasks(bench_root: Path, only: list[str] | None = None) -> list[BenchTask]:
    tasks = []
    for f in sorted(bench_root.glob("*/task.toml")):
        if only and f.parent.name not in only:
            continue
        d = tomllib.loads(f.read_text())
        tasks.append(BenchTask(f.parent.name, f.parent, d["description"],
                               d.get("verify_command", "python -m pytest -q"),
                               d.get("mode"), d.get("max_hours", 0.5)))
    if only and len(tasks) != len(only):
        missing = set(only) - {t.name for t in tasks}
        raise ValueError(f"unknown benchmark task(s): {', '.join(sorted(missing))}")
    return tasks


def load_team(path: Path) -> Team:
    d = tomllib.loads(path.read_text())
    agents = [AgentSpec(**a) for a in d.get("agents", [])]
    if not agents:
        raise ValueError("team file needs at least one [[agents]] entry")
    return Team(d.get("name", path.stem), agents, d.get("mode"), d.get("guardrails", {}))


def _fresh_repo(src: Path, dest: Path) -> Path:
    shutil.copytree(src, dest)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=bench", "-c", "user.email=bench@localhost",
                 "commit", "-qm", "benchmark start"]):
        subprocess.run(cmd, cwd=dest, check=True, capture_output=True)
    return dest


def run_task(task: BenchTask, team: Team, backend, hub: Path, seed_skills: Path | None,
             work: Path, attempt: int, weights: RewardWeights) -> dict:
    root = work / f"{task.name}-{attempt}"
    repo = _fresh_repo(task.root / "repo", root / "repo")
    hub_copy = root / "hub"
    if hub.exists():
        shutil.copytree(hub, hub_copy, ignore=shutil.ignore_patterns("drafts"))
    mode = team.mode or task.mode or (
        "converge" if any(a.role == "lead" for a in team.agents) else "diverge")
    allowed = {f.name for f in fields(Goal)}
    guard = {"budget_usd": 2.0, "max_hours": task.max_hours} | {
        k: v for k, v in team.guardrails.items() if k in allowed}
    goal = Goal(name=f"bench-{task.name}-{attempt}", description=task.description, repo=repo,
                mode=mode, verify_command=task.verify_command, agents=team.agents,
                reflect=False, **guard)
    goal.deny_patterns += team.guardrails.get("extra_deny", [])
    started = time.time()
    sup = Supervisor(goal, backend, root / "runs", hub_copy, seed_skills)
    stop = sup.run()
    m = metrics_from_store(sup.store, time.time() - started)
    return {"task": task.name, "attempt": attempt, "stop": stop, "reward": reward(m, weights),
            **m.to_dict()}


def run_bench(tasks: list[BenchTask], team: Team, backend, hub: Path,
              seed_skills: Path | None = None, repeats: int = 1,
              weights: RewardWeights | None = None, keep: Path | None = None) -> dict:
    weights = weights or RewardWeights()
    results = []
    with tempfile.TemporaryDirectory(prefix="colony-bench-") as tmp:
        work = keep or Path(tmp)
        for task in tasks:
            for i in range(repeats):
                results.append(run_task(task, team, backend, hub, seed_skills, work, i,
                                        weights))
    return {"team": team.name, "models": sorted({a.model for a in team.agents}),
            "hub": str(hub), "repeats": repeats, "created": time.time(),
            "results": results, "summary": summarize(results)}


def summarize(results: list[dict]) -> dict:
    if not results:
        return {}
    per_task: dict[str, list[dict]] = {}
    for r in results:
        per_task.setdefault(r["task"], []).append(r)
    return {
        "attempts": len(results),
        "success_rate": statistics.fmean(r["verified"] for r in results),
        "mean_reward": statistics.fmean(r["reward"] for r in results),
        "total_cost_usd": sum(r["cost_usd"] for r in results),
        "mean_output_tokens": statistics.fmean(r["output_tokens"] for r in results),
        "per_task_success": {t: statistics.fmean(r["verified"] for r in rs)
                             for t, rs in per_task.items()},
    }


def compare(base: dict, new: dict, max_success_drop: float = 0.0,
            max_cost_increase: float = 0.25, max_reward_drop: float = 0.05
            ) -> tuple[bool, list[str]]:
    """Gate: the candidate passes only if it is no worse than the baseline on success, and
    not much worse on reward and cost. Returns (passed, human-readable lines)."""
    b, n = base["summary"], new["summary"]
    lines, ok = [], True

    def check(label, cond, detail):
        nonlocal ok
        ok &= cond
        lines.append(f"{'PASS' if cond else 'FAIL'}  {label}: {detail}")

    check("success rate", n["success_rate"] >= b["success_rate"] - max_success_drop,
          f"{b['success_rate']:.2f} -> {n['success_rate']:.2f}")
    check("mean reward", n["mean_reward"] >= b["mean_reward"] - max_reward_drop,
          f"{b['mean_reward']:.3f} -> {n['mean_reward']:.3f}")
    if b["total_cost_usd"] > 0:
        check("cost", n["total_cost_usd"] <= b["total_cost_usd"] * (1 + max_cost_increase),
              f"${b['total_cost_usd']:.2f} -> ${n['total_cost_usd']:.2f}")
    for task, rate in b.get("per_task_success", {}).items():
        new_rate = n.get("per_task_success", {}).get(task)
        if new_rate is None:
            check(f"task {task}", False, "missing from candidate results")
        elif new_rate < rate - max_success_drop:
            check(f"task {task}", False, f"{rate:.2f} -> {new_rate:.2f}")
    return ok, lines


def save(results: dict, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(results, indent=1))
    return path
