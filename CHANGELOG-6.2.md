# Evolving Profile 6.2.0

Release date: 2026-11-24

6.2 is a storage-governance and reliability release. Memory that is never
retrieved no longer occupies vector-index space, raw logs stop growing without
bound, and every space saving is reversible. Retrieval got faster and less
repetitive at the same time, because most of what was removed was noise that
competed for top-k slots.

## Highlights

### Storage governance

- **Hot/warm admission tiering (`v6/memory_tiering.py`, daemon job `memory-tiering`, every 10 min).**
  Low-value units (`recall_demoted`, `assistant_unconfirmed`, `fragment_noise`, unconfirmed
  assistant statements) and duplicates move to a *warm* tier: their embedding and graph links
  leave the HNSW index, while text, provenance, keyword/exhaustive search and `read_source`
  keep working. The embedding is kept in `memory_unit_warm`, so `restore <unit_id>` or
  `restore --reason <r>` puts a unit back.
  Duplicates are exact text matches or cosine ≥ 0.98 **and** identical numbers **and** event
  times within one day — the guard exists because "1,180 rows migrated" and "1,280 rows
  migrated" are different facts with near-identical vectors. The representative keeps the
  evidence: `proof_count` is credited and the merged ids are listed in `merged_duplicates`.
- **Half-precision embeddings (migration `e2f3a4b5c6d7`).** `memory_units.embedding` is stored as
  `halfvec`; per-bank HNSW indexes use `halfvec_cosine_ops`. The table and its vector indexes
  shrink by about half with unchanged ranking. The migration is idempotent, skips pgvector < 0.7,
  and can be declined with `EVOLVING_PROFILE_EMBEDDING_STORAGE=vector` (read by the migration
  and by new-bank index creation, so the two never disagree). `downgrade` restores `vector`.
- **Temporal link cap 20 → 10 per unit.** Retrieval's graph spreading reads the top 10 links per
  unit (`per_source_limit=10`); links ranked beyond that were written and never read.
- **Event-log retention (`v6/events_retention.py`, daemon job `events-retention`, daily).**
  `tool_call` / `tool_result` rows of sessions idle for more than 30 days are serialised,
  compressed (zstd, or gzip when the zstd CLI is absent) and stored as one row in
  `ep_events.tool_event_archive`, so ordinary database backups include them. Rows are deleted
  only after a round-trip, row-count and SHA-256 check. User and assistant messages are never
  touched. The job deletes nothing unless the installed events pipeline carries the
  archived-session guard (see *Upgrade*), and `restore <archive_id>` re-inserts the rows.
- **Research workspace expiry fixed.** Reference manifests older than the 7-day read TTL were
  only swept on the `discover` path; the `user_research` path never swept, so dead manifests and
  their lock files accumulated. Both paths now share one sweep.
- **Recall trace slimming (`host-adapter/lib/trace_slim.py`).** Per-recall trace state stores ids
  and scores instead of full candidate text; full traces are kept for the last three recalls per
  session.
- **Storage benchmark (`v6/storage_bench.py`).** Freezes 30 real prompts and 100 known items on the
  first run and reports latency p50/p95, known-item hit@10 and top-10 overlap between runs, so a
  storage change can be judged on the same questions before and after.
- `admin restore` refreshes `memory_units_bm25` only when that materialized view exists
  (retrieval uses `search_vector` / `search_vector_cjk_v1`; the view was never queried).

### MCP adapter reliability

- **Live code reload.** Hosts keep the stdio MCP server alive for days, so a deploy used to leave
  them answering with old code. The adapter now fingerprints its sources and, *before* serving
  each request, re-executes itself in place when the code changed. stdin/stdout survive `exec`;
  the request being served and any buffered input are handed to the new image, so nothing is
  lost and the host connection is kept. A version that does not compile is not loaded.
  Disable with `EP_MCP_LIVE_RELOAD=0`.
- **Failure audit.** Tool failures record `error_kind`, exception type, a bounded traceback and the
  argument *shape* (types only, never values); argument validation errors are returned as
  JSON-RPC `-32602` instead of a generic error.
- **Schema drift audit.** Calls whose arguments violate the advertised `inputSchema` are recorded in
  `audit/mcp-schema-drift.jsonl` (rule and constraint only, never argument values) without being
  rejected, so declared schemas and server-side validation can no longer drift apart silently.
- `user_recall` now declares the `force_deep` / `facets` rule in its schema (`allOf`) instead of
  only enforcing it server-side.

### Preference consistency

- **One retrieval mode for both preference paths.** The prompt Hook hard-coded
  `semantic_recall="local"` while the MCP `user_preference` tool never passed it, so the same
  question could get different candidates depending on the entry point. Both now read
  `preference_semantic_mode()` from the shared guidance settings (default `local`).
- **Preference storage contract.** SQLite triggers reject a non-object `scope` on insert or update
  (`invalid_guidance_scope`) and make an active revision's payload immutable
  (`unit_revision_immutable`); withdrawals with a note remain allowed. `v6/ontology_hygiene.py`
  now writes a new revision instead of editing one in place. This closes the failure where a
  migration replaced structured `scope` objects with strings and readers crashed.

### Console

- Memory constellations share one visual language across facts, experiences, entities,
  multi-dimensional preferences and agent memory. Preferences show dashed region boxes around
  dimensions; region rings appear only in the overview, not when a single dimension or category
  is selected.
- Every graph view pages with **Load more** and states its window plainly
  ("Showing 240 of 13,609 records") instead of the former "current window … indexed total" text.

## Measured effect

One macOS installation, one personal bank of ~75k units, same prompts and known items before and
after (`v6/storage_bench.py`). The before/after includes all 6.2 changes together.

| Measure | Before | After |
| --- | --- | --- |
| `memory_units` table | 885 MB | 525 MB |
| Vector (HNSW) indexes | 146 MB | 69 MB |
| `ep_events.events` | 440 MB | 204 MB (237 MB of tool rows archived as 16.5 MB) |
| Research workspace | 79 MB | 6 MB |
| Trace write per recall | 4.89 MB | 0.91 MB |
| Recall latency p50 / p95 | 0.221 s / 0.354 s | 0.168–0.181 s / 0.286–0.354 s |
| Known-item hit@10 | 84 / 100 | 87 / 100 |
| Exact duplicates in top 10 | 9.7 % | 0.3 % |
| Low-value units in top 10 | 18.3 % | 0 % |

These are one installation's numbers, not a guarantee for other banks; run the benchmark on
your own data before and after upgrading.

## Upgrade

1. Run the API migrations (`e2f3a4b5c6d7` converts embeddings to `halfvec`; on ~75k units it took
   about 13 s, during which the table is locked). Set `EVOLVING_PROFILE_EMBEDDING_STORAGE=vector`
   *before* migrating to keep 32-bit vectors.
2. Restart the scheduler daemon; `memory-tiering` is scheduled only when a bank is configured
   (`EVOLVING_PROFILE_BANK_ID`). Optionally run `python -m v6.memory_tiering backfill --dry-run`,
   then without `--dry-run`, to tier existing memory once.
3. `events-retention` stays inert (`ledger_guard_missing`) unless your installed
   `events/ledger.py` skips rebuilding sessions listed in `ep_events.pruned_tool_sessions`.
   Without that guard a ledger rebuild of an archived session would delete its ledger rows.
4. Already-running MCP adapters from 6.1 do not have live reload yet; reconnect them once.

Rollback: `alembic downgrade d1e2f3a4b5c6` restores `vector`; `python -m v6.memory_tiering restore
--reason low_value` / `--reason duplicate` returns warm units; `python -m v6.events_retention
restore <archive_id>` re-inserts archived events.

## Known limits

- Warm units are not returned by vector recall by design; keyword and exhaustive search and
  `read_source` still find them.
- Links removed from warm units are re-created only by normal link maintenance after `restore`.
- The events pipeline that builds `ep_events` ledgers is installation-specific and not part of this
  package; see step 3 above.
- The governed HTTP MCP tests require fastmcp 2.x; with fastmcp 3 installed they fail before and
  after this release alike.
