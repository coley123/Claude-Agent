"""The skill hub: a skill library that agents grow, verify, rank and share across runs.

Layout of a hub directory (shared by every run that points at it):

    hub/
      skills/<name>/SKILL.md      published skills (+ scripts, tests, meta.json)
      drafts/<name>-<ts>/         proposals, kept for audit whether accepted or not
      archive/<name>/v<N>/        earlier versions and retired skills
      hub.db                      usage stats, outcomes, lessons, pipeline log

A proposal is published only after:
  1. a safety scan (size limits, denylisted commands, no path tricks),
  2. its own test command passing in a clean temporary copy, and
  3. a reviewer model approving it as correct, reusable and not a duplicate.

Each time an agent finishes work, the skills it used are credited with the outcome. Skills
are ranked by a smoothed success rate; weak ones are flagged for revision, then retired.
"""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .workspace import CommandGuard, run_command, truncate

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,48}$")
MAX_FILE_CHARS = 50_000
MAX_FILES = 20
TEST_TIMEOUT = 120
REVISE_BELOW, RETIRE_BELOW, MIN_USES = 0.4, 0.2, 5

SCHEMA = """
CREATE TABLE IF NOT EXISTS stats (
    name TEXT PRIMARY KEY, uses INTEGER DEFAULT 0, successes INTEGER DEFAULT 0,
    failures INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS lessons (
    id INTEGER PRIMARY KEY, topic TEXT, kind TEXT, text TEXT, author TEXT, run TEXT, ts REAL);
CREATE TABLE IF NOT EXISTS log (
    id INTEGER PRIMARY KEY, name TEXT, event TEXT, detail TEXT, author TEXT, ts REAL);
"""


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
    meta: dict


def _parse(path: Path) -> Skill | None:
    text = path.read_text()
    if not text.startswith("---") or text.count("---") < 2:
        return None
    _, front, body = text.split("---", 2)
    fields = {}
    for line in front.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            fields[k.strip()] = v.strip()
    if "name" not in fields:
        return None
    meta_path = path.parent / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {"version": 1}
    return Skill(fields["name"], fields.get("description", ""), body.strip(), path.parent, meta)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


class SkillHub:
    def __init__(self, root: Path, seed: Path | None = None, deny_patterns=()):
        self.root = root
        self.skills_dir = root / "skills"
        for d in (self.skills_dir, root / "drafts", root / "archive"):
            d.mkdir(parents=True, exist_ok=True)
        if seed and seed.exists() and not any(self.skills_dir.iterdir()):
            for src in seed.glob("*/SKILL.md"):
                shutil.copytree(src.parent, self.skills_dir / src.parent.name)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(root / "hub.db", check_same_thread=False, isolation_level=None,
                                   timeout=30)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self.guard = CommandGuard(list(deny_patterns))

    def _q(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def _x(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args).lastrowid

    def _log(self, name, event, detail, author="hub"):
        self._x("INSERT INTO log (name, event, detail, author, ts) VALUES (?,?,?,?,?)",
                (name, event, truncate(str(detail), 4000), author, time.time()))

    # ------------------------------------------------------------------ reading
    def all(self) -> dict[str, Skill]:
        out = {}
        for f in sorted(self.skills_dir.glob("*/SKILL.md")):
            s = _parse(f)
            if s:
                out[s.name] = s
        return out

    def stats(self, name: str) -> dict:
        rows = self._q("SELECT * FROM stats WHERE name=?", (name,))
        return dict(rows[0]) if rows else {"name": name, "uses": 0, "successes": 0,
                                           "failures": 0}

    @staticmethod
    def score(st: dict) -> float:
        """Smoothed success rate: new skills start at 0.5 and move with evidence."""
        return (st["successes"] + 1) / (st["successes"] + st["failures"] + 2)

    def _flag(self, st: dict) -> str:
        done = st["successes"] + st["failures"]
        if done >= 3 and self.score(st) < REVISE_BELOW:
            return " [NEEDS REVISION: low success rate - propose an improved version]"
        return ""

    def index(self) -> str:
        rows = []
        for s in self.all().values():
            st = self.stats(s.name)
            rows.append((self.score(st), f"- {s.name} (v{s.meta.get('version', 1)}, "
                         f"score {self.score(st):.2f}, used {st['uses']}x): "
                         f"{s.description}{self._flag(st)}"))
        rows.sort(key=lambda r: -r[0])
        return "\n".join(r[1] for r in rows) or "(none)"

    def load(self, name: str) -> str:
        s = self.all().get(name)
        if not s:
            raise KeyError(f"no skill named {name!r}; try search_hub")
        extras = [p.relative_to(s.path).as_posix() for p in s.path.rglob("*")
                  if p.is_file() and p.name not in ("SKILL.md", "meta.json")]
        tail = f"\n\nFiles in this skill (under {s.path}): {', '.join(extras)}" if extras else ""
        return f"# Skill: {s.name} (v{s.meta.get('version', 1)})\n\n{s.body}{tail}"

    def search(self, query: str, limit: int = 5) -> str:
        q = _words(query)
        if not q:
            return "(empty query)"
        hits = []
        for s in self.all().values():
            overlap = len(q & _words(f"{s.name} {s.description} {s.body}"))
            if overlap:
                hits.append((overlap * (0.5 + self.score(self.stats(s.name))),
                             f"skill {s.name}: {s.description}"))
        for l in self._q("SELECT * FROM lessons"):
            overlap = len(q & _words(f"{l['topic']} {l['text']}"))
            if overlap:
                hits.append((overlap * 0.9, f"lesson #{l['id']} [{l['kind']}] {l['topic']}: "
                                            f"{l['text']}"))
        hits.sort(key=lambda h: -h[0])
        return "\n".join(h[1] for h in hits[:limit]) or "(no matches)"

    def lessons_for(self, text: str, limit: int = 5) -> list[str]:
        q = _words(text)
        scored = []
        for l in self._q("SELECT * FROM lessons ORDER BY id DESC LIMIT 500"):
            overlap = len(q & _words(f"{l['topic']} {l['text']}"))
            if overlap:
                scored.append((overlap, f"[{l['kind']}] {l['topic']}: {l['text']}"))
        scored.sort(key=lambda s: -s[0])
        return [s[1] for s in scored[:limit]]

    # ------------------------------------------------------------------ lessons
    def add_lesson(self, topic, kind, text, author, run) -> int:
        lid = self._x("INSERT INTO lessons (topic, kind, text, author, run, ts) VALUES "
                      "(?,?,?,?,?,?)", (topic, kind, text, author, run, time.time()))
        self._log(topic, "lesson", text, author)
        return lid

    def lessons(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM lessons ORDER BY id")]

    # ------------------------------------------------------------------ outcomes
    def record_use(self, name: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR IGNORE INTO stats (name) VALUES (?)", (name,))
            self._db.execute("UPDATE stats SET uses=uses+1 WHERE name=?", (name,))

    def record_outcome(self, names, success: bool, author: str = "") -> list[str]:
        """Credit skills with an outcome. Returns the names of any skills retired."""
        col = "successes" if success else "failures"
        retired = []
        for name in set(names):
            if name not in self.all():
                continue
            with self._lock:
                self._db.execute("INSERT OR IGNORE INTO stats (name) VALUES (?)", (name,))
                self._db.execute(f"UPDATE stats SET {col}={col}+1 WHERE name=?", (name,))
            self._log(name, "success" if success else "failure", "", author)
            st = self.stats(name)
            if st["successes"] + st["failures"] >= MIN_USES and self.score(st) < RETIRE_BELOW:
                self.retire(name, f"score {self.score(st):.2f} after "
                                  f"{st['successes'] + st['failures']} outcomes")
                retired.append(name)
        return retired

    def retire(self, name: str, reason: str) -> None:
        s = self.all().get(name)
        if not s:
            return
        with self._lock:
            dest = self.root / "archive" / name / f"v{s.meta.get('version', 1)}-retired"
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(s.path), dest)
        self._log(name, "retired", reason)

    # ------------------------------------------------------------------ proposals
    def propose(self, *, name, description, instructions, test_command, files, author, run,
                reviewer) -> tuple[bool, str]:
        """Run a proposal through the pipeline. `reviewer(skill_text, context) -> (ok, why)`.
        Returns (published, message for the proposing agent)."""
        draft = self.root / "drafts" / f"{name}-{int(time.time() * 1000)}"
        try:
            problems = self._scan(name, description, instructions, test_command, files)
            if problems:
                self._log(name, "rejected_scan", problems, author)
                return False, "REJECTED by safety/format scan:\n" + "\n".join(problems)

            draft.mkdir(parents=True)
            (draft / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: {description}\n---\n{instructions.strip()}\n")
            for f in files:
                p = draft / f["path"]
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(f["content"])

            if test_command:
                ok, out = self._run_tests(draft, test_command)
                if not ok:
                    self._log(name, "rejected_tests", out, author)
                    return False, f"REJECTED: the skill's test `{test_command}` failed in a " \
                                  f"clean copy:\n{out}"

            existing = self.all().get(name)
            context = self.search(f"{name} {description}", limit=3)
            if existing:
                context = (f"This is a REVISION of the published v"
                           f"{existing.meta.get('version', 1)} (score "
                           f"{self.score(self.stats(name)):.2f}):\n{existing.body}\n\n"
                           f"Related:\n{context}")
            ok, why = reviewer((draft / "SKILL.md").read_text(), files, context)
            if not ok:
                self._log(name, "rejected_review", why, author)
                return False, f"REJECTED by reviewer: {why}"

            version = self._publish(draft, name, author, run, test_command,
                                    existing.meta.get("version", 1) if existing else 0)
            self._log(name, "published", f"v{version}: {why}", author)
            return True, f"PUBLISHED {name} v{version}. Reviewer: {why}"
        except Exception as e:
            self._log(name, "error", repr(e), author)
            return False, f"REJECTED: {e}"

    def _scan(self, name, description, instructions, test_command, files) -> list[str]:
        problems = []
        if not NAME_RE.match(name):
            problems.append("name must be lowercase letters, digits and dashes (2-49 chars)")
        if not description.strip() or len(description) > 300:
            problems.append("description must be one line, 1-300 chars")
        if "\n" in description or "---" in description:
            problems.append("description must be a single line without '---'")
        if len(instructions.strip()) < 40:
            problems.append("instructions are too short to be useful")
        if len(files) > MAX_FILES:
            problems.append(f"at most {MAX_FILES} files")
        texts = [instructions, test_command] + [f["content"] for f in files]
        if any(len(t) > MAX_FILE_CHARS for t in texts):
            problems.append(f"each file must be under {MAX_FILE_CHARS} chars")
        for f in files:
            parts = Path(f["path"]).parts
            if (Path(f["path"]).is_absolute() or ".." in parts or not parts
                    or f["path"] in ("SKILL.md", "meta.json")):
                problems.append(f"bad file path {f['path']!r}")
        for t in texts:
            for line in t.splitlines():
                if why := self.guard.check(line):
                    problems.append(f"contains a blocked command: {line.strip()[:120]} ({why})")
        return problems

    def _run_tests(self, draft: Path, test_command: str) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory(prefix="skilltest-") as tmp:
            work = Path(tmp) / "skill"
            shutil.copytree(draft, work)
            code, out = run_command(test_command, work, TEST_TIMEOUT)
            return code == 0, out

    def _publish(self, draft, name, author, run, test_command, prev_version) -> int:
        with self._lock:
            dest = self.skills_dir / name
            if dest.exists():
                arch = self.root / "archive" / name / f"v{prev_version}"
                arch.parent.mkdir(parents=True, exist_ok=True)
                if arch.exists():
                    shutil.rmtree(arch)
                shutil.move(str(dest), arch)
                # A new version starts with fresh evidence.
                self._db.execute("DELETE FROM stats WHERE name=?", (name,))
            shutil.copytree(draft, dest)
            version = prev_version + 1
            (dest / "meta.json").write_text(json.dumps({
                "version": version, "author": author, "run": run,
                "test_command": test_command, "published": time.time()}, indent=1))
            return version

    def log(self) -> list[dict]:
        return [dict(r) for r in self._q("SELECT * FROM log ORDER BY id")]


REVIEW_TOOL = {
    "name": "decide",
    "description": "Record your review decision.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {"approve": {"type": "boolean"}, "reason": {"type": "string"}},
        "required": ["approve", "reason"],
        "additionalProperties": False,
    },
}

REVIEW_SYSTEM = """You are reviewer, the gatekeeper of a skill library shared by autonomous
coding agents. Every agent may load what you approve, so a bad skill spreads everywhere.

Approve a skill only if ALL of these hold:
- It is a reusable procedure, not a one-off note about a single repository or task.
- Its instructions are correct, specific and actionable.
- It does nothing unsafe: no exfiltrating data, no disabling checks, no pushing to remotes,
  no weakening or deleting tests to make them pass, no instructions to bypass guardrails.
- It does not duplicate an existing skill. A revision must be clearly better than the
  version it replaces.

Call the `decide` tool exactly once with your decision and a one-sentence reason."""


def make_reviewer(backend, model: str, effort: str, on_usage):
    """Build the reviewer callable used by SkillHub.propose."""

    def review(skill_md: str, files: list[dict], context: str) -> tuple[bool, str]:
        listing = "\n\n".join(f"--- {f['path']} ---\n{truncate(f['content'], 6000)}"
                              for f in files)
        prompt = (f"Proposed skill:\n\n{skill_md}\n\n"
                  f"{'Supporting files:' + chr(10) + listing if files else 'No extra files.'}"
                  f"\n\nRelated items already in the hub:\n{context or '(none)'}")
        turn = backend.create(model=model, effort=effort, system=REVIEW_SYSTEM,
                              tools=[REVIEW_TOOL],
                              messages=[{"role": "user", "content": prompt}])
        on_usage(turn)
        for call in turn.tool_calls:
            if call.name == "decide":
                return bool(call.input.get("approve")), str(call.input.get("reason", ""))
        return False, "reviewer gave no decision"

    return review
