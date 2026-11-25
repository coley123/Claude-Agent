"""v4: the self-improvement loop.

`colony serve loop.toml` runs indefinitely:

  1. Take the next goal file from the queue folder, run it, file it under done/ or failed/.
     Every run uses the shared hub, so skills and lessons accumulate (v2).
  2. Every `export_every` finished goals, export the training data (v3).
  3. If training is enabled and enough new successful sessions have accumulated, run an
     improvement cycle: train a candidate (your command), deploy it (your command),
     benchmark it, and promote it (your command) only if it passes the gate against the
     current baseline. A candidate that fails the gate is rolled back.

Guardrails: a daily spending cap, a pause after repeated goal failures (a person removes the
PAUSED file to resume), a STOP file, and a log of every decision in the loop folder.
Training, deployment and promotion are shell commands you configure, because they depend
on your hardware and serving setup.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Callable

from .config import load_goal
from .supervisor import Supervisor


@dataclass
class TrainConfig:
    enabled: bool = False
    command: str = ""  # placeholders: {data} {out} {cycle}
    deploy_command: str = ""  # serve the candidate; placeholders: {out} {cycle}
    promote_command: str = ""  # make the candidate the current model; {out} {cycle}
    rollback_command: str = ""  # undo deploy after a failed gate; {cycle}
    team: str = "examples/team-local.toml"  # the team the candidate is benchmarked as
    baseline: str = "results/baseline.json"
    min_new_sessions: int = 50
    tasks: list[str] = field(default_factory=list)  # benchmark subset; empty = all
    repeats: int = 2
    max_success_drop: float = 0.0
    max_cost_increase: float = 0.25
    command_timeout_hours: float = 12.0


@dataclass
class LoopConfig:
    root: Path  # folder holding queue/, done/, failed/, state.json, loop.log
    hub: Path
    runs: Path
    data_dir: Path
    bench_dir: Path
    poll_seconds: float = 30.0
    export_every: int = 5
    daily_budget_usd: float = 50.0
    pause_after_failures: int = 3
    train: TrainConfig = field(default_factory=TrainConfig)


def load_loop_config(path: Path, project_root: Path) -> LoopConfig:
    d = tomllib.loads(path.read_text())
    lp = d.get("loop", {})

    def p(key, default):
        v = Path(lp.get(key, default))
        return v if v.is_absolute() else (path.parent / v).resolve()

    return LoopConfig(
        root=p("root", "loop"), hub=p("hub", project_root / "hub"),
        runs=p("runs", project_root / "runs"), data_dir=p("data_dir", project_root / "data"),
        bench_dir=p("bench_dir", project_root / "benchmarks"),
        poll_seconds=lp.get("poll_seconds", 30.0), export_every=lp.get("export_every", 5),
        daily_budget_usd=lp.get("daily_budget_usd", 50.0),
        pause_after_failures=lp.get("pause_after_failures", 3),
        train=TrainConfig(**d.get("train", {})))


class Loop:
    def __init__(self, cfg: LoopConfig, backend_factory: Callable, project_root: Path,
                 seed_skills: Path | None = None):
        self.cfg = cfg
        self.backend_factory = backend_factory
        self.project_root = project_root
        self.seed_skills = seed_skills
        for d in ("queue", "running", "done", "failed"):
            (cfg.root / d).mkdir(parents=True, exist_ok=True)
        self.state_path = cfg.root / "state.json"
        self.state = json.loads(self.state_path.read_text()) if self.state_path.exists() else {
            "goals_done": 0, "goals_failed": 0, "consecutive_failures": 0,
            "since_export": 0, "exported_sessions": 0, "trained_on_sessions": 0,
            "cycle": 0, "spend": {"date": "", "usd": 0.0}, "cycles": [], "current_model": None}

    # ------------------------------------------------------------------ bookkeeping
    def _save(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=1))

    def log(self, event: str, **data) -> None:
        line = json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **data})
        with open(self.cfg.root / "loop.log", "a") as f:
            f.write(line + "\n")
        print(line, flush=True)

    def _spent_today(self) -> float:
        today = date.today().isoformat()
        if self.state["spend"]["date"] != today:
            self.state["spend"] = {"date": today, "usd": 0.0}
        return self.state["spend"]["usd"]

    def blocked(self) -> str | None:
        if (self.cfg.root / "STOP").exists():
            return "stop"
        if (self.cfg.root / "PAUSED").exists():
            return "paused"
        if self._spent_today() >= self.cfg.daily_budget_usd:
            return "daily budget reached"
        return None

    # ------------------------------------------------------------------ main loop
    def serve(self, once: bool = False) -> None:
        self.log("loop_start", queue=str(self.cfg.root / "queue"))
        while True:
            why = self.blocked()
            if why == "stop":
                self.log("loop_stop")
                return
            if why:
                if once:
                    self.log("blocked", reason=why)
                    return
                time.sleep(self.cfg.poll_seconds)
                continue
            did = self.run_next_goal()
            if did:
                self.maybe_improve()
            if once:
                return
            if not did:
                time.sleep(self.cfg.poll_seconds)

    def run_next_goal(self) -> bool:
        queue = sorted((self.cfg.root / "queue").glob("*.toml"))
        if not queue:
            return False
        src = queue[0]
        try:
            goal = load_goal(src)  # resolve relative repo paths before moving the file
        except Exception as e:
            shutil.move(src, self.cfg.root / "failed" / src.name)
            self.log("goal_invalid", file=src.name, error=repr(e))
            return True
        running = self.cfg.root / "running" / src.name
        shutil.move(src, running)
        self.log("goal_start", file=src.name, goal=goal.name)
        try:
            sup = Supervisor(goal, self.backend_factory(), self.cfg.runs, self.cfg.hub,
                             self.seed_skills)
            reason = sup.run()
            cost = sup.store.total_cost()
            ok = bool(sup.store.get_meta("goal_done"))
            report = sup.run_dir / "REPORT.md"
        except Exception as e:
            reason, cost, ok, report = f"crashed: {e!r}", 0.0, False, None
        self._spent_today()
        self.state["spend"]["usd"] += cost
        dest = self.cfg.root / ("done" if ok else "failed") / src.name
        shutil.move(running, dest)
        if report and report.exists():
            shutil.copy(report, dest.with_suffix(".report.md"))
        self.state["goals_done" if ok else "goals_failed"] += 1
        self.state["consecutive_failures"] = 0 if ok else self.state["consecutive_failures"] + 1
        self.state["since_export"] += 1
        self.log("goal_end", file=src.name, verified=ok, reason=reason, cost_usd=round(cost, 4))
        if self.state["consecutive_failures"] >= self.cfg.pause_after_failures:
            (self.cfg.root / "PAUSED").write_text(
                f"Paused after {self.state['consecutive_failures']} failed goals in a row. "
                "Check loop/failed/*.report.md, fix the cause, then delete this file.\n")
            self.log("paused", consecutive_failures=self.state["consecutive_failures"])
        self._save()
        return True

    # ------------------------------------------------------------------ improvement
    def maybe_improve(self) -> None:
        if self.state["since_export"] < self.cfg.export_every:
            return
        from .training.export import export_runs

        self.state["since_export"] = 0
        data = self.cfg.data_dir / "sft.jsonl"
        summary = export_runs(self.cfg.runs, data)
        self.state["exported_sessions"] = summary.get("written", 0)
        self.log("exported", **{k: v for k, v in summary.items() if k != "models"})
        self._save()
        t = self.cfg.train
        new = self.state["exported_sessions"] - self.state["trained_on_sessions"]
        if t.enabled and new >= t.min_new_sessions:
            self.improvement_cycle(data)

    def _hook(self, name: str, command: str, **fmt) -> bool:
        if not command:
            self.log("hook_skipped", hook=name)
            return True
        cmd = command.format(**fmt)
        self.log("hook_start", hook=name, command=cmd)
        try:
            p = subprocess.run(cmd, shell=True, cwd=self.project_root, capture_output=True,
                               text=True, timeout=self.cfg.train.command_timeout_hours * 3600)
        except subprocess.TimeoutExpired:
            self.log("hook_failed", hook=name, error="timeout")
            return False
        (self.cfg.root / "hooks").mkdir(exist_ok=True)
        (self.cfg.root / "hooks" / f"{fmt.get('cycle')}-{name}.log").write_text(
            p.stdout + "\n[stderr]\n" + p.stderr)
        self.log("hook_end", hook=name, exit_code=p.returncode)
        return p.returncode == 0

    def improvement_cycle(self, data: Path) -> dict:
        from . import bench

        t = self.cfg.train
        self.state["cycle"] += 1
        cycle = self.state["cycle"]
        out = self.cfg.data_dir.parent / "checkpoints" / f"cycle-{cycle}"
        record = {"cycle": cycle, "sessions": self.state["exported_sessions"],
                  "candidate": str(out), "result": None}
        fmt = {"data": data, "out": out, "cycle": cycle}

        if not self._hook("train", t.command, **fmt):
            record["result"] = "train failed"
        elif not self._hook("deploy", t.deploy_command, **fmt):
            record["result"] = "deploy failed"
        else:
            team = bench.load_team(self._path(t.team))
            tasks = bench.load_tasks(self.cfg.bench_dir, t.tasks or None)
            results = bench.run_bench(tasks, team, self.backend_factory(), self.cfg.hub,
                                      self.seed_skills, t.repeats)
            res_path = bench.save(results, self.cfg.root / "bench" / f"cycle-{cycle}.json")
            baseline = self._path(t.baseline)
            if baseline.exists():
                ok, lines = bench.compare(json.loads(baseline.read_text()), results,
                                          t.max_success_drop, t.max_cost_increase)
            else:
                ok, lines = True, ["no baseline yet: the first candidate becomes the baseline"]
            record["gate"] = lines
            if ok and self._hook("promote", t.promote_command, **fmt):
                baseline.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy(res_path, baseline)
                self.state["current_model"] = str(out)
                record["result"] = "promoted"
            else:
                self._hook("rollback", t.rollback_command, **fmt)
                record["result"] = "rejected by gate" if not ok else "promote failed"
        self.state["trained_on_sessions"] = self.state["exported_sessions"]
        self.state["cycles"].append(record)
        self.log("cycle_end", **{k: v for k, v in record.items() if k != "gate"},
                 gate=record.get("gate"))
        self._save()
        return record

    def _path(self, p: str) -> Path:
        q = Path(p)
        return q if q.is_absolute() else self.project_root / q
