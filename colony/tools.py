"""Tool definitions the agents see, and the code that runs them."""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .workspace import run_command, safe_path, truncate

if TYPE_CHECKING:
    from .agent import Agent


def _tool(tool_name: str, description: str, **props) -> dict:
    return {
        "name": tool_name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": False,
        },
    }


S = {"type": "string"}
I = {"type": "integer"}

TOOLS = [
    _tool("bash", "Run a shell command in your own git worktree (your workspace). Use it to "
          "explore, build and run tests. Output is truncated past 20k chars.", command=S),
    _tool("read_file", "Read a file in your workspace.", path=S),
    _tool("write_file", "Create or overwrite a file in your workspace.", path=S, content=S),
    _tool("edit_file", "Replace one exact, unique occurrence of `old` with `new` in a file.",
          path=S, old=S, new=S),
    _tool("memory_read", "Read your private notes. They survive context resets."),
    _tool("memory_write", "Replace your private notes. Keep them current: what you are doing, "
          "what you tried, what failed, what is next. Your context is reset periodically and "
          "these notes are how you continue.", notes=S),
    _tool("board_read", "Read the shared blackboard (recent posts by all agents)."),
    _tool("board_post", "Post to the shared blackboard for all agents.",
          kind={"type": "string", "enum": ["finding", "claim", "plan", "status", "warning"]},
          content=S),
    _tool("send_message", "Send a message to one agent by name, or to 'all'.", to=S,
          kind={"type": "string",
                "enum": ["request_help", "offer", "reply", "finding", "status", "blocked"]},
          content=S),
    _tool("read_inbox", "Read your unread messages."),
    _tool("list_tasks", "List all tasks with status and owner."),
    _tool("create_task", "(lead only) Create a task for the team. `skills` names skills the "
          "owner should load. `verify_command` is a shell command that must pass before the "
          "task is accepted; use \"\" for none.", title=S, detail=S,
          skills={"type": "array", "items": S}, verify_command=S),
    _tool("claim_task", "Claim an open task so nobody else works on it.", task_id=I),
    _tool("release_task", "Give a claimed task back to the pool, with the reason.",
          task_id=I, reason=S),
    _tool("sync_workspace", "Merge the team's integrated work into your workspace. Do this "
          "before starting a new task."),
    _tool("list_skills", "List the skill library."),
    _tool("load_skill", "Load a skill's full instructions.", name=S),
    _tool("submit", "Submit work for verification. Your changes are committed automatically. "
          "Converge mode: pass the task_id. Diverge mode: pass 0 to submit your approach for "
          "the whole goal. Only a passing verifier marks work done.", task_id=I, summary=S),
    _tool("report_blocked", "Report that you are blocked. Set needs_human true only for a "
          "decision or access only a person can provide. Keep trying other avenues meanwhile.",
          reason=S, needs_human={"type": "boolean"}),
    _tool("wait", "Pause until something changes (new message, task or blackboard post). Use "
          "when you have nothing useful to do right now.", reason=S),
]


class WaitSignal(Exception):
    pass


def execute(agent: "Agent", name: str, args: dict) -> tuple[str, bool]:
    """Run one tool call. Returns (result_text, is_error)."""
    try:
        return _dispatch(agent, name, args), False
    except WaitSignal:
        raise
    except Exception as e:  # tool errors go back to the model, not up the stack
        return f"Error: {e}", True


def _dispatch(agent: "Agent", name: str, a: dict) -> str:
    sup, store, me = agent.sup, agent.sup.store, agent.spec.name
    ws = sup.ws.path(me)

    if name == "bash":
        if reason := sup.guard.check(a["command"]):
            store.log(me, agent.session, "guard_block", {"command": a["command"]})
            raise PermissionError(reason)
        code, out = run_command(a["command"], ws, sup.goal.command_timeout)
        return f"exit code {code}\n{out}"
    if name == "read_file":
        return truncate(safe_path(ws, a["path"]).read_text())
    if name == "write_file":
        p = safe_path(ws, a["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(a["content"])
        return f"wrote {len(a['content'])} chars to {a['path']}"
    if name == "edit_file":
        p = safe_path(ws, a["path"])
        text = p.read_text()
        n = text.count(a["old"])
        if n != 1:
            raise ValueError(f"`old` must occur exactly once; found {n} occurrences")
        p.write_text(text.replace(a["old"], a["new"]))
        return "edited"
    if name == "memory_read":
        return store.memory(me) or "(no notes yet)"
    if name == "memory_write":
        store.set_memory(me, a["notes"])
        return "notes saved"
    if name == "board_read":
        return _fmt([f"#{p['id']} [{p['kind']}] {p['author']}: {p['content']}"
                     for p in store.board()])
    if name == "board_post":
        store.post(me, a["kind"], a["content"])
        return "posted"
    if name == "send_message":
        names = {s.name for s in sup.goal.agents}
        if a["to"] != "all" and a["to"] not in names:
            raise ValueError(f"unknown agent {a['to']!r}; agents: {', '.join(sorted(names))}")
        store.send(me, a["to"], a["kind"], a["content"])
        return "sent"
    if name == "read_inbox":
        return _fmt([f"from {m['sender']} [{m['kind']}]: {m['content']}"
                     for m in store.read_inbox(me)])
    if name == "list_tasks":
        return _fmt([f"#{t['id']} [{t['status']}] owner={t['owner'] or '-'} {t['title']}"
                     f" skills={t['skills']}" for t in store.tasks()])
    if name == "create_task":
        if agent.spec.role != "lead":
            raise PermissionError("only the lead creates tasks; message the lead instead")
        tid = store.create_task(a["title"], a["detail"], me, a["skills"],
                                a["verify_command"] or None)
        return f"created task #{tid}"
    if name == "claim_task":
        if not store.claim_task(a["task_id"], me):
            raise ValueError("task is not open (already claimed, done, or missing)")
        t = store.task(a["task_id"])
        loaded = "".join(f"\n\n{sup.skills.load(s)}" for s in t["skills"]
                         if s in sup.skills.all())
        return f"claimed #{t['id']}: {t['title']}\n\n{t['detail']}{loaded}"
    if name == "release_task":
        t = store.task(a["task_id"])
        if not t or t["owner"] != me:
            raise ValueError("you do not own that task")
        store.update_task(a["task_id"], status="open", owner=None, result=a["reason"])
        return "released"
    if name == "sync_workspace":
        ok, msg = sup.ws.sync_from_integration(me)
        return msg
    if name == "list_skills":
        return sup.skills.index()
    if name == "load_skill":
        return sup.skills.load(a["name"])
    if name == "submit":
        return sup.submit(agent, a["task_id"], a["summary"])
    if name == "report_blocked":
        store.post(me, "warning", f"BLOCKED: {a['reason']}")
        store.log(me, agent.session, "blocked", a)
        if a["needs_human"]:
            sup.escalate(me, a["reason"])
            return "escalated to a human; continue with anything else you can do meanwhile"
        return "recorded; ask another agent for help or try a different approach"
    if name == "wait":
        raise WaitSignal(a["reason"])
    raise ValueError(f"unknown tool {name}")


def _fmt(lines: list[str]) -> str:
    return "\n".join(lines) if lines else "(empty)"


def summarize_input(args: dict) -> str:
    return truncate(json.dumps(args, sort_keys=True), 2000)
