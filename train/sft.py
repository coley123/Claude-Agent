"""Supervised fine-tuning (LoRA) of a small model on colony sessions.

Input: the JSONL from `python -m colony train-export` (successful sessions by default).
Loss is applied only to the assistant's own tokens (replies and tool calls), never to
the system prompt, briefings or tool results.

Example (one 24-80 GB GPU for an 8B model):
  pip install -r train/requirements.txt
  python train/sft.py --model Qwen/Qwen3-8B --data data/sft.jsonl --out checkpoints/sft
"""
from __future__ import annotations

import argparse
from pathlib import Path

from common import SERVE_HINT, Collator, load_model, load_samples, split


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="Qwen/Qwen3-8B")
    ap.add_argument("--data", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--adapter", help="continue training an existing LoRA adapter")
    ap.add_argument("--epochs", type=float, default=2)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--max-len", type=int, default=16384)
    ap.add_argument("--grad-accum", type=int, default=16)
    ap.add_argument("--lora-r", type=int, default=32)
    ap.add_argument("--val-frac", type=float, default=0.05)
    ap.add_argument("--attn", default="sdpa", help="sdpa, or flash_attention_2 if installed")
    args = ap.parse_args()

    from transformers import Trainer, TrainingArguments

    tok, model = load_model(args.model, args.attn, args.adapter, args.lora_r)
    samples, stats = load_samples(args.data, tok, args.max_len)
    print(f"data: {stats}")
    if not samples:
        raise SystemExit("no trainable samples; export more successful runs first")
    train, val = split(samples, args.val_frac)

    trainer = Trainer(
        model=model,
        args=TrainingArguments(
            output_dir=str(args.out), per_device_train_batch_size=1,
            per_device_eval_batch_size=1, gradient_accumulation_steps=args.grad_accum,
            num_train_epochs=args.epochs, learning_rate=args.lr, lr_scheduler_type="cosine",
            warmup_ratio=0.03, bf16=True, logging_steps=5, save_strategy="epoch",
            eval_strategy="epoch" if val else "no", report_to="none",
            remove_unused_columns=False),
        train_dataset=train, eval_dataset=val or None,
        data_collator=Collator(tok.pad_token_id))
    trainer.train()
    model.save_pretrained(args.out)
    tok.save_pretrained(args.out)
    print(f"saved adapter to {args.out}")
    print(SERVE_HINT.format(base=args.model, out=args.out, r=args.lora_r))


if __name__ == "__main__":
    main()
