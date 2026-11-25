# Colony

A persistent multi-agent coding team. Each agent has its own objective, private memory and
inbox. Agents talk over a message bus, coordinate on a shared blackboard, decide whether to
help each other, load skills when a task needs them, and keep going until a verifier says
the goal is done.

This is **v1**: the harness. v2 adds agents that write skills into a shared hub; v3
fine-tunes a small model on the episodes this harness records. See [Roadmap](#roadmap).

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

**Fluid skills**: skills are folders in `skills/` with a `SKILL.md`. Agents see a one-line
index and `load_skill` on demand. A task can name skills, and claiming the task loads them.
Starter skills: `repo-orientation`, `debug-failing-test`, `test-first-change`, `ask-for-help`.

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
docker run --rm -it -e ANTHROPIC_API_KEY -v "$PWD/runs:/app/runs" colony run examples/converge.toml
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
  tools.py       the 20 tools agents can call
  agent.py       system prompt, sessions, stall detection, transcripts
  supervisor.py  threads, verification, integration, waiting, stop conditions, report
  cli.py         run / status / stop / export
skills/          starter skill library
examples/        demo project template and two goal files
tests/           harness tests with a scripted model
```

## Roadmap

- **v2: skill hub.** After verified work, agents write reusable skills (with tests) into
  the library; skills are verified in a clean sandbox, reviewed, ranked by real outcomes,
  and refined on failure. Episodes and failure notes become searchable for new agents.
- **v3: own model.** Fine-tune a small model on successful episodes (SFT), then multi-turn
  RL with verifier rewards. A regression benchmark gates every new skill set and model.
