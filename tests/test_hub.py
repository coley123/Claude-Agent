import json
import shutil
import subprocess
from pathlib import Path

import pytest

from colony.config import DEFAULT_DENY, AgentSpec, Goal
from colony.hub import SkillHub
from colony.supervisor import Supervisor
from fake_backend import ScriptedBackend

ROOT = Path(__file__).resolve().parent.parent

GOOD = dict(
    name="pytest-single-test",
    description="Run one pytest test in isolation and read its full traceback.",
    instructions="1. Run `python -m pytest path::name -x -q`.\n2. Read the whole traceback "
                 "from the bottom up.\n3. Re-run after each change.",
    test_command="",
    files=[],
)


def approve(*_):
    return True, "useful and general"


def reject(*_):
    return False, "one-off note"


@pytest.fixture
def hub(tmp_path):
    return SkillHub(tmp_path / "hub", seed=ROOT / "skills", deny_patterns=DEFAULT_DENY)


def propose(hub, reviewer=approve, **over):
    return hub.propose(**(GOOD | over), author="ada", run="r1", reviewer=reviewer)


def test_seeded_from_starter_skills(hub):
    assert "debug-failing-test" in hub.all()


def test_publish_then_revise_archives_old_version(hub):
    ok, msg = propose(hub)
    assert ok and "v1" in msg
    assert "pytest-single-test (v1" in hub.index()
    hub.record_outcome(["pytest-single-test"], True)
    ok, msg = propose(hub, instructions=GOOD["instructions"] + "\n4. Then run the full suite.")
    assert ok and "v2" in msg
    assert (hub.root / "archive" / "pytest-single-test" / "v1" / "SKILL.md").exists()
    assert hub.stats("pytest-single-test")["successes"] == 0  # new version, fresh evidence
    assert "full suite" in hub.load("pytest-single-test")


def test_reviewer_rejection_does_not_publish(hub):
    ok, msg = propose(hub, reviewer=reject)
    assert not ok and "one-off" in msg
    assert "pytest-single-test" not in hub.all()


@pytest.mark.parametrize("over,needle", [
    ({"instructions": "Run tests, then git push origin main to share them with everyone."},
     "blocked command"),
    ({"name": "Bad Name!"}, "name must be"),
    ({"files": [{"path": "../escape.py", "content": "x"}]}, "bad file path"),
    ({"instructions": "too short"}, "too short"),
])
def test_scan_rejects(hub, over, needle):
    ok, msg = propose(hub, **over)
    assert not ok and needle in msg


def test_skill_tests_run_in_clean_copy(hub):
    files = [{"path": "slugify.py", "content": "def slugify(s):\n    return '-'.join(s.lower().split())\n"},
             {"path": "test_slugify.py", "content": "from slugify import slugify\n\n"
              "def test_it():\n    assert slugify('A B') == 'a-b'\n"}]
    ok, msg = propose(hub, name="slugify-helper", files=files,
                      test_command="python -m pytest -q -p no:cacheprovider")
    assert ok, msg
    broken = [files[0], {"path": "test_slugify.py", "content": "def test_it():\n    assert False\n"}]
    ok, msg = propose(hub, name="slugify-broken", files=broken,
                      test_command="python -m pytest -q -p no:cacheprovider")
    assert not ok and "failed in a clean copy" in msg


def test_ranking_flags_then_retires(hub):
    propose(hub)
    name = "pytest-single-test"
    for _ in range(3):
        hub.record_outcome([name], False)
    assert "NEEDS REVISION" in hub.index()
    retired = []
    for _ in range(3):
        retired += hub.record_outcome([name], False)
    assert retired == [name] and name not in hub.all()
    assert any(p.name.endswith("retired") for p in (hub.root / "archive" / name).iterdir())


def test_index_orders_by_score(hub):
    propose(hub)
    for _ in range(4):
        hub.record_outcome(["pytest-single-test"], True)
    assert hub.index().splitlines()[0].startswith("- pytest-single-test")


def test_search_finds_skills_and_lessons(hub):
    hub.add_lesson("pandas", "fails", "read_csv dtype guessing breaks zip codes", "ada", "r1")
    assert "zip codes" in hub.search("zip code parsing with pandas")
    assert "debug-failing-test" in hub.search("failing test")
    assert hub.lessons_for("load the csv of zip codes")


# ---------------------------------------------------------------- end to end

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


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(ROOT / "examples" / "calc-template", dest)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=dest, check=True)
    return dest


def test_run_credits_skills_and_reflection_grows_hub(repo, tmp_path):
    skill = GOOD | {"name": "even-median", "description": "Compute a median correctly for "
                    "even-length lists.", "instructions": "Sort, then for even length average "
                    "the two middle values; for odd length take the middle one. Test both."}
    scripts = {
        "ada": [
            [("load_skill", {"name": "debug-failing-test"})],
            [("write_file", {"path": "calc/ops.py", "content": FIXED_OPS}),
             ("write_file", {"path": "calc/parse.py", "content": FIXED_PARSE})],
            [("submit", {"task_id": 0, "summary": "fixed"})],
            # reflection session
            [("search_hub", {"query": "median"})],
            [("record_lesson", {"topic": "median", "kind": "fails",
                                "lesson": "len//2 alone is wrong for even-length lists"}),
             ("propose_skill", skill)],
            "done reflecting",
        ],
        "reviewer": [[("decide", {"approve": True, "reason": "general and correct"})]],
    }
    goal = Goal(name="hub-e2e", description="fix the median and parsing tests", repo=repo,
                mode="diverge", verify_command="python -m pytest -q -p no:cacheprovider",
                agents=[AgentSpec("ada")])
    hub_dir = tmp_path / "hub"
    sup = Supervisor(goal, ScriptedBackend(scripts), tmp_path / "runs", hub_dir, ROOT / "skills")
    assert sup.run() == "goal verified"

    hub = SkillHub(hub_dir)
    assert hub.stats("debug-failing-test")["successes"] == 1
    assert "even-median" in hub.all()
    assert json.loads((hub_dir / "skills" / "even-median" / "meta.json").read_text())["author"] == "ada"
    report = (sup.run_dir / "REPORT.md").read_text()
    assert "Skills published this run: even-median" in report
    assert "Lessons recorded: 1" in report
    assert any(u["agent"] == "reviewer" for u in sup.store.usage())

    # A later run in a different colony sees the lesson in its first briefing.
    goal2 = Goal(name="next-run", description="median is wrong for even length lists",
                 repo=repo, mode="diverge", verify_command="true", agents=[AgentSpec("bo")],
                 reflect=False)
    backend2 = ScriptedBackend({"bo": [[("submit", {"task_id": 0, "summary": "noop"})]]})
    sup2 = Supervisor(goal2, backend2, tmp_path / "runs", hub_dir, ROOT / "skills")
    sup2.run()
    first_brief = backend2.calls[0][1][0]["content"]
    assert "len//2 alone is wrong" in first_brief
    assert "even-median" in sup2.agents[0].system


def test_reflection_cannot_use_work_tools(repo, tmp_path):
    scripts = {"ada": [[("submit", {"task_id": 0, "summary": "x"})],
                       [("write_file", {"path": "x.py", "content": "evil"})], "ok"]}
    goal = Goal(name="refl", description="d", repo=repo, mode="diverge",
                verify_command="true", agents=[AgentSpec("ada")])
    sup = Supervisor(goal, ScriptedBackend(scripts), tmp_path / "runs", tmp_path / "hub",
                     ROOT / "skills")
    assert sup.run() == "goal verified"
    ev = [e for e in sup.store.events() if e["kind"] == "tool"
          and e["data"]["name"] == "write_file"]
    assert ev and ev[0]["data"]["error"] and "not available" in ev[0]["data"]["output"]
