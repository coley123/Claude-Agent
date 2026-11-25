"""Collect GRPO-style rollouts: K attempts per benchmark task, scored relative to each other.

Each attempt is a full colony run (`bench.run_task`) with the policy being trained, usually
a local model served by vLLM (`model = "local:policy"`). Every work session of every agent
in an attempt becomes one training sample carrying that attempt's group advantage.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from ..agent import REFLECTION_TOOLS
from ..chatformat import to_chat, tools_to_chat
from ..tools import TOOLS
from .metrics import RewardWeights, group_advantages


def collect(tasks, team, backend, hub: Path, out: Path, k: int = 4,
            seed_skills: Path | None = None, weights: RewardWeights | None = None) -> dict:
    from ..bench import run_task

    weights = weights or RewardWeights()
    out.parent.mkdir(parents=True, exist_ok=True)
    groups, samples = [], 0
    with tempfile.TemporaryDirectory(prefix="colony-rollouts-") as tmp, out.open("w") as f:
        work = Path(tmp)
        for task in tasks:
            results = [run_task(task, team, backend, hub, seed_skills, work, i, weights)
                       for i in range(k)]
            advs = group_advantages([r["reward"] for r in results])
            groups.append({"task": task.name, "rewards": [r["reward"] for r in results],
                           "advantages": advs})
            for r, adv in zip(results, advs):
                if adv == 0.0:
                    continue  # no signal relative to the group
                tdir = work / f"{task.name}-{r['attempt']}" / "runs"
                for path in sorted(tdir.glob("*/transcripts/*.json")):
                    t = json.loads(path.read_text())
                    tools = REFLECTION_TOOLS if t.get("kind") == "reflection" else TOOLS
                    f.write(json.dumps({
                        "messages": to_chat(t["system"], t["messages"], args_as_dict=True),
                        "tools": tools_to_chat(tools),
                        "advantage": adv, "reward": r["reward"],
                        "meta": {"task": task.name, "attempt": r["attempt"],
                                 "agent": t["agent"], "session": t["session"]},
                    }) + "\n")
                    samples += 1
    return {"groups": groups, "samples": samples, "out": str(out)}
