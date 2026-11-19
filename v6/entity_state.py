"""Generalized bi-temporal mutable entity state ledger for EP v6 (project / service / client / config).

Separates mutable operational state from immutable `world` facts so status changes never fight stale memories.
"""
from __future__ import annotations

import argparse
import json
import os
from typing import Any


DDL = """
CREATE SCHEMA IF NOT EXISTS ep_events;

CREATE TABLE IF NOT EXISTS ep_events.entity_state (
    id bigserial PRIMARY KEY,
    entity_type text NOT NULL CHECK (entity_type IN ('project', 'service', 'client', 'config')),
    entity_key text NOT NULL,
    status text NOT NULL DEFAULT 'active',
    summary text,
    attributes jsonb NOT NULL DEFAULT '{}'::jsonb,
    hosts text[] NOT NULL DEFAULT '{}'::text[],
    valid_from timestamptz NOT NULL DEFAULT now(),
    valid_to timestamptz,
    superseded_by bigint,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_entity_state_active
    ON ep_events.entity_state (entity_type, entity_key)
    WHERE valid_to IS NULL;

CREATE INDEX IF NOT EXISTS idx_entity_state_type_status
    ON ep_events.entity_state (entity_type, status)
    WHERE valid_to IS NULL;
"""


def _connect_pg(dsn: str | None = None, *, user: str | None = None, port: int = 15432, dbname: str = "evolving_profile"):
    import psycopg2

    if dsn:
        return psycopg2.connect(dsn)
    kw: dict[str, Any] = {"port": port, "dbname": dbname}
    if user:
        kw["user"] = user
    return psycopg2.connect(**kw)


def ensure_entity_state_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()


def upsert_entity_state(
    conn,
    *,
    entity_type: str,
    entity_key: str,
    status: str,
    summary: str | None = None,
    attributes: dict[str, Any] | None = None,
    hosts: list[str] | None = None,
) -> str:
    """Bi-temporal upsert: if status changes, close old row (valid_to=now()) and insert a superseding row."""
    attrs_json = json.dumps(attributes or {}, ensure_ascii=False)
    hosts_arr = hosts or []
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, status, COALESCE(summary, '')
            FROM ep_events.entity_state
            WHERE entity_type = %s AND entity_key = %s AND valid_to IS NULL
            """,
            (entity_type, entity_key),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute(
                """
                INSERT INTO ep_events.entity_state (entity_type, entity_key, status, summary, attributes, hosts)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                """,
                (entity_type, entity_key, status, summary, attrs_json, hosts_arr),
            )
            return "inserted"

        old_id, old_status, _old_summary = row
        if old_status != status:
            cur.execute(
                """
                UPDATE ep_events.entity_state
                SET valid_to = now(), updated_at = now()
                WHERE id = %s
                """,
                (old_id,),
            )
            cur.execute(
                """
                INSERT INTO ep_events.entity_state (entity_type, entity_key, status, summary, attributes, hosts)
                VALUES (%s, %s, %s, %s, %s::jsonb, %s)
                RETURNING id
                """,
                (entity_type, entity_key, status, summary, attrs_json, hosts_arr),
            )
            new_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE ep_events.entity_state SET superseded_by = %s WHERE id = %s",
                (new_id, old_id),
            )
            return "superseded"

        cur.execute(
            """
            UPDATE ep_events.entity_state
            SET summary = %s, attributes = %s::jsonb, hosts = %s, updated_at = now()
            WHERE id = %s
            """,
            (summary, attrs_json, hosts_arr, old_id),
        )
        return "updated"


def sync_projects_to_entity_state(
    *, dsn: str | None = None, user: str | None = None, port: int = 15432, dbname: str = "evolving_profile"
) -> dict[str, Any]:
    """Project ep_events.project_state rows into generalized ep_events.entity_state."""
    conn = _connect_pg(dsn, user=user, port=port, dbname=dbname)
    counts = {"inserted": 0, "updated": 0, "superseded": 0}
    try:
        ensure_entity_state_schema(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT to_regclass('ep_events.project_state') IS NOT NULL
                """
            )
            if not cur.fetchone()[0]:
                return {"status": "no_project_state_table", **counts}

            cur.execute(
                """
                SELECT project_key, status, COALESCE(latest_summary, latest_user_request, ''),
                       hosts, session_count, turn_count, last_active_at
                FROM ep_events.project_state
                WHERE valid_to IS NULL
                """
            )
            rows = cur.fetchall()

        for pkey, status, summary, hosts, sessions, turns, last_active in rows:
            action = upsert_entity_state(
                conn,
                entity_type="project",
                entity_key=pkey,
                status=status or "active",
                summary=(summary or "")[:400],
                attributes={
                    "session_count": int(sessions or 0),
                    "turn_count": int(turns or 0),
                    "last_active_at": last_active.isoformat() if last_active else None,
                },
                hosts=list(hosts or []),
            )
            counts[action] = counts.get(action, 0) + 1
        conn.commit()
        return {"status": "ok", "total_projected": len(rows), **counts}
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=os.environ.get("PGUSER", "evolving_profile_admin"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PGPORT", "15432")))
    parser.add_argument("--dbname", default=os.environ.get("PGDATABASE", "evolving_profile"))
    parser.add_argument("--dsn", default=os.environ.get("EVOLVING_PROFILE_ADMIN_DSN"))
    args = parser.parse_args()
    res = sync_projects_to_entity_state(dsn=args.dsn, user=args.user, port=args.port, dbname=args.dbname)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
