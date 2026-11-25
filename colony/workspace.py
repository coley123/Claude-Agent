"""Per-agent git worktrees, path confinement, and the command guard.

This is NOT a security sandbox. It stops honest mistakes (writing outside the workspace,
pushing to a remote). Run the whole colony inside a container (see Dockerfile) for real
isolation.
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path

GIT_LOCK = threading.Lock()  # worktree/merge operations on one repo must not interleave
MAX_OUTPUT = 20_000

# Agents' commands never see provider credentials.
_SECRET_ENV = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "OPENAI_API_KEY", "GH_TOKEN",
               "GITHUB_TOKEN")


def clean_env() -> dict:
    return {k: v for k, v in os.environ.items() if k not in _SECRET_ENV}


def truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n... [{len(text) - limit} chars truncated] ...\n{text[-half:]}"


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=check,
                          env=clean_env())


def run_command(command: str, cwd: Path, timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, text=True,
                           timeout=timeout, env=clean_env())
        out = p.stdout + (f"\n[stderr]\n{p.stderr}" if p.stderr.strip() else "")
        return p.returncode, truncate(out.strip() or "(no output)")
    except subprocess.TimeoutExpired:
        return 124, f"command timed out after {timeout}s"


class CommandGuard:
    def __init__(self, patterns: list[str]):
        self.patterns = [re.compile(p) for p in patterns]

    def check(self, command: str) -> str | None:
        for p in self.patterns:
            if p.search(command):
                return f"blocked by guardrail ({p.pattern}); this needs human approval"
        return None


def safe_path(root: Path, rel: str) -> Path:
    p = (root / rel).resolve()
    if p != root.resolve() and root.resolve() not in p.parents:
        raise ValueError(f"path escapes the workspace: {rel}")
    if ".git" in p.relative_to(root.resolve()).parts:
        raise ValueError("editing .git directly is not allowed")
    return p


class Workspaces:
    """One integration worktree plus one worktree per agent, all on run-scoped branches."""

    def __init__(self, repo: Path, run_dir: Path, run_id: str):
        self.repo = repo
        self.root = run_dir / "workspaces"
        self.prefix = f"colony/{run_id}"
        self.integration_branch = f"{self.prefix}/integration"
        self.integration = self.root / "_integration"

    def branch(self, agent: str) -> str:
        return f"{self.prefix}/{agent}"

    def path(self, agent: str) -> Path:
        return self.root / agent

    def setup(self, agents: list[str]) -> None:
        if git(self.repo, "rev-parse", "--git-dir", check=False).returncode != 0:
            raise RuntimeError(f"{self.repo} is not a git repository")
        if git(self.repo, "rev-parse", "HEAD", check=False).returncode != 0:
            raise RuntimeError(f"{self.repo} has no commits yet")
        self.root.mkdir(parents=True, exist_ok=True)
        with GIT_LOCK:
            self._add(self.integration, self.integration_branch, "HEAD")
            for a in agents:
                self._add(self.path(a), self.branch(a), self.integration_branch)

    def _add(self, path: Path, branch: str, start: str) -> None:
        if path.exists():
            return  # resuming
        exists = git(self.repo, "rev-parse", "--verify", branch, check=False).returncode == 0
        if exists:
            git(self.repo, "worktree", "add", str(path), branch)
        else:
            git(self.repo, "worktree", "add", "-b", branch, str(path), start)
        git(path, "config", "user.name", "colony-agent")
        git(path, "config", "user.email", "colony-agent@localhost")

    def commit_all(self, agent: str, message: str) -> str:
        ws = self.path(agent)
        with GIT_LOCK:
            git(ws, "add", "-A")
            if git(ws, "diff", "--cached", "--quiet", check=False).returncode != 0:
                git(ws, "commit", "-q", "-m", message)
            return git(ws, "rev-parse", "HEAD").stdout.strip()

    def merge_into_integration(self, agent: str) -> tuple[bool, str]:
        with GIT_LOCK:
            p = git(self.integration, "merge", "--no-ff", "--no-edit", self.branch(agent),
                    check=False)
            if p.returncode != 0:
                git(self.integration, "merge", "--abort", check=False)
                return False, truncate(p.stdout + p.stderr, 4000)
            return True, "merged"

    def sync_from_integration(self, agent: str) -> tuple[bool, str]:
        ws = self.path(agent)
        with GIT_LOCK:
            git(ws, "add", "-A")
            if git(ws, "diff", "--cached", "--quiet", check=False).returncode != 0:
                git(ws, "commit", "-q", "-m", "wip before sync")
            p = git(ws, "merge", "--no-edit", self.integration_branch, check=False)
            if p.returncode != 0:
                return False, ("merge conflicts - resolve them in your workspace, then commit:\n"
                               + truncate(p.stdout + p.stderr, 4000))
            return True, truncate(p.stdout.strip() or "up to date", 4000)
