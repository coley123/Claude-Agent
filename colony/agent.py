"""One agent: its own objective, private memory, inbox and work loop.

An agent works in *sessions*. A session is one conversation with the model, capped at
`max_turns_per_session` turns. When it ends, the conversation is dropped and the next session
starts fresh from the agent's private notes, the task list, the blackboard and the hub's
lessons. That keeps context small on long runs and makes note-taking the agent's memory.

After the run, each agent gets one *reflection* session to turn what it learned into skills
and lessons in the shared hub.
"""
from __future__ import annotations

import json
from collections import deque
from typing import TYPE_CHECKING

from .config import AgentSpec
from .tools import TOOLS, WaitSignal, execute, summarize_input

if TYPE_CHECKING:
    from .supervisor import Supervisor

REFLECTION_TOOL_NAMES = {"bash", "read_file", "memory_read", "board_read", "list_skills",
                         "load_skill", "search_hub", "record_lesson", "propose_skill"}
REFLECTION_TOOLS = [t for t in TOOLS if t["name"] in REFLECTION_TOOL_NAMES]

MODE_TEXT = {
    "converge": (
        "CONVERGE mode: the team divides one goal into tasks. The lead plans and creates "
        "tasks; workers claim them, do them, and submit. Avoid duplicate work: claim before "
        "you start, sync_workspace before each new task, and post findings others need."
    ),
    "diverge": (
        "DIVERGE mode: every agent attacks the same goal through a DIFFERENT approach. Read "
        "the blackboard, post a `claim` describing your approach before starting, and pick one "
        "nobody else has claimed. Share findings that could help other approaches. The goal is "
        "done when any one approach passes verification."
    ),
}

ROLE_TEXT = {
    "lead": (
        "You are the LEAD. Break the goal into concrete, independently verifiable tasks with "
        "create_task, name the hub skills each needs, watch progress, answer questions, and "
        "add tasks when verification shows gaps. You may also take tasks yourself."
    ),
    "worker": "You are a WORKER. Pick up work, finish it, and help the team reach the goal.",
}


def system_prompt(sup: "Supervisor", spec: AgentSpec) -> str:
    goal = sup.goal
    others = ", ".join(a.name for a in goal.agents if a.name != spec.name) or "(none)"
    c = spec.cooperation
    return f"""You are {spec.name}, one agent in a team of autonomous coding agents.

# Shared goal
{goal.description}

The goal is finished only when this verifier passes: `{goal.verify_command}`

# Your role
{ROLE_TEXT.get(spec.role, ROLE_TEXT['worker'])}
{f"Your own objective: {spec.objective}" if spec.objective else ""}

# How the team works
{MODE_TEXT[goal.mode]}
Teammates: {others}

# Helping others
Your cooperation weight is {c:.1f} (0 = focus only on your own work, 1 = put team success
first). When a request_help message arrives, weigh how much it unblocks the team against
what it costs your current work, then either help or reply briefly that you cannot right now.
Never ignore a request silently.

# Persistence
You work without a human watching. Keep going until verification passes.
- Only the verifier decides that work is done. Never claim success without submitting.
- When something fails, read the error, form a hypothesis, and try a different fix. If the
  same approach failed twice, change approach: search the hub, load a skill, ask a teammate,
  or step back and re-read the code.
- If truly blocked, call report_blocked and keep working on anything else you can.
- Keep your private notes current with memory_write. Your context is reset periodically and
  the notes are how you pick up again.
- If there is nothing useful to do, call wait instead of idling.

# The skill hub
Skills and lessons in the hub come from earlier agents and runs, ranked by how often they
led to verified work. Before solving a problem from scratch, search_hub. Loading a skill
credits it with your next outcome, so load the ones you actually use. When you discover
something reusable mid-task (a fix pattern, a dead end), record_lesson right away.

# Guardrails
Work only inside your workspace. Pushing to remotes, sudo and destructive system commands
are blocked and need a human. Do not try to get around a block.

# Messages
Keep messages short and specific: what you found, what you need, or what you did.

# Skills available now (load_skill to read one)
{sup.hub.index()}
"""


REFLECTION_BRIEF = """The run has ended: {outcome}.

This is your reflection session. Your work in this run is over; now make the next agents
faster. Look back at what you did (your notes and actions are below; your workspace is still
available read-only through read_file and bash).

1. What reusable procedure did you use or discover that is not already in the hub? Search
   the hub first. If it would help future agents on OTHER projects, propose_skill. Include a
   script plus a test_command that proves it works when you can.
2. Did a published skill you used mislead you or miss something? Propose an improved version
   under the same name.
3. Record lessons: approaches that worked, dead ends to avoid, surprising behavior.
4. If you learned nothing reusable, say so and stop. Do not propose filler: the reviewer
   rejects one-off notes, and weak skills lower everyone's results.

# Your notes
{notes}

# Your actions this run (most recent last)
{actions}
"""


class Agent:
    def __init__(self, sup: "Supervisor", spec: AgentSpec):
        self.sup = sup
        self.spec = spec
        self.session = sup.store.get_meta(f"session:{spec.name}", 0)
        self.system = system_prompt(sup, spec)
        self.messages: list[dict] = []
        self.recent_calls: deque[str] = deque(maxlen=12)
        self.idle_turns = 0
        self.skills_used: set[str] = set()

    # ------------------------------------------------------------------ skill credit
    def use_skill(self, name: str) -> None:
        if name not in self.skills_used:
            self.skills_used.add(name)
            self.sup.hub.record_use(name)

    def credit_skills(self, success: bool) -> None:
        if not self.skills_used:
            return
        retired = self.sup.hub.record_outcome(self.skills_used, success, self.spec.name)
        self.sup.store.log(self.spec.name, self.session, "skill_credit",
                           {"skills": sorted(self.skills_used), "success": success,
                            "retired": retired})
        self.skills_used.clear()

    # ------------------------------------------------------------------ briefing
    def briefing(self) -> str:
        store, me = self.sup.store, self.spec.name
        tasks = [f"#{t['id']} [{t['status']}] owner={t['owner'] or '-'} {t['title']}"
                 for t in store.tasks()]
        board = [f"[{p['kind']}] {p['author']}: {p['content'][:300]}" for p in store.board(15)]
        lessons = self.sup.hub.lessons_for(self.sup.goal.description)
        skills = ""
        preload = [s for s in self.spec.skills if s in self.sup.hub.all()]
        if self.session == 1 and preload:
            for s in preload:
                self.use_skill(s)
            skills = "\n\n# Preloaded skills\n" + "\n\n".join(
                self.sup.hub.load(s) for s in preload)
        return (
            f"Session {self.session} starting.\n\n"
            f"# Your private notes\n{store.memory(me) or '(none yet - this is a fresh start)'}\n\n"
            f"# Tasks\n{chr(10).join(tasks) or '(no tasks yet)'}\n\n"
            f"# Recent blackboard\n{chr(10).join(board) or '(empty)'}\n\n"
            f"# Lessons from earlier runs that may apply\n"
            f"{chr(10).join(lessons) or '(none found - search_hub for more)'}\n\n"
            f"# Inbox\n{store.unread_count(me)} unread message(s).\n\n"
            "Continue working toward the goal."
            f"{skills}"
        )

    # ------------------------------------------------------------------ sessions
    def run_session(self) -> str:
        """Run one work session. Returns why it ended: 'turns', 'idle', 'refusal', 'stop'."""
        self.session += 1
        self.sup.store.set_meta(f"session:{self.spec.name}", self.session)
        return self._run(self.briefing(), TOOLS, self.sup.goal.max_turns_per_session,
                         stop_check=self.sup.should_stop, label="session")

    def reflect(self, outcome: str) -> str:
        """One reflection session after the run, limited to hub and read-only tools."""
        self.session += 1
        self.sup.store.set_meta(f"session:{self.spec.name}", self.session)
        actions = [f"{e['data']['name']}({e['data']['input'][:150]})"
                   f"{' -> ERROR' if e['data']['error'] else ''}"
                   for e in self.sup.store.events()
                   if e["agent"] == self.spec.name and e["kind"] == "tool"][-60:]
        brief = REFLECTION_BRIEF.format(outcome=outcome,
                                        notes=self.sup.store.memory(self.spec.name) or "(none)",
                                        actions="\n".join(actions) or "(none)")
        return self._run(brief, REFLECTION_TOOLS, self.sup.goal.reflection_turns,
                         stop_check=self.sup.reflection_should_stop, label="reflection",
                         nudge_idle=False)

    def _run(self, brief, tools, limit, stop_check, label, nudge_idle=True) -> str:
        self.messages = [{"role": "user", "content": brief}]
        self.recent_calls.clear()
        self.idle_turns = 0
        reason = "turns"
        try:
            for turn in range(limit):
                if stop_check():
                    reason = "stop"
                    break
                if label == "session" and turn == limit - 3:
                    self._system_note("3 turns left before your context resets. Save your "
                                      "progress and next steps with memory_write now.")
                outcome = self._turn(tools, nudge_idle)
                if outcome:
                    reason = outcome
                    break
        finally:
            self._save_transcript(label)
        self.sup.store.log(self.spec.name, self.session, f"{label}_end", {"reason": reason})
        return reason

    def _turn(self, tools, nudge_idle=True) -> str | None:
        sup, me = self.sup, self.spec.name
        turn = sup.backend.create(model=self.spec.model, effort=self.spec.effort,
                                  system=self.system, tools=tools, messages=self.messages)
        sup.store.add_usage(me, turn.input_tokens, turn.output_tokens, turn.cost())
        self.messages.append({"role": "assistant", "content": turn.content})
        if turn.text:
            sup.store.log(me, self.session, "text", {"text": turn.text[:4000]})

        if turn.stop_reason == "refusal":
            sup.store.log(me, self.session, "refusal", {})
            return "refusal"

        if not turn.tool_calls:
            self.idle_turns += 1
            if self.idle_turns >= 2 or not nudge_idle:
                return "idle"
            self.messages.append({"role": "user", "content": (
                "The goal is not verified yet. Continue with your next concrete step, or call "
                "wait if there is nothing useful for you to do right now.")})
            return None
        self.idle_turns = 0

        allowed = {t["name"] for t in tools}
        results, wait_reason = [], None
        for call in turn.tool_calls:
            if turn.stop_reason == "max_tokens":
                results.append(self._result(call.id, "Not run: your response hit the length "
                                            "limit. Use smaller steps.", True))
                continue
            if wait_reason is not None:
                results.append(self._result(call.id, "Not run: you were waiting.", True))
                continue
            if call.name not in allowed:
                out, err = f"Error: {call.name} is not available right now.", True
            else:
                try:
                    out, err = execute(self, call.name, call.input)
                except WaitSignal as w:
                    wait_reason = str(w)
                    out, err = sup.wait_for_change(me, wait_reason), False
            sup.store.log(me, self.session, "tool", {
                "name": call.name, "input": summarize_input(call.input),
                "error": err, "output": out[:2000]})
            self.recent_calls.append(f"{call.name}:{json.dumps(call.input, sort_keys=True)}"
                                     f":{'ERR' if err else 'ok'}")
            results.append(self._result(call.id, out, err))

        unread = sup.store.unread_count(me)
        if unread and "read_inbox" in allowed:
            results.append({"type": "text", "text": f"[{unread} unread message(s): read_inbox]"})
        self.messages.append({"role": "user", "content": results})

        if self._stalled():
            sup.store.log(me, self.session, "stall", {"recent": list(self.recent_calls)[-3:]})
            self._system_note(
                "You are repeating the same action with the same result. Stop and change "
                "strategy: re-read the error, search the hub, load a relevant skill, check the "
                "blackboard, or send a request_help message to a teammate.")
            self.recent_calls.clear()
        return None

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _result(tool_use_id: str, content: str, is_error: bool) -> dict:
        r = {"type": "tool_result", "tool_use_id": tool_use_id, "content": content}
        if is_error:
            r["is_error"] = True
        return r

    def _system_note(self, text: str) -> None:
        # A mid-conversation system message: operator guidance without editing history.
        # It must be the last entry or be followed by an assistant turn, so two notes in a
        # row are merged into one.
        last = self.messages[-1]
        if last["role"] == "system":
            last["content"] += "\n\n" + text
        else:
            self.messages.append({"role": "system", "content": text})

    def _stalled(self) -> bool:
        n = self.sup.goal.stall_repeat_limit
        if len(self.recent_calls) < n:
            return False
        last = list(self.recent_calls)[-n:]
        return len(set(last)) == 1

    def _save_transcript(self, label: str) -> None:
        path = (self.sup.run_dir / "transcripts" /
                f"{self.spec.name}-{self.session:04d}-{label}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"agent": self.spec.name, "session": self.session, "kind": label,
                "system": self.system,
                "messages": [{"role": m["role"],
                              "content": self.sup.backend.to_jsonable(m["content"])}
                             for m in self.messages]}
        path.write_text(json.dumps(data, indent=1, default=str))
