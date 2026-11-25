import shutil
import subprocess
from pathlib import Path

import pytest

from colony.config import AgentSpec, Goal
from colony.supervisor import Supervisor
from colony.workspace import CommandGuard, safe_path
from colony.config import DEFAULT_DENY
from fake_backend import ScriptedBackend

ROOT = Path(__file__).resolve().parent.parent
FIXED_OPS = '''def add(a, b):
    return a + b


def mean(values):
    return sum(values) / len(values)


def median(values):
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2
'''
FIXED_PARSE = '''def parse_numbers(text):
    return [float(x) for x in text.split(",") if x.strip()]
'''
FIX = [("write_file", {"path": "calc/ops.py", "content": FIXED_OPS}),
       ("write_file", {"path": "calc/parse.py", "content": FIXED_PARSE})]


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(ROOT / "examples" / "calc-template", dest)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=dest, check=True)
    return dest


def make(repo, tmp_path, mode, agents, scripts, **kw):
    goal = Goal(name=f"test-{mode}", description="fix the tests", repo=repo, mode=mode,
                verify_command="python -m pytest -q -p no:cacheprovider", agents=agents, **kw)
    return Supervisor(goal, ScriptedBackend(scripts), tmp_path / "runs", tmp_path / "hub",
                      ROOT / "skills")


def test_diverge_first_verified_approach_wins(repo, tmp_path):
    sup = make(repo, tmp_path, "diverge",
               [AgentSpec("ada"), AgentSpec("brook")],
               {"ada": [[("board_post", {"kind": "claim", "content": "rewrite both modules"})],
                        FIX,
                        [("submit", {"task_id": 0, "summary": "fixed ops and parse"})]],
                "brook": [[("wait", {"reason": "watching"})]]})
    assert sup.run() == "goal verified"
    assert sup.store.get_meta("winner")["branch"].endswith("/ada")
    # main is untouched: nothing merges without a human
    assert "len(values) - 1" in (repo / "calc" / "ops.py").read_text()
    assert "goal verified" in (sup.run_dir / "REPORT.md").read_text()


def test_diverge_rejects_failing_submission(repo, tmp_path):
    from colony.agent import Agent
    sup = make(repo, tmp_path, "diverge", [AgentSpec("ada")],
               {"ada": [[("submit", {"task_id": 0, "summary": "no changes"})]]},
               max_turns_per_session=1)
    sup.ws.setup(["ada"])
    Agent(sup, sup.goal.agents[0]).run_session()
    tool_events = [e for e in sup.store.events() if e["kind"] == "tool"]
    assert "REJECTED" in tool_events[0]["data"]["output"]
    assert not sup.store.get_meta("goal_done")


def test_converge_lead_plans_worker_delivers(repo, tmp_path):
    task = {"title": "fix all", "detail": "make tests pass", "skills": ["debug-failing-test"],
            "verify_command": ""}
    sup = make(repo, tmp_path, "converge",
               [AgentSpec("lead", role="lead"), AgentSpec("ada")],
               {"lead": [[("create_task", task)], [("wait", {"reason": "delegated"})]],
                "ada": [[("wait", {"reason": "waiting for tasks"})],
                        [("claim_task", {"task_id": 1})],
                        FIX,
                        [("submit", {"task_id": 1, "summary": "fixed"})]]})
    assert sup.run() == "goal verified"
    assert sup.store.task(1)["status"] == "done"
    assert sup.store.get_meta("winner")["branch"].endswith("/integration")
    # the claimed task delivered its skill to the worker
    claim = next(e for e in sup.store.events()
                 if e["kind"] == "tool" and e["data"]["name"] == "claim_task")
    assert "Skill: debug-failing-test" in claim["data"]["output"]


def test_only_lead_creates_tasks(repo, tmp_path):
    sup = make(repo, tmp_path, "converge", [AgentSpec("lead", role="lead"), AgentSpec("ada")],
               {})
    from colony.agent import Agent
    from colony.tools import execute
    out, err = execute(Agent(sup, sup.goal.agents[1]), "create_task",
                       {"title": "x", "detail": "", "skills": [], "verify_command": ""})
    assert err and "only the lead" in out


def test_stall_detection_injects_strategy_note(repo, tmp_path):
    bad = [("read_file", {"path": "missing.py"})]
    sup = make(repo, tmp_path, "diverge", [AgentSpec("ada")],
               {"ada": [bad, bad, bad, "giving up", "still nothing"]},
               stall_repeat_limit=3, max_turns_per_session=10)
    sup.ws.setup(["ada"])
    from colony.agent import Agent
    a = Agent(sup, sup.goal.agents[0])
    a.run_session()
    assert any(e["kind"] == "stall" for e in sup.store.events())
    # the note went in as a mid-conversation system message, as the last entry before a turn
    sys_msgs = [m for m in a.messages if m["role"] == "system"]
    assert sys_msgs and "change strategy" in sys_msgs[0]["content"]


def test_messages_and_inbox_notice(repo, tmp_path):
    sup = make(repo, tmp_path, "diverge", [AgentSpec("ada"), AgentSpec("brook")], {})
    sup.store.send("brook", "ada", "request_help", "how do I run tests?")
    assert sup.store.unread_count("ada") == 1
    assert sup.store.read_inbox("ada")[0]["content"] == "how do I run tests?"
    assert sup.store.unread_count("ada") == 0
    sup.store.send("ada", "all", "finding", "use pytest")
    assert sup.store.unread_count("brook") == 1 and sup.store.unread_count("ada") == 0


def test_guardrails():
    g = CommandGuard(DEFAULT_DENY)
    assert g.check("git push origin main")
    assert g.check("sudo rm x")
    assert g.check("python -m pytest -q") is None
    root = Path("/tmp/ws")
    with pytest.raises(ValueError):
        safe_path(root, "../etc/passwd")
    with pytest.raises(ValueError):
        safe_path(root, ".git/config")


def test_budget_stops_run(repo, tmp_path):
    sup = make(repo, tmp_path, "diverge", [AgentSpec("ada")], {}, budget_usd=0.0)
    assert sup.run().startswith("budget")


def test_worker_wait_does_not_sleep_through_open_tasks(repo, tmp_path):
    sup = make(repo, tmp_path, "converge", [AgentSpec("lead", role="lead"), AgentSpec("ada")],
               {})
    sup.store.create_task("t", "d", "lead")
    assert "open tasks" in sup.wait_for_change("ada", "idle", timeout=5)
    sup.store.send("ada", "lead", "status", "hi")
    assert "unread" in sup.wait_for_change("lead", "idle", timeout=5)
