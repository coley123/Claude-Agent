"""Export colony transcripts as a supervised fine-tuning (SFT) dataset.

Each line of the output is one agent session:

    {"messages": [...chat format...], "tools": [...], "meta": {...}}

`messages` and `tools` use the chat-completions format that Hugging Face chat templates
render (tool-call arguments as dicts), produced by the same converter the local backend
serves with. `meta.success` says whether the session ended in verified work (or, for a
reflection session, a published skill); train on successes only for plain SFT.
"""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from ..agent import REFLECTION_TOOLS
from ..chatformat import to_chat, tools_to_chat
from ..store import Store
from ..tools import TOOLS

SUCCESS_PREFIXES = ("VERIFIED",)


def _successful_sessions(store: Store) -> set[tuple[str, int]]:
    ok = set()
    for e in store.events():
        d = e["data"]
        if e["kind"] == "tool" and d["name"] == "submit" and not d["error"]:
            out = str(d["output"])
            if out.startswith(SUCCESS_PREFIXES) or "accepted" in out.split("\n")[0]:
                ok.add((e["agent"], e["session"]))
        if e["kind"] == "skill_proposal" and d.get("published"):
            ok.add((e["agent"], e["session"]))
    return ok


def export_runs(runs_root: Path, out: Path, only_success: bool = True,
                include_reflection: bool = True, min_tool_calls: int = 1) -> dict:
    """Write the dataset and return a summary of what was kept and skipped."""
    summary: Counter = Counter()
    models: Counter = Counter()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for db in sorted(runs_root.glob("*/colony.db")):
            run_dir = db.parent
            store = Store(db)
            good = _successful_sessions(store)
            run_verified = bool(store.get_meta("goal_done"))
            for path in sorted((run_dir / "transcripts").glob("*.json")):
                t = json.loads(path.read_text())
                kind = t.get("kind", "session")
                if kind == "reflection" and not include_reflection:
                    summary["skipped_reflection"] += 1
                    continue
                n_calls = sum(1 for m in t["messages"] if m["role"] == "assistant"
                              and isinstance(m["content"], list)
                              and any(b.get("type") == "tool_use" for b in m["content"]))
                if n_calls < min_tool_calls:
                    summary["skipped_no_tools"] += 1
                    continue
                success = (t["agent"], t["session"]) in good
                if only_success and not success:
                    summary["skipped_unsuccessful"] += 1
                    continue
                tools = REFLECTION_TOOLS if kind == "reflection" else TOOLS
                model = t.get("model", "unknown")
                models[model] += 1
                f.write(json.dumps({
                    "messages": to_chat(t["system"], t["messages"], args_as_dict=True),
                    "tools": tools_to_chat(tools),
                    "meta": {"run": run_dir.name, "agent": t["agent"],
                             "session": t["session"], "kind": kind, "success": success,
                             "run_verified": run_verified, "model": model,
                             "tool_calls": n_calls},
                }) + "\n")
                summary["written"] += 1
    summary_dict = dict(summary) | {"models": dict(models), "out": str(out)}
    return summary_dict


TERMS_NOTE = ("Note: this data contains outputs from hosted models. Check your model "
              "provider's terms before using them to train another model.")
