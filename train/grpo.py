"""One policy-gradient update on collected rollouts (GRPO-style, offline).

Input: the JSONL from `python -m colony train-rollouts`, where every session carries its
attempt's group advantage (reward relative to the other attempts on the same task).

Objective per session: -advantage * mean log-prob of the assistant's tokens, plus a KL
penalty that keeps the policy near the starting adapter (the reference).

This is the simplest correct form of the method: collect rollouts with the current policy,
run this script once, serve the new adapter, repeat. At larger scale (online updates, many
GPUs, importance sampling over stale rollouts) move to a dedicated framework such as verl,
OpenRLHF, SkyRL or ART; the rollout format and reward stay the same.

Example:
  python -m colony train-rollouts --team examples/team-local.toml --k 4 --out data/r1.jsonl
  python train/grpo.py --model Qwen/Qwen3-8B --adapter checkpoints/sft \
      --rollouts data/r1.jsonl --out checkpoints/rl-1
"""
from __future__ import annotations

import argparse
import random
from pathlib import Path

from common import SERVE_HINT, Collator, IGNORE, load_model, load_samples


def token_logprobs(model, batch):
    import torch

    out = model(input_ids=batch["input_ids"], attention_mask=batch["attention_mask"])
    logits = out.logits[:, :-1].float()
    targets = batch["labels"][:, 1:]
    mask = targets != IGNORE
    lp = torch.log_softmax(logits, dim=-1).gather(
        -1, targets.clamp(min=0).unsqueeze(-1)).squeeze(-1)
    return lp, mask


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3-8B")
    ap.add_argument("--adapter", required=True, help="the policy's current LoRA adapter")
    ap.add_argument("--rollouts", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--lr", type=float, default=5e-6)
    ap.add_argument("--kl", type=float, default=0.04, help="KL penalty weight (beta)")
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--grad-accum", type=int, default=8)
    ap.add_argument("--max-len", type=int, default=16384)
    ap.add_argument("--max-grad-norm", type=float, default=1.0)
    ap.add_argument("--attn", default="sdpa")
    args = ap.parse_args()

    import torch

    tok, model = load_model(args.model, args.attn, args.adapter, lora_r=0)
    samples, stats = load_samples(args.rollouts, tok, args.max_len, extra_keys=("advantage",))
    print(f"rollouts: {stats}")
    if not samples:
        raise SystemExit("no trainable samples (all advantages zero or nothing fits max-len)")
    device = next(model.parameters()).device
    collate = Collator(tok.pad_token_id)
    params = [p for p in model.parameters() if p.requires_grad]
    opt = torch.optim.AdamW(params, lr=args.lr)
    model.train()
    step = 0
    for epoch in range(args.epochs):
        random.Random(epoch).shuffle(samples)
        for i, sample in enumerate(samples):
            batch = {k: v.to(device) for k, v in collate([sample]).items()}
            with torch.no_grad(), model.disable_adapter():
                ref_lp, _ = token_logprobs(model, batch)
            lp, mask = token_logprobs(model, batch)
            n = mask.sum().clamp(min=1)
            pg = -(batch["advantage"][:, None] * lp * mask).sum() / n
            # k3 estimator of KL(policy || reference), always >= 0
            diff = (ref_lp - lp) * mask
            kl = ((torch.exp(diff) - diff - 1) * mask).sum() / n
            loss = (pg + args.kl * kl) / args.grad_accum
            loss.backward()
            if (i + 1) % args.grad_accum == 0 or i + 1 == len(samples):
                torch.nn.utils.clip_grad_norm_(params, args.max_grad_norm)
                opt.step()
                opt.zero_grad()
                step += 1
                print(f"epoch {epoch} step {step}: pg {pg.item():.4f} kl {kl.item():.4f}")
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print(f"saved adapter to {args.out}")
    print(SERVE_HINT.format(base=args.model, out=args.out, r="<adapter rank>"))


if __name__ == "__main__":
    main()
