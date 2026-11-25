import json
from pathlib import Path

from colony import bench
from colony.cli import main
from fake_backend import ScriptedBackend

ROOT = Path(__file__).resolve().parent.parent
FIX = [("write_file", {"path": "calc/ops.py", "content":
        "def add(a, b):\n    return a + b\n\n\ndef mean(v):\n    return sum(v) / len(v)\n\n\n"
        "def median(v):\n    s = sorted(v)\n    n = len(s)\n    m = n // 2\n"
        "    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2\n"}),
       ("write_file", {"path": "calc/parse.py", "content":
        "def parse_numbers(t):\n    return [float(x) for x in t.split(',') if x.strip()]\n"})]


def test_all_benchmark_tasks_load_and_start_failing():
    tasks = bench.load_tasks(ROOT / "benchmarks")
    assert {t.name for t in tasks} >= {"calc", "textutils", "inventory"}


def test_bench_run_and_gate(tmp_path):
    team = bench.load_team(ROOT / "examples" / "team-claude.toml")
    # A team that never fixes the task keeps trying until its time limit; keep it short.
    team.guardrails["max_hours"] = 0.002
    tasks = bench.load_tasks(ROOT / "benchmarks", ["calc"])
    hub = tmp_path / "hub"
    good = bench.run_bench(tasks, team, ScriptedBackend(
        {"solo": [FIX, [("submit", {"task_id": 0, "summary": "fixed"})]]}),
        hub, ROOT / "skills")
    bad = bench.run_bench(tasks, team, ScriptedBackend(
        {"solo": [[("submit", {"task_id": 0, "summary": "nothing"})]]}),
        hub, ROOT / "skills")
    assert good["summary"]["success_rate"] == 1.0
    assert bad["summary"]["success_rate"] == 0.0
    assert not hub.exists() or not (hub / "hub.db").exists()  # the real hub is never touched

    ok, lines = bench.compare(good, good)
    assert ok
    ok, lines = bench.compare(good, bad)
    assert not ok and any(l.startswith("FAIL  success rate") for l in lines)

    base, cand = tmp_path / "base.json", tmp_path / "cand.json"
    bench.save(good, base)
    bench.save(bad, cand)
    assert main(["bench", "compare", str(base), str(base)]) == 0
    assert main(["bench", "compare", str(base), str(cand)]) == 1


class Alternating(ScriptedBackend):
    """Fixes the task on even attempts, gives up on odd ones: a group with spread."""

    def __init__(self):
        super().__init__({})
        self.attempt = -1

    def create(self, *, model, effort, system, tools, messages):
        if len(messages) == 1:  # first turn of an attempt's session
            self.attempt += 1
            self.scripts["solo"] = ([FIX, [("submit", {"task_id": 0, "summary": "fixed"})]]
                                    if self.attempt % 2 == 0 else
                                    [[("submit", {"task_id": 0, "summary": "nothing"})]])
        return super().create(model=model, effort=effort, system=system, tools=tools,
                              messages=messages)


def test_rollouts_carry_group_advantages(tmp_path):
    from colony.training.rollouts import collect

    team = bench.load_team(ROOT / "examples" / "team-claude.toml")
    team.guardrails["max_hours"] = 0.002
    out = tmp_path / "rollouts.jsonl"
    summary = collect(bench.load_tasks(ROOT / "benchmarks", ["calc"]), team, Alternating(),
                      tmp_path / "hub", out, k=2, seed_skills=ROOT / "skills")
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert summary["groups"][0]["rewards"][0] > summary["groups"][0]["rewards"][1]
    assert {r["advantage"] > 0 for r in rows} == {True, False}
    assert all(r["messages"][0]["role"] == "system" for r in rows)
