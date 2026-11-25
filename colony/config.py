"""Goal and agent configuration, loaded from a TOML goal file."""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# List prices in USD per million tokens (input, output). Used for the budget cap.
PRICES = {
    "claude-fable-5-2": (8.0, 40.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}

DEFAULT_DENY = [
    r"\bgit\s+push\b",
    r"\bgit\s+remote\b",
    r"\bgit\s+config\s+--global\b",
    r"\bsudo\b",
    r"\brm\s+-rf\s+/(\s|$)",
    r"\bmkfs\b",
    r"\bshutdown\b|\breboot\b",
    r":\(\)\s*\{",  # fork bomb
]


@dataclass
class AgentSpec:
    name: str
    role: str = "worker"  # "lead" or "worker"
    model: str = "claude-opus-5-5"
    effort: str = "high"
    skills: list[str] = field(default_factory=list)  # skills preloaded at start
    objective: str = ""  # this agent's own objective, on top of the shared goal
    cooperation: float = 0.5  # 0 = only own objective, 1 = only team success


@dataclass
class Goal:
    name: str
    description: str
    repo: Path
    mode: str = "converge"  # "converge" (divide the work) or "diverge" (competing approaches)
    verify_command: str = "python -m pytest -q"
    verify_timeout: int = 600
    agents: list[AgentSpec] = field(default_factory=list)
    # Guardrails
    budget_usd: float = 20.0
    max_hours: float = 4.0
    max_turns_per_session: int = 40
    command_timeout: int = 300
    stall_repeat_limit: int = 3
    deny_patterns: list[str] = field(default_factory=lambda: list(DEFAULT_DENY))
    # Skill hub (v2)
    reflect: bool = True  # after the run, agents turn what they learned into skills/lessons
    reflection_turns: int = 12
    reflection_budget_usd: float = 2.0  # extra spend allowed for reflection after the run
    reviewer_model: str = "claude-opus-5-5"
    reviewer_effort: str = "high"


def load_goal(path: str | Path) -> Goal:
    path = Path(path)
    data = tomllib.loads(path.read_text())
    g = data["goal"]
    agents = [AgentSpec(**a) for a in data.get("agents", [])]
    if not agents:
        raise ValueError("goal file needs at least one [[agents]] entry")
    if len({a.name for a in agents}) != len(agents):
        raise ValueError("agent names must be unique")
    guard = data.get("guardrails", {})
    repo = Path(g["repo"])
    if not repo.is_absolute():
        repo = (path.parent / repo).resolve()
    goal = Goal(
        name=g["name"],
        description=g["description"],
        repo=repo,
        mode=g.get("mode", "converge"),
        verify_command=g.get("verify_command", "python -m pytest -q"),
        verify_timeout=g.get("verify_timeout", 600),
        agents=agents,
        **{k: g[k] for k in ("reflect", "reflection_turns", "reflection_budget_usd",
                                  "reviewer_model", "reviewer_effort")
           if k in g},
        **{k: v for k, v in guard.items() if k != "extra_deny"},
    )
    goal.deny_patterns += guard.get("extra_deny", [])
    if goal.mode not in ("converge", "diverge"):
        raise ValueError("mode must be 'converge' or 'diverge'")
    if goal.mode == "converge" and not any(a.role == "lead" for a in agents):
        raise ValueError("converge mode needs one agent with role = 'lead'")
    return goal
