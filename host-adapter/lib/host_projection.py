"""What a tool call sends to the host Agent, as opposed to what EP records.

A tool result serves two readers. The audit trail needs every receipt, binding
and score; the Agent needs the content and whatever lets it take the next step.
Sending both to the Agent made single results larger than hosts accept (a recall
of eight candidates was 75k characters, 55% of it scoring detail), so the Agent
never saw them inline. The full value is still written to the activity ledger
and the returned-content archive before this projection runs; passing
``verbose: true`` to any tool returns it unprojected.

The rules are tables, not code paths: which top-level keys are audit-only, and
which fields of each kind of listed item the Agent acts on.
"""
from __future__ import annotations

import json
import os

# Receipts, identities and scoring internals: recorded by EP, not acted on.
# Deliberately kept: ``plane``/``stage`` (which memory layer a result came from,
# so external or agent-process results are never read as user history),
# ``long_term_model_created`` (a documented read-only promise) and the *state* of
# ``observability_binding`` (whether this call was attributed to the prompt).
AUDIT_ONLY_KEYS = frozenset({
    'tool_call_id', 'occurrence_id', 'host_call_id', 'mcp_request_id', 'invocation_started_at',
    'purpose', 'adapter_version', 'adapter_build_sha256', 'adapter_config_generation',
    'prompt_binding', 'relevance_audit', 'relevance_policy', 'facet_receipts',
    'record_facets', 'adaptive_hint', 'evidence_chain', 'evidence_contract',
    'history_discovery', 'scope_filter', 'source_audit', 'delivery', 'budget', 'decision_policy',
    'strategy', 'discovery_seconds', 'tool_call_count', 'source_state_checked_at',
    'selection_revision', 'selector_version', 'semantic_gate', 'foreground_model_calls',
    'confidence_semantics', 'candidate_policy', 'focus_terms',
    'negative_focus_terms', 'context_source', 'requires_receipt', 'history_receipt_required',
    'bank_detail_read', 'catalog_miss_does_not_prove_bank_absence', 'source_navigation_boundary',
    'raw_discovered_reference_count', 'source_navigation_reference_count', 'source_navigation_returned_count',
})

_MEMORY_FIELDS = ('id', 'type', 'text', 'state', 'occurred_start', 'occurred_end', 'mentioned_at',
                  'document_id', 'authority', 'invalidation_reason')
_MEMORY_METADATA_FIELDS = ('source_role', 'statement_kind', 'origin_agent', 'project')
_PREFERENCE_FIELDS = ('id', 'revision', 'content_sha256', 'section_id', 'model_id', 'model_title', 'primary_category',
                      'text', 'content', 'applies_when', 'exceptions', 'effect_on_action', 'use_mode', 'reason')
_CATALOG_FIELDS = ('topic_id', 'id', 'title', 'abstract', 'overview', 'entities', 'time_range', 'source_count',
                   'pending_changes', 'refreshed_at', 'fact_types', 'read_more')
_NAVIGATION_FIELDS = ('memory_id', 'document_id', 'subject', 'relation', 'title')

# Tools whose listed items are projected, and how. Drill-down tools (catalog_read,
# read_source, read_preference_unit, read_research pages of sources) are absent on
# purpose: their job is to return the detail the listing left out.
_MEMORY_TOOLS = frozenset({'recall', 'research', 'user_recall', 'user_research', 'read_research', 'find_sources'})
_PREFERENCE_TOOLS = frozenset({'user_preference', 'get_preference', 'get_task_guidance', 'refresh_runtime_guidance'})
_CATALOG_LIST_TOOLS = frozenset({'catalog_list', 'catalog_search'})

_EMPTY = (None, '', [], {})
CATALOG_OVERVIEW_CHARS = 240


def full_response_requested(verbose: bool = False) -> bool:
    return bool(verbose) or os.environ.get('EVOLVING_PROFILE_MCP_RESPONSE', 'compact').strip().lower() == 'full'


def _pick(row: dict, fields) -> dict:
    return {key: row[key] for key in fields if row.get(key) not in _EMPTY}


def _memory(row):
    if not isinstance(row, dict):
        return row
    item = _pick(row, _MEMORY_FIELDS)
    metadata = row.get('metadata') if isinstance(row.get('metadata'), dict) else {}
    item.update(_pick(metadata, _MEMORY_METADATA_FIELDS))
    relevance = row.get('relevance')
    level = relevance.get('level') if isinstance(relevance, dict) else relevance
    if level not in _EMPTY:
        item['relevance'] = level
    return item


def _preference(row):
    if not isinstance(row, dict):
        return row
    item = _pick(row, _PREFERENCE_FIELDS)
    # user_id names the owner of the whole registry; only narrower scope is news.
    scope = {key: value for key, value in (row.get('scope') or {}).items() if key != 'user_id' and value not in _EMPTY}
    if scope:
        item['scope'] = scope
    reason = row.get('selection_reason')
    if isinstance(reason, dict):
        if reason.get('tier'):
            item['match'] = reason['tier']
        if reason.get('applicability_risks'):
            item['applicability_risks'] = reason['applicability_risks']
    validity = (row.get('preference_audit') or {}).get('validity_kind')
    if validity and validity != 'stable':
        item['validity'] = validity
    return item


def _catalog(row):
    if not isinstance(row, dict):
        return row
    item = _pick(row, _CATALOG_FIELDS)
    # A listing is for choosing where to look; catalog_read returns the whole overview.
    overview = item.get('overview')
    if isinstance(overview, str) and len(overview) > CATALOG_OVERVIEW_CHARS:
        item['overview'] = overview[:CATALOG_OVERVIEW_CHARS].rstrip() + '…'
    if item.get('pending_changes') == 0:
        item.pop('pending_changes')
    coverage = row.get('coverage')
    if isinstance(coverage, dict) and coverage.get('total') is not None:
        item['coverage'] = {key: coverage[key] for key in ('total', 'sampled') if coverage.get(key) is not None}
    review = row.get('review')
    if isinstance(review, dict) and review.get('state'):
        item['review'] = _pick(review, ('state', 'reason'))
    for key in ('children', 'source_locators'):
        if isinstance(row.get(key), list) and row[key]:
            item[key.rstrip('s') + '_count'] = len(row[key])
    return item


def _scenario_followup(value):
    if not isinstance(value, dict):
        return value
    result = _pick(value, ('decision', 'reason'))
    scenarios = [_pick(row, ('scenario_id', 'navigation_title', 'status')) for row in value.get('scenarios') or [] if isinstance(row, dict)]
    if scenarios:
        result['scenarios'] = scenarios
    return result


def _advisories(value: dict, result: dict) -> None:
    """Two audit records double as warnings to the Agent; keep only the warning."""
    scope = value.get('query_scope_audit')
    result.pop('query_scope_audit', None)
    # The query names amounts or splits the user's prompt did not: say which.
    if isinstance(scope, dict) and scope.get('status') == 'review_added_anchors':
        result['query_scope_audit'] = _pick(scope, ('status', 'added_amounts_wan', 'added_budget_splits', 'next_action', 'boundary'))
    review = value.get('jev_review')
    result.pop('jev_review', None)
    answers = review.get('answers') if isinstance(review, dict) else None
    if isinstance(answers, dict) and answers:
        result['jev_review'] = {'status': review.get('status'), 'effect': review.get('effect') or 'advisory',
                                'answers': {key: ({'choice': item.get('choice')} if isinstance(item, dict) else item) for key, item in answers.items()}}


def project(tool: str, value):
    """The Agent-facing form of one tool result. Non-dict values pass through."""
    if not isinstance(value, dict):
        return value
    result = {key: item for key, item in value.items() if key not in AUDIT_ONLY_KEYS and item not in _EMPTY}
    _advisories(value, result)
    binding = value.get('observability_binding')
    if isinstance(binding, dict) and binding.get('state'):
        result['observability_binding'] = {'state': binding['state']}
    if tool in _MEMORY_TOOLS:
        if isinstance(result.get('memories'), list):
            result['memories'] = [_memory(row) for row in result['memories']]
        if isinstance(result.get('source_navigation'), list):
            result['source_navigation'] = [_pick(row, _NAVIGATION_FIELDS) if isinstance(row, dict) else row
                                           for row in result['source_navigation']]
        if 'scenario_followup' in result:
            result['scenario_followup'] = _scenario_followup(result['scenario_followup'])
        if (value.get('guidance_view') or {}).get('status') == 'view_unavailable':
            result.pop('guidance_view', None)
    elif tool in _PREFERENCE_TOOLS:
        for key in ('stable_profile', 'included', 'model_sections', 'already_loaded_valid'):
            if isinstance(result.get(key), list):
                result[key] = [_preference(row) for row in result[key]]
        # Same rows as ``included`` under a second name.
        result.pop('preference_candidates', None)
        receipt = value.get('delivery_receipt')
        if isinstance(receipt, dict):
            # The host-side receipt check reads exactly these three fields.
            result['delivery_receipt'] = _pick(receipt, ('occurrence_id', 'record_ids', 'content_sha256'))
    elif tool in _CATALOG_LIST_TOOLS:
        if isinstance(result.get('items'), list):
            result['items'] = [_catalog(row) for row in result['items']]
    elif tool == 'memory_check':
        if isinstance(result.get('catalog_hints'), list):
            result['catalog_hints'] = [_pick(row, ('topic_id', 'title', 'time_range', 'source_count')) if isinstance(row, dict) else row
                                       for row in result['catalog_hints']]
        result.pop('catalog_probe', None)
    return result


def compact_result(tool: str, result, *, verbose: bool = False) -> bool:
    """Project the text blocks of an MCP tool result in place. Returns whether anything changed.

    Error results and non-JSON text are left alone: a failure should arrive whole.
    """
    if full_response_requested(verbose) or not isinstance(result, dict) or result.get('isError'):
        return False
    changed = False
    for block in result.get('content') or []:
        if not isinstance(block, dict) or block.get('type') != 'text':
            continue
        try:
            value = json.loads(block.get('text') or '')
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            block['text'] = json.dumps(project(tool, value), ensure_ascii=False, separators=(',', ':'))
            changed = True
    return changed
