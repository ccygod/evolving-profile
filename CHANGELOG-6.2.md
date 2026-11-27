# Evolving Profile 6.2.2

## 6.2.2 — 2026-11-27

- **Link-map prompt list loads in well under a second.** The Console asked the status service
  first with a 3 s timeout; the status service needed ~3.2 s, so every load waited 3 s, threw the
  answer away and fell back to the Console's own list (~4 s per load in total). The Console's own
  list is now the primary source — it also verifies Claude, Trae and other hosts against their
  transcripts, where the status service's classifier verified only Codex turns (8 vs 267 natural
  prompts on one install). The status service remains the fallback
  (`EVOLVING_PROFILE_PROMPT_LIST_SOURCE=status` prefers it). Measured on one install: warm
  4.3 s → 0.5–0.7 s, first request after a restart ~2 s, identical output.
- Receipt directories are indexed once per process (binding ids and timestamps only, re-read when
  a file changes) instead of parsing every receipt on every request; only the receipts that match
  the returned rows are loaded in full.
- The status service caches its bounded hook-receipt projection per file and projects new files
  in one batched `jq` call instead of one process per file (~300 per request); its prompt list
  dropped from ~3.1 s to ~1.5 s.
- Real place, organisation and person names that earlier releases carried in test samples and
  keyword lists are replaced with example names throughout the repository and its history.

## 6.2.1 — 2026-11-26

6.2.1 brings this repository in line with the running installation it is cut from. Earlier
releases synced selected files only, so components finished after a release cut never shipped —
most visibly the console link map, which fresh installs still showed as the old fixed picture
with editable label boxes. A module-by-module comparison found about 110 files whose code
differed and about 140 files that existed only in the running installation; all of them are now
reviewed and synced, keeping this repository's sanitised wording and its own later fixes.

### What fresh installs now get

- **Data-driven link map ("Prompt journey").** Each prompt's path through the serial spine and the
  parallel user-memory, agent-memory and external-RAG lanes, drawn from its receipts, with
  per-node counts and dimmed nodes for steps without a receipt. Read-only APIs `prompt-trace`,
  `tool-stats` and `host-catalog`; the prompt list covers Claude, Trae, WorkBuddy and Doubao Work.
- **Console:** Bank selection and access notice, exhaustive-search panel, consolidation progress,
  Claude-native prompt verification.
- **API / Worker:** worker leases with fencing (`claim_version`, `lease_expires_at`, heartbeats) so
  two workers cannot both process one operation; runtime identity for API, worker and telemetry;
  an extraction run where every chunk fails is an error, not an empty success.
- **Host adapter:** host-fit and host projection, personal-record search, supplied-material
  handling, scenario form sources and automatic draft/attribution repair, memory envelope, and
  Claude mirror context — 37 modules plus their tests.
- **Guidance:** unit consolidation and corpus-centred semantic thresholds.

### Fixes found during the sync

- **Migration id collision.** 6.2.0 published the halfvec migration as `e2f3a4b5c6d7`, an id the
  worker-lease migration already used. The worker-lease migration keeps `e2f3a4b5c6d7`; halfvec is
  now `f3a4b5c6d7e8` and also adds the lease columns, so a database stamped by 6.2.0 (which ran
  halfvec under the old id) still receives them. Both paths were run on a scratch database.
- **Retention worker lost acknowledgement.** An early "processing" return skipped recovery of a
  submitted batch whose acknowledgement was lost; recovery now runs first.
- **Host traces and isolated installs.** Archived Codex sessions and WorkBuddy traces outside the
  state root are read only for the default install, so tests and second installs no longer count
  this machine's real sessions (`EVOLVING_PROFILE_HOST_ARCHIVED_SESSIONS_ROOT`,
  `EVOLVING_PROFILE_WORKBUDDY_TRACES_ROOT` override).
- `host-adapter/lib/content.py` imports on Python 3.9 again (`from __future__ import annotations`).
- Scenario overview counts automatic AI reports both under the new status and on older
  "waiting for source" rows.

### Verification (2026-11-26)

| Scope | Result |
| --- | --- |
| Host adapter | 2124 passed, 17 skipped (governed-HTTP tests excluded: fastmcp 3 in the test env) |
| Guidance / controller / status / v6 | 162 / 184 / 56 / 24 passed |
| Console | 700 passed; TypeScript and production build pass |
| API (full suite, scratch Postgres) | 5606 passed vs 5568 before the sync; every remaining failure also fails before the sync, fails on the running installation's code, or is timing-dependent under parallel load and passes alone |
| Migrations | fresh install and 6.2.0 upgrade both run on a scratch database |
| Release checks | `release-parity.py`, `release-preflight.py`, `verify-package.sh` (secret and personal-data scan) pass |
| Browser | link map, agent memory and overview pages render against a running install |

### Release process

- `scripts/release-parity.py` compares every module with the source an installation runs and fails
  on any unexplained difference. Paths, personal-data patterns and accepted differences stay in a
  local config that is never committed. See `docs/RELEASING.md`.

## 6.2.0 — 2026-11-24

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
- **Half-precision embeddings (migration `f3a4b5c6d7e8`; published as `e2f3a4b5c6d7` in 6.2.0).** `memory_units.embedding` is stored as
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

1. Run the API migrations (`f3a4b5c6d7e8` converts embeddings to `halfvec`; on ~75k units it took
   about 13 s, during which the table is locked). Set `EVOLVING_PROFILE_EMBEDDING_STORAGE=vector`
   *before* migrating to keep 32-bit vectors.
2. Restart the scheduler daemon; `memory-tiering` is scheduled only when a bank is configured
   (`EVOLVING_PROFILE_BANK_ID`). Optionally run `python -m v6.memory_tiering backfill --dry-run`,
   then without `--dry-run`, to tier existing memory once.
3. `events-retention` stays inert (`ledger_guard_missing`) unless your installed
   `events/ledger.py` skips rebuilding sessions listed in `ep_events.pruned_tool_sessions`.
   Without that guard a ledger rebuild of an archived session would delete its ledger rows.
4. Already-running MCP adapters from 6.1 do not have live reload yet; reconnect them once.

Rollback: `alembic downgrade e2f3a4b5c6d7` restores `vector`; `python -m v6.memory_tiering restore
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
