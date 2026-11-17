"""Deterministic checks for the provider circuit breaker and fallback routing.

Run: python test_provider_breaker.py  (no network, no database, no model calls)
"""
import tempfile
import urllib.error
from pathlib import Path

import lib.provider_breaker as pb
from lib.context_incremental import ContextIncremental


def test_breaker_transitions():
    s = pb.initial_state()
    for t in range(pb.FAILURE_THRESHOLD - 1):
        s = pb.record_failure(s, 100 + t, 'http_403')
        assert s['state'] == pb.CLOSED, s
    s = pb.record_failure(s, 200, 'http_403')
    assert s['state'] == pb.OPEN and s['opened_at'] == 200
    assert pb.route(s, 200) == 'fallback'
    assert pb.route(s, 200 + pb.PROBE_INTERVAL_SECONDS) == 'probe_then_route'
    s = pb.record_probe(s, 200 + pb.PROBE_INTERVAL_SECONDS, ok=False)
    assert s['state'] == pb.OPEN and pb.route(s, 201 + pb.PROBE_INTERVAL_SECONDS) == 'fallback'
    s = pb.record_probe(s, 500000, ok=True)
    assert s['state'] == pb.HALF_OPEN and pb.route(s, 500001) == 'primary'
    s = pb.record_success(s, 500002)
    assert s['state'] == pb.CLOSED and s['consecutive_failures'] == 0


def test_half_open_failure_reopens():
    s = {**pb.initial_state(), 'state': pb.HALF_OPEN, 'consecutive_failures': 3}
    s = pb.record_failure(s, 900, 'http_403')
    assert s['state'] == pb.OPEN and s['opened_at'] == 900


def test_success_resets_failure_streak():
    s = pb.initial_state()
    s = pb.record_failure(s, 1, 'http_403')
    s = pb.record_failure(s, 2, 'http_403')
    s = pb.record_success(s, 3)
    s = pb.record_failure(s, 4, 'http_403')
    assert s['state'] == pb.CLOSED and s['consecutive_failures'] == 1


def test_state_roundtrip_and_corrupt_file():
    with tempfile.TemporaryDirectory() as root:
        assert pb.load_state(root)['state'] == pb.CLOSED
        pb.save_state(root, {**pb.initial_state(), 'state': pb.OPEN, 'opened_at': 5})
        assert pb.load_state(root)['state'] == pb.OPEN
        (Path(root) / pb.STATE_FILE).write_text('{not json')
        assert pb.load_state(root)['state'] == pb.CLOSED


def _runner(root):
    obj = ContextIncremental.__new__(ContextIncremental)
    obj.root = Path(root)
    obj.session_root = 'x'
    return obj


def _config():
    return {'EVOLVING_PROFILE_API_LLM_MODEL': 'primary-m', 'EVOLVING_PROFILE_API_LLM_BASE_URL': 'https://p',
            'EVOLVING_PROFILE_API_LLM_API_KEY': 'k',
            '_coverage_fallbacks': [{'model': 'fb-m', 'base_url': 'https://f', 'api_key': 'k2'}]}


def test_routing_end_to_end(monkeypatch_probe=None):
    import lib.context_incremental as ci
    calls = []
    primary_ok = {'value': False}

    def processor(source, row, root, cfg):
        model = cfg['EVOLVING_PROFILE_API_LLM_MODEL']
        calls.append(model)
        if model == 'primary-m' and not primary_ok['value']:
            raise urllib.error.HTTPError('https://p', 403, 'forbidden', {}, None)
        return {'status': 'model_reviewed', 'model': model}

    ci._probe_provider_models = lambda cfg: primary_ok['value']
    with tempfile.TemporaryDirectory() as root:
        obj = _runner(root)
        for _ in range(pb.FAILURE_THRESHOLD):
            assert obj._process_with_fallbacks(processor, {}, {}, _config())['model'] == 'fb-m'
        assert pb.load_state(root)['state'] == pb.OPEN
        calls.clear()
        assert obj._process_with_fallbacks(processor, {}, {}, _config())['model'] == 'fb-m'
        assert calls == ['fb-m'], calls          # open: no wasted primary request
        primary_ok['value'] = True
        state = pb.load_state(root)
        state['last_probe_at'] = None
        state['opened_at'] = 0                   # probe interval has elapsed
        pb.save_state(root, state)
        calls.clear()
        assert obj._process_with_fallbacks(processor, {}, {}, _config())['model'] == 'primary-m'
        assert pb.load_state(root)['state'] == pb.CLOSED


def test_validation_error_is_not_retried_nor_counted():
    with tempfile.TemporaryDirectory() as root:
        obj = _runner(root)
        def processor(source, row, root_, cfg):
            raise ValueError('scenario_model_response_incomplete')
        try:
            obj._process_with_fallbacks(processor, {}, {}, _config())
            raise AssertionError('expected ValueError')
        except ValueError:
            pass
        assert pb.load_state(root)['consecutive_failures'] == 0


if __name__ == '__main__':
    import sys
    failed = 0
    for name, fn in list(globals().items()):
        if name.startswith('test_') and callable(fn):
            try:
                fn()
                print('PASS', name)
            except Exception as error:
                failed += 1
                print('FAIL', name, type(error).__name__, error)
    sys.exit(1 if failed else 0)
