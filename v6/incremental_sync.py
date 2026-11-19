"""EP v6.1 Incremental Event-Log Delta Export for lightweight daily sync and multi-device replication."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


def export_delta(data_root: str | Path | None = None, *, limit: int = 50000) -> dict[str, Any]:
    root = Path(data_root or os.environ.get("EP_DATA_ROOT") or Path.home() / ".evolving-profile").expanduser()
    host_adapter = root / "runtime" / "host-adapter"
    if str(host_adapter) not in sys.path:
        sys.path.insert(0, str(host_adapter))

    out_dir = root / "backups" / "incremental"
    out_dir.mkdir(parents=True, exist_ok=True)
    cursor_path = out_dir / "cursor.json"
    cursor = json.loads(cursor_path.read_text(encoding="utf-8")) if cursor_path.is_file() else {"last_ingested_at": None}

    from lib.exhaustive_query import _connect

    with _connect() as conn, conn.cursor() as cur:
        if cursor.get("last_ingested_at"):
            cur.execute(
                """
                SELECT event_id, host, session_id, source_kind, seq, event_type, role,
                       occurred_at::text, ingested_at::text, project_key, body, tool
                FROM ep_events.events
                WHERE ingested_at > %s::timestamptz
                ORDER BY ingested_at ASC, event_id ASC
                LIMIT %s
                """,
                (cursor["last_ingested_at"], limit),
            )
        else:
            # First run: export last 7 days as initial incremental delta baseline
            cur.execute(
                """
                SELECT event_id, host, session_id, source_kind, seq, event_type, role,
                       occurred_at::text, ingested_at::text, project_key, body, tool
                FROM ep_events.events
                WHERE ingested_at >= NOW() - INTERVAL '7 days'
                ORDER BY ingested_at ASC, event_id ASC
                LIMIT %s
                """,
                (limit,),
            )
        rows = cur.fetchall()

    if not rows:
        return {"status": "unchanged", "exported_events": 0, "cursor": cursor.get("last_ingested_at")}

    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    target = out_dir / f"events-delta-{stamp}.jsonl.gz"
    cols = ("event_id", "host", "session_id", "source_kind", "seq", "event_type", "role",
            "occurred_at", "ingested_at", "project_key", "body", "tool")
    with gzip.open(target, "wt", encoding="utf-8", compresslevel=6) as gz:
        for r in rows:
            gz.write(json.dumps(dict(zip(cols, r)), ensure_ascii=False) + "\n")

    sha256 = hashlib.sha256(target.read_bytes()).hexdigest()
    new_cursor = rows[-1][8]  # ingested_at
    manifest = {
        "schema": "evolving-profile.incremental-delta.v1",
        "file": target.name,
        "bytes": target.stat().st_size,
        "sha256": sha256,
        "exported_events": len(rows),
        "from_ingested_at": cursor.get("last_ingested_at"),
        "to_ingested_at": new_cursor,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (out_dir / f"events-delta-{stamp}.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    cursor_path.write_text(json.dumps({"last_ingested_at": new_cursor, "latest_file": target.name}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"status": "exported", **manifest}


def main() -> int:
    print(json.dumps(export_delta(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
