"""EP v6 Unified Event-Driven Scheduler Daemon replacing fragmented polling LaunchAgents."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable


@dataclass
class ScheduledJob:
    name: str
    interval_seconds: float
    handler: Callable[[], dict[str, Any]]
    max_backoff_seconds: float = 1800.0


class SchedulerDaemon:
    """Single resident scheduler with SQLite state, non-overlapping locks, exponential backoff, and quiet idle logs."""

    def __init__(self, state_db: Path, *, clock: Callable[[], float] = time.time):
        self.state_db = Path(state_db)
        self.state_db.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._jobs: dict[str, ScheduledJob] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._stop_event = threading.Event()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.state_db), timeout=10.0)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS job_state (
                    name TEXT PRIMARY KEY,
                    last_run_at REAL NOT NULL DEFAULT 0,
                    next_due_at REAL NOT NULL DEFAULT 0,
                    last_status TEXT NOT NULL DEFAULT 'never',
                    consecutive_failures INTEGER NOT NULL DEFAULT 0,
                    last_summary_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            conn.commit()

    def register(self, job: ScheduledJob) -> None:
        self._jobs[job.name] = job
        self._locks.setdefault(job.name, threading.Lock())
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO job_state (name, next_due_at) VALUES (?, 0)",
                (job.name,),
            )
            conn.commit()

    def run_job(self, name: str, *, force: bool = False) -> dict[str, Any]:
        job = self._jobs[name]
        lock = self._locks[name]
        if not lock.acquire(blocking=False):
            return {"job": name, "status": "already_running"}
        try:
            now = self._clock()
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT next_due_at, consecutive_failures FROM job_state WHERE name=?",
                    (name,),
                ).fetchone()
                next_due, failures = (row[0], row[1]) if row else (0.0, 0)
            if not force and now < next_due:
                return {"job": name, "status": "not_due", "next_due_at": next_due}
            try:
                summary = job.handler() or {}
                status = str(summary.get("status") or "ok")
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE job_state
                        SET last_run_at=?, next_due_at=?, last_status=?, consecutive_failures=0, last_summary_json=?
                        WHERE name=?
                        """,
                        (now, now + job.interval_seconds, status, json.dumps(summary, ensure_ascii=False), name),
                    )
                    conn.commit()
                return {"job": name, "status": status, "summary": summary}
            except Exception as exc:
                failures += 1
                backoff = min(job.max_backoff_seconds, job.interval_seconds * (2 ** min(failures, 6)))
                err = {"error": type(exc).__name__, "detail": str(exc)[:200]}
                with self._connect() as conn:
                    conn.execute(
                        """
                        UPDATE job_state
                        SET last_run_at=?, next_due_at=?, last_status='error', consecutive_failures=?, last_summary_json=?
                        WHERE name=?
                        """,
                        (now, now + backoff, failures, json.dumps(err, ensure_ascii=False), name),
                    )
                    conn.commit()
                return {"job": name, "status": "error", "backoff_seconds": backoff, **err}
        finally:
            lock.release()

    def tick(self) -> list[dict[str, Any]]:
        results = []
        for name in sorted(self._jobs):
            res = self.run_job(name, force=False)
            if res.get("status") != "not_due":
                results.append(res)
        return results

    def status_snapshot(self) -> dict[str, Any]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name, last_run_at, next_due_at, last_status, consecutive_failures, last_summary_json FROM job_state ORDER BY name"
            ).fetchall()
        return {
            "jobs": [
                {
                    "name": r[0],
                    "last_run_at": r[1],
                    "next_due_at": r[2],
                    "last_status": r[3],
                    "consecutive_failures": r[4],
                    "last_summary": json.loads(r[5]),
                }
                for r in rows
            ]
        }

    def stop(self) -> None:
        self._stop_event.set()


def create_daemon_control_server(daemon: SchedulerDaemon, host: str = "127.0.0.1", port: int = 12096) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_GET(self) -> None:
            if self.path in ("/health", "/jobs"):
                self._send(200, {"status": "healthy", **daemon.status_snapshot()})
            else:
                self._send(404, {"error": "not_found"})

        def do_POST(self) -> None:
            if self.path.startswith("/trigger/"):
                name = self.path.split("/trigger/", 1)[1].strip("/")
                if name not in daemon._jobs:
                    self._send(404, {"error": "unknown_job"})
                    return
                self._send(200, daemon.run_job(name, force=True))
            else:
                self._send(404, {"error": "not_found"})

        def log_message(self, format: str, *args: Any) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)
