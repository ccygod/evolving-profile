"""Cross-platform SQLite physical storage hygiene for EP v6 (macOS / Windows / Linux).

Safely bounds unbounded local SQLite growth:
1. Prunes old `status = 'delivered'` rows in `memory-os/capture/capture.sqlite3` while keeping
   the most recent N delivered rows plus ALL `pending` and `blocked` rows, then runs VACUUM.
2. Archives stale legacy SQLite indexes (e.g. `control-plane/fast-memory-index.sqlite3`)
   unmodified for >= min_stale_days into `backups/stale-sqlite-archive/`.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any


def prune_capture_sqlite(
    capture_db: str | Path,
    *,
    keep_recent_delivered: int = 1000,
    dry_run: bool = False,
) -> dict[str, Any]:
    path = Path(capture_db)
    if not path.exists():
        return {"status": "skipped", "reason": "capture_db_missing"}

    before_mb = round(path.stat().st_size / (1024 * 1024), 2)
    con = sqlite3.connect(str(path), timeout=30.0)
    try:
        cur = con.cursor()
        tables = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "captures" not in tables:
            return {"status": "skipped", "reason": "no_captures_table"}

        total_delivered = cur.execute("SELECT count(*) FROM captures WHERE status = 'delivered'").fetchone()[0]
        to_delete = max(0, total_delivered - keep_recent_delivered)
        if not dry_run and to_delete > 0:
            cur.execute(
                """
                DELETE FROM captures
                WHERE status = 'delivered'
                  AND capture_order NOT IN (
                      SELECT capture_order FROM captures
                      WHERE status = 'delivered'
                      ORDER BY capture_order DESC
                      LIMIT ?
                  )
                """,
                (keep_recent_delivered,),
            )
            deleted = cur.rowcount
            con.commit()
            try:
                cur.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            cur.execute("VACUUM")
        else:
            deleted = 0
    finally:
        con.close()

    after_mb = round(path.stat().st_size / (1024 * 1024), 2)
    return {
        "status": "ok",
        "before_mb": before_mb,
        "after_mb": after_mb,
        "reclaimed_mb": round(before_mb - after_mb, 2),
        "delivered_before": total_delivered,
        "delivered_pruned": deleted if not dry_run else to_delete,
        "keep_recent_delivered": keep_recent_delivered,
        "dry_run": dry_run,
    }


def archive_stale_legacy_sqlite(
    data_root: str | Path,
    *,
    min_stale_days: int = 30,
    dry_run: bool = False,
) -> dict[str, Any]:
    root = Path(data_root).expanduser()
    candidates = [
        root / "control-plane" / "fast-memory-index.sqlite3",
    ]
    archive_dir = root / "backups" / "stale-sqlite-archive"
    now = time.time()
    cutoff = now - min_stale_days * 86400.0
    archived: list[dict[str, Any]] = []
    for p in candidates:
        if not p.exists():
            continue
        st = p.stat()
        if st.st_mtime < cutoff:
            size_mb = round(st.st_size / (1024 * 1024), 2)
            entry = {"file": str(p.name), "size_mb": size_mb, "dry_run": dry_run}
            if not dry_run:
                archive_dir.mkdir(parents=True, exist_ok=True)
                dest = archive_dir / p.name
                shutil.move(str(p), str(dest))
                for suffix in ("-wal", "-shm"):
                    sidecar = Path(str(p) + suffix)
                    if sidecar.exists():
                        shutil.move(str(sidecar), str(archive_dir / sidecar.name))
                entry["archived_to"] = str(dest)
            archived.append(entry)
    return {"status": "ok", "archived": archived, "dry_run": dry_run}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", default=os.environ.get("EP_DATA_ROOT", str(Path.home() / ".evolving-profile")))
    parser.add_argument("--keep-delivered", type=int, default=1000)
    parser.add_argument("--stale-days", type=int, default=30)
    parser.add_argument("--apply", action="store_true", help="Apply pruning & archiving (default: dry-run)")
    args = parser.parse_args()

    root = Path(args.data_root).expanduser()
    dry = not args.apply
    report = {
        "capture_sqlite": prune_capture_sqlite(
            root / "memory-os" / "capture" / "capture.sqlite3",
            keep_recent_delivered=args.keep_delivered,
            dry_run=dry,
        ),
        "stale_legacy_sqlite": archive_stale_legacy_sqlite(
            root, min_stale_days=args.stale_days, dry_run=dry
        ),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
