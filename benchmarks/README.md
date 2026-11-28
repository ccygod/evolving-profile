# Evolving Profile Benchmarks

Reproducible evaluation scripts for measuring **Evolving Profile (EP)** on public long-term AI agent memory benchmarks—tracking not only **answer accuracy**, but also **recall latency**, **prompt token budget**, and **source attribution coverage**.

## Supported Benchmarks

1. **LongMemEval-S** ([xiaowu0162/LongMemEval](https://github.com/xiaowu0162/LongMemEval))
   - Multi-session conversational memory, temporal reasoning, knowledge updates, user preferences, and abstention.
2. **PersonaMem** ([bowen-upenn/PersonaMem](https://github.com/bowen-upenn/PersonaMem))
   - Dynamic user preference tracking and multi-session personalization.
3. **Production Storage & Retrieval Governance** (`python -m v6.storage_bench`)
   - Built-in before/after latency, Hit@10, duplicate rate, and low-value noise benchmark on your own bank.

## Isolation & Safety Guarantee

Every benchmark run creates a dedicated ephemeral bank (`bench-longmemeval-<id>` or `bench-personamem-<id>`) and deletes it on completion (unless `--keep-bank` is passed). Your personal bank is never read or modified.

## Quick Run

```bash
# 1. LongMemEval-S stratified run
python3 benchmarks/run_longmemeval.py \
  --dataset /path/to/longmemeval_s_cleaned.json \
  --api-url http://127.0.0.1:12088 \
  --llm-url http://127.0.0.1:12095/v1 \
  --model qwen3.7-flash-2026-07-15 \
  --limit 30 \
  --output benchmarks/results/longmemeval_s_smoke30.json

# 2. PersonaMem-32K run
python3 benchmarks/run_personamem.py \
  --questions-csv /path/to/questions_32k.csv \
  --contexts-jsonl /path/to/shared_contexts_32k.jsonl \
  --api-url http://127.0.0.1:12088 \
  --llm-url http://127.0.0.1:12095/v1 \
  --model qwen3.7-flash-2026-07-15 \
  --max-contexts 1 \
  --limit 25 \
  --output benchmarks/results/personamem_32k_run1.json
```

## Initial Smoke-Test Baselines (`2026-11-28`, `qwen3.7-flash` + raw `/recall`)

> **Scope note:** These are initial small-sample smoke baselines verifying end-to-end isolation, recall latency, token reduction, and source attribution using a lightweight flash model without `/reflect` synthesis. Full-dataset leaderboard runs (using `/reflect` + stronger reasoning models) are tracked separately.

| Benchmark | Sample | Recall p50 / p95 | Mean Context Tokens | Source Attribution | Smoke Accuracy (`qwen3.7-flash`) | Raw Artifact |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **LongMemEval-S** | 6 stratified categories | **51.4 ms / 96.5 ms** | **~4,632** | **100%** | 66.7% (4/6) | [longmemeval_s_run1.json](results/longmemeval_s_run1.json) |
| **PersonaMem-32K** | 9 Qs (1 shared 32K context) | **100.8 ms / 149.1 ms** | **~5,618** (-82% vs 32K) | **100%** | 55.6% (5/9) | [personamem_32k_run1.json](results/personamem_32k_run1.json) |
