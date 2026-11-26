"""Offline tests for slack_bridge: a fake Slack workspace and scripted model replies."""
import sys
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "slack_bridge"))
import bridge as B  # noqa: E402

OWNER = "UOWNER"
HERMES = "UHERMES"


class Workspace:
    """Shared message store that every fake client posts into."""

    def __init__(self):
        self.msgs: dict[str, list[dict]] = {}
        self.reactions = []
        self.n = 1000
        self.bridge = None
        self.deliver = True

    def add(self, channel, msg):
        self.n += 1
        msg = dict(msg, ts=f"{self.n}.000100", channel=channel)
        self.msgs.setdefault(channel, []).append(msg)
        return msg


class FakeClient:
    def __init__(self, ws, user_id, bot_id, name):
        self.ws, self.user_id, self.bot_id, self.name = ws, user_id, bot_id, name

    def auth_test(self):
        return {"user_id": self.user_id, "bot_id": self.bot_id}

    def conversations_replies(self, channel, ts, limit=200):
        return {"messages": [m for m in self.ws.msgs.get(channel, [])
                             if m["ts"] == ts or m.get("thread_ts") == ts]}

    def conversations_history(self, channel, latest, inclusive=True, limit=8):
        ms = [m for m in self.ws.msgs.get(channel, []) if float(m["ts"]) <= float(latest)]
        return {"messages": list(reversed(ms[-limit:]))}

    def chat_postMessage(self, channel, thread_ts, text):
        m = self.ws.add(channel, {"user": self.user_id, "bot_id": self.bot_id, "text": text,
                                  "thread_ts": thread_ts, "bot_profile": {"name": self.name}})
        if self.ws.deliver and self.ws.bridge:  # every bot "receives" the new message
            for key in self.ws.bridge.parts:
                self.ws.bridge.on_message(key, dict(m))
        return {"ts": m["ts"]}

    def reactions_add(self, channel, timestamp, name):
        self.ws.reactions.append((timestamp, name))

    def users_info(self, user):
        return {"user": {"real_name": user}}


def make(tmp_path, replies, **bridge_over):
    over = "\n".join(f"{k} = {v}" for k, v in bridge_over.items())
    (tmp_path / "bridge.toml").write_text(textwrap.dedent(f"""
        [bridge]
        data_dir = "data"
        workdir = "."
        owner_ids = ["{OWNER}"]
        {over}
        [[external]]
        name = "Hermes"
        user_id = "{HERMES}"
        role = "ops"
        [[external]]
        name = "Coley"
        user_id = "{OWNER}"
        role = "owner"
        human = true
        [[participants]]
        name = "Claude"
        role = "engineering"
        command = ["claude"]
        [[participants]]
        name = "GPT"
        role = "direction"
        command = ["codex"]
        [[participants]]
        name = "Grok"
        role = "review"
        command = ["grok"]
        daily_cap = 2
    """))
    cfg = B.load_config(tmp_path / "bridge.toml")
    ws = Workspace()
    clients = {"claude": FakeClient(ws, "UCLAUDE", "BCLAUDE", "Claude"),
               "gpt": FakeClient(ws, "UGPT", "BGPT", "GPT"),
               "grok": FakeClient(ws, "UGROK", "BGROK", "Grok")}
    calls = []

    def runner(p, prompt, workdir):
        calls.append((p.key, prompt))
        r = replies(p.key, prompt, len(calls))
        if isinstance(r, Exception):
            raise r
        return B.RunResult(text=r, exit_code=0)

    br = B.Bridge(cfg, clients, runner=runner, sync=True)
    br.identify()
    ws.bridge = br
    return br, ws, calls


def human_says(br, ws, text, channel="C1", thread_ts=None, user=OWNER):
    m = ws.add(channel, {"user": user, "text": text, **({"thread_ts": thread_ts} if thread_ts else {})})
    for key in br.parts:
        br.on_message(key, dict(m))
    return m


def test_mention_gets_reply_in_thread(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: "Hi from " + k)
    root = human_says(br, ws, "<@UCLAUDE> what's the plan?")
    assert [c[0] for c in calls] == ["claude"]
    replies = [m for m in ws.msgs["C1"] if m.get("thread_ts") == root["ts"]]
    assert replies[0]["text"] == "Hi from claude"
    assert "@Claude what's the plan?" in calls[0][1]  # mention made readable in the prompt
    assert "@GPT" in calls[0][1] and "@Hermes" in calls[0][1]  # roster in the prompt


def test_unmentioned_bots_stay_quiet(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: "x")
    human_says(br, ws, "just a note, no mentions")
    assert calls == []


def test_agents_hand_off_to_each_other(tmp_path):
    def replies(k, p, n):
        return {"claude": "Plan drafted. @GPT please review it.",
                "gpt": "Looks right. @Grok try to break it.",
                "grok": "Found one gap; no further action needed."}[k]
    br, ws, calls = make(tmp_path, replies)
    human_says(br, ws, "<@UCLAUDE> draft the plan")
    assert [c[0] for c in calls] == ["claude", "gpt", "grok"]
    posted = [m["text"] for m in ws.msgs["C1"] if m.get("bot_id")]
    assert "<@UGPT>" in posted[0] and "<@UGROK>" in posted[1]


def test_self_mention_is_not_converted(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: "I, @Claude, will do it")
    human_says(br, ws, "<@UCLAUDE> go")
    assert len(calls) == 1
    assert ws.msgs["C1"][-1]["text"] == "I, Claude, will do it"


def test_loop_limit_stops_ping_pong_and_tells_owner(tmp_path):
    def replies(k, p, n):
        return f"@GPT your turn ({n})" if k == "claude" else f"@Claude your turn ({n})"
    br, ws, calls = make(tmp_path, replies, max_bot_chain=4)
    human_says(br, ws, "<@UCLAUDE> start")
    assert len(calls) == 4
    assert "bot messages in a row" in ws.msgs["C1"][-1]["text"]
    assert f"<@{OWNER}>" in ws.msgs["C1"][-1]["text"]
    # a person replying in the thread resets the chain
    root = ws.msgs["C1"][0]["ts"]
    human_says(br, ws, "<@UGROK> summarize", thread_ts=root)
    assert calls[4][0] == "grok"  # the person reset the chain, so Grok could answer


def test_daily_cap(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: "ok")
    for _ in range(3):
        human_says(br, ws, "<@UGROK> again")
    assert [c[0] for c in calls] == ["grok", "grok"]
    assert "daily cap" in ws.msgs["C1"][-1]["text"]


def test_hourly_limit(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: "ok", max_replies_per_hour=2)
    for _ in range(3):
        human_says(br, ws, "<@UCLAUDE> again")
    assert len(calls) == 2


def test_pause_and_resume_by_owner_only(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: "ok")
    human_says(br, ws, "!im1 pause gpt", user="USTRANGER")
    assert "Only the owner" in ws.msgs["C1"][-1]["text"]
    human_says(br, ws, "!im1 pause gpt")
    human_says(br, ws, "<@UGPT> hello")
    assert calls == []
    human_says(br, ws, "!im1 resume gpt")
    human_says(br, ws, "<@UGPT> hello again")
    assert [c[0] for c in calls] == ["gpt"]
    human_says(br, ws, "!im1 status")
    assert "IM1 bridge status" in ws.msgs["C1"][-1]["text"]


def test_stop_file(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: "ok")
    (br.cfg.data_dir / "STOP").write_text("")
    human_says(br, ws, "<@UCLAUDE> hi")
    assert calls == []


def test_duplicate_events_run_once(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: "ok")
    m = human_says(br, ws, "<@UCLAUDE> hi")
    br.on_message("claude", dict(m))  # Slack redelivery
    assert len(calls) == 1


def test_identical_reply_not_posted_twice(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: "same answer")
    root = human_says(br, ws, "<@UCLAUDE> q1")
    human_says(br, ws, "<@UCLAUDE> q2", thread_ts=root["ts"])
    assert len(calls) == 2
    assert sum(1 for m in ws.msgs["C1"] if m.get("text") == "same answer") == 1


def test_error_is_reported_and_logged(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: RuntimeError("`grok` is not installed"))
    human_says(br, ws, "<@UGROK> hi")
    assert "couldn't reply" in ws.msgs["C1"][-1]["text"]
    row = br.ledger.q("SELECT status, error FROM runs")[0]
    assert row["status"] == "error" and "not installed" in row["error"]


def test_audit_keeps_exact_prompt_and_reply(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: "reply text")
    human_says(br, ws, "<@UCLAUDE> hi")
    row = br.ledger.q("SELECT * FROM runs")[0]
    assert Path(row["prompt_path"]).read_text("utf-8") == calls[0][1]
    assert B.sha(calls[0][1]) == row["prompt_sha256"]
    assert Path(row["reply_path"]).read_text("utf-8") == "reply text"
    assert (br.cfg.data_dir / "bridge.log").exists()


def test_allowed_channels(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: "ok", allowed_channels='["C2"]')
    human_says(br, ws, "<@UCLAUDE> hi", channel="C1")
    assert calls == []
    human_says(br, ws, "<@UCLAUDE> hi", channel="C2")
    assert len(calls) == 1


# ---------------------------------------------------------------- knowledge bank

def test_save_shows_in_every_later_prompt_and_files(tmp_path):
    def replies(k, p, n):
        if n == 1:
            return ('Saving that.\n[[save kind=link title="MCP spec" confidence=high]]'
                    'https://modelcontextprotocol.io/specification[[/save]]')
        return "noted"
    br, ws, calls = make(tmp_path, replies)
    human_says(br, ws, "<@UCLAUDE> save the MCP spec link")
    post = ws.msgs["C1"][-1]["text"]
    assert "[[save" not in post and "Saved #1 (link, unverified): MCP spec" in post
    human_says(br, ws, "<@UGROK> what do we know?")
    assert "#1 [link/unverified/high] MCP spec (by Claude)" in calls[1][1]
    assert "modelcontextprotocol.io" in calls[1][1]
    files = sorted(p.name for p in br.cfg.knowledge_dir.iterdir())
    assert files == ["0001-mcp-spec.md", "INDEX.md"]
    assert "author: Claude" in (br.cfg.knowledge_dir / "0001-mcp-spec.md").read_text()


def test_author_cannot_verify_own_entry_but_peer_can(tmp_path):
    script = {
        1: '[[save kind=fact title="Tests pass"]]33/33 durable tests pass[[/save]]',
        2: '[[verify id=1]]I ran them again[[/verify]]',
        3: '[[verify id=1]]evidence: durable/d1-gateway-knowledge-tests-20261125.log, 33/33 OK[[/verify]]',
    }
    br, ws, calls = make(tmp_path, lambda k, p, n: script[n])
    human_says(br, ws, "<@UCLAUDE> record it")
    human_says(br, ws, "<@UCLAUDE> verify it")
    assert "another participant or the owner must verify" in ws.msgs["C1"][-1]["text"]
    assert br.kb.get(1)["status"] == "unverified"
    human_says(br, ws, "<@UGPT> verify #1")
    assert br.kb.get(1)["status"] == "verified"
    assert "GPT: evidence" in br.kb.get(1)["evidence"]


def test_repetition_does_not_verify_and_dispute_is_kept(tmp_path):
    script = {
        1: '[[save kind=hypothesis title="Bigger model fixes it"]]claim[[/save]]',
        2: '[[save kind=hypothesis title="Bigger model fixes it"]]claim again[[/save]]',
        3: '[[dispute id=1]]BoxBoard-II showed 0 organic assigns across arms[[/dispute]]',
    }
    br, ws, calls = make(tmp_path, lambda k, p, n: script[n])
    human_says(br, ws, "<@UCLAUDE> a")
    human_says(br, ws, "<@UGPT> b")
    assert "already has this title" in ws.msgs["C1"][-1]["text"]
    human_says(br, ws, "<@UGROK> c")
    e = br.kb.get(1)
    assert e["status"] == "disputed" and "DISPUTE by Grok" in e["evidence"]
    actions = [r["action"] for r in br.ledger.q("SELECT action FROM knowledge_log ORDER BY id")]
    assert actions == ["save", "dispute"]


def test_update_of_verified_entry_resets_status_and_keeps_history(tmp_path):
    script = {
        1: '[[save kind=decision title="Use SQLite"]]v1[[/save]]',
        2: '[[verify id=1]]benchmarked[[/verify]]',
        3: '[[update id=1 reason="new data"]]v2[[/update]]',
    }
    br, ws, calls = make(tmp_path, lambda k, p, n: script[n])
    human_says(br, ws, "<@UCLAUDE> a")
    human_says(br, ws, "<@UGPT> b")
    human_says(br, ws, "<@UCLAUDE> c")
    e = br.kb.get(1)
    assert (e["body"], e["revision"], e["status"]) == ("v2", 2, "unverified")
    snaps = br.ledger.q("SELECT snapshot FROM knowledge_log WHERE entry=1 ORDER BY id")
    assert '"body": "v1"' in snaps[0]["snapshot"]


def test_owner_can_save_and_verify_from_slack(tmp_path):
    br, ws, calls = make(tmp_path, lambda k, p, n: "ok")
    human_says(br, ws, '[[save kind=decision title="$0 rule"]]No paid APIs.[[/save]]')
    assert "Saved #1" in ws.msgs["C1"][-1]["text"]
    human_says(br, ws, "[[verify id=1]]owner decision[[/verify]]")
    assert br.kb.get(1)["status"] == "verified"
    assert calls == []  # no model was run for this


# ---------------------------------------------------------------- real subprocess runner

def test_run_cli_stdin_outfile_and_json(tmp_path):
    py = sys.executable
    p = B.Participant(name="T", command=[py, "-c", "import sys;print(sys.stdin.read().upper())"])
    assert B.run_cli(p, "hello", tmp_path).text == "HELLO"
    p = B.Participant(name="T", input="arg", command=[py, "-c", "import sys;print(sys.argv[1][::-1])", "{prompt}"])
    assert B.run_cli(p, "abc", tmp_path).text == "cba"
    p = B.Participant(name="T", command=[py, "-c",
                      "import sys;open(sys.argv[1],'w').write('from file');print('noise')", "{out_file}"])
    assert B.run_cli(p, "x", tmp_path).text == "from file"
    p = B.Participant(name="T", output="claude-json", command=[py, "-c",
                      "import json;print(json.dumps({'result':'hi','usage':{'input_tokens':5,'output_tokens':2},"
                      "'total_cost_usd':0.01}))"])
    r = B.run_cli(p, "x", tmp_path)
    assert (r.text, r.input_tokens, r.output_tokens, r.cost_usd) == ("hi", 5, 2, 0.01)


def test_run_cli_errors(tmp_path):
    with pytest.raises(RuntimeError, match="not installed"):
        B.run_cli(B.Participant(name="T", command=["no-such-cli-xyz"]), "x", tmp_path)
    with pytest.raises(RuntimeError, match="no reply"):
        B.run_cli(B.Participant(name="T", command=[sys.executable, "-c", "import sys;sys.stderr.write('auth expired');sys.exit(1)"]),
                  "x", tmp_path)


def test_run_cli_hides_slack_tokens(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_SLACK_BOT_TOKEN", "xoxb-secret")
    p = B.Participant(name="T", command=[sys.executable, "-c",
                      "import os;print(os.environ.get('CLAUDE_SLACK_BOT_TOKEN','hidden'))"])
    assert B.run_cli(p, "x", tmp_path).text == "hidden"


def test_split_long_reply():
    chunks = B.split("line\n" * 2000, limit=1000)
    assert all(len(c) <= 1000 for c in chunks) and "".join(chunks) == "line\n" * 2000


def test_example_config_loads():
    cfg = B.load_config(Path(__file__).resolve().parents[1] / "slack_bridge" / "bridge.example.toml")
    assert [p.name for p in cfg.participants] == ["Claude", "GPT", "Grok"]
    assert [e.name for e in cfg.externals] == ["Hermes", "Coley"]
