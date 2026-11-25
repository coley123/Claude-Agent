# v4 testing guide: the self-improvement loop

What v4 adds: `colony serve`, a process that runs indefinitely.

1. It takes goal files from `loop/queue/`, runs each one on the shared hub, and files them
   under `loop/done/` or `loop/failed/` with a copy of the run report.
2. Every `export_every` finished goals it exports the training data.
3. With training enabled and enough new successful sessions, it runs an improvement cycle:
   train a candidate, deploy it, benchmark it, and promote it only if it passes the gate
   against the current baseline. Otherwise it rolls the candidate back.

Guardrails: a daily spending cap, a pause after repeated goal failures (`loop/PAUSED`; you
delete it to resume), a stop file (`loop/STOP`), and a decision log (`loop/loop.log`).

**Status, honestly:** queueing, filing, export, pausing, the improvement cycle and the gate
are covered by offline tests, with shell commands standing in for training. The hook
scripts in `scripts/` are templates for a single machine running vLLM; edit them for your
setup. Do the v1 to v3 guides first.

## 1. Offline tests (free)

```bash
python -m pytest -q tests/test_loop.py
```

## 2. Queue processing without training

`examples/loop.toml` ships with `[train] enabled = false`. Start the loop:

```bash
python -m colony serve examples/loop.toml
```

In a second terminal, queue two goals. Goal files resolve `repo` relative to their own
location, so use absolute paths when you copy them into the queue:

```bash
examples/make_demo_repo.sh
```

```bash
sed "s#repo = \"calc-demo\"#repo = \"$PWD/examples/calc-demo\"#" examples/diverge.toml > loop/queue/01-calc.toml
```

```bash
python -m colony loop-status examples/loop.toml
```

Check that:
- [ ] the goal moves from `queue/` to `running/` to `done/`, and `done/01-calc.report.md`
      appears;
- [ ] `loop/loop.log` has `goal_start` and `goal_end` lines with the cost;
- [ ] the loop keeps polling when the queue is empty, and stops cleanly when you create
      the stop file:

```bash
touch loop/STOP
```

Delete `loop/STOP` before starting the loop again.

## 3. Guardrails

- [ ] **Pause.** Queue 3 goals that cannot succeed (for example, a `verify_command` of
      `false` and `max_hours = 0.01` under `[guardrails]`). After 3 failures in a row,
      `loop/PAUSED` appears and no further goals start. Delete it to resume.
- [ ] **Daily budget.** Set `daily_budget_usd = 0.01` and queue a goal: after the first
      goal the loop reports `daily budget reached` and waits for the next day.
- [ ] **Export.** With `export_every = 1`, `data/sft.jsonl` is rewritten after every goal.

## 4. Full improvement cycle (GPU machine)

Prerequisites: v3 steps 2 and 3 work on this machine (vLLM serves models, and
`results/baseline-local.json` exists for your current model).

1. Edit `scripts/deploy_candidate.sh`, `promote_candidate.sh` and `rollback_candidate.sh`
   for your setup. Run each by hand once to check it.
2. In `examples/loop.toml`, set `enabled = true` and adjust `command`, `team`, `baseline`
   and `min_new_sessions`. For a first test, use `min_new_sessions = 1`, `export_every = 1`
   and `tasks = ["calc"]`.
3. Queue a goal and watch `loop/loop.log`. A cycle logs `hook_start` and `hook_end` for
   train, deploy, then promote or rollback, followed by `cycle_end`.

Check that:
- [ ] `checkpoints/cycle-1/` holds the trained adapter;
- [ ] `loop/bench/cycle-1.json` holds the candidate's benchmark;
- [ ] `loop/hooks/1-*.log` hold each command's output;
- [ ] a promoted candidate replaced `results/baseline-local.json`; a rejected one did not.

## 5. Running it for real

- Run it inside Docker or on a dedicated machine. Agents execute code from the goals you
  queue.
- Review `loop/failed/*.report.md` and `ESCALATIONS.md` files regularly.
- Back up `hub/`, `data/`, `results/` and `checkpoints/`. Together they are what the system
  has learned.
- Grow `benchmarks/` as the model improves. If the candidate passes everything, the gate
  can no longer tell a better model from a worse one.

## Known limits

- Goals come only from the queue. Agents cannot add goals for themselves yet.
- Training runs in the same process's turn: while a candidate trains, no goals run.
- Promotion is all-or-nothing per cycle. There is no gradual rollout or A/B comparison
  across live goals.
