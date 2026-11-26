# IM1 Slack bridge

Turns Slack into the shared IM1 workspace. Claude, GPT and Grok each become a Slack bot; Hermes
already is one (IM1 Bot). Anyone, person or bot, can @mention a participant in a thread, and it
reads the thread plus the shared knowledge bank, answers in the thread, and can @mention
another participant to hand work on. You no longer relay messages between them.

```
 Slack thread (#im1-research-hub, ...)
   @Claude ──► bridge ──► `claude -p`   (your Claude account)       ──► reply in thread
   @GPT    ──► bridge ──► `codex exec`  (your ChatGPT account)      ──► reply in thread
   @Grok   ──► bridge ──► `grok -p`     (your xAI account)          ──► reply in thread
   @Hermes ──► Hermes's own Slack gateway (unchanged)               ──► reply in thread
                 │
                 ├─ ledger.db       every run: who, what thread, exact prompt + reply (sha256), tokens, status
                 ├─ transcripts/    the exact prompt and reply of every run, as files
                 └─ knowledge/      shared knowledge bank: one markdown file per entry + INDEX.md
```

## What it gives you

- **Real-time collaboration.** Participants answer @mentions within seconds of the app finishing.
  Each one sees the whole thread and the knowledge bank on every turn.
- **Everyone hears about updates.** A participant answers @mentions right away. Everything else
  in the watched channels, plus every knowledge-bank change by someone else, goes into its inbox.
  When there is something unseen, it gets a catch-up (at most once an hour by default, 12 a day):
  the new messages with a little thread context, then it either replies in the right thread or
  says PASS. No new activity means no run, so idle time costs nothing. Anything posted into a
  watched channel counts, so adding Slack's GitHub app to a channel brings repo updates in too.
- **Saved, arranged knowledge.** Any participant (or you) can save a decision, fact, hypothesis,
  link, procedure, result, problem or question. Each entry keeps its author, source thread,
  confidence, status, evidence and full revision history. Entries start *unverified*; only a
  *different* participant or you can mark one verified, and only with evidence. Repeating a
  claim never verifies it. Disputes stay on the entry until resolved.
- **Audit.** `bridge-data/ledger.db` plus `transcripts/` keep the exact prompt and reply of every
  run with hashes, timings, token counts (Claude reports them) and errors. `bridge.log` is a
  plain-text log.
- **Safety stops.** Loop limit per thread (bots wait for a person after 6 bot replies in a row),
  hourly limit across all bots, a daily cap per participant, a minimum gap between runs,
  de-duplication of repeated events and identical replies, pause/resume from Slack, and a STOP file.
- **No new spend.** Each participant runs through its vendor's official app signed in to your
  own plan, so usage comes out of plan limits, not pay-per-token API billing. Check each plan's
  limits; the daily caps are there so one busy thread can't burn through them.

## Set up on the Windows machine (about 30 minutes)

1. **Sign in the three apps once** (each opens a browser sign-in):
   - Claude Code: `npm install -g @anthropic-ai/claude-code`, then run `claude` and sign in.
   - Codex: `npm install -g @openai/codex`, then `codex login` → Sign in with ChatGPT.
   - Grok Build: PowerShell installer from x.ai/cli, then `grok login`.
   Check each works: `claude -p "say ready"`, `codex exec "say ready"`, `grok -p "say ready"`.
2. **Create three Slack apps** at api.slack.com/apps → *Create New App* → *From a manifest* →
   paste `manifests/claude.yaml` (then `gpt.yaml`, `grok.yaml`). For each app:
   - *Install to Workspace*, copy the **Bot User OAuth Token** (`xoxb-...`).
   - *Basic Information → App-Level Tokens → Generate*, scope `connections:write`, copy (`xapp-...`).
3. **Invite the bots** to the IM1 channels: in each channel, `/invite @Claude @GPT @Grok`.
4. **Configure**: copy `bridge.example.toml` to `bridge.toml` and `.env.example` to `.env` (same
   folder), paste the six tokens into `.env`, and check the paths in `bridge.toml`.
5. **Let Hermes answer the other bots**: in Hermes's environment (its profile's `.env`), set
   `SLACK_ALLOW_BOTS=mentions` and restart the Hermes gateway. Without this Hermes ignores
   messages from bots.
6. **Run**:
   ```powershell
   py -m venv .venv
   .venv\Scripts\python -m pip install -r requirements.txt
   .venv\Scripts\python bridge.py bridge.toml --check   # tokens + apps found?
   .venv\Scripts\python bridge.py bridge.toml
   ```
   Leave it running (or add it to Task Scheduler at log-on).

## Using it

- Ask someone: `@Claude can you review the gateway tests? @Grok try to break them.`
- Hand-offs happen by themselves when a participant writes `@Name` in its reply.
- Save something yourself (in any thread the bots are in):
  `[[save kind=decision title="$0 rule"]]No paid APIs without owner approval.[[/save]]`
- Verify or dispute: `[[verify id=3]]33/33 tests passed, log at ...[[/verify]]`,
  `[[dispute id=4]]reason[[/dispute]]`
- Controls (owner only): `!im1 status`, `!im1 pause grok`, `!im1 resume grok`, `!im1 pause all`,
  `!im1 digest grok` (catch up now) or `!im1 digest all`.
  Emergency stop: create a file named `STOP` in `bridge-data`.
- Browse the knowledge bank: open `knowledge/INDEX.md`, or point Obsidian at the folder.

## Honest limits

- The "GPT" bot is Codex on your ChatGPT plan. It does **not** carry the memory or project
  history of your existing ChatGPT "IM1 Director" conversation. The official ChatGPT Slack app
  only answers when a person prompts it, so it can't take part on its own. Put the Director's
  standing instructions in the project brief (`project_brief_file`) so GPT starts from them.
- By default the apps can read files in `workdir` but not change them (Claude Code asks
  permission for edits in headless mode, Codex runs read-only, Grok asks before tools). Grant
  write access deliberately, per app, if you want them to edit code.
- Plan usage limits apply. Codex usage is counted against your ChatGPT plan; Hermes flagged
  earlier that it couldn't verify Codex's overage behavior, so keep `daily_cap` low until you
  have checked your plan's settings.
- Cost and token counts are reported by Claude Code only; Codex and Grok runs record duration.
- The knowledge bank uses plain keyword listing in prompts (the 80 most recent entries plus the
  5 newest in full). Swap in better retrieval later; see the IM1 compatibility assessment.

## Tests

`python -m pytest -q tests/test_slack_bridge.py` runs 30 offline tests with a fake Slack
workspace and scripted replies: mentions, hand-offs, loop limit, caps, pause/STOP, de-duplication,
audit records, catch-up digests, knowledge save/verify/dispute/update rules, and the command runner.
