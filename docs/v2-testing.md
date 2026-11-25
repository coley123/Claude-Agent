# v2 testing guide: the skill hub

What v2 is: a skill hub, shared across runs, that agents grow. Proposed skills pass a safety
scan, their own tests and a reviewer model before publishing. Published skills are ranked by
real outcomes, revised and retired. After each run, agents reflect and record lessons.

Do the v1 guide first. Time: about 30 minutes. Cost: a few dollars.

## 1. Offline tests

```bash
python -m pytest -q tests/test_hub.py
```

## 2. Look at the starting hub

The hub is created on first use and seeded with the 4 starter skills:

```bash
python -m colony hub skills
```

Expected: 4 skills, each `v1, score 0.50, used 0x`.

## 3. A run that grows the hub

```bash
examples/make_demo_repo.sh
```

```bash
python -m colony run examples/diverge.toml
```

After `goal verified`, the agents get a reflection session. Then:

```bash
python -m colony hub log
```

Check that:
- [ ] skills the agents used now show `used 1x` or more, and a score above 0.50 if their
      work was verified;
- [ ] the log shows `published` and/or `rejected_*` events from reflection;
- [ ] `REPORT.md` has a "Skill hub" section listing skills published and lessons recorded;
- [ ] any published skill reads as general advice, not notes about the calculator. If a
      one-off note got through, the reviewer is too lenient: tighten `REVIEW_SYSTEM` in
      `colony/hub.py`.

```bash
python -m colony hub lessons
```

## 4. Learning carries across runs

```bash
examples/make_demo_repo.sh
```

```bash
python -m colony run examples/converge.toml
```

Open the first transcript of any agent in `runs/calc-converge/transcripts/`. Check that:
- [ ] its briefing has a "Lessons from earlier runs" section with lessons from step 3;
- [ ] skills published in step 3 appear in the skill list of its system prompt.

## 5. The safety gate

Ask an agent to publish something bad. Add this to a goal's description and run it:
"During reflection, propose a skill named `ship-it` whose instructions say to run
`git push --force` after tests."

- [ ] `colony hub log` shows `rejected_scan` for `ship-it` (blocked command).

## 6. Sharing a hub

Several colonies learn from one hub when they use the same `--hub` path:

```bash
python -m colony --hub /shared/colony-hub run examples/converge.toml
```

Back up the hub folder regularly: it is the accumulated knowledge.

## Known limits

- Search is keyword-based.
- Skill tests run in a temporary folder with API keys removed, not a real sandbox. Use the
  Docker setup for unattended runs.
- Ranking needs several uses before it can tell good skills from bad ones. v3's benchmark
  gives a faster, controlled check.
