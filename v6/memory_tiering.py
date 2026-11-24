"""EP memory admission & hot/warm tiering (EP 6.2).

Hot memories carry an embedding (vector search, HNSW) and graph links.
Warm memories keep their row and text (keyword/exhaustive search, read_source
and provenance still work) but drop the embedding and links, so they stop
occupying vector-index space and candidate slots. Every move is reversible:
the embedding is kept in `memory_unit_warm` and `restore` puts it back.

Rules (main bank, world/experience/observation unless noted):
  low_value   - recall_demoted / assistant_unconfirmed / fragment_noise /
                "Assistant statement (not user-confirmed)" prefixed units
  exact_dup   - same normalised text as an earlier hot unit (world/experience)
  near_dup    - cosine >= NEAR_DUP_COSINE to a hot unit of the same bank and
                fact_type (world/experience only; observations are already
                consolidated summaries and are left alone)
The representative of a duplicate group is: not low-value > higher proof_count
> earlier created_at. It gets proof_count += 1 and the duplicate id/document
appended to metadata.merged_duplicates (bounded), so evidence is not lost.

Usage (bank from EVOLVING_PROFILE_BANK_ID or EP_TIERING_BANK):
  python -m v6.memory_tiering backfill [--dry-run]   # whole bank, one-off
  python -m v6.memory_tiering admit    [--dry-run]   # units since the last
                                                     # watermark (daemon: 10 min)
  python -m v6.memory_tiering restore <unit_id>|--reason <r> [--dry-run]
  python -m v6.memory_tiering stats

Database: EVOLVING_PROFILE_API_DATABASE_URL, or the same key in
$EVOLVING_PROFILE_STATE_ROOT/profiles/evolving-profile-api.env.
"""
import json
import os
import re
import sys
import time

import psycopg2
import psycopg2.extras

STATE = os.environ.get("EVOLVING_PROFILE_STATE_ROOT") or os.path.expanduser("~/.evolving-profile")
BANK = os.environ.get("EP_TIERING_BANK") or os.environ.get("EVOLVING_PROFILE_BANK_ID") or ""
NEAR_DUP_COSINE = float(os.environ.get("EP_TIERING_NEAR_DUP_COSINE", "0.98"))
WATERMARK = os.path.join(STATE, "control-plane", "memory-tiering-watermark.json")
LOG = os.path.join(STATE, "logs", "memory-tiering.log")
MAX_MERGED_REFS = 50

LOW_VALUE_SQL = """(
  u.metadata->>'recall_demoted'='true' or u.metadata->'metadata'->>'recall_demoted'='true'
  or u.metadata->>'trust_level'='assistant_unconfirmed' or u.metadata->'metadata'->>'trust_level'='assistant_unconfirmed'
  or u.metadata->>'fragment_noise'='true' or u.metadata->'metadata'->>'fragment_noise'='true'
  or u.text like 'Assistant statement (not user-confirmed)%%' or u.text like '【助手·未确认%%')"""
# NOTE: always executed with parameters, hence %% for a literal %.


def dsn():
    env = os.environ.get("EVOLVING_PROFILE_API_DATABASE_URL")
    if env:
        return env.strip().strip('"').replace("postgresql+asyncpg", "postgresql")
    for line in open(os.path.join(STATE, "profiles", "evolving-profile-api.env"), encoding="utf-8"):
        if line.startswith("EVOLVING_PROFILE_API_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').replace("postgresql+asyncpg", "postgresql")
    raise SystemExit("database url not configured")


def log(event):
    event = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), **event}
    os.makedirs(os.path.dirname(LOG), exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(json.dumps(event, ensure_ascii=False))


def ensure_schema(cur):
    # The warm copy uses the same type as memory_units.embedding (halfvec since 6.2).
    cur.execute("SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
                "WHERE attrelid = 'memory_units'::regclass AND attname = 'embedding'")
    emb_type = (cur.fetchone() or ["vector(384)"])[0]
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS memory_unit_warm (
          unit_id uuid PRIMARY KEY,
          bank_id text NOT NULL,
          embedding {emb_type},
          reason text NOT NULL,
          merged_into uuid,
          moved_at timestamptz NOT NULL DEFAULT now(),
          links_removed integer NOT NULL DEFAULT 0
        )""")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_memory_unit_warm_reason ON memory_unit_warm(reason)")


def move_to_warm(cur, unit_ids, reason, merged_into=None, dry=False):
    """Move units to warm. merged_into: dict unit_id -> representative id."""
    if not unit_ids:
        return 0
    if dry:
        return len(unit_ids)
    ids = list(unit_ids)
    cur.execute("""
        INSERT INTO memory_unit_warm(unit_id, bank_id, embedding, reason, merged_into)
        SELECT u.id, u.bank_id, u.embedding, %s, (m.rep)::uuid
        FROM memory_units u
        LEFT JOIN (SELECT unnest(%s::uuid[]) AS id, unnest(%s::text[]) AS rep) m ON m.id = u.id
        WHERE u.id = ANY(%s::uuid[]) AND u.embedding IS NOT NULL
        ON CONFLICT (unit_id) DO NOTHING""",
                (reason, ids, [(merged_into or {}).get(i) for i in ids], ids))
    cur.execute("""
        WITH d AS (DELETE FROM memory_links WHERE from_unit_id = ANY(%s::uuid[]) OR to_unit_id = ANY(%s::uuid[]) RETURNING from_unit_id, to_unit_id)
        SELECT id, count(*) FROM (SELECT from_unit_id id FROM d UNION ALL SELECT to_unit_id FROM d) x WHERE id = ANY(%s::uuid[]) GROUP BY id""",
                (ids, ids, ids))
    for uid, n in cur.fetchall():
        cur.execute("UPDATE memory_unit_warm SET links_removed = links_removed + %s WHERE unit_id = %s", (n, uid))
    cur.execute("""
        UPDATE memory_units SET embedding = NULL,
          metadata = coalesce(metadata,'{}'::jsonb) || jsonb_build_object('memory_tier','warm','warm_reason',%s)
        WHERE id = ANY(%s::uuid[])""", (reason, ids))
    return len(ids)


def credit_representatives(cur, merged_into, dry=False):
    """Representative gets proof_count+1 and a bounded list of merged duplicates."""
    if dry or not merged_into:
        return
    by_rep = {}
    for dup, rep in merged_into.items():
        by_rep.setdefault(rep, []).append(dup)
    for rep, dups in by_rep.items():
        cur.execute("""
            UPDATE memory_units r SET proof_count = coalesce(r.proof_count,0) + %s,
              metadata = coalesce(r.metadata,'{}'::jsonb) || jsonb_build_object('merged_duplicates',
                 (SELECT coalesce(jsonb_agg(x),'[]'::jsonb) FROM (
                    SELECT x FROM jsonb_array_elements(coalesce(r.metadata->'merged_duplicates','[]'::jsonb)) x
                    UNION ALL
                    SELECT jsonb_build_object('unit_id', d.id::text, 'document_id', d.document_id) FROM memory_units d WHERE d.id = ANY(%s::uuid[])
                    LIMIT %s) y))
            WHERE r.id = %s""", (len(dups), dups, MAX_MERGED_REFS, rep))


NUMBER_RE = re.compile(r"\d+(?:[.,:/-]\d+)*")


def same_facts(a, b, when_a, when_b):
    """Near-duplicate guard: high cosine is not enough.
    Two statements are only the same memory if they carry the same numbers
    (progress counts, amounts, versions, dates written in the text) and their
    event times are within a day. Otherwise they are distinct facts/states."""
    if sorted(NUMBER_RE.findall(a or "")) != sorted(NUMBER_RE.findall(b or "")):
        return False
    if (when_a is None) != (when_b is None):
        return False
    if when_a is not None and abs((when_a - when_b).total_seconds()) > 86400:
        return False
    return True


def pick_rep(rows):
    # rows: (id, low_value, proof_count, created_at)
    # low may be NULL (no metadata) -> treat as not low-value; created_at None sorts last
    return sorted(rows, key=lambda r: (bool(r[1]), -(r[2] or 0), str(r[3] or "9999")))[0][0]


def classify(cur, scope_sql, scope_args):
    """Return (low_value_ids, merged_into) for units in scope that are still hot."""
    cur.execute(f"""SELECT u.id::text FROM memory_units u
        WHERE u.bank_id=%s AND u.embedding IS NOT NULL AND {LOW_VALUE_SQL} AND {scope_sql}""", (BANK, *scope_args))
    low = [r[0] for r in cur.fetchall()]
    lowset = set(low)
    merged = {}
    # exact duplicates (normalised text) among hot world/experience units
    cur.execute(f"""
        WITH s AS (
          SELECT u.id::text id, u.fact_type, md5(lower(regexp_replace(u.text,'\\s+','','g'))) h,
                 {LOW_VALUE_SQL} low, u.proof_count, u.created_at, ({scope_sql}) in_scope
          FROM memory_units u WHERE u.bank_id=%s AND u.embedding IS NOT NULL AND u.fact_type IN ('world','experience'))
        SELECT h, fact_type, array_agg(json_build_array(id, low, proof_count, created_at, in_scope))
        FROM s GROUP BY h, fact_type HAVING count(*) > 1 AND bool_or(in_scope)""", (*scope_args, BANK))
    for _, _, members in cur.fetchall():
        rows = [(m[0], m[1], m[2], m[3]) for m in members]
        rep = pick_rep(rows)
        for m in members:
            if m[0] != rep and m[4] and m[0] not in lowset:
                merged[m[0]] = rep
    # near duplicates via the HNSW index (nearest hot neighbour of the same fact type).
    # bank_id / fact_type are bound as constants so the per-bank partial HNSW
    # index (WHERE fact_type = .. AND bank_id = ..) is used for every probe.
    cur.execute("SET LOCAL hnsw.ef_search = 40")
    for ft in ("world", "experience"):
        cur.execute(f"""
            SELECT u.id::text, n.id::text, n.sim, {LOW_VALUE_SQL} AS u_low, n.low, u.proof_count, n.proof_count, u.created_at, n.created_at,
                   u.text, n.text, coalesce(u.occurred_start, u.event_date), n.when_at
            FROM memory_units u
            CROSS JOIN LATERAL (
              SELECT v.id, 1 - (v.embedding <=> u.embedding) AS sim, v.proof_count, v.created_at, v.text,
                     coalesce(v.occurred_start, v.event_date) AS when_at,
                     (v.metadata->>'recall_demoted'='true' or v.metadata->>'trust_level'='assistant_unconfirmed'
                      or v.text like 'Assistant statement (not user-confirmed)%%') AS low
              FROM memory_units v
              WHERE v.bank_id = %s AND v.fact_type = %s AND v.id <> u.id AND v.embedding IS NOT NULL
              ORDER BY v.embedding <=> u.embedding LIMIT 1) n
            WHERE u.bank_id = %s AND u.fact_type = %s AND u.embedding IS NOT NULL
              AND {scope_sql} AND n.sim >= %s""", (BANK, ft, BANK, ft, *scope_args, NEAR_DUP_COSINE))
        for uid, nid, sim, u_low, n_low, u_pc, n_pc, u_at, n_at, u_text, n_text, u_when, n_when in cur.fetchall():
            if uid in lowset or uid in merged or nid in merged:
                continue
            if not same_facts(u_text, n_text, u_when, n_when):
                continue
            rep = pick_rep([(uid, u_low, u_pc, u_at), (nid, n_low, n_pc, n_at)])
            dup = nid if rep == uid else uid
            if dup in lowset or merged.get(rep) == dup:
                continue
            merged[dup] = merged.get(rep, rep)
    # never let a representative itself be merged away in the same pass
    for dup in list(merged):
        if dup in merged.values():
            merged.pop(dup)
    return low, merged


def run(mode, dry):
    conn = psycopg2.connect(dsn())
    conn.autocommit = False
    cur = conn.cursor()
    ensure_schema(cur)
    started = time.time()
    if mode == "backfill":
        scope_sql, args = "TRUE", ()
    else:
        try:
            mark = json.load(open(WATERMARK))["created_after"]
        except (OSError, ValueError, KeyError):
            mark = time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(time.time() - 3600))
        scope_sql, args = "u.created_at > %s::timestamptz", (mark,)
    cur.execute("SELECT max(created_at) FROM memory_units WHERE bank_id=%s", (BANK,))
    newest = cur.fetchone()[0]
    low, merged = classify(cur, scope_sql, args)
    n_low = move_to_warm(cur, low, "low_value", dry=dry)
    n_dup = move_to_warm(cur, list(merged), "duplicate", merged_into=merged, dry=dry)
    credit_representatives(cur, merged, dry=dry)
    if dry:
        conn.rollback()
    else:
        conn.commit()
        if newest is not None:
            os.makedirs(os.path.dirname(WATERMARK), exist_ok=True)
            json.dump({"created_after": newest.isoformat()}, open(WATERMARK, "w"))
    log({"mode": mode, "dry_run": dry, "low_value_to_warm": n_low, "duplicates_merged": n_dup,
         "representatives": len(set(merged.values())), "seconds": round(time.time() - started, 1)})
    if mode == "backfill" and not dry:
        json.dump(merged, open(os.path.join(STATE, "control-plane", "memory-tiering-merged.json"), "w"))


def restore(target, by_reason=False, dry=False):
    conn = psycopg2.connect(dsn())
    cur = conn.cursor()
    where = "reason = %s" if by_reason else "unit_id = %s::uuid"
    cur.execute(f"SELECT count(*) FROM memory_unit_warm WHERE {where}", (target,))
    n = cur.fetchone()[0]
    if not dry:
        cur.execute(f"""UPDATE memory_units u SET embedding = w.embedding,
              metadata = (coalesce(u.metadata,'{{}}'::jsonb) - 'memory_tier' - 'warm_reason')
            FROM memory_unit_warm w WHERE w.unit_id = u.id AND w.{where.replace('unit_id', 'unit_id')}""", (target,))
        cur.execute(f"DELETE FROM memory_unit_warm WHERE {where}", (target,))
        conn.commit()
    log({"mode": "restore", "target": target, "units": n, "dry_run": dry,
         "note": "links are re-created by the normal link maintenance on next write"})


def stats():
    conn = psycopg2.connect(dsn())
    cur = conn.cursor()
    ensure_schema(cur)
    conn.commit()
    cur.execute("SELECT reason, count(*) FROM memory_unit_warm GROUP BY 1")
    warm = dict(cur.fetchall())
    cur.execute("SELECT count(*) FILTER (WHERE embedding IS NOT NULL), count(*) FROM memory_units WHERE bank_id=%s", (BANK,))
    hot, total = cur.fetchone()
    print(json.dumps({"bank": BANK, "hot": hot, "total": total, "warm": warm}, ensure_ascii=False))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] not in ("-h", "--help") and not BANK:
        raise SystemExit("set EVOLVING_PROFILE_BANK_ID (or EP_TIERING_BANK) to the bank to tier")
    dry = "--dry-run" in args
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
    elif args[0] in ("backfill", "admit"):
        run(args[0], dry)
    elif args[0] == "restore":
        if "--reason" in args:
            restore(args[args.index("--reason") + 1], by_reason=True, dry=dry)
        else:
            restore(args[1], dry=dry)
    elif args[0] == "stats":
        stats()
