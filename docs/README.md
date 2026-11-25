# Testing guides

Follow these in order. Each assumes the previous ones work.

| Version | What it adds | Guide | Needs |
|---|---|---|---|
| v1 | Multi-agent harness: memory, messaging, tasks, persistence, guardrails | [v1-testing.md](v1-testing.md) | Anthropic API key |
| v2 | Shared skill hub: proposals, review, ranking, lessons, reflection | [v2-testing.md](v2-testing.md) | Anthropic API key |
| v3 | Your own model: local agents, benchmark gate, data export, training | [v3-testing.md](v3-testing.md) | GPU machine for steps 2 onward |
| v4 | Self-improvement loop: goal queue, export, train, gate, promote | [v4-testing.md](v4-testing.md) | GPU machine for step 4 |

Every guide starts with offline tests that need no key and cost nothing:

```bash
python -m pytest -q
```

When something fails, the most useful things to share are the run's `REPORT.md`, the
output of `python -m colony status <goal file>`, the last transcripts in
`runs/<name>/transcripts/`, and for v4 `loop/loop.log`.
