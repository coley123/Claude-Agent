"""Skill library: folders with a SKILL.md (frontmatter name/description + instructions).

Agents see only the one-line index and load full skills on demand, so the library can grow
without filling every agent's context. In v2 agents will author skills into this library.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path


def _parse(path: Path) -> Skill | None:
    text = path.read_text()
    if not text.startswith("---"):
        return None
    _, front, body = text.split("---", 2)
    meta = {}
    for line in front.strip().splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    if "name" not in meta:
        return None
    return Skill(meta["name"], meta.get("description", ""), body.strip(), path.parent)


class SkillLibrary:
    def __init__(self, root: Path):
        self.root = root

    def all(self) -> dict[str, Skill]:
        skills = {}
        for f in sorted(self.root.glob("*/SKILL.md")):
            s = _parse(f)
            if s:
                skills[s.name] = s
        return skills

    def index(self) -> str:
        return "\n".join(f"- {s.name}: {s.description}" for s in self.all().values()) or "(none)"

    def load(self, name: str) -> str:
        s = self.all().get(name)
        if not s:
            raise KeyError(f"no skill named {name!r}; available: {', '.join(self.all())}")
        extras = [p.relative_to(s.path).as_posix() for p in s.path.rglob("*")
                  if p.is_file() and p.name != "SKILL.md"]
        tail = f"\n\nFiles in this skill (under {s.path}): {', '.join(extras)}" if extras else ""
        return f"# Skill: {s.name}\n\n{s.body}{tail}"
