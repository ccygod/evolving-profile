"""Fit the Hook's injected context into the size a host will show inline.

Claude Code keeps an injected context of up to about 10,000 characters in the
conversation. A longer one is written to a file and the model sees a 2KB
preview, which here is the navigation map: the candidate preferences and the
route hint, after it, are lost even though EP recorded them as delivered. So
the context has to arrive under the limit, not be cut by the host.

Fitting is by block, least valuable first, and each step runs only while the
text is still too long. A guidance record is never cut: dropping a whole record
is a decision the entry already makes with its own budget.
"""
from __future__ import annotations

import json
import re

# The host keeps comfortably less than its stated limit: the limit is counted in
# characters of the whole hook output, and a few hundred characters of framing
# are added after EP's text.
DEFAULT_HOST_LIMITS = {'claude-code': 9000, 'claude-desktop': 9000, 'claude-cli': 9000}

ROUTE_HINT_KEYS = ('topic_id', 'title', 'time_range', 'source_count', 'memory_id', 'type', 'topic', 'mentioned_at')
_ROUTE = re.compile(r'(<evolving_profile_memory_route[^>]*>\n本轮已执行低成本目录探针：)(\{[^\n]*\})(\n)')
_NAV = re.compile(r'(<evolving_profile_navigation_map[^>]*>)([\s\S]*?)(</evolving_profile_navigation_map>)')
_DIMENSION_LINE = re.compile(r'^((?:沟通与呈现|学习与理解|分析与决策|执行与协作|质量与交付) \d+ 条)｜.*$')
CATALOG_POINTER = '主题域入口：catalog_list 列出，catalog_read(topic_id) 进入子主题。'
NAV_OMITTED = '地图因宿主上限已省略；catalog_list 可列出主题，偏好与历史以工具返回为准。'


def host_limit(host: str, overrides: dict | None = None) -> int | None:
    """Inline limit in characters for a host, or None when the host has no known cap."""
    table = {**DEFAULT_HOST_LIMITS, **{str(k).casefold(): v for k, v in (overrides or {}).items()}}
    value = table.get(str(host or '').casefold())
    return int(value) if isinstance(value, (int, float)) and value > 0 else None


def _compact_route_hints(text: str) -> str:
    def replace(match):
        try:
            payload = json.loads(match.group(2))
        except ValueError:
            return match.group(0)
        payload['catalog_hints'] = [{key: hint[key] for key in ROUTE_HINT_KEYS if key in hint}
                                    for hint in payload.get('catalog_hints') or [] if isinstance(hint, dict)]
        return match.group(1) + json.dumps(payload, ensure_ascii=False) + match.group(3)
    return _ROUTE.sub(replace, text, count=1)


def _rewrite_nav(text: str, transform) -> str:
    def replace(match):
        return match.group(1) + transform(match.group(2)) + match.group(3)
    return _NAV.sub(replace, text, count=1)


def _drop_domain_lines(body: str) -> str:
    lines, pointer_added, kept = body.split('\n'), False, []
    for line in lines:
        if line.startswith('domain:') or line.startswith('以下每个domain入口'):
            if not pointer_added:
                kept.append(CATALOG_POINTER)
                pointer_added = True
            continue
        kept.append(line)
    return '\n'.join(kept)


def _collapse_dimension_lines(body: str) -> str:
    return '\n'.join(_DIMENSION_LINE.sub(r'\1', line) for line in body.split('\n'))


def _omit_nav(body: str) -> str:
    return '\n' + NAV_OMITTED + '\n'


STEPS = (
    ('route_hints_to_locators', _compact_route_hints),
    ('nav_domain_lines', lambda text: _rewrite_nav(text, _drop_domain_lines)),
    ('nav_dimension_topics', lambda text: _rewrite_nav(text, _collapse_dimension_lines)),
    ('nav_omitted', lambda text: _rewrite_nav(text, _omit_nav)),
)


def fit_to_limit(text: str, limit: int | None) -> tuple[str, dict]:
    """(fitted text, report). Without a limit, or already inside it, the text is returned unchanged."""
    report = {'limit': limit, 'before': len(text), 'steps': [], 'after': len(text), 'fits': True}
    if not limit or len(text) <= limit:
        return text, report
    for name, step in STEPS:
        shorter = step(text)
        if shorter != text:
            text = shorter
            report['steps'].append(name)
        if len(text) <= limit:
            break
    report.update(after=len(text), fits=len(text) <= limit)
    return text, report
