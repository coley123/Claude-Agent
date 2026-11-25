"""Tokenize a chat-format sample so loss applies only to the assistant's own tokens.

Works with any Hugging Face tokenizer that has a chat template (duck-typed: it only needs
`apply_chat_template`), so it can be tested without torch.
"""
from __future__ import annotations

IGNORE = -100


def _ids(tok, messages, tools, gen_prompt):
    out = tok.apply_chat_template(messages, tools=tools or None, tokenize=True,
                                  add_generation_prompt=gen_prompt)
    if isinstance(out, dict) or hasattr(out, "input_ids"):
        out = out["input_ids"]
    return list(out)


def tokenize_with_mask(tok, messages: list[dict], tools: list[dict] | None,
                       max_len: int) -> tuple[list[int], list[int], int] | None:
    """Return (input_ids, labels, n_assistant_tokens), or None if nothing trainable fits.

    Assistant spans are found by rendering the conversation up to each assistant turn. If a
    chat template rewrites earlier turns (so a prefix does not match), that turn is skipped
    rather than trained on misaligned labels.
    """
    full = _ids(tok, messages, tools, False)
    labels = [IGNORE] * len(full)
    trained = 0
    for i, m in enumerate(messages):
        if m["role"] != "assistant":
            continue
        start = len(_ids(tok, messages[:i], tools, True))
        upto = _ids(tok, messages[:i + 1], tools, False)
        if full[:len(upto)] != upto or start > len(upto):
            continue
        for j in range(start, len(upto)):
            labels[j] = full[j]
        trained += len(upto) - start
    if len(full) > max_len:
        full, labels = full[:max_len], labels[:max_len]
        trained = sum(1 for l in labels if l != IGNORE)
    if trained == 0:
        return None
    return full, labels, trained
