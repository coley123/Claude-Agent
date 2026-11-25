"""The supervisor: starts agents, verifies submissions, enforces guardrails, ends the run."""
from __future__ import annotations

import re
import threading
import time
import traceback
from pathlib import Path

from .agent import Agent
from .backend import Backend
from .config import Goal
from .skills import SkillLibrary
from .store import Store
from .workspace import CommandGuard, Workspaces, run_command

WAIT_TIMEOUT = 300  # seconds an agent waits before being told nothing changed
POLL = 1.0
MAX_CONSECUTIVE_ERRORS = 5


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "run"


class Supervisor:
    def __init__(self, goal: Goal, backend: Backend, runs_root: Path, skills_root: Path):
        self.goal = goal
        self.backend = backend
        self.run_id = slug(goal.name)
        self.run_dir = runs_root / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.store = Store(self.run_dir / "colony.db")
        self.skills = SkillLibrary(skills_root)
        self.ws = Workspaces(goal.repo, self.run_dir, self.run_id)
        self.guard = CommandGuard(goal.deny_patterns)
        self.integration_lock = threading.Lock()
        self.waiting: set[str] = set()
        self._wait_lock = threading.Lock()
        self.started = self.store.get_meta("started") or time.time()
        self.store.set_meta("started", self.started)
        self.stop_reason: str | None = None

    # ------------------------------------------------------------------ lifecycle
    def run(self) -> str:
        self.ws.setup([a.name for a in self.goal.agents])
        self.store.log("supervisor", 0, "run_start", {"mode": self.goal.mode,
                                                      "agents": [a.name for a in self.goal.agents]})
        agents = [Agent(self, spec) for spec in self.goal.agents]
        threads = [threading.Thread(target=self._agent_loop, args=(a,), name=a.spec.name,
                                    daemon=True) for a in agents]
        for t in threads:
            t.start()
        all_waiting_since = None
        while any(t.is_alive() for t in threads):
            time.sleep(POLL)
            if self.should_stop():
                continue
            # If every agent is waiting, nothing will change on its own: run the verifier
            # and post the result, which wakes them with fresh information.
            with self._wait_lock:
                everyone_waiting = len(self.waiting) == len(agents)
            if everyone_waiting:
                all_waiting_since = all_waiting_since or time.time()
                if time.time() - all_waiting_since > 30:
                    self._nudge_all_waiting()
                    all_waiting_since = None
            else:
                all_waiting_since = None
        for t in threads:
            t.join(timeout=5)
        self.write_report()
        return self.stop_reason or "finished"

    def _agent_loop(self, agent: Agent) -> None:
        name, errors = agent.spec.name, 0
        while not self.should_stop():
            try:
                reason = agent.run_session()
                errors = 0
                if reason in ("idle", "refusal"):
                    self.wait_for_change(name, f"session ended: {reason}")
            except Exception as e:
                errors += 1
                self.store.log(name, agent.session, "error",
                               {"error": repr(e), "trace": traceback.format_exc()[-3000:]})
                if errors >= MAX_CONSECUTIVE_ERRORS:
                    self.escalate(name, f"agent stopped after {errors} consecutive errors: {e!r}")
                    return
                time.sleep(min(60, 2 ** errors))

    def should_stop(self) -> bool:
        if self.stop_reason:
            return True
        if self.store.get_meta("goal_done"):
            self.stop_reason = "goal verified"
        elif (self.run_dir / "STOP").exists():
            self.stop_reason = "stopped by STOP file"
        elif self.store.total_cost() >= self.goal.budget_usd:
            self.stop_reason = f"budget of ${self.goal.budget_usd:.2f} reached"
        elif time.time() - self.started > self.goal.max_hours * 3600:
            self.stop_reason = f"time limit of {self.goal.max_hours}h reached"
        return self.stop_reason is not None

    # ------------------------------------------------------------------ waiting
    def _state_signature(self, agent: str) -> tuple:
        tasks = self.store.tasks()
        board = self.store.board(1)
        return (self.store.unread_count(agent),
                tuple((t["id"], t["status"], t["owner"]) for t in tasks),
                board[-1]["id"] if board else 0)

    def wait_for_change(self, agent: str, reason: str, timeout: float = WAIT_TIMEOUT) -> str:
        self.store.log(agent, 0, "wait", {"reason": reason})
        # Don't sleep through work that is already there.
        if self.store.unread_count(agent):
            return f"Not waiting: you have {self.store.unread_count(agent)} unread message(s)."
        spec = next(a for a in self.goal.agents if a.name == agent)
        mine = [t for t in self.store.tasks("claimed") if t["owner"] == agent]
        if spec.role != "lead" and not mine and self.store.tasks("open"):
            return "Not waiting: there are open tasks to claim (list_tasks)."
        before = self._state_signature(agent)
        with self._wait_lock:
            self.waiting.add(agent)
        try:
            deadline = time.time() + timeout
            while time.time() < deadline:
                if self.should_stop():
                    return f"run is ending: {self.stop_reason}"
                now = self._state_signature(agent)
                if now != before:
                    changed = []
                    if now[0] != before[0]:
                        changed.append(f"{now[0]} unread message(s)")
                    if now[1] != before[1]:
                        changed.append("the task list changed")
                    if now[2] != before[2]:
                        changed.append("new blackboard post")
                    return "Woke up: " + ", ".join(changed) + "."
                time.sleep(POLL)
            return f"No changes for {int(timeout)}s. The goal is still not verified."
        finally:
            with self._wait_lock:
                self.waiting.discard(agent)

    def _nudge_all_waiting(self) -> None:
        with self.integration_lock:
            code, out = run_command(self.goal.verify_command, self.ws.integration,
                                    self.goal.verify_timeout)
        if code == 0 and self.goal.mode == "converge":
            self._complete(self.ws.integration_branch, "supervisor")
            return
        open_tasks = len(self.store.tasks("open"))
        self.store.post("supervisor", "status",
                        f"All agents are waiting but the goal is not verified "
                        f"({open_tasks} open task(s)). Verifier output on the integrated work:\n"
                        f"{out[-3000:]}\nLead: create tasks for what is missing. Workers: "
                        f"claim open tasks or propose a new approach.")
        n = self.store.get_meta("all_waiting_nudges", 0) + 1
        self.store.set_meta("all_waiting_nudges", n)
        if n == 3:
            self.escalate("supervisor", "the team has stalled three times with the goal "
                          "unverified; a human may need to clarify the goal")

    # ------------------------------------------------------------------ verification
    def submit(self, agent: Agent, task_id: int, summary: str) -> str:
        me = agent.spec.name
        sha = self.ws.commit_all(me, f"{me}: {summary[:60]}")
        self.store.log(me, agent.session, "submit", {"task_id": task_id, "sha": sha,
                                                     "summary": summary})
        ws = self.ws.path(me)

        if task_id:
            t = self.store.task(task_id)
            if not t or t["owner"] != me or t["status"] != "claimed":
                raise ValueError("you can only submit a task you have claimed")
            if t["verify_command"]:
                code, out = run_command(t["verify_command"], ws, self.goal.verify_timeout)
                if code != 0:
                    self.store.log(me, agent.session, "verify_fail", {"task_id": task_id})
                    return f"REJECTED: task check `{t['verify_command']}` failed:\n{out}"

        if self.goal.mode == "diverge":
            if task_id:
                self.store.update_task(task_id, status="done", result=summary)
                return f"task #{task_id} accepted"
            code, out = run_command(self.goal.verify_command, ws, self.goal.verify_timeout)
            if code != 0:
                self.store.log(me, agent.session, "verify_fail", {"task_id": 0})
                return f"REJECTED: goal verifier failed on your approach:\n{out}"
            self._complete(self.ws.branch(me), me)
            return "VERIFIED: your approach passes the goal verifier. The goal is complete."

        # converge: integrate, then check the whole goal on the integrated work
        with self.integration_lock:
            ok, msg = self.ws.merge_into_integration(me)
            if not ok:
                return ("REJECTED: your branch conflicts with the team's integrated work. Run "
                        f"sync_workspace, resolve the conflicts, then submit again.\n{msg}")
            if task_id:
                self.store.update_task(task_id, status="done", result=summary)
                self.store.post(me, "status", f"finished task #{task_id}: {summary}")
            code, out = run_command(self.goal.verify_command, self.ws.integration,
                                    self.goal.verify_timeout)
        if code == 0:
            self._complete(self.ws.integration_branch, me)
            return "VERIFIED: the integrated work passes the goal verifier. The goal is complete."
        self.store.log(me, agent.session, "goal_verify_fail", {"task_id": task_id})
        return (f"{'Task #%d accepted and merged. ' % task_id if task_id else 'Merged. '}"
                f"The goal verifier still fails on the integrated work:\n{out[-4000:]}")

    def _complete(self, branch: str, by: str) -> None:
        self.store.set_meta("goal_done", True)
        self.store.set_meta("winner", {"branch": branch, "by": by})
        self.store.post("supervisor", "status", f"GOAL VERIFIED on {branch} (by {by})")
        self.store.log(by, 0, "goal_done", {"branch": branch})

    def escalate(self, who: str, reason: str) -> None:
        with open(self.run_dir / "ESCALATIONS.md", "a") as f:
            f.write(f"- {time.strftime('%Y-%m-%d %H:%M:%S')} **{who}**: {reason}\n")
        self.store.log(who, 0, "escalation", {"reason": reason})

    # ------------------------------------------------------------------ reporting
    def write_report(self) -> Path:
        winner = self.store.get_meta("winner")
        lines = [f"# Run report: {self.goal.name}", "",
                 f"- Result: **{self.stop_reason or 'running'}**",
                 f"- Mode: {self.goal.mode}",
                 f"- Elapsed: {(time.time() - self.started) / 60:.1f} min",
                 f"- Estimated cost: ${self.store.total_cost():.2f}", ""]
        if winner:
            lines += ["## Verified work", "",
                      f"Branch `{winner['branch']}` in `{self.goal.repo}` (by {winner['by']}).",
                      "Nothing was merged into your main branch. To review and merge:", "",
                      "```bash", f"git -C {self.goal.repo} diff HEAD...{winner['branch']}", "```",
                      "", "```bash", f"git -C {self.goal.repo} merge {winner['branch']}", "```",
                      ""]
        lines += ["## Tasks", ""] + [
            f"- #{t['id']} [{t['status']}] {t['title']} (owner: {t['owner'] or '-'})"
            for t in self.store.tasks()] + ["", "## Usage", ""] + [
            f"- {u['agent']}: {u['input_tokens']} in / {u['output_tokens']} out, "
            f"${u['cost_usd']:.2f}" for u in self.store.usage()]
        esc = self.run_dir / "ESCALATIONS.md"
        if esc.exists():
            lines += ["", "## Needs a human", "", esc.read_text()]
        path = self.run_dir / "REPORT.md"
        path.write_text("\n".join(lines) + "\n")
        return path
