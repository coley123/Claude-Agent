import json
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from colony.backend import LocalBackend, RouterBackend
from colony.chatformat import from_chat_choice, to_chat, tools_to_chat
from colony.config import AgentSpec, Goal
from colony.supervisor import Supervisor
from colony.tools import TOOLS

ROOT = Path(__file__).resolve().parent.parent


def test_to_chat_converts_blocks_notes_and_errors():
    msgs = [
        {"role": "user", "content": "brief"},
        {"role": "assistant", "content": [
            {"type": "thinking", "thinking": "", "signature": "s"},
            {"type": "text", "text": "looking"},
            {"type": "tool_use", "id": "t1", "name": "bash", "input": {"command": "ls"}}]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "boom", "is_error": True},
            {"type": "text", "text": "[1 unread message(s): read_inbox]"}]},
        {"role": "system", "content": "change strategy"},
    ]
    out = to_chat("SYS", msgs)
    assert [m["role"] for m in out] == ["system", "user", "assistant", "tool", "user"]
    assert out[2]["tool_calls"][0]["function"] == {"name": "bash",
                                                   "arguments": '{"command": "ls"}'}
    assert out[3] == {"role": "tool", "tool_call_id": "t1", "content": "ERROR: boom"}
    # the inbox notice and the system note are merged into one user turn
    assert "unread" in out[4]["content"] and "[system note] change strategy" in out[4]["content"]
    assert to_chat("S", msgs, args_as_dict=True)[2]["tool_calls"][0]["function"][
        "arguments"] == {"command": "ls"}


def test_from_chat_choice_and_tools():
    blocks, calls = from_chat_choice({"content": "ok", "tool_calls": [
        {"id": "c1", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
        {"function": {"name": "bash", "arguments": "not json"}}]})
    assert blocks[0] == {"type": "text", "text": "ok"}
    assert calls[0] == ("c1", "read_file", {"path": "a.py"})
    assert calls[1][2] == {"__unparseable_arguments__": "not json"}
    t = tools_to_chat(TOOLS)
    assert t[0]["type"] == "function" and "parameters" in t[0]["function"]


class FakeServer:
    """Minimal OpenAI-compatible server that replays scripted assistant messages."""

    def __init__(self, replies):
        self.replies, self.requests = list(replies), []
        outer = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                outer.requests.append(body)
                msg = outer.replies.pop(0) if outer.replies else {"content": "done"}
                finish = "tool_calls" if msg.get("tool_calls") else "stop"
                data = json.dumps({"choices": [{"message": msg, "finish_reason": finish}],
                                   "usage": {"prompt_tokens": 50, "completion_tokens": 5}})
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(data.encode())

            def log_message(self, *a):
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/v1"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()


def call(name, args, i):
    return {"id": f"c{i}", "type": "function",
            "function": {"name": name, "arguments": json.dumps(args)}}


def test_local_backend_turn():
    srv = FakeServer([{"content": None, "tool_calls": [call("bash", {"command": "ls"}, 1)]}])
    try:
        t = LocalBackend(srv.url).create(model="qwen", effort="high", system="S",
                                         tools=TOOLS, messages=[{"role": "user", "content": "go"}])
        assert t.stop_reason == "tool_use" and t.tool_calls[0].input == {"command": "ls"}
        assert t.cost() == 0 and srv.requests[0]["model"] == "qwen"
    finally:
        srv.close()


@pytest.fixture
def repo(tmp_path):
    dest = tmp_path / "repo"
    shutil.copytree(ROOT / "examples" / "calc-template", dest)
    for cmd in (["git", "init", "-q", "-b", "main"], ["git", "add", "-A"],
                ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "init"]):
        subprocess.run(cmd, cwd=dest, check=True)
    return dest


def test_full_run_on_local_model(repo, tmp_path):
    """A whole run where the only agent is a local model reached through the router."""
    fix_ops = ("def add(a, b):\n    return a + b\n\n\ndef mean(v):\n    return sum(v) / len(v)\n"
               "\n\ndef median(v):\n    s = sorted(v)\n    n = len(s)\n    m = n // 2\n"
               "    return s[m] if n % 2 else (s[m - 1] + s[m]) / 2\n")
    fix_parse = "def parse_numbers(t):\n    return [float(x) for x in t.split(',') if x.strip()]\n"
    srv = FakeServer([
        {"content": "fixing", "tool_calls": [
            call("write_file", {"path": "calc/ops.py", "content": fix_ops}, 1),
            call("write_file", {"path": "calc/parse.py", "content": fix_parse}, 2)]},
        {"content": None, "tool_calls": [call("submit", {"task_id": 0, "summary": "fixed"}, 3)]},
    ])
    try:
        goal = Goal(name="local-run", description="fix tests", repo=repo, mode="diverge",
                    verify_command="python -m pytest -q -p no:cacheprovider",
                    agents=[AgentSpec("qw", model="local:qwen3-8b")], reflect=False)
        router = RouterBackend(anthropic=object(), local=LocalBackend(srv.url))
        sup = Supervisor(goal, router, tmp_path / "runs", tmp_path / "hub", ROOT / "skills")
        assert sup.run() == "goal verified"
        # second request carried the tool results back in chat format
        roles = [m["role"] for m in srv.requests[1]["messages"]]
        assert roles[:2] == ["system", "user"] and roles.count("tool") == 2
    finally:
        srv.close()
