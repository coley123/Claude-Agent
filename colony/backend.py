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


class LocalBackend:
    """An open-weight model behind an OpenAI-compatible chat completions server.

    Works with vLLM (`vllm serve <model> --enable-auto-tool-choice --tool-call-parser ...`),
    SGLang, llama.cpp's server and similar. Messages are converted with `chatformat`, the same
    converter the training export uses, so the model sees the format it was trained on.
    Local models have no list price, so they cost $0 against the budget; `max_hours` still
    applies.
    """

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 timeout: float = 600.0, max_tokens: int = 8192):
        import os

        self.base_url = (base_url or os.environ.get("COLONY_LOCAL_URL")
                         or "http://localhost:8000/v1").rstrip("/")
        self.api_key = api_key or os.environ.get("COLONY_LOCAL_API_KEY", "")
        self.timeout = timeout
        self.max_tokens = max_tokens

    def create(self, *, model, effort, system, tools, messages) -> ModelTurn:
        import json
        import urllib.request

        from .chatformat import from_chat_choice, to_chat, tools_to_chat

        body = {"model": model, "messages": to_chat(system, messages),
                "tools": tools_to_chat(tools), "max_tokens": self.max_tokens}
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = json.loads(r.read())
        choice = data["choices"][0]
        blocks, calls = from_chat_choice(choice["message"])
        finish = choice.get("finish_reason")
        stop = {"tool_calls": "tool_use", "length": "max_tokens"}.get(finish, "end_turn")
        if calls and stop == "end_turn":
            stop = "tool_use"
        usage = data.get("usage") or {}
        return ModelTurn(
            content=blocks, stop_reason=stop,
            tool_calls=[ToolCall(i, n, a) for i, n, a in calls],
            text="\n".join(b["text"] for b in blocks if b["type"] == "text"),
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0), model=model)

    def to_jsonable(self, content):
        return content


class RouterBackend:
    """Routes each agent to a backend by its model name.

    `model = "local:<served-model-name>"` goes to the local server; anything else goes to
    Claude. This lets one team mix a Claude lead with local-model workers. The Claude client
    is created only if an agent actually uses Claude, so all-local teams need no API key.
    """

    def __init__(self, anthropic=None, local=None):
        self._anthropic = anthropic
        self.local = local or LocalBackend()

    @property
    def anthropic(self):
        if self._anthropic is None:
            self._anthropic = AnthropicBackend()
        return self._anthropic

    def create(self, *, model, **kw) -> ModelTurn:
        if model.startswith("local:"):
            return self.local.create(model=model.removeprefix("local:"), **kw)
        return self.anthropic.create(model=model, **kw)

    def to_jsonable(self, content):
        if isinstance(content, list) and content and not isinstance(content[0], dict):
            return self.anthropic.to_jsonable(content)
        return content
