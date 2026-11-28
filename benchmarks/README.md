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

## Measured Baselines (`2026-11-28`, isolated ephemeral banks)

> **Scope note:** Every run below executes against a fresh ephemeral bank that is deleted immediately after evaluation. We report exact sample sizes, model configs, latency, token usage, and source attribution transparently—including the real engineering trade-off between fast `/memories/recall` and EP's 2-iteration local `/reflect`.

| Benchmark | Sample & Mode | Recall p50 / p95 | Mean Context Tokens | Source Attribution | Accuracy | Raw Artifact |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **PersonaMem-32K** | **30 Qs** (3 shared 32K contexts, `gpt-6-luna` + `high` `/recall`) | **85.1 ms / 146.0 ms** | **~8,275** (-74% vs 32K) | **100%** | **66.7%** (20/30)<br/>*Generalizing: **100%** (4/4) · Update Reasons: **85.7%** (6/7) · Pref Recs: **80.0%** (4/5)* | [personamem_32k_gpt6_high.json](results/personamem_32k_gpt6_high.json) |
| **PersonaMem-32K** | 9 Qs (1 context, `qwen3.7-flash` + `mid` `/recall`) | **100.8 ms / 149.1 ms** | **~5,618** (-82% vs 32K) | **100%** | 55.6% (5/9) | [personamem_32k_run1.json](results/personamem_32k_run1.json) |
| **LongMemEval-S** | 6 stratified categories (`qwen3.7-flash` + `mid` `/recall`) | **51.4 ms / 96.5 ms** | **~4,632** | **100%** | **66.7%** (4/6) | [longmemeval_s_run1.json](results/longmemeval_s_run1.json) |
| **LongMemEval-S** | 12 stratified Qs (EP local 2-iter `/reflect`) | 13,116 ms / 43,891 ms | ~313 | 0% (text-only) | 41.7% (5/12) | [longmemeval_s_reflect_high.json](results/longmemeval_s_reflect_high.json) |

### Key Engineering Finding (`/recall` vs. 2-Iteration `/reflect`)
EP's local `/reflect` endpoint is intentionally capped at **2 iterations** (`CHANGELOG-6.0.md`) for low-cost background consolidation. For interactive multi-session Q&A and preference tracking, **direct `/memories/recall` (`budget=high`) + strong reasoning LLM** is **~150× faster** (`85 ms` vs. `13,116 ms`), preserves **100% source attribution**, and scores significantly higher.
