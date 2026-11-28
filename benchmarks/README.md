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

| Benchmark | Sample & Mode | Recall + Readback p50 / p95 | Mean Context Tokens | Source Attribution | Accuracy | Raw Artifact |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- |
| **LongMemEval-S** | **12 stratified Qs (`ep_sota`: timestamped + 2-facet + `read_source` + `gpt-6-luna`)** | **102.5 ms / 142.9 ms** | ~23,851 | **100%** | **91.7%** (11/12)<br/>*Knowledge-Update **100%** · Temporal **100%** · Preference **100%** · User **100%** · Assistant **100%** · Multi-Session 50%* | [longmemeval_s_ep_sota_12q.json](results/longmemeval_s_ep_sota_12q.json) |
| **PersonaMem-32K** | **30 Qs (`ep_sota`: 8-turn fine chunks + causal time + 3-facet + `read_source` + `gpt-6-luna`)** | **188.4 ms / 251.1 ms** | ~21,244 | **100%** | **70.0%** (21/30)<br/>*Pref Evolution **100%** (3/3) · Generalizing **100%** (4/4) · Update Reasons **85.7%** (6/7) · Pref Recs **80.0%** (4/5) · Shared Facts 60% (3/5) · New Ideas 16.7% (1/6)* | [personamem_32k_ep_sota_30q.json](results/personamem_32k_ep_sota_30q.json) |
| **PersonaMem-32K** | 30 Qs (baseline `gpt-6-luna` + `high` `/recall`, 24-turn coarse chunks) | **85.1 ms / 146.0 ms** | **~8,275** (-74% vs 32K) | **100%** | 66.7% (20/30) | [personamem_32k_gpt6_high.json](results/personamem_32k_gpt6_high.json) |
| **LongMemEval-S** | 6 stratified Qs (`qwen3.7-flash` + `mid` `/recall`) | **51.4 ms / 96.5 ms** | **~4,632** | **100%** | 66.7% (4/6) | [longmemeval_s_run1.json](results/longmemeval_s_run1.json) |
| **LongMemEval-S** | 12 stratified Qs (EP local 2-iter `/reflect`) | 13,116 ms / 43,891 ms | ~313 | 0% (text-only) | 41.7% (5/12) | [longmemeval_s_reflect_high.json](results/longmemeval_s_reflect_high.json) |

### Key A/B Engineering Findings (Why `ep_sota` Jumped +50 pts on LongMemEval-S)
1. **Original-source readback (`read_source`) + chronological ordering** pushed LongMemEval-S from **41.7%** (2-iter `/reflect`) and **66.7%** (raw `/recall`) up to **91.7% (11/12)** with **100% accuracy on 5/6 categories** (including `knowledge-update` and `temporal-reasoning`) at **102.5 ms** p50 latency.
2. On **PersonaMem-32K**, fine-grained causal-timestamped chunking + `read_source` pushed `track_full_preference_evolution` to **100% (3/3)**, `generalizing_to_new_scenarios` to **100% (4/4)**, `recalling_the_reasons_behind_previous_updates` to **85.7% (6/7)**, and `provide_preference_aligned_recommendations` to **80.0% (4/5)** (**83.3% = 20/24** across those 5 categories; `suggest_new_ideas` novelty filtering remains an open improvement area).
