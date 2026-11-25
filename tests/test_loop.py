import json
import shutil
from pathlib import Path

from colony import bench
from colony.loop import Loop, LoopConfig, TrainConfig, load_loop_config
from fake_backend import ScriptedBackend

ROOT = Path(__file__).resolve().parent.parent
FIX = [("write_file", {"path": "calc/ops.py", "content":
        "def add(a, b):\n    return a + b\n\n\ndef mean(v):\n    return sum(v) / len(v)\n\n\n"
        "def median(v):\n    s = sorted(v)\n    n = len(s)\n    m = n // 2\n"
        "    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2\n"}),
       ("write_file", {"path": "calc/parse.py", "content":
        "def parse_numbers(t):\n    return [float(x) for x in t.split(',') if x.strip()]\n"})]
GOOD = [FIX, [("submit", {"task_id": 0, "summary": "fixed"})]]
BAD = [[("submit", {"task_id": 0, "summary": "nothing"})]]


def make_repo(tmp_path, name):
    import subprocess
    dest = tmp_path / name
    shutil.copytree(ROOT / "examples" / "calc-template", dest)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "i"]):
        subprocess.run(cmd, cwd=dest, check=True)
    return dest


def goal_file(queue: Path, name: str, repo: Path):
    (queue / f"{name}.toml").write_text(f'''
[goal]
name = "{name}"
description = "fix the calculator tests"
repo = "{repo}"
mode = "diverge"
verify_command = "python -m pytest -q -p no:cacheprovider"
reflect = false

[[agents]]
name = "solo"

[guardrails]
max_hours = 0.002
''')


class Factory:
    """Hands out a scripted backend per run; scripts are consumed in order."""

    def __init__(self, scripts):
        self.scripts = list(scripts)

    def __call__(self):
        return ScriptedBackend({"solo": self.scripts.pop(0) if self.scripts else GOOD})


def cfg(tmp_path, **train):
    return LoopConfig(root=tmp_path / "loop", hub=tmp_path / "hub", runs=tmp_path / "runs",
                      data_dir=tmp_path / "data", bench_dir=ROOT / "benchmarks",
                      poll_seconds=0, export_every=2, pause_after_failures=2,
                      train=TrainConfig(**train))


def test_queue_processing_export_and_pause(tmp_path):
    c = cfg(tmp_path)
    loop = Loop(c, Factory([GOOD, BAD, BAD]), ROOT, ROOT / "skills")
    for i in range(3):
        goal_file(c.root / "queue", f"g{i}", make_repo(tmp_path, f"r{i}"))
    (c.root / "queue" / "zz-broken.toml").write_text("not toml [[[")
    for _ in range(3):
        loop.serve(once=True)
    assert (c.root / "done" / "g0.toml").exists()
    assert (c.root / "done" / "g0.report.md").exists()
    assert (c.root / "failed" / "g1.toml").exists() and (c.root / "failed" / "g2.toml").exists()
    # exported after 2 goals: the one successful session
    assert (c.data_dir / "sft.jsonl").exists() and loop.state["exported_sessions"] == 1
    # two failures in a row pause the loop; the broken goal file waits
    assert (c.root / "PAUSED").exists() and loop.blocked() == "paused"
    loop.serve(once=True)
    assert (c.root / "queue" / "zz-broken.toml").exists()
    (c.root / "PAUSED").unlink()
    loop.serve(once=True)
    assert (c.root / "failed" / "zz-broken.toml").exists()


def test_improvement_cycle_promotes_then_rejects(tmp_path):
    team = tmp_path / "team.toml"
    team.write_text('name = "cand"\n[[agents]]\nname = "solo"\nmodel = "local:candidate"\n'
                    '[guardrails]\nmax_hours = 0.002\n')
    c = cfg(tmp_path, enabled=True, min_new_sessions=0, tasks=["calc"], repeats=1,
            team=str(team), baseline=str(tmp_path / "results" / "baseline.json"),
            command="mkdir -p {out} && echo trained > {out}/adapter.txt",
            deploy_command="echo deploy {out}", promote_command="echo promote {out}",
            rollback_command="echo rollback {cycle}")
    data = tmp_path / "data" / "sft.jsonl"
    data.parent.mkdir(parents=True)
    data.write_text("")

    loop = Loop(c, Factory([GOOD]), ROOT, ROOT / "skills")
    rec = loop.improvement_cycle(data)
    assert rec["result"] == "promoted"  # no baseline yet: first candidate becomes it
    assert (tmp_path / "checkpoints" / "cycle-1" / "adapter.txt").exists()
    assert json.loads((tmp_path / "results" / "baseline.json").read_text())[
        "summary"]["success_rate"] == 1.0

    loop.backend_factory = Factory([BAD])
    rec = loop.improvement_cycle(data)
    assert rec["result"] == "rejected by gate"
    assert any(l.startswith("FAIL") for l in rec["gate"])
    assert loop.state["current_model"].endswith("cycle-1")
    log = (c.root / "loop.log").read_text()
    assert '"hook": "rollback"' in log

    loop.cfg.train.command = "exit 3"
    assert loop.improvement_cycle(data)["result"] == "train failed"


def test_example_loop_config_loads():
    c = load_loop_config(ROOT / "examples" / "loop.toml", ROOT)
    assert c.root == ROOT / "loop" and c.train.enabled is False
