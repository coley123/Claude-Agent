# Colony

A persistent multi-agent coding team. Each agent has its own objective, private memory and
inbox. Agents talk over a message bus, coordinate on a shared blackboard, decide whether to
help each other, load skills when a task needs them, and keep going until a verifier says
the goal is done.

Agents also grow a shared **skill hub**: they write reusable skills and lessons, which are
verified, reviewed, ranked by real outcomes and shared with every later run.

Status: v1 (the harness) and v2 (the skill hub) are built. v3 fine-tunes a small model on
the episodes this harness records. See [Roadmap](#roadmap).

## Quick start

```bash
pip install -e ".[dev]"
```

```bash
export ANTHROPIC_API_KEY=...
```

```bash
examples/make_demo_repo.sh
```

```bash
python -m colony run examples/converge.toml
```

The demo repo is a tiny calculator library with 4 failing tests. In another terminal:

```bash
python -m colony status examples/converge.toml
```

```bash
python -m colony stop examples/converge.toml
```

When the run ends, `runs/<goal-name>/REPORT.md` says what happened, what it cost, and which
branch holds the verified work. **Nothing is merged into your main branch automatically.**

Run the harness tests (no API key needed; they use a scripted model):

```bash
python -m pytest -q
```

## How it works

```
                 goal file (TOML)
                        |
                   Supervisor ------------ budget / time / STOP file / escalations
          ______________|______________
         |              |              |
       lead           ada            brook      one thread each, own model + effort
         |              |              |
   own worktree   own worktree   own worktree   git branch colony/<run>/<agent>
         \______________|______________/
                        |
     SQLite: tasks · messages · blackboard · private notes · events · usage
                        |
          verifier (e.g. pytest) on submit -> integration branch -> REPORT.md
```

**Modes**
- `converge`: the lead splits the goal into tasks (`create_task`, naming the skills each
  needs); workers claim, do and `submit` them. Each submission is merged into an integration
  branch, and the goal verifier runs on the integrated work.
- `diverge`: every agent takes a different approach to the whole goal and posts a `claim`
  so others pick something else. The first approach that passes the verifier wins.

**Persistence**
- Only the verifier marks work done. Agents can't declare success.
- Work happens in sessions of `max_turns_per_session` turns. Then the context resets, and
  the agent continues from its private notes, the task list and the blackboard.
- Stall detection: when an agent repeats the same action with the same result, it gets a
  note telling it to change strategy, load a skill, or ask for help.
- `wait` costs nothing while idle and wakes on new messages, tasks or blackboard posts. If
  every agent is waiting, the supervisor runs the verifier and posts the output to restart
  the team. Repeated stalls are escalated.
- Crashes and restarts resume from the SQLite state and worktrees.

**Helping**: each agent has a `cooperation` weight (0 = own objective only, 1 = team first)
that tells it how to weigh `request_help` messages against its own work.

**Fluid skills**: skills are folders with a `SKILL.md`. Agents see a one-line index and
`load_skill` on demand. A task can name skills, and claiming the task loads them. Starter
skills in `skills/` seed an empty hub: `repo-orientation`, `debug-failing-test`,
`test-first-change`, `ask-for-help`.

## The skill hub

The hub (default `hub/`, set with `--hub`) outlives runs. Point several colonies at the same
hub and they all learn from each other.

```
hub/
  skills/<name>/      published skills: SKILL.md, scripts, tests, meta.json (version, author)
  drafts/             every proposal, kept for audit
  archive/<name>/     earlier versions and retired skills
  hub.db              usage stats, outcomes, lessons, pipeline log
```

**Growing it.** Agents call `propose_skill` whenever they find something reusable. After a
run ends with the goal verified (or the time limit), each agent gets a reflection session
limited to read-only and hub tools: it reviews its notes and actions, then proposes skills,
improves skills that misled it, and records lessons. `reflection_budget_usd` caps that spend.

**Publishing pipeline.** A proposal is published only if it passes all three:
1. a safety and format scan: name, size and path rules, plus the same command denylist the
   agents run under;
2. its own `test_command`, run in a clean temporary copy of the skill folder;
3. a reviewer model (`reviewer_model`) that rejects one-off notes, unsafe or test-weakening
   advice, duplicates, and revisions that aren't better than the current version.

A proposal under an existing name is a revision: the old version moves to `archive/` and
the new one starts with fresh stats.

**Ranking.** Loading a skill (directly, by claiming a task that names it, or by preloading)
credits it with that agent's next outcome: success when its work is accepted or verified,
failure when it releases the task or the run ends unverified. The score is a smoothed
success rate, `(successes + 1) / (outcomes + 2)`. The index lists skills best-first. After
3 outcomes, a score below 0.4 flags the skill for revision. After 5 outcomes, a score below
0.2 retires it to `archive/`.

**Lessons.** `record_lesson` stores short "works / fails / tip" notes. Every session's
briefing includes the lessons most relevant to the goal, and `search_hub` searches skills
and lessons together. Search is keyword-based for now.

Inspect the hub:

```bash
python -m colony hub skills
```

```bash
python -m colony hub lessons
```

```bash
python -m colony hub log
```

## Guardrails

- Each agent edits only its own worktree; paths outside it and `.git` are refused.
- Command denylist: `git push`, `git remote`, `sudo`, destructive system commands. Extend it
  with `extra_deny` in the goal file. A blocked action needs a human.
- Provider API keys and GitHub tokens are removed from the environment of agent commands.
- Hard stops: `budget_usd`, `max_hours`, and a `STOP` file (`colony stop`).
- `ESCALATIONS.md` in the run dir lists anything that needs a person.
- Every tool call, submission and verifier result goes to the event log.

**The command guard is not a security sandbox.** For unattended runs, use the Dockerfile:

```bash
docker build -t colony .
```

```bash
docker run --rm -it -e ANTHROPIC_API_KEY -v "$PWD/runs:/app/runs" -v "$PWD/hub:/app/hub" colony run examples/converge.toml
```

## Goal file

```toml
[goal]
name = "my-goal"
description = "What done looks like, in plain words."
repo = "path/to/git/repo"          # relative to this file, or absolute
mode = "converge"                  # or "diverge"
verify_command = "python -m pytest -q"

[[agents]]
name = "lead"
role = "lead"                      # converge mode needs exactly one lead
model = "claude-opus-5-5"
effort = "high"                    # low | medium | high | xhigh | max
skills = ["repo-orientation"]      # preloaded at start
objective = ""                     # optional personal objective
cooperation = 0.8

[guardrails]
budget_usd = 20.0
max_hours = 4.0
max_turns_per_session = 40
stall_repeat_limit = 3
extra_deny = ['\bnpm\s+publish\b']
```

Skill hub settings go under `[goal]`: `reflect = true`, `reflection_turns = 12`,
`reflection_budget_usd = 2.0`, `reviewer_model = "claude-opus-5-5"`,
`reviewer_effort = "high"`.

Models and list prices (USD per million input/output tokens) used for the budget estimate:
`claude-opus-5-5` $4/$20, `claude-sonnet-5-5` $2/$10, `claude-haiku-5-5` $0.10/$0.50,
`claude-fable-5-2` $8/$40. The budget is an estimate; check real spend in the Claude Console.

Refusal fallbacks are on: if a model declines a request, the API retries it on a fallback
model within the same call. Remove `fallbacks` in `colony/backend.py` to turn this off.

## Data for later training

- `runs/<goal>/transcripts/` holds the full conversation of every agent session.
- `python -m colony export <goal file>` writes the event log as `episodes.jsonl`.

These are the raw material for v2's skill hub and v3's fine-tuning. Check your model
provider's terms before you use its outputs to train another model.

## Layout

```
colony/
  config.py      goal file, agent specs, prices, default denylist
  store.py       SQLite state: tasks, messages, blackboard, notes, events, usage
  backend.py     model backends (Claude now; add a local model here for v3)
  workspace.py   git worktrees, path confinement, command guard
  hub.py         skill hub: proposals, scan, tests, reviewer, ranking, lessons, search
  tools.py       the 23 tools agents can call
  agent.py       system prompt, sessions, stall detection, transcripts
  supervisor.py  threads, verification, integration, waiting, stop conditions, report
  cli.py         run / status / stop / export
skills/          starter skills that seed a new hub
examples/        demo project template and two goal files
tests/           harness tests with a scripted model
```

## Roadmap

- **v2: skill hub (done).** Agents propose skills and lessons. Proposals are scanned,
  tested and reviewed, then ranked by real outcomes, revised and retired.
  Next steps here: embedding search, a held-out benchmark that gates publishing.
- **v3: own model.** Fine-tune a small model on successful episodes (SFT), then multi-turn
  RL with verifier rewards. A regression benchmark gates every new skill set and model.
