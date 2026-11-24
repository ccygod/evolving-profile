"""Both production preference paths must use the same retrieval mode (2026-11-24).

Incident: the prompt Hook hard-coded semantic_recall="local" while the MCP
user_preference tool never passed it, so the same question got different
preference candidates depending on the entry path, and the evaluation script
(which only drives the Hook path) could not see the difference.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "guidance"))
sys.path.insert(0, str(Path(__file__).resolve().parent))


class PreferencePathParityTest(unittest.TestCase):
    def _with_setting(self, value):
        import mcp_runtime
        tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump({"semantic_recall": value} if value is not None else {}, tmp)
        tmp.close()
        self.addCleanup(os.unlink, tmp.name)
        old = mcp_runtime.GUIDANCE_SETTINGS_PATH
        mcp_runtime.GUIDANCE_SETTINGS_PATH = Path(tmp.name)
        self.addCleanup(setattr, mcp_runtime, "GUIDANCE_SETTINGS_PATH", old)

    def test_hook_request_follows_shared_setting(self):
        import guidance_entry
        for value in ("off", "local"):
            self._with_setting(value)
            request = guidance_entry.build_request({}, "请帮我检查一下", "ctx")
            self.assertEqual(request["semantic_recall"], value)

    def test_default_is_local_when_unset_or_invalid(self):
        import mcp_runtime
        for value in (None, "bogus"):
            self._with_setting(value)
            self.assertEqual(mcp_runtime.preference_semantic_mode(), "local")

    def test_mcp_tool_applies_shared_setting(self):
        src = (Path(__file__).resolve().parent / "evolving_profile_controller_mcp.py").read_text(encoding="utf-8")
        body = src.split("def preference(args):", 1)[1].split("\ndef ", 1)[0]
        self.assertIn("preference_semantic_mode()", body)
        self.assertNotIn("'semantic_recall':'local'", body.replace(" ", ""))


if __name__ == "__main__":
    unittest.main()
