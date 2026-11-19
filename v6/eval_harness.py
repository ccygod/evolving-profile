"""EP v6.1 Automated Golden Eval Harness: quantifies retrieval latency, write signal quality, and runtime health."""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any


def _http_ok(url: str, timeout: float = 3.0) -> dict[str, Any]:
    t0 = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = json.loads(resp.read())
        return {"ok": True, "latency_ms": int((time.time() - t0) * 1000), "status": data.get("status")}
    except Exception as exc:
        return {"ok": False, "latency_ms": int((time.time() - t0) * 1000), "error": type(exc).__name__}


def run_eval(data_root: str | Path | None = None, *, bank_id: str | None = None) -> dict[str, Any]:
    root = Path(data_root or os.environ.get("EP_DATA_ROOT") or Path.home() / ".evolving-profile").expanduser()
    host_adapter = root / "runtime" / "host-adapter"
    if str(host_adapter) not in sys.path:
        sys.path.insert(0, str(host_adapter))

    report: dict[str, Any] = {
        "schema": "evolving-profile.v6-eval.v1",
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "services": {
            "controller_12079": _http_ok("http://127.0.0.1:12079/health"),
            "memory_api_12088": _http_ok("http://127.0.0.1:12088/health"),
            "v6_router_12095": _http_ok("http://127.0.0.1:12095/health"),
            "v6_daemon_12096": _http_ok("http://127.0.0.1:12096/health"),
        },
        "db_metrics": {},
    }

    from lib.exhaustive_query import _connect

    resolved_bank = bank_id or os.environ.get("EVOLVING_PROFILE_BANK_ID")
    with _connect() as conn, conn.cursor() as cur:
        t0 = time.time()
        cur.execute("SELECT status, count(*) FROM ep_events.project_state GROUP BY status")
        ps = dict(cur.fetchall())
        report["db_metrics"]["project_state_lookup_ms"] = int((time.time() - t0) * 1000)
        report["db_metrics"]["project_state_counts"] = ps

        cur.execute("SELECT host, count(*), count(DISTINCT session_id) FROM ep_events.work_ledger GROUP BY host ORDER BY 2 DESC")
        report["db_metrics"]["work_ledger_hosts"] = [{"host": r[0], "turns": r[1], "sessions": r[2]} for r in cur.fetchall()]

        if not resolved_bank:
            cur.execute("SELECT bank_id FROM public.memory_units GROUP BY bank_id ORDER BY count(*) DESC LIMIT 1")
            row = cur.fetchone()
            resolved_bank = row[0] if row else None

        if resolved_bank:
            cur.execute(
                """
                SELECT count(*),
                       count(*) FILTER (WHERE occurred_start IS NOT NULL),
                       count(*) FILTER (WHERE text LIKE 'Assistant statement (not user-confirmed)%%')
                FROM public.memory_units
                WHERE bank_id = %s AND mentioned_at >= NOW() - INTERVAL '7 days'
                """,
                (resolved_bank,),
            )
            tot, with_occ, asst = cur.fetchone()
            report["db_metrics"]["last_7d_units"] = {
                "total": tot,
                "occurred_start_ratio": round(with_occ / tot, 4) if tot else 0.0,
                "unconfirmed_assistant_ratio": round(asst / tot, 4) if tot else 0.0,
            }

        cur.execute("SELECT count(*), pg_size_pretty(pg_database_size(current_database())) FROM public.memory_links")
        links, db_size = cur.fetchone()
        report["db_metrics"]["memory_links_total"] = links
        report["db_metrics"]["database_size"] = db_size

    out_path = root / "logs" / "v6-eval-latest.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    print(json.dumps(run_eval(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
