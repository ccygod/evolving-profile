"""Unit and integration tests for EP v6 architecture pillars."""
from __future__ import annotations

import io
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from v6.bootstrap import render_v6_daemon_plist, render_v6_router_plist, verify_code_data_separation
from v6.daemon import ScheduledJob, SchedulerDaemon, build_production_jobs, create_daemon_control_server
from v6.entity_graph import compute_entity_idf_weight
from v6.event_projector import classify_event_for_memory, project_bi_temporal_state
from v6.model_router import ModelRouter, ProviderEndpoint, create_router_server, load_router_from_data_root
from v6.pg_autotune import compute_pg_tuning
from v6.reranker import rerank_memories


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
    rplist = render_v6_router_plist(python_bin="/usr/bin/python3", code_root=code, data_root=data)
    assert "com.evolving-profile.v6-model-router" in rplist


def test_load_router_from_data_root_and_production_jobs(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "profiles").mkdir()
    (tmp_path / "config" / "runtime-settings.json").write_text(
        json.dumps({
            "providers": {
                "primary": {"base_url": "https://p.invalid/v1", "api_key": "k1", "model": "m-primary"},
                "fallbacks": [{"base_url": "https://f.invalid/v1", "api_key": "k2", "model": "m-fallback"}],
            }
        }),
        encoding="utf-8",
    )
    (tmp_path / "profiles" / "evolving-profile-api.env").write_text(
        "EVOLVING_PROFILE_API_LLM_BASE_URL=https://api.invalid/v1\n"
        "EVOLVING_PROFILE_API_LLM_API_KEY=k3\n"
        "EVOLVING_PROFILE_API_LLM_MODEL=m-api\n"
        "EVOLVING_PROFILE_API_REFLECT_LLM_MODEL=m-reflect\n",
        encoding="utf-8",
    )
    router = load_router_from_data_root(tmp_path)
    snap = router.snapshot()
    assert snap["scopes"]["default"] == ["settings_primary", "settings_fallback_1"]
    assert snap["scopes"]["retain"] == ["api_primary"]
    assert snap["scopes"]["reflect"] == ["reflect_primary", "api_primary"]

    jobs = build_production_jobs(tmp_path, bank_id="test-bank")
    assert {j.name for j in jobs} == {
        "retention-worker", "context-worker", "guidance-worker",
        "topic-catalog", "topic-semantics", "context-links", "events-sync", "incremental-sync", "entity-graph",
    }


def test_model_router_parallel_race_and_round_robin():
    import time as _time

    def race_opener(req, timeout=10):
        if "slow.invalid" in req.full_url:
            _time.sleep(0.15)
            return io.BytesIO(json.dumps({"choices": [{"message": {"content": "slow"}}]}).encode())
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": "fast"}}]}).encode())

    router = ModelRouter(
        {
            "default": [
                ProviderEndpoint("slow", "https://slow.invalid/v1", "k1", "m1"),
                ProviderEndpoint("fast", "https://fast.invalid/v1", "k2", "m2"),
            ],
            "batch": [
                ProviderEndpoint("pA", "https://a.invalid/v1", "k1", "mA"),
                ProviderEndpoint("pB", "https://b.invalid/v1", "k2", "mB"),
            ],
        },
        scope_modes={"default": "parallel_race", "batch": "round_robin"},
        opener=race_opener,
    )
    res, winner = router.chat_completions({"messages": [{"role": "user", "content": "race"}]})
    assert winner == "fast" and res["choices"][0]["message"]["content"] == "fast"

    # Round-robin alternates across healthy providers
    _, w1 = router.chat_completions({"messages": []}, scope="batch")
    _, w2 = router.chat_completions({"messages": []}, scope="batch")
    assert {w1, w2} == {"pA", "pB"}


def test_bi_temporal_reranker():
    items = [
        {"id": "1", "score": 0.9, "text": "Assistant statement (not user-confirmed): old report",
         "project_key": "p1", "occurred_at": "2026-08-01T00:00:00Z"},
        {"id": "2", "score": 0.85, "text": "用户明确决定：默认使用 EP 6.1", "trust_level": "user_explicit",
         "project_key": "p1", "occurred_at": "2026-11-18T00:00:00Z"},
        {"id": "3", "score": 0.95, "text": "旧配置", "superseded_by": "2",
         "project_key": "p2", "occurred_at": "2026-11-18T00:00:00Z"},
    ]
    ranked = rerank_memories(items, project_states={"p1": {"status": "active"}, "p2": {"status": "archived"}})
    assert ranked[0]["id"] == "2"
    assert ranked[-1]["id"] in {"1", "3"}


def test_pg_autotune_adapts_across_hardware_tiers():
    low = compute_pg_tuning(4 * 1024 ** 3, cpu_count=4)
    mid = compute_pg_tuning(16 * 1024 ** 3, cpu_count=10)
    high = compute_pg_tuning(64 * 1024 ** 3, cpu_count=24)
    assert low["shared_buffers"] == "512MB" and low["work_mem"] == "8MB"
    assert mid["shared_buffers"] == "2048MB" and mid["work_mem"] == "32MB" and mid["maintenance_work_mem"] == "512MB"
    assert high["shared_buffers"] == "4096MB" and high["work_mem"] == "64MB" and high["max_parallel_workers_per_gather"] == "4"


def test_entity_idf_weight_filters_stop_entities():
    assert compute_entity_idf_weight(1, 10000) is None          # singleton -> no edge
    assert compute_entity_idf_weight(2000, 10000) is None       # >8% stop-entity -> filtered
    rare = compute_entity_idf_weight(5, 10000)
    common = compute_entity_idf_weight(400, 10000)
    assert rare is not None and common is not None and rare > common
