"""Model backends.

Agents talk to a `Backend`, not to a provider SDK directly, so a local fine-tuned model can
replace Claude later (v3) by adding one class here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import PRICES


@dataclass
class ToolCall:
    id: str
    name: str
    input: dict


@dataclass
class ModelTurn:
    content: Any  # passed back to the backend unchanged as the assistant message
    stop_reason: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    model: str = ""

    def cost(self) -> float:
        p_in, p_out = PRICES.get(self.model, (0.0, 0.0))
        return (self.input_tokens * p_in + self.output_tokens * p_out) / 1_000_000


class Backend(Protocol):
    def create(self, *, model: str, effort: str, system: str, tools: list[dict],
               messages: list[dict]) -> ModelTurn: ...

    def to_jsonable(self, content: Any) -> Any: ...


class AnthropicBackend:
    """Claude via the Messages API.

    - Thinking is always on for Claude Opus 5.5; depth is set with `effort`.
    - Server-side refusal fallbacks are enabled (`fallbacks: "default"`), so a policy
      decline is retried on a fallback model inside the same call.
    - Automatic prompt caching keeps the stable system prompt + tool list cheap.
    """

    def __init__(self, client=None):
        import anthropic

        self.client = client or anthropic.Anthropic(max_retries=6)

    def create(self, *, model, effort, system, tools, messages) -> ModelTurn:
        resp = self.client.beta.messages.create(
            model=model,
            max_tokens=16000,
            system=system,
            tools=tools,
            messages=messages,
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        calls = [ToolCall(b.id, b.name, b.input) for b in resp.content if b.type == "tool_use"]
        text = "\n".join(b.text for b in resp.content if b.type == "text")
        u = resp.usage
        input_tokens = (u.input_tokens or 0) + (u.cache_creation_input_tokens or 0) + (
            u.cache_read_input_tokens or 0) // 10  # cache reads bill at ~0.1x
        return ModelTurn(
            content=resp.content,  # appended back verbatim: thinking blocks must not be edited
            stop_reason=resp.stop_reason,
            tool_calls=calls,
            text=text,
            input_tokens=input_tokens,
            output_tokens=u.output_tokens or 0,
            model=model,
        )

    def to_jsonable(self, content):
        if isinstance(content, list):
            return [self.to_jsonable(c) for c in content]
        return content.to_dict() if hasattr(content, "to_dict") else content
