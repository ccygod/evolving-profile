#!/usr/bin/env python3
"""Move exhausted deterministic context failures to an explicit manual-review state.

This is a bounded state migration: it never marks a summary complete, never
resets attempts, and preserves the original error code, source revision and a
job_evidence row before changing status.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from lib.context_summary import context_index_lock, read_context_index, update_context_index
from lib.context_retry import MANUAL_REVIEW_ERRORS
from lib.context_incremental import ContextIncremental


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-root", default=str(Path.home() / ".evolving-profile"))
    args = parser.parse_args()
    root = Path(args.state_root).expanduser()
    context = root / "context"
    db_path = context / "context-incremental.sqlite"
    index_path = context / "context-index.json"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    db_backup = context / f"context-incremental.sqlite.pre-manual-review-{stamp}"
    index_backup = context / f"context-index.json.pre-manual-review-{stamp}"
    shutil.copy2(db_path, db_backup)
    shutil.copy2(index_path, index_backup)

    changed: list[dict] = []
    with context_index_lock(db_path):
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status='failed' AND error_code IN (%s)"
                % ",".join("?" for _ in MANUAL_REVIEW_ERRORS),
                tuple(sorted(MANUAL_REVIEW_ERRORS)),
            ).fetchall()
            for row in rows:
                prior = dict(row)
                evidence = {
                    "kind": "manual_review_classification",
                    "classified_at": utc_now(),
                    "reason": "deterministic_non_retryable_context_failure",
                    "error_code": row["error_code"],
                    "source_revision": row["revision"],
                    "input_revision": row["input_revision"],
                    "prior_job": prior,
                }
                conn.execute(
                    "INSERT INTO job_evidence(session_id,evidence) VALUES(?,?)",
                    (row["session_id"], json.dumps(evidence, ensure_ascii=False, sort_keys=True)),
                )
                conn.execute(
                    """UPDATE jobs SET status='manual_review', due=0, lease_until=0,
                       token=NULL, prior_status='failed', source_check_pending=0
                       WHERE session_id=? AND revision=? AND status='failed'""",
                    (row["session_id"], row["revision"]),
                )
                changed.append({"session_id": row["session_id"], "error_code": row["error_code"], "source_revision": row["revision"]})
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    if changed:
        changed_by_id = {row["session_id"]: row for row in changed}
        with context_index_lock(index_path):
            current = read_context_index(index_path)
            for row in current.get("sessions", []):
                item = changed_by_id.get(row.get("session_id"))
                if not item:
                    continue
                if row.get("source_stat_revision") not in {None, item["source_revision"]}:
                    continue
                row.update(
                    status="raw_available_summary_manual_review",
                    error_code=item["error_code"],
                    summary_failure_detail="自动摘要失败已停止重试，等待来源/结构人工复核。原文和失败证据保留。",
                    updated_at=utc_now(),
                )
            update_context_index(index_path, lambda _: current)

    worker = ContextIncremental(str(root), str(Path.home() / ".codex/sessions"), bank_id=os.environ.get("EVOLVING_PROFILE_BANK_ID"))
    progress = worker.progress()
    report = {
        "schema": "evolving-profile.context-manual-review.v1",
        "classified": len(changed),
        "records": changed,
        "db_backup": str(db_backup),
        "index_backup": str(index_backup),
        "progress": progress,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
