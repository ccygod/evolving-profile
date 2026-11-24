import gzip, json, os, tempfile, time
from pathlib import Path

from lib import trace_slim as ts


def _state(n=30, **extra):
    items = [{"id": f"id-{i}", "type": "world", "score": 1.0 + i, "text": "全文" * 500,
              "admission": {"decision": "rejected" if i % 2 else "qualified", "reason": "r" * 400}} for i in range(n)]
    base = {"query_id": "q", "session_id": "s", "injected_ids": ["id-0"], "injected_items": [{"id": "id-0", "text": "T" * 900}],
            "candidate_items": items, "rejected_items": items[1::2],
            "pipeline_stages": {"retrieval": {"candidate_items": items, "count": n}, "controller_admission": {"rejected_items": items}},
            "admission_decisions": [{"record_id": f"id-{i}", "decision": "reject", "binding": {"x": "y" * 500}} for i in range(n)],
            "controller_execution": {"execution_complete": True}}
    base.update(extra)
    return base


def test_slim_items_reference_only():
    out = ts.slim_recall_state(_state())
    first = out["candidate_items"][0]
    assert first["id"] == "id-0" and first["score"] == 1.0 and len(first["preview"]) <= ts.PREVIEW_CHARS
    assert "text" not in first
    assert set(out["rejected_items"][0]) <= {"id", "decision", "reason", "score"}
    assert len(out["rejected_items"][0]["reason"]) <= ts.REASON_CHARS
    assert "binding" not in out["admission_decisions"][0]
    # reader-facing fields untouched
    assert out["injected_items"][0]["text"] == "T" * 900 and out["injected_ids"] == ["id-0"] and out["query_id"] == "q"
    assert len(json.dumps(out)) < len(json.dumps(_state())) / 5


def test_slim_is_idempotent_and_keeps_counts():
    once = ts.slim_recall_state(_state(60))
    twice = ts.slim_recall_state(once)
    assert once["candidate_items"] == twice["candidate_items"]
    stage = once["pipeline_stages"]["retrieval"]
    assert stage["count"] == 60 and stage["candidate_items"][-1] == {"_truncated_count": 60 - ts.OBJECT_LIST_MAX}


def test_sampling_keeps_last_three_full_traces(tmp_path):
    d = str(tmp_path)
    name = "last_recall-abcdef0123.json"
    for i in range(5):
        out = ts.prepare_recall_state(name, _state(query_id=f"q{i}"), d)
        assert out["trace_detail"]["mode"] == "slim"
        time.sleep(0.01)
    files = sorted(p.name for p in tmp_path.glob("*.trace-*.json.gz"))
    assert len(files) == ts.FULL_TRACE_KEEP_RECENT == 3
    ids = sorted(json.loads(gzip.open(tmp_path / f).read())["query_id"] for f in files)
    assert ids == ["q2", "q3", "q4"]
    assert "text" in json.loads(gzip.open(tmp_path / files[0]).read())["candidate_items"][0]


def test_global_state_gets_slim_without_ring(tmp_path):
    out = ts.prepare_recall_state("last_recall.json", _state(), str(tmp_path))
    assert out["trace_detail"]["mode"] == "slim" and not list(tmp_path.iterdir())


def test_degraded_recall_keeps_full_detail(tmp_path):
    for extra in ({"outcome": "failed"}, {"retrieval_failure": "boom"}, {"controller_execution": {"execution_complete": False}}):
        st = _state(**extra)
        out = ts.prepare_recall_state("last_recall-abcdef0123.json", st, str(tmp_path))
        assert out["trace_detail"]["mode"] == "full_degraded"
        assert out["candidate_items"] == st["candidate_items"]
    assert not list(tmp_path.glob("*.gz"))


def test_small_state_untouched(tmp_path):
    st = {"query_id": "q", "injected_ids": []}
    assert ts.prepare_recall_state("last_recall.json", st, str(tmp_path)) is st


def test_relevance_audit_slim_keeps_kept_and_ids():
    audit = {"kept_count": 1, "decisions": [
        {"id": "a", "kept": True, "level": "strong", "score": 3, "match_signals": {"x": "y" * 900}},
        {"id": "b", "kept": False, "level": "none", "score": 0.0, "reasons": ["why"], "exclusion_reason": "why", "match_signals": {"x": "y" * 900}},
    ]}
    out = ts.slim_relevance_audit(audit)
    assert out["decisions"][0]["match_signals"]
    assert out["decisions"][1] == {"id": "b", "kept": False, "level": "none", "score": 0.0, "exclusion_reason": "why"}
    assert out["kept_count"] == 1 and ts.slim_relevance_audit(out) == out


def test_workspace_write_slims_audit_and_archive_fallback(tmp_path, monkeypatch):
    import evolving_profile_controller_mcp as mcp
    monkeypatch.setattr(mcp, "PROCESS_MEMORY_WORKSPACE_ROOT", tmp_path)
    audit = {"decisions": [{"id": "b", "kept": False, "level": "none", "score": 0, "exclusion_reason": "w", "match_signals": {"k": "v" * 800}}]}
    mcp._write_process_workspace("ws1", {"candidate_order": ["b"], "relevance_audit": audit})
    stored = json.loads((tmp_path / "ws1.json").read_text())
    assert "match_signals" not in stored["relevance_audit"]["decisions"][0] and stored["candidate_order"] == ["b"]
    # archived (gzip) workspace is still readable by id
    (tmp_path / "archive").mkdir()
    (tmp_path / "archive" / "ws2.json.gz").write_bytes(gzip.compress(json.dumps({"candidate_order": ["z"]}).encode()))
    assert mcp._read_process_workspace("ws2")["candidate_order"] == ["z"]
