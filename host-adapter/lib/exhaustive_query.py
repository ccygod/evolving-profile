"""Exhaustive, zero-truncation SQL query engine for Evolving Profile (EP).

Provides deterministic, 100%-coverage retrieval across:
1. public.memory_units (idx_memory_units_cjk_v1 GIN full-text + unit_entities + occurred/mentioned time)
2. ep_events.work_ledger (started_at + project_key + user_request + summary_line)
3. ep_events.summaries (period_start / period_end + project_keys)

Also returns an explicit `source_coverage` manifest showing which hosts have
data in the requested window and how many memory_units have true `occurred_start`
vs `mentioned_at` timestamps.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
from pathlib import Path
from typing import Any

EVENTS_DIR = Path(os.environ.get("EVOLVING_PROFILE_HOME") or Path.home() / ".evolving-profile") / "events"
DEFAULT_BANK = os.environ.get("EVOLVING_PROFILE_BANK_ID", "")


def _connect():
    if str(EVENTS_DIR) not in sys.path:
        sys.path.insert(0, str(EVENTS_DIR))
    import envelope
    return envelope.connect()


def _parse_date(value: str | None, *, end_of_day: bool = False) -> dt.datetime | None:
    if not value or not str(value).strip():
        return None
    raw = str(value).strip()
    if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
        raw += "T23:59:59+08:00" if end_of_day else "T00:00:00+08:00"
    parsed = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    return parsed


def _keyword_clause(columns: list[str], keywords: list[str], *, cjk_vector_col: str | None = None, match_mode: str = "any") -> tuple[str, list[Any]]:
    cleaned = [k.strip() for k in (keywords or []) if isinstance(k, str) and k.strip()]
    if not cleaned:
        return "", []
    parts = []
    params: list[Any] = []
    for kw in cleaned[:16]:
        sub_ors = []
        if cjk_vector_col and len(kw) >= 2:
            sub_ors.append(f"{cjk_vector_col} @@ plainto_tsquery('simple', %s)")
            params.append(kw)
        like = f"%{kw}%"
        for col in columns:
            sub_ors.append(f"COALESCE({col}, '') ILIKE %s")
            params.append(like)
        parts.append("(" + " OR ".join(sub_ors) + ")")
    joiner = " AND " if str(match_mode or "any").lower() == "all" else " OR "
    return "(" + joiner.join(parts) + ")", params


def _has_table(cur, schema: str, table: str) -> bool:
    cur.execute(
        "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema=%s AND table_name=%s)",
        (schema, table),
    )
    return bool(cur.fetchone()[0])


def exhaustive_search(
    *,
    bank_id: str = DEFAULT_BANK,
    start_date: str | None = None,
    end_date: str | None = None,
    keywords: list[str] | None = None,
    project: str | None = None,
    host: str | None = None,
    fact_types: list[str] | None = None,
    entity_category: str | None = None,
    time_mode: str = "either",
    group_by: str = "project",
    limit: int = 100,
    offset: int = 0,
    include_summaries: bool = True,
    match_mode: str = "any",
    include_all_banks: bool = True,
) -> dict[str, Any]:
    """Run an exhaustive SQL scan across memory_units, work_ledger, and summaries."""
    t_start = _parse_date(start_date, end_of_day=False)
    t_end = _parse_date(end_date, end_of_day=True)
    limit = max(1, min(int(limit or 100), 500))
    offset = max(0, int(offset or 0))
    kw_list = [k for k in (keywords or []) if isinstance(k, str) and k.strip()]
    time_mode = time_mode if time_mode in ("either", "occurred", "mentioned") else "either"

    conn = _connect()
    try:
        cur = conn.cursor()

        # 0. Honest Source Coverage Manifest for the requested window
        cov_where = ["1=1"]
        cov_params: list[Any] = []
        if t_start is not None:
            cov_where.append("started_at >= %s")
            cov_params.append(t_start)
        if t_end is not None:
            cov_where.append("started_at <= %s")
            cov_params.append(t_end)
        cur.execute(
            f"""
            SELECT host, COUNT(*) AS turns, COUNT(DISTINCT session_id) AS sessions,
                   MIN(started_at AT TIME ZONE 'Asia/Shanghai')::text,
                   MAX(started_at AT TIME ZONE 'Asia/Shanghai')::text
            FROM ep_events.work_ledger
            WHERE {' AND '.join(cov_where)}
            GROUP BY host
            ORDER BY turns DESC
            """,
            cov_params,
        )
        host_coverage = [
            {"host": r[0], "turns": r[1], "sessions": r[2], "first_at": r[3], "last_at": r[4]}
            for r in cur.fetchall()
        ]

        # 1. Exhaustive scan on ep_events.work_ledger
        has_proj_alias = _has_table(cur, "ep_events", "project_aliases")
        wl_where = ["1=1"]
        wl_params: list[Any] = []
        if t_start is not None:
            wl_where.append("w.started_at >= %s")
            wl_params.append(t_start)
        if t_end is not None:
            wl_where.append("w.started_at <= %s")
            wl_params.append(t_end)
        if host and host.strip():
            wl_where.append("w.host = %s")
            wl_params.append(host.strip())
        if project and project.strip():
            wl_where.append("(COALESCE(w.project_key,'') ILIKE %s OR COALESCE(w.project_path,'') ILIKE %s)")
            plike = f"%{project.strip()}%"
            wl_params.extend([plike, plike])
        kw_sql, kw_params = _keyword_clause(
            ["w.user_request", "w.final_answer", "w.project_key", "w.summary_line"], kw_list, match_mode=match_mode
        )
        if kw_sql:
            wl_where.append(kw_sql)
            wl_params.extend(kw_params)

        wl_join = (
            "LEFT JOIN ep_events.project_aliases pa ON pa.raw_project_key = w.project_key"
            if has_proj_alias
            else ""
        )
        proj_col = (
            "COALESCE(NULLIF(pa.canonical_project, ''), NULLIF(w.project_key, ''), NULLIF(w.project_path, ''), '(未归类项目)')"
            if has_proj_alias
            else "COALESCE(NULLIF(w.project_key, ''), NULLIF(w.project_path, ''), '(未归类项目)')"
        )
        wl_cond = " AND ".join(wl_where)
        cur.execute(f"SELECT COUNT(*) FROM ep_events.work_ledger w WHERE {wl_cond}", wl_params)
        wl_total = int(cur.fetchone()[0])

        group_expr = {
            "project": proj_col,
            "month": "to_char(w.started_at AT TIME ZONE 'Asia/Shanghai', 'YYYY-MM')",
            "week": "to_char(date_trunc('week', w.started_at AT TIME ZONE 'Asia/Shanghai'), 'YYYY-MM-DD')",
            "host": "w.host",
        }.get(group_by, proj_col)

        cur.execute(
            f"""
            SELECT {group_expr} AS grp,
                   COUNT(*) AS turn_count,
                   COUNT(DISTINCT w.session_id) AS session_count,
                   MIN(w.started_at AT TIME ZONE 'Asia/Shanghai')::text AS first_at,
                   MAX(w.started_at AT TIME ZONE 'Asia/Shanghai')::text AS last_at,
                   array_agg(DISTINCT w.host) AS hosts
            FROM ep_events.work_ledger w
            {wl_join}
            WHERE {wl_cond}
            GROUP BY grp
            ORDER BY last_at DESC NULLS LAST
            """,
            wl_params,
        )
        wl_groups = [
            {
                "group": r[0],
                "turn_count": r[1],
                "session_count": r[2],
                "first_at": r[3],
                "last_at": r[4],
                "hosts": r[5],
            }
            for r in cur.fetchall()
        ]

        cur.execute(
            f"""
            SELECT w.host, w.session_id, w.turn_seq, {proj_col} AS project_key,
                   (w.started_at AT TIME ZONE 'Asia/Shanghai')::text AS started_at,
                   w.status, LEFT(COALESCE(w.user_request, ''), 240) AS user_request,
                   LEFT(COALESCE(w.summary_line, w.final_answer, ''), 240) AS summary_preview
            FROM ep_events.work_ledger w
            {wl_join}
            WHERE {wl_cond}
            ORDER BY w.started_at DESC NULLS LAST, w.turn_seq DESC
            LIMIT %s OFFSET %s
            """,
            wl_params + [limit, offset],
        )
        wl_items = [
            {
                "host": r[0],
                "session_id": r[1],
                "turn_seq": r[2],
                "project_key": r[3],
                "started_at": r[4],
                "status": r[5],
                "user_request": r[6],
                "summary_preview": r[7],
            }
            for r in cur.fetchall()
        ]

        # 2. Exhaustive scan on public.memory_units (with idx_memory_units_cjk_v1 + optional entity_category)
        time_col = {
            "occurred": "COALESCE(mu.occurred_start, mu.event_date)",
            "mentioned": "mu.mentioned_at",
            "either": "COALESCE(mu.event_date, mu.occurred_start, mu.mentioned_at, mu.created_at)",
        }[time_mode]

        _TEST_BANK_REGEX = r"^(test[_-]|pool-test-|md-|maint-|mmcron-|mmscope-|recon-|wh-retain-)"
        if bank_id and not include_all_banks:
            mu_where = ["mu.bank_id = %s"]
            mu_params: list[Any] = [bank_id]
        elif bank_id and bank_id != DEFAULT_BANK:
            mu_where = ["mu.bank_id = %s"]
            mu_params = [bank_id]
        else:
            mu_where = ["mu.bank_id !~ %s"]
            mu_params = [_TEST_BANK_REGEX]
        if t_start is not None:
            mu_where.append(f"{time_col} >= %s")
            mu_params.append(t_start)
        if t_end is not None:
            mu_where.append(f"{time_col} <= %s")
            mu_params.append(t_end)
        if fact_types:
            mu_where.append("mu.fact_type = ANY(%s)")
            mu_params.append(list(fact_types))
        if project and project.strip():
            mu_where.append("(COALESCE(mu.context,'') ILIKE %s OR COALESCE(mu.text,'') ILIKE %s)")
            plike = f"%{project.strip()}%"
            mu_params.extend([plike, plike])
        mu_kw_sql, mu_kw_params = _keyword_clause(
            ["mu.text", "mu.context"], kw_list, cjk_vector_col="mu.search_vector_cjk_v1", match_mode=match_mode
        )
        if mu_kw_sql:
            mu_where.append(mu_kw_sql)
            mu_params.extend(mu_kw_params)

        has_ent_cls = _has_table(cur, "ep_events", "entity_classifications")
        if entity_category and entity_category.strip() and has_ent_cls:
            mu_where.append(
                """EXISTS (
                    SELECT 1 FROM public.unit_entities ue
                    JOIN ep_events.entity_classifications ec ON ec.entity_id = ue.entity_id
                    WHERE ue.unit_id = mu.id AND ec.category = %s
                )"""
            )
            mu_params.append(entity_category.strip())

        mu_cond = " AND ".join(mu_where)
        cur.execute(
            f"""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE mu.occurred_start IS NOT NULL)
            FROM public.memory_units mu
            WHERE {mu_cond}
            """,
            mu_params,
        )
        mu_total, mu_with_occurred = cur.fetchone()
        mu_total = int(mu_total)
        mu_with_occurred = int(mu_with_occurred)

        cur.execute(
            f"""
            SELECT mu.fact_type, COUNT(*)
            FROM public.memory_units mu
            WHERE {mu_cond}
            GROUP BY mu.fact_type
            ORDER BY COUNT(*) DESC
            """,
            mu_params,
        )
        mu_by_type = {r[0]: r[1] for r in cur.fetchall()}

        cur.execute(
            f"""
            SELECT mu.id::text, mu.fact_type, mu.document_id,
                   ({time_col} AT TIME ZONE 'Asia/Shanghai')::text AS effective_at,
                   (mu.occurred_start AT TIME ZONE 'Asia/Shanghai')::text AS occurred_at,
                   (mu.mentioned_at AT TIME ZONE 'Asia/Shanghai')::text AS mentioned_at,
                   LEFT(mu.text, 400) AS text,
                   LEFT(COALESCE(mu.context, ''), 120) AS context
            FROM public.memory_units mu
            WHERE {mu_cond}
            ORDER BY {time_col} DESC NULLS LAST
            LIMIT %s OFFSET %s
            """,
            mu_params + [limit, offset],
        )
        mu_items = [
            {
                "id": r[0],
                "fact_type": r[1],
                "document_id": r[2],
                "effective_at": r[3],
                "occurred_at": r[4],
                "mentioned_at": r[5],
                "text": r[6],
                "context": r[7],
            }
            for r in cur.fetchall()
        ]

        # 3. Pre-computed summaries
        summaries = []
        if include_summaries:
            sm_where = ["status IN ('ok', 'active')"]
            sm_params: list[Any] = []
            if t_start is not None:
                sm_where.append("period_end >= %s")
                sm_params.append(t_start)
            if t_end is not None:
                sm_where.append("period_start <= %s")
                sm_params.append(t_end)
            if project and project.strip():
                sm_where.append("array_to_string(project_keys, ',') ILIKE %s")
                sm_params.append(f"%{project.strip()}%")
            cur.execute(
                f"""
                SELECT level, key, path,
                       (period_start AT TIME ZONE 'Asia/Shanghai')::text,
                       (period_end AT TIME ZONE 'Asia/Shanghai')::text,
                       project_keys
                FROM ep_events.summaries
                WHERE {' AND '.join(sm_where)}
                ORDER BY period_end DESC NULLS LAST
                LIMIT 50
                """,
                sm_params,
            )
            summaries = [
                {
                    "level": r[0],
                    "key": r[1],
                    "path": r[2],
                    "period_start": r[3],
                    "period_end": r[4],
                    "project_keys": r[5],
                }
                for r in cur.fetchall()
            ]

        artifacts_items = []
        artifacts_total = 0
        if _has_table(cur, "ep_events", "artifacts"):
            art_where = ["1=1"]
            art_params: list[Any] = []
            if t_start is not None:
                art_where.append("mentioned_at >= %s")
                art_params.append(t_start)
            if t_end is not None:
                art_where.append("mentioned_at <= %s")
                art_params.append(t_end)
            if project and project.strip():
                art_where.append("(COALESCE(project_key, '') ILIKE %s OR file_path ILIKE %s)")
                plike = f"%{project.strip()}%"
                art_params.extend([plike, plike])
            art_kw_sql, art_kw_params = _keyword_clause(
                ["file_name", "file_path", "snippet"], kw_list, match_mode=match_mode
            )
            if art_kw_sql:
                art_where.append(art_kw_sql)
                art_params.extend(art_kw_params)
            cur.execute(f"SELECT COUNT(*) FROM ep_events.artifacts WHERE {' AND '.join(art_where)}", art_params)
            artifacts_total = int(cur.fetchone()[0])
            cur.execute(
                f"""
                SELECT file_name, file_path, ext, project_key, source_kind,
                       left(COALESCE(snippet, ''), 180),
                       (mentioned_at AT TIME ZONE 'Asia/Shanghai')::text
                FROM ep_events.artifacts
                WHERE {' AND '.join(art_where)}
                ORDER BY mentioned_at DESC NULLS LAST
                LIMIT 50
                """,
                art_params,
            )
            artifacts_items = [
                {
                    "file_name": r[0],
                    "file_path": r[1],
                    "ext": r[2],
                    "project_key": r[3],
                    "source_kind": r[4],
                    "snippet": r[5],
                    "mentioned_at": r[6],
                }
                for r in cur.fetchall()
            ]

        return {
            "schema": "evolving-profile.exhaustive-search.v2",
            "mode": "exhaustive_sql_scan",
            "match_mode": match_mode,
            "source_coverage": {
                "work_ledger_hosts_in_window": host_coverage,
                "memory_units_with_occurred_start": mu_with_occurred,
                "memory_units_total": mu_total,
                "occurred_start_ratio": round(mu_with_occurred / mu_total, 4) if mu_total else 0.0,
                "entity_classifications_ready": has_ent_cls,
                "project_aliases_ready": has_proj_alias,
                "include_all_banks": include_all_banks,
            },
            "filters": {
                "bank_id": bank_id,
                "start_date": start_date,
                "end_date": end_date,
                "keywords": kw_list,
                "project": project,
                "host": host,
                "fact_types": fact_types,
                "entity_category": entity_category,
                "time_mode": time_mode,
                "group_by": group_by,
            },
            "work_ledger": {
                "total_matched": wl_total,
                "returned": len(wl_items),
                "has_more": (offset + len(wl_items)) < wl_total,
                "next_offset": (offset + len(wl_items)) if (offset + len(wl_items)) < wl_total else None,
                "groups": wl_groups,
                "items": wl_items,
            },
            "memory_units": {
                "total_matched": mu_total,
                "returned": len(mu_items),
                "has_more": (offset + len(mu_items)) < mu_total,
                "next_offset": (offset + len(mu_items)) if (offset + len(mu_items)) < mu_total else None,
                "by_fact_type": mu_by_type,
                "items": mu_items,
            },
            "summaries": {
                "matched_count": len(summaries),
                "items": summaries,
            },
            "artifacts": {
                "total_matched": artifacts_total,
                "returned": len(artifacts_items),
                "items": artifacts_items,
            },
        }
    finally:
        conn.close()
