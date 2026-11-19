"""Unit and integration tests for EP v6 architecture pillars."""
from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from v6.bootstrap import render_v6_daemon_plist, verify_code_data_separation
from v6.daemon import ScheduledJob, SchedulerDaemon, create_daemon_control_server
from v6.event_projector import classify_event_for_memory, project_bi_temporal_state
from v6.model_router import ModelRouter, ProviderEndpoint, create_router_server


def test_model_router_failover_circuit_breaker_and_recovery(tmp_path):
    now = [1000.0]
    calls = []

    def fake_opener(req, timeout=10):
        body = json.loads(req.data.decode("utf-8"))
        calls.append((req.full_url, body["model"]))
        if "primary.invalid" in req.full_url:
            if now[0] < 1400.0:
                raise urllib.error.HTTPError(req.full_url, 403, "Forbidden", {}, None)
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "primary_ok"}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode())
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "fallback_ok"}}], "usage": {"prompt_tokens": 12, "completion_tokens": 6}}).encode())

    router = ModelRouter(
        {
            "default": [
                ProviderEndpoint("primary", "https://primary.invalid/v1", "k1", "m-primary", failure_threshold=2, cooldown_seconds=300),
                ProviderEndpoint("fallback", "https://fallback.invalid/v1", "k2", "m-fallback"),
            ]
        },
        telemetry_path=tmp_path / "telemetry.jsonl",
        opener=fake_opener,
        clock=lambda: now[0],
    )

    # First 2 calls fail on primary -> failover to fallback -> primary breaker opens
    res1, p1 = router.chat_completions({"messages": [{"role": "user", "content": "hi"}]})
    assert p1 == "fallback" and res1["choices"][0]["message"]["content"] == "fallback_ok"
    res2, p2 = router.chat_completions({"messages": [{"role": "user", "content": "hi2"}]})
    assert p2 == "fallback"
    assert router.snapshot()["breakers"]["primary"]["state"] == "open"

    # Third call skips primary completely while breaker is open
    before_calls = len(calls)
    res3, p3 = router.chat_completions({"messages": [{"role": "user", "content": "hi3"}]})
    assert p3 == "fallback"
    assert len(calls) == before_calls + 1  # only fallback called

    # After cooldown expires, primary half-opens and recovers
    now[0] = 1401.0
    res4, p4 = router.chat_completions({"messages": [{"role": "user", "content": "hi4"}]})
    assert p4 == "primary" and res4["choices"][0]["message"]["content"] == "primary_ok"
    assert router.snapshot()["breakers"]["primary"]["state"] == "closed"
    assert (tmp_path / "telemetry.jsonl").is_file()


def test_model_router_http_server_roundtrip(tmp_path):
    def fake_opener(req, timeout=10):
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "routed"}}], "usage": {"prompt_tokens": 3, "completion_tokens": 2}}).encode())

    router = ModelRouter(
        {"default": [ProviderEndpoint("p1", "https://p1.invalid/v1", "k", "m1")]},
        opener=fake_opener,
    )
    server = create_router_server(router, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/v1/chat/completions",
            data=json.dumps({"messages": [{"role": "user", "content": "ping"}]}).encode(),
            headers={"Content-Type": "application/json", "X-EP-Scope": "default"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["choices"][0]["message"]["content"] == "routed"
        assert data["_ep_router"]["provider"] == "p1"
    finally:
        server.shutdown()
        server.server_close()


def test_scheduler_daemon_backoff_and_control_server(tmp_path):
    now = [2000.0]
    daemon = SchedulerDaemon(tmp_path / "scheduler.sqlite3", clock=lambda: now[0])
    state = {"count": 0, "should_fail": True}

    def flaky():
        state["count"] += 1
        if state["should_fail"]:
            raise RuntimeError("boom")
        return {"status": "ok", "runs": state["count"]}

    daemon.register(ScheduledJob("sync", interval_seconds=60, handler=flaky))
    out1 = daemon.tick()
    assert out1[0]["status"] == "error" and out1[0]["backoff_seconds"] == 120

    # Still within backoff window -> not due
    now[0] = 2050.0
    assert daemon.tick() == []

    # Past backoff -> succeeds and resets failures
    now[0] = 2125.0
    state["should_fail"] = False
    out2 = daemon.tick()
    assert out2[0]["status"] == "ok" and out2[0]["summary"]["runs"] == 2

    # Control server health & manual trigger
    server = create_daemon_control_server(daemon, port=0)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=5) as resp:
            health = json.loads(resp.read())
        assert health["status"] == "healthy" and health["jobs"][0]["consecutive_failures"] == 0
        req = urllib.request.Request(f"http://127.0.0.1:{port}/trigger/sync", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            trig = json.loads(resp.read())
        assert trig["status"] == "ok" and trig["summary"]["runs"] == 3
    finally:
        server.shutdown()
        server.server_close()


def test_event_projector_semantic_gate_and_bi_temporal_state():
    events = [
        {"host": "codex", "session_id": "s1", "project_key": "ep", "event_type": "user_message", "role": "user",
         "occurred_at": "2026-11-18T01:00:00Z", "body": "以后所有备份必须先校验哈希"},
        {"host": "codex", "session_id": "s1", "project_key": "ep", "event_type": "assistant_message", "role": "assistant",
         "occurred_at": "2026-11-18T01:00:05Z", "body": "已完成备份到 /tmp/x"},
        {"host": "antigravity", "session_id": "s2", "project_key": "ep", "event_type": "user_message", "role": "user",
         "occurred_at": "2026-11-18T02:00:00Z", "body": "好的"},
    ]
    assert classify_event_for_memory(events[0]).plane == "memory_high_priority"
    assert classify_event_for_memory(events[1]).plane == "work_ledger_only"
    assert classify_event_for_memory(events[2]).plane == "work_ledger_only"

    projected = project_bi_temporal_state(events)
    assert projected["ep"]["hosts"] == ["antigravity", "codex"]
    assert projected["ep"]["session_count"] == 2
    assert projected["ep"]["memory_candidates"] == 1
    assert projected["ep"]["valid_from"] == "2026-11-18T01:00:00Z"
    assert projected["ep"]["last_active_at"] == "2026-11-18T02:00:00Z"


def test_bootstrap_code_data_separation_and_plist(tmp_path):
    code = Path(__file__).resolve().parents[2]
    data = tmp_path / "ep-data"
    data.mkdir()
    check = verify_code_data_separation(code, data)
    assert check["ok"] is True and check["issues"] == []
    # Nested data inside code must fail
    bad = verify_code_data_separation(code, code / "nested-data")
    assert bad["ok"] is False and "data_root_nested_inside_code_root" in bad["issues"]
    plist = render_v6_daemon_plist(python_bin="/usr/bin/python3", code_root=code, data_root=data)
    assert "com.evolving-profile.v6-daemon" in plist and str(code) in plist and str(data) in plist
