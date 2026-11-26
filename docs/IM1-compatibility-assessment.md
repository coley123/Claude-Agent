# IM1 compatibility assessment and proof-of-concept plan

Prepared by Claude, 2026-11-25, for independent review by GPT, Grok, Hermes and the owner.
Responds to the *IM1 Research-Based Architecture Amendment*. Status labels: **VERIFIED** =
checked against a primary source or run here; **REPORTED** = from IM1 Slack posts, not re-run
by me; **HYPOTHESIS** = my judgement, needs testing.

## 1. Bottom line

1. **Communication is the blocker, and it is now solvable this week with existing parts.**
   Today Hermes (IM1 Bot) is the only participant that acts on its own. "ChatGPT" posts in Slack
   are the owner's account using the ChatGPT app, so the owner is still the relay. The new
   `slack_bridge/` (branch `slack-bridge`) makes Claude, GPT and Grok Slack participants that
   answer @mentions and @mention each other, using each vendor's official app signed in to the
   owner's plan. Hermes joins with one setting (`SLACK_ALLOW_BOTS=mentions`).
2. **Reuse, don't build: most of the memory and competency stack already exists** across
   Hermes (profiles, memory, session search, skills), Colony (`colony/hub.py`: candidate →
   tested → reviewed → published → ranked → retired skill pipeline) and IM1
   `collective-runtime` (durable task store, offers/accept/decline, hash-chained events).
3. **One hard constraint changes the Hermes plan for Claude.** Anthropic's terms say Claude
   subscription sign-ins are for Claude Code and Claude apps only; third-party harnesses
   (Hermes included) may not use them. So Claude participates through `claude -p` (Claude Code
   itself), not through a Hermes profile signed in to Anthropic. GPT and Grok can use either
   their official CLIs (default) or Hermes profiles; check each vendor's terms before using
   Hermes's OAuth sign-in for them.
4. **Defer Hindsight.** It is the strongest candidate for structured memory, but the IM1 machine
   reported only ~3.3 GB free VRAM and ~5 GB free RAM, below what GPT-OSS-20B needs, and the
   lifecycle proof of concept does not need it. Compare it against SQLite and Obsidian baselines
   in Phase 3, as the amendment says.

## 2. Component compatibility

| Component | What it gives IM1 | Verdict | Notes |
|---|---|---|---|
| **Slack** (existing workspace, 5 IM1 channels) | Shared real-time room, human-visible, already where the project lives | **REUSE NOW** | Bridge adds bots; IM1 Bot already there |
| **slack_bridge** (new, this branch) | @mention → official CLI → reply; agent hand-offs; loop/hour/day limits; pause/STOP; audit with exact prompts + hashes; shared knowledge bank with provenance and verification rules | **USE NOW** | 24 offline tests pass. Not yet run against real Slack (needs tokens on the Windows machine) |
| **Claude Code** (`claude -p`) | Claude participant on the owner's plan | **REUSE NOW** | VERIFIED: signed-in `claude -p` draws from plan limits, no extra bill (support.claude.com article 15036540). Reports tokens + cost per run |
| **Codex CLI** (`codex exec`) | GPT participant on ChatGPT plan | **REUSE NOW, capped** | Not the IM1 Director: no ChatGPT memory/project history carries over. Hermes previously could not verify Codex overage behaviour (REPORTED); keep `daily_cap` low until checked |
| **Grok Build** (`grok -p`) | Grok participant on xAI account | **REUSE NOW** | Headless `-p`, `--output-format plain`, `grok login` (docs.x.ai) |
| **Hermes gateway** (IM1 Bot) | Hermes participant in Slack | **REUSE NOW** | Set `SLACK_ALLOW_BOTS=mentions` so it answers other bots. Its own loop guard: `gateway.bot_loop_guard.max_hops` |
| **Hermes profiles / Bot Mode** | Separate config, memory, skills, credentials and history per profile under `~/.hermes/profiles/<name>/` | **REUSE for GPT/Grok/Hermes memory (Phase 3)** | Satisfies "separate namespaces; don't share one writable home". Not for Claude (terms). Bot-to-bot inside Hermes is fire-and-forget and not visible in Slack, so keep Slack as the visible channel |
| **Hermes built-in memory** | MEMORY.md (~800 tokens) + USER.md per profile; FTS5 session search | **REUSE as individual memory** | Small by design; no auto-compaction. Good for "who am I / what I learned", not for the shared bank |
| **Hermes skills** | SKILL.md (agentskills.io-compatible), `skill_manage`, write-approval staging, security scan on hub installs | **REUSE the format + approval gate** | No automated test harness in Hermes skills: pair with Colony's test + review pipeline |
| **Colony skill hub** (`colony/hub.py`) | Proposal → safety scan → own test in a clean copy → reviewer → publish; outcome-ranked; revise <0.4, retire <0.2; archive of versions | **REUSE as competency registry core** | Already does most of the staged promotion. Gaps: an "owner approved" stage, verifier must be a *different* participant, cost/savings fields |
| **Colony harness** (supervisor, worktrees, verifier) | Multi-agent coding runs with git worktrees and pytest verification | **REUSE for the PoC tasks** | Claude-API-only plus a local OpenAI-compatible backend; never run against a real model (its README). Use its benchmarks and verifier; drive agents through the bridge |
| **IM1 collective-runtime** (Windows, not on GitHub) | Durable TaskStore, typed actions, offers/accept/decline, hash-chained events, replay, private/shared knowledge rows | **KEEP as task system of record** | REPORTED 33/33 durable tests passing. Not visible to me: push it to GitHub so every participant can review it |
| **Git** | Version control for code, skills, artifacts | **REUSE** | Skills and knowledge mirror live in git; bridge `knowledge/` folder is git-friendly |
| **Obsidian** | Human-readable knowledge view | **REUSE as a viewer** | Point a vault at the bridge's `knowledge/` folder (one .md per entry + INDEX.md). Keep the DB as source of truth so two writers can't diverge |
| **MCP** | Interop for tools | **DEFER** | Hermes is an MCP client; Hindsight exposes an MCP endpoint per bank. Add only when a validated function needs to be shared across apps |
| **Hindsight** | Structured memory: world facts, experiences, observations, mental models; parallel semantic + keyword + graph + time recall; per-agent "banks"; MIT | **EVALUATE in Phase 3** | Paper (arXiv 2512.12818) reports 39% → 83.6% with an open 20B model vs full-context. Needs PostgreSQL/pgvector (or embedded) and an LLM for every retain/reflect: heavy on a machine with ~3 GB free VRAM |
| **Voyager** | Method: executable code skills, iterative prompting with execution feedback and self-verification, no fine-tuning | **ADAPT the method** | Already matched by Colony hub (skills with tests) + bridge verification rule. Use its "skill = tested code + description" discipline |
| **Letta, Generative Agents, MASkills** | (Hermes's 17:58 review) | **No change** | REPORTED: Letta shared memory cloud-oriented, no license file; Generative Agents stale; MASkills is a training loop (excluded by owner) |

## 3. Provider connectivity limits

- **ChatGPT "IM1 Director" cannot be connected as itself.** The ChatGPT Slack app answers only
  when the owner prompts it; there is no supported way for other agents to message that
  conversation, and API or Codex access does not carry its memory. Recommended: export the
  Director's standing instructions into the bridge's project brief, and treat "GPT" (Codex) as a
  new participant with that brief.
- **Claude subscription sign-in is first-party only.** Allowed: Claude Code (`claude -p`).
  Not allowed: Hermes or any other harness using Claude subscription tokens.
- **Usage limits apply to all three plans.** The bridge's per-participant daily caps and the
  hourly cap stop a runaway thread from exhausting a plan.
- **LM Studio / local models**: any OpenAI-compatible server can be added as a participant later
  (for example via Hermes with `lmstudio` provider). Blocked today by machine headroom (REPORTED).

## 4. Security

Least privilege by default: the three CLIs run read-only in `workdir` (Codex `--sandbox
read-only`; Claude Code and Grok ask before edits and headless runs can't approve). Slack tokens
are stripped from each CLI's environment. Thread text is labelled as data, and only the owner can
approve spending, permission changes or destructive actions. Knowledge entries can't be
self-verified, and repetition never upgrades a claim (memory-poisoning guard). Hermes's skill
scanner and write-approval staging cover skills. Remaining risks: prompt injection through thread
content when agents are later given write access; plan-limit exhaustion; Slack is a third-party
store for everything posted (don't post secrets).

## 5. Proof of concept: one skill's full lifecycle

Goal: demonstrate, with numbers, that a capability survives a session reset and saves resources
when reused. Uses Colony's real benchmark repos with objective pytest verifiers. Everything runs
in Slack threads through the bridge, so the audit trail is automatic.

| Step | Who | What | Evidence |
|---|---|---|---|
| 0. Baseline | GPT | Solve `benchmarks/textutils` from scratch, no skill. Record tokens, wall time, turns, pass/fail | bridge ledger + Codex `--json` usage; pytest output |
| 1. Solve | Claude | Solve `benchmarks/calc` in a scratch copy (write access granted for that copy only) | pytest 4 fail → 0 fail; `claude -p` JSON usage |
| 2. Record | Claude | Write `skills/fix-failing-pytest-lib/SKILL.md` (procedure + a `test_command`), save a `[[save kind=procedure]]` entry linking it | git commit; knowledge entry #N (unverified) |
| 3. Test | bridge/Colony hub | Run the skill's own `test_command` in a clean copy (Colony hub pipeline) | hub log: scan + test pass |
| 4. Verify | Grok | Independently re-run the skill on a fresh copy of `calc`; `[[verify id=N]]` with output | entry #N → verified by Grok (not the author) |
| 5. Approve | Owner | `[[verify id=N]]owner approved[[/verify]]` or a 👍 in thread | knowledge log |
| 6. Reset | Claude | New session (every `claude -p` run is stateless; also clear any Claude Code memory for this dir) | run IDs differ; prompt shows no prior thread |
| 7. Reuse | GPT | Solve `benchmarks/inventory` **with** the skill; Claude (post-reset) solves `textutils` with the skill | pytest; tokens/time vs step 0 |
| 8. Measure | anyone | Compare: success, tokens, wall time, turns; skill reuse count | report posted to #im1-results and saved as a `result` entry |

Pass criteria: every step's evidence exists; reuse runs pass their verifier; reuse uses fewer
tokens or less time than baseline on comparable tasks, or the report says honestly that it did
not. One task pair is an anecdote, not a result: repeat across the three benchmarks before
claiming savings.

## 6. Implementation sequence

| When | Deliverable | Effort |
|---|---|---|
| Day 1 | Bridge live in Slack: 3 Slack apps, tokens, CLIs signed in, Hermes `allow_bots`; smoke test: owner → Claude → @GPT → @Grok → @Hermes hand-off in one thread | ~1–2 h owner setup |
| Day 1–2 | Push `collective-runtime` to GitHub; project brief from the Director's instructions | ~1 h |
| Day 2–3 | Lifecycle PoC (section 5) with measurements | ~1 day |
| Week 2 | Competency registry fields on Colony hub (independent verifier, owner-approval stage, cost/savings); bridge → hub integration; Slack List or canvas status view | ~2–3 days |
| Week 2–3 | Individual memory via Hermes profiles for GPT/Grok/Hermes; Claude via per-participant memory files in its workdir | ~1–2 days |
| Week 3+ | Hindsight vs SQLite FTS vs Obsidian retrieval comparison on a fixed question set (only if headroom allows the local model, or with a free/plan route) | ~2 days |

## 7. Operating cost

$0 incremental if all three CLIs run on existing plans with overage/usage-credit billing turned
off and Hermes stays on its current route. Real limits are plan quotas: the defaults (40 replies
per participant per day, 40 per hour overall, 6 bot replies in a row per thread) are deliberately
conservative. Claude runs report token counts and a notional cost; Codex and Grok report
duration only until their JSON usage output is wired in.

## 8. Smallest working version (available now)

`slack_bridge/` + Hermes `SLACK_ALLOW_BOTS=mentions`. All four participants talk in Slack threads,
hand work to each other by @mention, read and write one shared knowledge bank with provenance and
verification rules, and every run is audited. Setup steps: `slack_bridge/README.md`.

## Sources

- Hermes memory, skills and Bot Mode docs: hermes-agent.nousresearch.com/docs (user-guide/features/memory, /skills, /bot-mode)
- Hindsight: github.com/vectorize-io/hindsight; arXiv:2512.12818
- Voyager: arXiv:2305.16291; voyager.minedojo.org
- Claude Code legal and compliance (subscription OAuth): code.claude.com/docs/en/legal-and-compliance
- Claude plan and `claude -p` usage: support.claude.com/en/articles/15036540
- Grok Build headless mode: docs.x.ai/build/cli/headless-scripting
- Codex authentication: developers.openai.com/codex/auth
- IM1 Slack: #im1-progress, #im1-research-hub, #im1-clarifications, #im1-results (2026-11-24/25)
