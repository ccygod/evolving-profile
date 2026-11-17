"""Circuit breaker for the background summary provider chain.

Rules (defaults):
- A transport-level failure of the primary (timeout, connection error, HTTP 401/403/429/5xx)
  counts as one failure. A validation failure does not count: a rejected output is not
  a provider outage.
- After FAILURE_THRESHOLD consecutive failures the breaker OPENS: jobs go straight to the
  fallback chain, without first paying for a failing primary request.
- While OPEN, the primary is probed with a cheap GET <base_url>/models (no tokens) at most
  every PROBE_INTERVAL_SECONDS (10 minutes). A successful probe moves the breaker to HALF_OPEN.
- HALF_OPEN: the next real job tries the primary once. Success CLOSES the breaker (back to
  the primary); failure re-OPENS it and restarts the probe timer.

State is one small JSON file, written atomically. Pure transitions are kept separate from
I/O so they can be tested without a network or a database.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

CLOSED, OPEN, HALF_OPEN = 'closed', 'open', 'half_open'
FAILURE_THRESHOLD = 3
PROBE_INTERVAL_SECONDS = 10 * 60
STATE_FILE = 'provider-breaker.json'


def initial_state():
    return {'state': CLOSED, 'consecutive_failures': 0, 'opened_at': None,
            'last_probe_at': None, 'last_failure_code': None}


def load_state(root):
    path = Path(root) / STATE_FILE
    try:
        data = json.loads(path.read_text())
        if isinstance(data, dict) and data.get('state') in (CLOSED, OPEN, HALF_OPEN):
            return {**initial_state(), **data}
    except (OSError, ValueError):
        pass
    return initial_state()


def save_state(root, state):
    path = Path(root) / STATE_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix='.breaker-')
    with os.fdopen(fd, 'w') as handle:
        json.dump(state, handle, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def route(state, now):
    """Return 'primary', 'fallback', or 'probe_then_route' for the next job."""
    if state['state'] in (CLOSED, HALF_OPEN):
        return 'primary'
    last = state.get('last_probe_at') or state.get('opened_at') or 0
    if now - last >= PROBE_INTERVAL_SECONDS:
        return 'probe_then_route'
    return 'fallback'


def record_success(state, now):
    return {**state, 'state': CLOSED, 'consecutive_failures': 0, 'opened_at': None,
            'last_failure_code': None}


def record_failure(state, now, code):
    if state['state'] == HALF_OPEN:
        return {**state, 'state': OPEN, 'opened_at': now, 'last_probe_at': None,
                'consecutive_failures': state['consecutive_failures'] + 1, 'last_failure_code': code}
    count = state['consecutive_failures'] + 1
    opened = count >= FAILURE_THRESHOLD and state['state'] == CLOSED
    return {**state, 'state': OPEN if (opened or state['state'] == OPEN) else CLOSED,
            'consecutive_failures': count,
            'opened_at': now if opened else state.get('opened_at'),
            'last_failure_code': code}


def record_probe(state, now, ok):
    if ok:
        return {**state, 'state': HALF_OPEN, 'last_probe_at': now}
    return {**state, 'last_probe_at': now}
