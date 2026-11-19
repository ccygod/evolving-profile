# Changelog — Evolving Profile 6.x

## 6.1.0 (2026-11-18)

- **Parallel & Round-Robin Model Routing (`v6/model_router.py`)**: Added `parallel_race` (primary + fallback raced concurrently, first valid response wins) and `round_robin` (concurrent load distribution across healthy providers) modes, plus `/v1/models` and `/metrics` endpoints.
- **Event-Driven File Watcher (`v6/daemon.py`)**: Added zero-dependency session-directory mtime watcher to wake `context-worker` and `retention-worker` immediately on new transcript activity, plus `incremental-sync` scheduled job (8 unified jobs total).
- **Bi-Temporal Reranker (`v6/reranker.py`)**: Added deterministic trust-level, supersession, project-status, and time-decay reranker.
- **Golden Eval Harness (`v6/eval_harness.py`)**: Automated live benchmark scorecard (`logs/v6-eval-latest.json`) tracking service health, O(1) `project_state` latency, write signal quality, and DB/graph size.
- **Incremental Delta Event Sync (`v6/incremental_sync.py`)**: Cursor-based gzip JSONL delta exporter with SHA-256 manifests (`backups/incremental/`) for lightweight multi-device replication.

## 6.0.0 (2026-11-18)

### Architecture
- **Unified Local Model Router (`v6/model_router.py`)**: Single OpenAI-compatible local gateway (`127.0.0.1:12095`) managing per-scope provider chains (`default`, `context`, `retain`, `consolidation`, `reflect`, `guidance`), circuit breaker (`closed` / `open` / `half_open`), automatic failover, and structured JSONL telemetry.
- **Unified Event-Driven Scheduler Daemon (`v6/daemon.py`)**: Single resident scheduler (`127.0.0.1:12096`) consolidating 7 fragmented polling LaunchAgents (`retention-worker`, `context-worker`, `guidance-worker`, `topic-catalog`, `topic-semantics`, `context-links`, `events-sync`) with SQLite cursor persistence, non-overlapping per-job locks, exponential backoff, quiet idle logging, and HTTP control plane (`/health`, `/jobs`, `/trigger/<job>`).
- **Event-Bus Semantic Ingestion Gate & Bi-Temporal Projector (`v6/event_projector.py`)**: Pre-LLM semantic routing (`memory_high_priority`, `memory_standard`, `work_ledger_only`) and O(1) bi-temporal project state projection (`valid_from` / `valid_to`).
- **Code / Data Separation (`v6/bootstrap.py`)**: Strict separation validator (`EP_CODE_ROOT` vs `EP_DATA_ROOT`) and LaunchAgent plist generators.

### Write Pipeline, Multi-Host & Operations
- **Multi-Host Coverage**: Added Antigravity host profile & native transcript adapter; merged Codex + Claude rollout discovery into multi-root `context-worker`; generalized `document_id` and per-host batch isolation in `retention_queue` / `retention_worker`.
- **Write Signal-to-Noise & Deduplication**: Normalized `content_sha256` queue deduplication, bounded assistant verbosity gate (`retainMaxAssistantChars`), local zero-LLM prefilter for unconfirmed assistant observations in `guidance`, and relaxed single-dimension atomic mental model activation (`len(refs) >= 2`).
- **Database, Graph & Log Governance**: Automated 14-day `audit_log` / `llm_requests` pruning, 30-day `async_operations` pruning, top-10 semantic + weak temporal `memory_links` graph pruning, and 7-day `instruction-receipts` rolling cleanup.
- **Reliability**: Lowered consolidation LLM batch size (`64 -> 8`), tuned reflect model (`qwen3.6-plus`, 2 iterations), pruned stale `retry_counts`, added `HF_HUB_OFFLINE=1` for worker restarts, and restored 100% green test suites.
