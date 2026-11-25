# v3 testing guide: your own model

What v3 adds:
- **Local models in the team.** `model = "local:<name>"` sends an agent to any
  OpenAI-compatible server (vLLM, SGLang, llama.cpp). Teams can mix Claude and local agents.
- **Benchmark and gate.** Fixed tasks in `benchmarks/`; `colony bench run` scores a team,
  `colony bench compare` passes or fails a candidate against a baseline.
- **Training data.** `colony train-export` turns run transcripts into a fine-tuning dataset;
  `colony train-rollouts` collects scored attempts for reinforcement learning.
- **Training scripts.** `train/sft.py` (LoRA fine-tuning) and `train/grpo.py` (one
  policy-gradient update with a KL penalty).

**Status, honestly:** the converter, local backend, benchmark, gate, export, rollouts, reward
and loss masking are covered by offline tests. The two training scripts in `train/` have
only been syntax-checked: the build environment had no GPU and no access to Hugging Face.
Expect to fix small things on the first GPU run, and please report them.

Do the v1 and v2 guides first.

## Before you train: check the terms

The dataset is made of model outputs. If they come from a hosted model (Claude or any
other provider), read that provider's terms on using outputs to train other models before
you train on them. If you want to avoid the question, generate the data with an open-weight
model served locally (`model = "local:..."` in every agent), whose license allows it.

## 1. Offline tests (free)

```bash
python -m pytest -q tests/test_local_backend.py tests/test_training.py tests/test_bench.py
```

## 2. Serve a local model

On a machine with a GPU (an 8B model in bf16 needs about 20 GB of GPU memory to serve):

```bash
pip install vllm
```

```bash
vllm serve Qwen/Qwen3-8B --served-model-name qwen3-8b --enable-auto-tool-choice --tool-call-parser hermes
```

The right `--tool-call-parser` depends on the model family; check vLLM's tool calling docs
for your model. If the server is not on `http://localhost:8000/v1`, tell the colony where:

```bash
export COLONY_LOCAL_URL=http://gpu-box:8000/v1
```

Smoke test (no Claude, no cost per token):

```bash
examples/make_demo_repo.sh
```

Edit `examples/diverge.toml` so both agents use `model = "local:qwen3-8b"`, then:

```bash
python -m colony run examples/diverge.toml
```

Check that:
- [ ] agents make tool calls (the event log is not empty): `python -m colony status examples/diverge.toml`;
- [ ] if the model writes tool calls as plain text instead of calling tools, the
      `--tool-call-parser` is wrong for that model.

Then try a mixed team (Claude lead, local workers):

```bash
python -m colony run examples/mixed-team.toml
```

## 3. Record a baseline

```bash
python -m colony bench run --team examples/team-claude.toml --out results/baseline-claude.json
```

```bash
python -m colony bench run --team examples/team-local.toml --out results/baseline-local.json
```

`team-local.toml` expects the model to be served as `candidate`; for the untrained base
model, serve it under that name (`--served-model-name candidate`) or edit the team file.
Use `--repeats 3` for numbers you can trust; single attempts are noisy.

Check that:
- [ ] `results/*.json` exist and their summaries show a success rate per task;
- [ ] the gate works on identical results:

```bash
python -m colony bench compare results/baseline-local.json results/baseline-local.json
```

## 4. Build a dataset

Run several goals (the demos, the benchmark tasks, your own projects) so `runs/` fills up.
Then:

```bash
python -m colony train-export --out data/sft.jsonl
```

The summary shows how many sessions were written and skipped, and which models produced
them. A first fine-tune is worth trying at a few hundred successful sessions; more and more
varied is better.

## 5. Fine-tune (GPU machine)

```bash
pip install -r train/requirements.txt
```

```bash
python train/sft.py --model Qwen/Qwen3-8B --data data/sft.jsonl --out checkpoints/sft
```

It prints the exact `vllm serve` command for the new adapter. Serve it as `candidate`, then:

```bash
python -m colony bench run --team examples/team-local.toml --repeats 3 --out results/sft.json
```

```bash
python -m colony bench compare results/baseline-local.json results/sft.json
```

Check that:
- [ ] the gate passes (the fine-tuned model is no worse than the base model);
- [ ] ideally success rises and tokens fall.

## 6. Reinforcement learning (GPU machine)

With the SFT adapter served as `candidate`:

```bash
python -m colony train-rollouts --team examples/team-local.toml --k 4 --out data/rollouts-1.jsonl
```

```bash
python train/grpo.py --model Qwen/Qwen3-8B --adapter checkpoints/sft --rollouts data/rollouts-1.jsonl --out checkpoints/rl-1
```

Serve `checkpoints/rl-1` as `candidate`, benchmark, compare against `results/sft.json`, and
repeat. Rules of thumb:
- Groups where every attempt scored the same teach nothing. If most groups are flat, the
  tasks are too easy or too hard for the model: add benchmark tasks of the right difficulty.
- Keep the token penalty small at first (`RewardWeights.tokens` in
  `colony/training/metrics.py`) and raise it once success is stable.
- Read some transcripts every round. Watch for agents gaming the reward, for example by
  submitting repeatedly without thinking.

## 7. Add benchmark tasks

A task is `benchmarks/<name>/task.toml` (description, verify_command) plus
`benchmarks/<name>/repo/` with the starting code. Good tasks have a clear verifier, fail at
the start, and take a capable agent a few minutes. Keep a few hard ones the current model
fails: they show progress.
