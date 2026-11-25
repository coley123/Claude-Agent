import json
import shutil
import subprocess
from pathlib import Path

import pytest

from colony.config import AgentSpec, Goal
from colony.supervisor import Supervisor
from colony.training.export import export_runs
from colony.training.metrics import (RewardWeights, RunMetrics, group_advantages,
                                     metrics_from_store, reward)
from fake_backend import ScriptedBackend

ROOT = Path(__file__).resolve().parent.parent
FIX = [("write_file", {"path": "calc/ops.py", "content":
        "def add(a, b):\n    return a + b\n\n\ndef mean(v):\n    return sum(v) / len(v)\n\n\n"
        "def median(v):\n    s = sorted(v)\n    n = len(s)\n    m = n // 2\n"
        "    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2\n"}),
       ("write_file", {"path": "calc/parse.py", "content":
        "def parse_numbers(t):\n    return [float(x) for x in t.split(',') if x.strip()]\n"})]


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(ROOT / "examples" / "calc-template", dest)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=dest, check=True)
    return dest


def run(repo, tmp_path, scripts, agents):
    goal = Goal(name="train-src", description="fix tests", repo=repo, mode="diverge",
                verify_command="python -m pytest -q -p no:cacheprovider", agents=agents,
                reflect=False)
    sup = Supervisor(goal, ScriptedBackend(scripts), tmp_path / "runs", tmp_path / "hub",
                     ROOT / "skills")
    sup.run()
    return sup


def test_export_keeps_only_successful_sessions(repo, tmp_path):
    sup = run(repo, tmp_path, {
        "ada": [[("submit", {"task_id": 0, "summary": "too early"})], FIX,
                [("submit", {"task_id": 0, "summary": "fixed"})]],
        "bo": [[("read_file", {"path": "calc/ops.py"})], [("wait", {"reason": "x"})]],
    }, [AgentSpec("ada"), AgentSpec("bo", model="claude-sonnet-5-5")])
    out = tmp_path / "sft.jsonl"
    summary = export_runs(tmp_path / "runs", out)
    rows = [json.loads(l) for l in out.read_text().splitlines()]
    assert summary["written"] == 1 and summary["skipped_unsuccessful"] == 1
    row = rows[0]
    assert row["meta"]["agent"] == "ada" and row["meta"]["success"]
    assert row["meta"]["model"] == "claude-opus-5-5"
    assert row["messages"][0]["role"] == "system"
    call = next(m for m in row["messages"] if m.get("tool_calls"))
    assert isinstance(call["tool_calls"][0]["function"]["arguments"], dict)
    # the rejected first attempt stays in: the model learns to recover from it
    assert any(m["role"] == "tool" and m["content"].startswith("REJECTED")
               for m in row["messages"])
    # --all keeps the unsuccessful one, labelled
    summary = export_runs(tmp_path / "runs", out, only_success=False)
    assert summary["written"] == 2

    m = metrics_from_store(sup.store)
    assert m.verified and m.rejections == 1 and m.tool_calls >= 5


def test_reward_and_advantages():
    base = dict(cost_usd=0, input_tokens=0, tool_calls=10, tool_errors=0, rejections=0,
                stalls=0, guard_blocks=0, help_requests=0, elapsed_s=1)
    win = RunMetrics(verified=True, output_tokens=20_000, **base)
    slow = RunMetrics(verified=True, output_tokens=200_000, **base)
    lose = RunMetrics(verified=False, output_tokens=5_000, **base)
    assert reward(win) > reward(slow) > reward(lose)
    assert reward(win, RewardWeights(tokens=0)) == 1.0
    adv = group_advantages([reward(win), reward(slow), reward(lose)])
    assert adv[0] > adv[1] > adv[2] and abs(sum(adv)) < 1e-9
    assert group_advantages([0.5, 0.5]) == [0.0, 0.0]
