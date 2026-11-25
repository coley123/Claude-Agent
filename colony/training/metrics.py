"""Per-run metrics and the reward used for reinforcement learning and benchmarks."""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass

from ..store import Store


@dataclass
class RunMetrics:
    verified: bool
    cost_usd: float
    input_tokens: int
    output_tokens: int
    tool_calls: int
    tool_errors: int
    rejections: int  # submissions the verifier rejected
    stalls: int
    guard_blocks: int
    help_requests: int
    elapsed_s: float

    def to_dict(self) -> dict:
        return asdict(self)


def metrics_from_store(store: Store, elapsed_s: float = 0.0) -> RunMetrics:
    events = store.events()
    tools = [e for e in events if e["kind"] == "tool"]
    usage = store.usage()
    return RunMetrics(
        verified=bool(store.get_meta("goal_done")),
        cost_usd=store.total_cost(),
        input_tokens=sum(u["input_tokens"] for u in usage),
        output_tokens=sum(u["output_tokens"] for u in usage),
        tool_calls=len(tools),
        tool_errors=sum(1 for e in tools if e["data"]["error"]),
        rejections=sum(1 for e in tools if e["data"]["name"] == "submit"
                       and str(e["data"]["output"]).startswith("REJECTED")),
        stalls=sum(1 for e in events if e["kind"] == "stall"),
        guard_blocks=sum(1 for e in events if e["kind"] == "guard_block"),
        help_requests=sum(1 for e in tools if e["data"]["name"] == "send_message"
                          and '"request_help"' in e["data"]["input"]),
        elapsed_s=elapsed_s,
    )


@dataclass
class RewardWeights:
    """Success dominates; the rest shapes *how* the team gets there.

    Ramp `tokens` up slowly during training: a large efficiency penalty early on teaches
    agents to stop communicating and verifying.
    """
    success: float = 1.0
    tokens: float = 0.2  # penalty at `token_budget` output tokens, linear below it
    token_budget: int = 200_000
    rejection: float = 0.05
    stall: float = 0.02
    guard_block: float = 0.1
    tool_error: float = 0.005


def reward(m: RunMetrics, w: RewardWeights | None = None) -> float:
    w = w or RewardWeights()
    r = w.success if m.verified else 0.0
    r -= w.tokens * min(1.0, m.output_tokens / w.token_budget)
    r -= w.rejection * m.rejections
    r -= w.stall * m.stalls
    r -= w.guard_block * m.guard_blocks
    r -= w.tool_error * m.tool_errors
    return r


def group_advantages(rewards: list[float]) -> list[float]:
    """GRPO-style advantages: each rollout's reward relative to its group of rollouts on
    the same task. A group where every rollout scored the same carries no signal."""
    if len(rewards) < 2:
        return [0.0] * len(rewards)
    mean = statistics.fmean(rewards)
    sd = statistics.pstdev(rewards)
    if sd < 1e-8:
        return [0.0] * len(rewards)
    return [(r - mean) / sd for r in rewards]
