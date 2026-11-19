# Evolving Profile v6 Architecture

EP v6 consolidates fragmented v5 polling workers, dual model configs, and duplicated ingestion loops into four decoupled pillars:

## 1. Unified Local Model Router (`v6/model_router.py`)
- Single OpenAI-compatible local gateway (`127.0.0.1:12095`) shared by Memory API, Consolidation Worker, Context Worker, Guidance Worker, and Topic Semantics.
- Per-scope provider chains (`default`, `retain`, `consolidation`, `reflect`, `context`, `guidance`) with built-in circuit breaker (`closed` / `open` / `half_open`) and automatic failover.
- Structured JSONL telemetry (`scope`, `provider`, `model`, `status`, `latency_ms`, `prompt_tokens`, `completion_tokens`).

## 2. Unified Event-Driven Scheduler Daemon (`v6/daemon.py`)
- Replaces fragmented polling LaunchAgents with one resident scheduler backed by SQLite (`job_state`).
- Enforces non-overlapping per-job locks, exponential backoff on failures, quiet idle cycles, and an HTTP control plane (`GET /health`, `GET /jobs`, `POST /trigger/<job>`).

## 3. Event-Bus Semantic Ingestion Gate & Bi-Temporal Projector (`v6/event_projector.py`)
- Routes normalized `ep_events.events` before any LLM spend:
  - `memory_high_priority`: explicit user rules, decisions, constraints, and corrections.
  - `memory_standard`: substantive user statements.
  - `work_ledger_only`: tool calls/results, procedural assistant prose, bare acknowledgments, and injected wrappers.
- Projects ordered events into bi-temporal O(1) project state (`valid_from`, `valid_to`, `hosts`, `session_count`, `latest_user_request`).

## 4. Strict Code / Data Separation (`v6/bootstrap.py`)
- `EP_CODE_ROOT`: single Git checkout (zero personal data or secrets).
- `EP_DATA_ROOT`: pure data/config/logs/backups directory (no copied `runtime/` Python trees).
- Includes `verify_code_data_separation()` and `render_v6_daemon_plist()` for zero-drift cutover.
