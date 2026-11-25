"""Convert between the harness's message format and the generic "chat completions" format.

The harness keeps conversations in the Claude Messages shape: content blocks (`text`,
`tool_use`, `tool_result`, `thinking`) and mid-conversation `system` messages. Open-weight
models served by vLLM / SGLang / llama.cpp, and most training libraries, use the chat
completions shape instead: `tool_calls` on assistant messages and `role: "tool"` results.

One converter serves both the local-model backend (v3) and the training-data export, so a
model is trained on exactly the format it is later served with.
"""
from __future__ import annotations

import json
from typing import Any

SYSTEM_NOTE_PREFIX = "[system note] "


def _get(block: Any, key: str, default=None):
    if isinstance(block, dict):
        return block.get(key, default)
    return getattr(block, key, default)


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_get(b, "text", "") for b in content if _get(b, "type") == "text")
    return str(content)


def tools_to_chat(tools: list[dict]) -> list[dict]:
    return [{"type": "function",
             "function": {"name": t["name"], "description": t.get("description", ""),
                          "parameters": t["input_schema"]}} for t in tools]


def to_chat(system: str, messages: list[dict], args_as_dict: bool = False) -> list[dict]:
    """Harness messages -> chat-completions messages.

    `args_as_dict=True` keeps tool-call arguments as dicts, which Hugging Face chat templates
    expect when rendering training data; servers expect JSON strings (the default).
    Thinking blocks are dropped: their text is never returned by the API anyway.
    """
    out: list[dict] = [{"role": "system", "content": system}]
    for m in messages:
        role, content = m["role"], m["content"]
        if role == "system":  # mid-conversation operator note
            out.append({"role": "user", "content": SYSTEM_NOTE_PREFIX + _text_of(content)})
        elif role == "assistant":
            blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
            text = "\n".join(_get(b, "text", "") for b in blocks if _get(b, "type") == "text")
            calls = []
            for b in blocks:
                if _get(b, "type") == "tool_use":
                    args = _get(b, "input", {}) or {}
                    calls.append({"id": _get(b, "id"), "type": "function",
                                  "function": {"name": _get(b, "name"),
                                               "arguments": args if args_as_dict
                                               else json.dumps(args)}})
            msg: dict = {"role": "assistant", "content": text}
            if calls:
                msg["tool_calls"] = calls
            out.append(msg)
        else:  # user
            if isinstance(content, str):
                out.append({"role": "user", "content": content})
                continue
            texts = []
            for b in content:
                t = _get(b, "type")
                if t == "tool_result":
                    body = _get(b, "content", "")
                    body = body if isinstance(body, str) else _text_of(body)
                    if _get(b, "is_error"):
                        body = "ERROR: " + body
                    out.append({"role": "tool", "tool_call_id": _get(b, "tool_use_id"),
                                "content": body})
                elif t == "text":
                    texts.append(_get(b, "text", ""))
            if texts:
                out.append({"role": "user", "content": "\n".join(texts)})
    return _merge_users(out)


def _merge_users(msgs: list[dict]) -> list[dict]:
    """Many chat templates reject two user messages in a row; merge them."""
    merged: list[dict] = []
    for m in msgs:
        if merged and m["role"] == "user" and merged[-1]["role"] == "user":
            merged[-1] = {"role": "user",
                          "content": merged[-1]["content"] + "\n\n" + m["content"]}
        else:
            merged.append(m)
    return merged


def from_chat_choice(message: dict) -> tuple[list[dict], list[tuple[str, str, dict]]]:
    """A chat-completions assistant message -> (harness content blocks, tool calls)."""
    blocks: list[dict] = []
    calls = []
    if message.get("content"):
        blocks.append({"type": "text", "text": message["content"]})
    for i, tc in enumerate(message.get("tool_calls") or []):
        fn = tc.get("function", {})
        raw = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except json.JSONDecodeError:
            args = {"__unparseable_arguments__": raw}
        cid = tc.get("id") or f"call_{i}"
        blocks.append({"type": "tool_use", "id": cid, "name": fn.get("name", ""), "input": args})
        calls.append((cid, fn.get("name", ""), args))
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    return blocks, calls
