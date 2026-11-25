"""Shared helpers for the training scripts."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from colony.training.masking import IGNORE, tokenize_with_mask  # noqa: E402


def load_samples(path: Path, tok, max_len: int, extra_keys=()) -> tuple[list[dict], dict]:
    samples, stats = [], {"rows": 0, "skipped": 0, "trained_tokens": 0}
    with open(path) as f:
        for line in f:
            row = json.loads(line)
            stats["rows"] += 1
            out = tokenize_with_mask(tok, row["messages"], row.get("tools"), max_len)
            if out is None:
                stats["skipped"] += 1
                continue
            ids, labels, n = out
            stats["trained_tokens"] += n
            samples.append({"input_ids": ids, "labels": labels,
                            "attention_mask": [1] * len(ids),
                            **{k: row[k] for k in extra_keys}})
    return samples, stats


def split(samples: list, val_frac: float, seed: int = 0) -> tuple[list, list]:
    samples = samples[:]
    random.Random(seed).shuffle(samples)
    n_val = int(len(samples) * val_frac)
    return samples[n_val:], samples[:n_val]


class Collator:
    """Right-pads a batch: pad token for inputs, IGNORE for labels, 0 for attention."""

    def __init__(self, pad_id: int):
        self.pad_id = pad_id

    def __call__(self, batch: list[dict]) -> dict:
        import torch

        n = max(len(b["input_ids"]) for b in batch)
        pad = lambda key, v: [b[key] + [v] * (n - len(b[key])) for b in batch]  # noqa: E731
        out = {"input_ids": torch.tensor(pad("input_ids", self.pad_id)),
               "labels": torch.tensor(pad("labels", IGNORE)),
               "attention_mask": torch.tensor(pad("attention_mask", 0))}
        if "advantage" in batch[0]:
            out["advantage"] = torch.tensor([b["advantage"] for b in batch],
                                            dtype=torch.float32)
        return out


def load_model(name: str, attn: str, adapter: str | None, lora_r: int, trainable=True):
    import torch
    from peft import LoraConfig, PeftModel, get_peft_model
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tok = AutoTokenizer.from_pretrained(name)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.bfloat16,
                                                 attn_implementation=attn)
    model.gradient_checkpointing_enable()
    model.enable_input_require_grads()
    if adapter:
        model = PeftModel.from_pretrained(model, adapter, is_trainable=trainable)
    else:
        model = get_peft_model(model, LoraConfig(
            r=lora_r, lora_alpha=2 * lora_r, lora_dropout=0.05, target_modules="all-linear",
            task_type="CAUSAL_LM"))
    model.print_trainable_parameters()
    return tok, model


SERVE_HINT = """
Serve it for the colony (vLLM example):
  vllm serve {base} --enable-lora --lora-modules candidate={out} \\
    --enable-auto-tool-choice --tool-call-parser hermes --max-lora-rank {r}
Then benchmark it against your baseline:
  python -m colony bench run --team examples/team-local.toml --out results/candidate.json
  python -m colony bench compare results/baseline.json results/candidate.json
"""
