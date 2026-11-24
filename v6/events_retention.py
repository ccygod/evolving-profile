"""Retention for ep_events.events tool rows (EP 6.2).

tool_call / tool_result rows are ~60% of ep_events.events and grow ~30 MB/day.
Their metadata is already projected into ep_events.ledger_actions, and nothing
reads the raw rows after the ledger is built (ledger.SEL never selects their
body; exhaustive search reads work_ledger/summaries/artifacts only).

Policy: for sessions idle > IDLE_DAYS (max occurred_at), the session's
tool_call/tool_result rows are serialised to JSON lines, zstd-compressed and
stored as ONE bytea row in ep_events.tool_event_archive (so the daily pg_dump
backs them up), verified by decompress + row count + sha256, and only then
deleted. ep_events.pruned_tool_sessions records the count so ledger.py can
refuse to rebuild a pruned session from incomplete events (see ledger guard).
user/assistant messages are never touched.

Safety gate: pruning runs only when the installed events pipeline
($EVOLVING_PROFILE_STATE_ROOT/events/ledger.py) carries the archived-session
guard; otherwise it reports ``skipped`` and deletes nothing.

Usage:
  python -m v6.events_retention [--dry-run] [--idle-days N] [--limit-sessions N]
  python -m v6.events_retention restore <archive_id>   # re-insert (ON CONFLICT DO NOTHING)
  python -m v6.events_retention stats
"""
import argparse
import datetime as dt
import hashlib
import json
import gzip
import os
import shutil
import subprocess
import sys

STATE = os.environ.get("EVOLVING_PROFILE_STATE_ROOT") or os.path.expanduser("~/.evolving-profile")
ZSTD = shutil.which("zstd")
IDLE_DAYS = 30
TOOL_TYPES = ("tool_call", "tool_result")

DDL = """
create table if not exists ep_events.tool_event_archive (
  archive_id   bigserial primary key,
  created_at   timestamptz not null default now(),
  idle_days    integer not null,
  sessions     integer not null,
  rows         integer not null,
  raw_bytes    bigint not null,
  raw_sha256   text not null,
  codec        text not null default 'zstd-19',
  payload      bytea not null
);
create table if not exists ep_events.pruned_tool_sessions (
  host text not null, session_id text not null,
  tool_events integer not null, pruned_at timestamptz not null default now(),
  archive_id bigint not null references ep_events.tool_event_archive(archive_id),
  primary key (host, session_id)
);
"""


def dsn():
    env = os.environ.get("EVOLVING_PROFILE_API_DATABASE_URL")
    if env:
        return env.strip().strip('"').replace("postgresql+asyncpg", "postgresql")
    for line in open(os.path.join(STATE, "profiles", "evolving-profile-api.env"), encoding="utf-8"):
        if line.startswith("EVOLVING_PROFILE_API_DATABASE_URL="):
            return line.split("=", 1)[1].strip().strip('"').replace("postgresql+asyncpg", "postgresql")
    raise SystemExit("database url not configured")


def connect():
    import psycopg2
    conn = psycopg2.connect(dsn())
    conn.autocommit = False
    return conn


CODEC = "zstd-19" if ZSTD else "gzip-9"


def compress(data, codec=CODEC):
    if codec.startswith("zstd"):
        return subprocess.run([ZSTD, "-q", "-c", "-19", "-T0"], input=data, capture_output=True, check=True).stdout
    return gzip.compress(data, 9)


def decompress(data, codec):
    if codec.startswith("zstd"):
        if not ZSTD:
            raise SystemExit("archive uses zstd but the zstd CLI is not installed")
        return subprocess.run([ZSTD, "-q", "-c", "-d"], input=data, capture_output=True, check=True).stdout
    return gzip.decompress(data)


def ledger_guard_installed():
    path = os.path.join(STATE, "events", "ledger.py")
    try:
        return "pruned_tool_sessions" in open(path, encoding="utf-8").read()
    except OSError:
        return False


def candidates(cur, idle_days, limit):
    cur.execute(
        "select host, session_id, count(*) filter (where event_type in %s) n from ep_events.events "
        "group by 1,2 having max(occurred_at) < now() - make_interval(days => %s) "
        "and count(*) filter (where event_type in %s) > 0 order by 1,2" + (" limit %s" % int(limit) if limit else ""),
        (TOOL_TYPES, idle_days, TOOL_TYPES))
    return cur.fetchall()


def prune(dry_run, idle_days, limit):
    if not ledger_guard_installed():
        return {"status": "skipped", "reason": "ledger_guard_missing"}
    conn = connect()
    with conn.cursor() as cur:
        cur.execute(DDL)
        sessions = candidates(cur, idle_days, limit)
        total = sum(n for _, _, n in sessions)
        if dry_run or not sessions:
            conn.rollback()
            return {"dry_run": dry_run, "sessions": len(sessions), "tool_rows": total}
        cur.execute("create temp table _prune(host text, session_id text) on commit drop")
        from psycopg2.extras import execute_values
        execute_values(cur, "insert into _prune values %s", [(h, s) for h, s, _ in sessions])
        cur.execute("select row_to_json(e)::text from ep_events.events e join _prune p using (host, session_id) "
                    "where e.event_type in %s order by e.host, e.session_id, e.seq, e.event_id", (TOOL_TYPES,))
        lines = [r[0] for r in cur.fetchall()]
        raw = ("\n".join(lines) + "\n").encode("utf-8")
        payload = compress(raw)
        # Verify before deleting anything: round-trip, row count and hash.
        back = decompress(payload, CODEC)
        if back != raw or back.count(b"\n") != len(lines) or len(lines) != total:
            conn.rollback()
            raise SystemExit("archive_verification_failed")
        sha = hashlib.sha256(raw).hexdigest()
        cur.execute("insert into ep_events.tool_event_archive(idle_days, sessions, rows, raw_bytes, raw_sha256, codec, payload) "
                    "values (%s,%s,%s,%s,%s,%s,%s) returning archive_id",
                    (idle_days, len(sessions), len(lines), len(raw), sha, CODEC, payload))
        aid = cur.fetchone()[0]
        cur.execute("select sha256(payload) = sha256(%s::bytea) from ep_events.tool_event_archive where archive_id=%s",
                    (payload, aid))
        if not cur.fetchone()[0]:
            conn.rollback()
            raise SystemExit("archive_store_verification_failed")
        execute_values(cur, "insert into ep_events.pruned_tool_sessions(host, session_id, tool_events, archive_id) values %s "
                       "on conflict (host, session_id) do update set tool_events = ep_events.pruned_tool_sessions.tool_events "
                       "+ excluded.tool_events, pruned_at = now(), archive_id = excluded.archive_id",
                       [(h, s, n, aid) for h, s, n in sessions])
        cur.execute("delete from ep_events.events e using _prune p where e.host=p.host and e.session_id=p.session_id "
                    "and e.event_type in %s", (TOOL_TYPES,))
        deleted = cur.rowcount
        if deleted != total:
            conn.rollback()
            raise SystemExit(f"delete_count_mismatch {deleted} != {total}")
    conn.commit()
    result = {"archive_id": aid, "sessions": len(sessions), "rows": total, "raw_mb": round(len(raw) / 1048576, 1),
              "stored_mb": round(len(payload) / 1048576, 2)}
    log(result)
    return result


def restore(aid):
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("select payload, raw_sha256, rows, codec from ep_events.tool_event_archive where archive_id=%s", (aid,))
        payload, sha, rows, codec = cur.fetchone()
        raw = decompress(bytes(payload), codec)
        if hashlib.sha256(raw).hexdigest() != sha:
            raise SystemExit("archive_hash_mismatch")
        recs = [json.loads(l) for l in raw.decode("utf-8").splitlines() if l]
        cols = list(recs[0].keys())
        from psycopg2.extras import execute_values, Json
        vals = [tuple(Json(r[c]) if c == "tool" and r[c] is not None else r[c] for c in cols) for r in recs]
        execute_values(cur, "insert into ep_events.events (%s) values %%s on conflict (event_id) do nothing" % ",".join(cols),
                       vals, page_size=1000)
        inserted = cur.rowcount
        cur.execute("delete from ep_events.pruned_tool_sessions where archive_id=%s", (aid,))
    conn.commit()
    out = {"restored_archive": aid, "rows": rows, "inserted": inserted}
    log(out)
    return out


def stats():
    conn = connect()
    with conn.cursor() as cur:
        cur.execute(DDL)
        cur.execute("select count(*), coalesce(sum(rows),0), coalesce(sum(raw_bytes),0), coalesce(sum(length(payload)),0) "
                    "from ep_events.tool_event_archive")
        a, r, raw, st = cur.fetchone()
        cur.execute("select pg_size_pretty(pg_total_relation_size('ep_events.events'))")
        size = cur.fetchone()[0]
    conn.commit()
    a, r, raw, st = int(a), int(r), int(raw), int(st)
    return {"archives": a, "archived_rows": r, "raw_mb": round(raw / 1048576, 1), "stored_mb": round(st / 1048576, 2),
            "events_table": size}


def log(obj):
    with open(os.path.join(STATE, "logs", "events-retention.log"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"at": dt.datetime.now().isoformat(timespec="seconds"), **obj}) + "\n")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "restore":
        print(json.dumps(restore(int(sys.argv[2]))))
    elif len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(stats()))
    else:
        ap = argparse.ArgumentParser()
        ap.add_argument("--dry-run", action="store_true")
        ap.add_argument("--idle-days", type=int, default=IDLE_DAYS)
        ap.add_argument("--limit-sessions", type=int, default=0)
        a = ap.parse_args()
        print(json.dumps(prune(a.dry_run, a.idle_days, a.limit_sessions)))
