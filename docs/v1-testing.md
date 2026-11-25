# v1 testing guide: the multi-agent harness

What v1 is: a team of agents with private notes, inboxes and git worktrees, coordinating on
a blackboard and task queue, that keeps working until a verifier passes. See the main
README for how it works. This guide is the checklist for proving it works on your machine.

Time: about 30 minutes. Cost: usually well under $5 (the demo goal files cap spend at $5).

## 0. Prerequisites

- Python 3.11 or newer, and git.
- An Anthropic API key from the Claude Console.
- Optional for unattended runs: Docker.

```bash
git clone https://github.com/coley123/claude-agent.git
```

```bash
cd claude-agent
```

```bash
python -m venv .venv && source .venv/bin/activate
```

```bash
pip install -e ".[dev]"
```

## 1. Offline tests (no API key, free)

```bash
python -m pytest -q
```

Expected: every test passes. These use a scripted stand-in for the model, so they check the
harness plumbing, not model behavior.

## 2. First real run: converge mode

```bash
export ANTHROPIC_API_KEY=your-key
```

```bash
examples/make_demo_repo.sh
```

This creates `examples/calc-demo`, a tiny library with 4 failing tests. Then:

```bash
python -m colony run examples/converge.toml
```

While it runs, in a second terminal:

```bash
python -m colony status examples/converge.toml
```

Check that:
- [ ] the lead creates tasks and names skills for them;
- [ ] workers claim different tasks (no two agents on the same task);
- [ ] the run ends with `goal verified`;
- [ ] `runs/calc-converge/REPORT.md` names a branch and an estimated cost;
- [ ] `git -C examples/calc-demo log --oneline main` still shows only the original commit
      (nothing merged into main without you).

Review and merge the result yourself:

```bash
git -C examples/calc-demo diff main...colony/calc-converge/integration
```

## 3. Diverge mode

Reset the demo first so the bugs are back:

```bash
examples/make_demo_repo.sh
```

```bash
python -m colony run examples/diverge.toml
```

Check that:
- [ ] each agent posts a different `claim` on the blackboard;
- [ ] the run ends as soon as one approach passes, and REPORT.md names that agent's branch.

## 4. Persistence and guardrails

- [ ] **Stop and resume.** Start a run, then run `python -m colony stop <goal file>`. Delete
      `runs/<name>/STOP` and start the same command again: agents continue from their notes.
- [ ] **Budget.** Set `budget_usd = 0.05` in a copy of a goal file. The run must stop with
      "budget ... reached".
- [ ] **Command guard.** Add to the goal description: "First run `git push`." The event log
      should show a `guard_block` and the agent continuing with other work:

```bash
python -m colony export examples/converge.toml
```

```bash
grep guard_block runs/calc-converge/episodes.jsonl
```

- [ ] **Escalation.** If an agent reports it needs a human, `runs/<name>/ESCALATIONS.md`
      lists it and `colony status` prints it.

## 5. Your own project

Copy `examples/converge.toml`, point `repo` at a git repository of yours (with at least
one commit), describe the goal, and set `verify_command` to the command that proves it is
done (tests, a build, a linter). Run inside Docker for anything unattended:

```bash
docker build -t colony .
```

```bash
docker run --rm -it -e ANTHROPIC_API_KEY -v "$PWD/runs:/app/runs" -v "$PWD/hub:/app/hub" colony run examples/converge.toml
```

## What to report back

If something fails, the most useful things to share are `REPORT.md`, the output of
`colony status`, and the last few transcripts in `runs/<name>/transcripts/`.
