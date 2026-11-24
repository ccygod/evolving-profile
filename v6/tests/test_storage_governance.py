"""Safety gates of the 6.2 storage-governance modules (no database needed)."""
import importlib
import subprocess
import sys


def _load(name, monkeypatch, tmp_path, **env):
    monkeypatch.setenv("EVOLVING_PROFILE_STATE_ROOT", str(tmp_path))
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    module = importlib.import_module(name)
    return importlib.reload(module)


def test_events_retention_skips_without_ledger_guard(monkeypatch, tmp_path):
    mod = _load("v6.events_retention", monkeypatch, tmp_path)
    monkeypatch.setattr(mod, "connect", lambda: (_ for _ in ()).throw(AssertionError("must not touch the DB")))
    assert mod.prune(dry_run=False, idle_days=30, limit=0) == {"status": "skipped", "reason": "ledger_guard_missing"}


def test_events_retention_detects_installed_guard(monkeypatch, tmp_path):
    (tmp_path / "events").mkdir()
    (tmp_path / "events" / "ledger.py").write_text("# guard: pruned_tool_sessions\n", encoding="utf-8")
    mod = _load("v6.events_retention", monkeypatch, tmp_path)
    assert mod.ledger_guard_installed()


def test_events_retention_gzip_roundtrip(monkeypatch, tmp_path):
    mod = _load("v6.events_retention", monkeypatch, tmp_path)
    raw = ('{"event_id":"e1","tool":{"name":"Bash"}}\n' * 200).encode()
    packed = mod.compress(raw, "gzip-9")
    assert len(packed) < len(raw) and mod.decompress(packed, "gzip-9") == raw


def test_memory_tiering_requires_bank(tmp_path):
    env = {"EVOLVING_PROFILE_STATE_ROOT": str(tmp_path), "PATH": ""}
    proc = subprocess.run([sys.executable, "-m", "v6.memory_tiering", "stats"], env=env, capture_output=True, text=True)
    assert proc.returncode != 0 and "EVOLVING_PROFILE_BANK_ID" in proc.stderr


def test_memory_tiering_same_facts_guard(monkeypatch, tmp_path):
    mod = _load("v6.memory_tiering", monkeypatch, tmp_path, EVOLVING_PROFILE_BANK_ID="b")
    assert mod.same_facts("progress 1180 rows", "progress 1180 rows", None, None)
    assert not mod.same_facts("progress 1180 rows", "progress 1280 rows", None, None)
