import json
from pathlib import Path


REGISTRY = Path(__file__).resolve().parents[1] / 'config' / 'route-registry.json'


def test_route_registry_has_unique_canonical_routes_and_lanes():
    value = json.loads(REGISTRY.read_text(encoding='utf-8'))
    routes = value['routes']
    names = [item['canonical_route'] for item in routes]
    assert len(names) == len(set(names))
    assert {'user_recall', 'user_research', 'user_preference', 'agent_recall', 'agent_research'} <= set(names)
    assert all(item.get('lane') for item in routes)
    assert all(item.get('receipt_schema') for item in routes)


def test_agent_research_is_distinct_from_agent_recall():
    value = json.loads(REGISTRY.read_text(encoding='utf-8'))
    routes = {item['canonical_route']: item for item in value['routes']}
    assert routes['agent_recall']['receipt_schema'] != routes['agent_research']['receipt_schema']
    assert routes['agent_research'].get('mode') == 'graph_research'


def test_agent_research_returns_graph_research_receipt_without_writes():
    import evolving_profile_controller_mcp as controller
    value = controller.research_agent_process_memory({'query': '跨项目分析 React 调试失败与修复模式', 'limit': 5})
    payload = json.loads(value['content'][0]['text'])
    assert payload.get('schema') == 'evolving-profile.agent-process-research.v1'
    assert payload.get('mode') == 'graph_research'
    assert payload.get('writes') is False
    assert payload.get('promotes') is False
