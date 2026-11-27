"""The launcher must never hand the ungoverned Codex identity to a host that did not ask for it."""
import json
import os
import subprocess
import unittest
from pathlib import Path

LAUNCHER = Path(__file__).resolve().parents[2] / 'bin' / 'evolving-profile-controller-mcp.zsh'
WRITERS = {'record_agent_trajectory', 'promote_agent_process_memory', 'write_agent_process_workspace', 'update_task_state',
           'record_evidence_decision', 'review_operation_risk', 'record_agent_process_draft'}


def listed_tools(extra_env):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('CLAUDE', 'EVOLVING_PROFILE', 'HINDSIGHT', 'CODEBUDDY'))}
    env.update(extra_env)
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"}, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}]
    done = subprocess.run([str(LAUNCHER)], input='\n'.join(json.dumps(line) for line in lines) + '\n', capture_output=True, text=True, env=env, timeout=120)
    for line in done.stdout.splitlines():
        row = json.loads(line)
        if row.get('id') == 2:
            return {tool['name'] for tool in row['result']['tools']}
    raise AssertionError(done.stderr[-300:])


@unittest.skipUnless(LAUNCHER.is_file(), 'deployed launcher not present')
class LauncherIdentityTests(unittest.TestCase):
    def test_an_unknown_host_is_governed_and_cannot_write(self):
        tools = listed_tools({})
        self.assertFalse(WRITERS & tools)
        self.assertIn('user_recall', tools)

    def test_known_hosts_stay_governed(self):
        for env in ({'EVOLVING_PROFILE_HOST_ID': 'hermes'}, {'CODEBUDDY_HOST': 'workbuddy-desktop'}, {'CLAUDE_CODE_ENTRYPOINT': 'cli'}):
            self.assertFalse(WRITERS & listed_tools(env), env)

    def test_codex_authority_needs_an_explicit_codex_identity(self):
        self.assertTrue(WRITERS & listed_tools({'EVOLVING_PROFILE_HOST_ID': 'codex'}))
        self.assertFalse(WRITERS & listed_tools({'EVOLVING_PROFILE_HOST_ID': 'codex', 'EVOLVING_PROFILE_ACCESS_MODE': 'read-only'}))


if __name__ == '__main__':
    unittest.main()
