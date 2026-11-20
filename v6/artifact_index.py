"""Cross-platform zero-LLM Deliverable / Artifact Index & Hybrid Rare-Term Search for EP v6.

Solves the retrieval blind spot where concrete output files (PDFs, DOCX, PPTX, scripts, etc.)
are buried in raw conversation chunks or work_ledger and drowned out by generic semantic memories.
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Any


DDL = """
CREATE SCHEMA IF NOT EXISTS ep_events;

CREATE TABLE IF NOT EXISTS ep_events.artifacts (
    id bigserial PRIMARY KEY,
    file_path text NOT NULL,
    file_name text NOT NULL,
    ext text NOT NULL,
    project_key text,
    source_kind text NOT NULL,
    source_id text NOT NULL,
    snippet text,
    mentioned_at timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_ep_artifacts_path
    ON ep_events.artifacts (file_path);

CREATE INDEX IF NOT EXISTS idx_ep_artifacts_name_trgm
    ON ep_events.artifacts USING gin (file_name gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_ep_artifacts_path_trgm
    ON ep_events.artifacts USING gin (file_path gin_trgm_ops);

CREATE INDEX IF NOT EXISTS idx_ep_artifacts_mentioned
    ON ep_events.artifacts (mentioned_at DESC NULLS LAST);

GRANT USAGE ON SCHEMA ep_events TO PUBLIC;
GRANT SELECT ON ep_events.artifacts TO PUBLIC;
"""

# Cross-platform deliverable path extractor (Unix /Users/..., /home/..., and Windows C:\... or D:/...)
DELIVERABLE_EXTS = (
    "pdf", "docx", "doc", "pptx", "ppt", "xlsx", "xls", "md", "csv", "zip",
    "mp4", "mov", "png", "jpg", "jpeg", "svg", "html", "py", "ts", "tsx", "sql",
)
_EXT_ALT = "|".join(DELIVERABLE_EXTS)
PATH_RE = re.compile(
    rf"(?:file://)?((?:/(?:Users|home|Volumes|mnt|opt)/|[A-Za-z]:[/\\])[^\s\"'`<>|()\[\]{{}};,]+?\.(?:{_EXT_ALT}))(?=$|[\s\"'`<>|()\[\]{{}};,：，。！？])",
    re.I,
)

NOISE_PATH_MARKERS = (
    "/.git/", "/node_modules/", "/__pycache__/", "/.venv", "/.system_generated/",
    "/Library/Caches/", "/.cache/", "/tmp/", "/private/tmp/", "/.next/", "/site-packages/",
    ".bak", "/AppData/Local/Temp/",
)


def extract_deliverable_paths(text: str) -> list[tuple[str, str, str, str]]:
    """Extract (file_path, file_name, ext, snippet) tuples from text without LLM calls."""
    if not text:
        return []
    found: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    for m in PATH_RE.finditer(text):
        raw_path = m.group(1).strip().rstrip(".,;:")
        if len(raw_path) < 8 or len(raw_path) > 400:
            continue
        low = raw_path.lower()
        if any(noise.lower() in low for noise in NOISE_PATH_MARKERS):
            continue
        if raw_path in seen:
            continue
        seen.add(raw_path)
        norm_sep = raw_path.replace("\\", "/")
        fname = norm_sep.rsplit("/", 1)[-1]
        ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
        start = max(0, m.start() - 100)
        end = min(len(text), m.end() + 120)
        snippet = re.sub(r"\s+", " ", text[start:end]).strip()[:260]
        found.append((raw_path, fname, ext, snippet))
    return found


def _connect_pg(dsn: str | None = None, *, user: str | None = None, port: int = 15432, dbname: str = "evolving_profile"):
    import psycopg2

    if dsn:
        return psycopg2.connect(dsn)
    kw: dict[str, Any] = {"port": port, "dbname": dbname}
    if user:
        kw["user"] = user
    return psycopg2.connect(**kw)


def ensure_artifacts_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute(DDL)
    conn.commit()


def build_artifact_index(
    *,
    dsn: str | None = None,
    user: str | None = None,
    port: int = 15432,
    dbname: str = "evolving_profile",
    chunk_limit: int = 15000,
) -> dict[str, Any]:
    """Scan ep_events.work_ledger and public.chunks to populate ep_events.artifacts."""
    conn = _connect_pg(dsn, user=user, port=port, dbname=dbname)
    indexed = 0
    wl_rows = 0
    chunk_rows = 0
    try:
        ensure_artifacts_schema(conn)
        batch: dict[str, tuple[str, str, str, str | None, str, str, str, Any]] = {}

        with conn.cursor() as cur:
            # 1. Scan work_ledger summaries & user_requests
            cur.execute("SELECT to_regclass('ep_events.work_ledger') IS NOT NULL")
            if cur.fetchone()[0]:
                cur.execute(
                    """
                    SELECT COALESCE(turn_key, COALESCE(session_id, 'sess') || ':' || COALESCE(turn_seq::text, '0')), project_key,
                           COALESCE(user_request, '') || E'\n' || COALESCE(final_answer, '') || E'\n' || COALESCE(summary_line, ''),
                           started_at
                    FROM ep_events.work_ledger
                    WHERE final_answer ~* '\\.(pdf|docx?|pptx?|xlsx?|md|csv|zip|mp4|png|jpe?g|svg|html|py|tsx?|sql)'
                       OR summary_line ~* '\\.(pdf|docx?|pptx?|xlsx?|md|csv|zip|mp4|png|jpe?g|svg|html|py|tsx?|sql)'
                       OR user_request ~* '\\.(pdf|docx?|pptx?|xlsx?|md|csv|zip|mp4|png|jpe?g|svg|html|py|tsx?|sql)'
                    ORDER BY started_at DESC NULLS LAST
                    LIMIT 10000
                    """
                )
                for row_id, pkey, txt, started_at in cur.fetchall():
                    wl_rows += 1
                    for fpath, fname, ext, snippet in extract_deliverable_paths(txt):
                        if fpath not in batch:
                            batch[fpath] = (fpath, fname, ext, pkey, "work_ledger", row_id, snippet, started_at)

            # 2. Scan public.chunks (where raw assistant deliverable links often live!)
            cur.execute(
                """
                SELECT chunk_id, document_id, chunk_text, created_at
                FROM public.chunks
                WHERE chunk_text ~* '\\.(pdf|docx?|pptx?|xlsx?|md|csv|zip|mp4|png|jpe?g|svg|html|py|tsx?|sql)'
                ORDER BY created_at DESC NULLS LAST
                LIMIT %s
                """,
                (chunk_limit,),
            )
            for cid, doc_id, txt, created_at in cur.fetchall():
                chunk_rows += 1
                for fpath, fname, ext, snippet in extract_deliverable_paths(txt or ""):
                    if fpath not in batch:
                        batch[fpath] = (fpath, fname, ext, None, "chunk", str(cid or doc_id), snippet, created_at)

            # Upsert batch
            if batch:
                from psycopg2.extras import execute_values

                execute_values(
                    cur,
                    """
                    INSERT INTO ep_events.artifacts
                        (file_path, file_name, ext, project_key, source_kind, source_id, snippet, mentioned_at)
                    VALUES %s
                    ON CONFLICT (file_path) DO UPDATE SET
                        project_key = COALESCE(EXCLUDED.project_key, ep_events.artifacts.project_key),
                        snippet = CASE WHEN length(EXCLUDED.snippet) > length(COALESCE(ep_events.artifacts.snippet, ''))
                                       THEN EXCLUDED.snippet ELSE ep_events.artifacts.snippet END,
                        mentioned_at = GREATEST(ep_events.artifacts.mentioned_at, EXCLUDED.mentioned_at),
                        updated_at = now()
                    """,
                    list(batch.values()),
                    page_size=500,
                )
                indexed = len(batch)
        conn.commit()
        return {
            "status": "ok",
            "work_ledger_scanned": wl_rows,
            "chunks_scanned": chunk_rows,
            "artifacts_upserted": indexed,
        }
    finally:
        conn.close()


_GENERIC_SEARCH_STOP = {
    "之前", "做的", "放哪", "在哪", "哪里", "文件", "路径", "目录", "一下", "帮我",
    "找一下", "搜索", "怎么", "什么", "教师", "学生", "培训", "大纲", "资料", "方案",
}


def extract_rare_search_terms(query: str) -> list[str]:
    """Extract high-specificity tokens (extensions, Latin identifiers, rare CJK compounds) for hybrid fallback."""
    q = (query or "").strip()
    if not q:
        return []
    terms: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        t = tok.strip()
        if len(t) >= 2 and t.lower() not in seen and t not in _GENERIC_SEARCH_STOP:
            seen.add(t.lower())
            terms.append(t)

    # 1. File extensions & Latin identifiers (e.g., ImageGen, pdf, docx, pg_autotune)
    for m in re.findall(r"\.?[A-Za-z][A-Za-z0-9_.-]{1,40}", q):
        _add(m)

    # 2. CJK segments: keep 3-6 char rare compounds & tail phrases (often the exact discriminator like 竖版全生图)
    for seg in re.findall(r"[\u4e00-\u9fff]{2,}", q):
        if 2 <= len(seg) <= 8:
            _add(seg)
        if len(seg) > 4:
            # Split off generic prefixes like 培训大纲 / 教师培训 to isolate the rare tail discriminator
            for prefix in ("教师培训大纲", "学生培训大纲", "培训大纲", "课程大纲", "教师培训", "学生培训"):
                if seg.startswith(prefix) and len(seg) - len(prefix) >= 2:
                    _add(seg[len(prefix):])
            # Also take trailing 4-char and 3-char windows
            _add(seg[-4:])
            _add(seg[-3:])
    return terms[:8]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", default=os.environ.get("PGUSER", "evolving_profile_admin"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PGPORT", "15432")))
    parser.add_argument("--dbname", default=os.environ.get("PGDATABASE", "evolving_profile"))
    parser.add_argument("--dsn", default=os.environ.get("EVOLVING_PROFILE_ADMIN_DSN"))
    args = parser.parse_args()
    print(json.dumps(build_artifact_index(dsn=args.dsn, user=args.user, port=args.port, dbname=args.dbname), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
