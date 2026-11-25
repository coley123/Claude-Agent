"""A scripted backend so the harness can be tested without an API key."""
import itertools
import re
import threading

from colony.backend import ModelTurn, ToolCall

_ids = itertools.count(1)


class ScriptedBackend:
    """scripts: {agent_name: [step, ...]} where a step is a list of (tool, input) pairs,
    or a string (a text-only reply). When a script runs out the agent replies with text."""

    def __init__(self, scripts):
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.calls = []
        self.lock = threading.Lock()

    def create(self, *, model, effort, system, tools, messages):
        name = re.search(r"You are (\S+),", system).group(1)
        with self.lock:
            self.calls.append((name, messages))
            step = self.scripts[name].pop(0) if self.scripts.get(name) else "nothing to do"
        if isinstance(step, str):
            return ModelTurn(content=[{"type": "text", "text": step}], stop_reason="end_turn",
                             text=step, input_tokens=100, output_tokens=10, model=model)
        calls = [ToolCall(f"tu_{next(_ids)}", n, i) for n, i in step]
        content = [{"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
                   for c in calls]
        return ModelTurn(content=content, stop_reason="tool_use", tool_calls=calls,
                         input_tokens=100, output_tokens=10, model=model)

    def to_jsonable(self, content):
        return content
