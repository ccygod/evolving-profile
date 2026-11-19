# Evolving Profile 6.1.0

Release date: 2026-11-19

## Highlights

### Cross-Platform Control Plane & Zero-Shell Scheduling
- **Zero-Shell Pure-Python Scheduler (`v6/daemon.py`, `:12096`)**: Eliminated all remaining `.zsh` / BSD-`date` dependencies across all 13 background jobs (`retention-worker`, `context-worker`, `guidance-worker`, `topic-catalog`, `topic-semantics`, `context-links`, `events-sync`, `incremental-sync`, `entity-graph`, `entity-state`, `ontology-hygiene`, `sqlite-hygiene`, `pg-analyze`) for macOS, Windows, and Linux portability.
- **Unified Model Router (`v6/model_router.py`, `:12095`)**: Keeps strict primary-first `failover` by default across 6 scopes with circuit breaking, health probing, and telemetry (supports high primary concurrency without racing paid fallback models).
- **Native Windows / Cross-Platform Installer (`scripts/install.py` + `scripts/install.ps1`)**: Runs preflight, secret scan, `uv` Python 3.11+ environment provisioning, SDK & Console builds, and `.env` templating natively on PowerShell without requiring WSL or replacing system Python.

### Adaptive Postgres 18 & pgvector 0.8 Kernel
- **Hardware-Adaptive Auto-Tuner (`v6/pg_autotune.py`)**: Detects physical RAM and CPU count across macOS, Linux, and Windows to compute safe `shared_buffers`, `effective_cache_size`, `work_mem`, `maintenance_work_mem`, `max_wal_size`, `random_page_cost`, `max_parallel_workers_per_gather`, `jit = off`, `autovacuum_vacuum_scale_factor = 0.05`, `idle_in_transaction_session_timeout = 60s`, and `shared_preload_libraries = pg_stat_statements`.
- **pgvector 0.8+ Iterative HNSW Scan**: Automatically detects `pgvector >= 0.8` and sets `ALTER DATABASE ... SET hnsw.iterative_scan = 'relaxed_order'` to prevent over-filtering shortfalls on filtered vector recall, plus full-database `ANALYZE` planner calibration.

### Bi-Temporal Entity State, Ontology Hygiene & Retrieval Quality
- **Generalized Mutable Entity State (`v6/entity_state.py`)**: Introduces `ep_events.entity_state` (`project` / `service` / `client` / `config`) with `valid_from`, `valid_to`, and `superseded_by` so mutable operational state no longer conflicts with immutable `world` facts.
- **Ontology & Preference Governance (`v6/ontology_hygiene.py`)**: Deduplicates active mental models by normalized title, deactivates stale `superseded` units, enriches active preferences with first-class `enforcement` (`hard_guardrail` / `default_policy` / `soft_style`) and `scope_level` (`global` / `project_scoped` / `task_local`, preserving structured `scope` dicts), soft-demotes unconfirmed assistant experiences and short fragments, and prunes test banks, orphan partial HNSW indexes, and aged singleton entities.
- **Bi-Temporal & Trust Reranker (`v6/reranker.py`)** & **SQLite/Release Hygiene (`v6/sqlite_hygiene.py`)**: Down-ranks `recall_demoted` / `assistant_unconfirmed` / `fragment_noise` memories while keeping them auditable; bounds `capture.sqlite3` growth and archives obsolete releases.
- **Golden Quality Gate (`v6/eval_harness.py`)**: Automated health, P95 latency, duplicate-model, unconfirmed-demotion, `entity_state`, and `pg_stat_statements` Top-5 slow-SQL verification.

### Web Console Visualization Alignment
- **Preference Constellation & Details (`console/src/components/preference-view.tsx`)**: Highlights `hard_guardrail` nodes and displays `enforcement` and `scope_level` badges.
- **Memory Constellation (`console/src/components/constellation.tsx`)**: Softly dims `recall_demoted` / `assistant_unconfirmed` / `fragment_noise` stars while retaining full hover/click traceability.
- **Full-Chain Topology (`console/src/components/flow-lanes-overview.tsx`)**: Surfaces **Entity State**, **Bi-temporal & Trust Rerank**, and **Facts, Entity State & Hygiene** writeback.
