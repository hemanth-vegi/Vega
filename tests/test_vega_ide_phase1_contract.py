"""Contract tests for the isolated Vega-IDE specification payload."""

import os
import unittest
from unittest.mock import patch

from integrations.vega_ide_spec import (
    build_handoff_payload,
    bump_spec_version,
    is_ide_bridge_enabled,
    validate_handoff_payload,
)


class VegaIDESpecContractTests(unittest.TestCase):
    def make_payload(self):
        return build_handoff_payload(
            title="Add local status reporting",
            readme_spec_markdown="# Feature Spec: Add local status reporting",
        )

    def test_bridge_flag_defaults_off_and_accepts_explicit_true(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(is_ide_bridge_enabled())
        with patch.dict(os.environ, {"VEGA_IDE_BRIDGE_ENABLED": "true"}, clear=True):
            self.assertTrue(is_ide_bridge_enabled())

    def test_new_payload_waits_for_user_approval(self):
        payload = self.make_payload()
        self.assertFalse(payload["approved_by_user"])
        self.assertEqual(payload["status"], "awaiting_approval")
        self.assertEqual(payload["mode"], "plan_only")
        self.assertTrue(validate_handoff_payload(payload)["ok"])

    def test_revision_increments_version_links_parent_and_resets_approval(self):
        original = self.make_payload()
        original["approved_by_user"] = True
        revised = bump_spec_version(original, "# Feature Spec: Revised", "Clarified done criteria")
        self.assertEqual(revised["spec_version"], 2)
        self.assertEqual(revised["parent_spec_id"], original["spec_id"])
        self.assertNotEqual(revised["spec_id"], original["spec_id"])
        self.assertFalse(revised["approved_by_user"])
        self.assertEqual(revised["status"], "awaiting_approval")

    def test_redacts_tokens_env_lines_paths_and_nested_context(self):
        token = "ghp_" + "a" * 32
        workspace_root = "\\".join(("C:", "Users", "example", "project"))
        payload = build_handoff_payload(
            title="Protect credentials",
            readme_spec_markdown="# Feature Spec: Protect credentials",
            ide_context={
                "workspace_root": workspace_root,
                "active_file": workspace_root + r"\.env",
                "selected_text": f"Authorization: Bearer abc.def.ghi\nGITHUB_TOKEN={token}",
                "recent_diagnostics": [
                    {"message": "GEMINI_API_KEY=AIza" + "B" * 32},
                    "OPENROUTER_API_KEY=sk-or-v1-" + "c" * 32,
                ],
            },
        )
        context_text = repr(payload["ide_context"])
        for secret in (token, "abc.def.ghi", "AIza" + "B" * 32, "sk-or-v1-" + "c" * 32):
            self.assertNotIn(secret, context_text)
        self.assertNotIn("\\Users\\example", context_text)
        self.assertIn("REDACTED_ENV_PATH", context_text)

    def test_validation_reports_missing_and_invalid_fields_without_raising(self):
        missing = validate_handoff_payload({})
        self.assertFalse(missing["ok"])
        self.assertTrue(any("missing field" in item for item in missing["errors"]))

        invalid = self.make_payload()
        invalid["status"] = "approved"
        invalid["approval_tier"] = "unsafe"
        invalid["constraints"]["stay_within_scope"] = False
        result = validate_handoff_payload(invalid)
        self.assertFalse(result["ok"])
        self.assertGreaterEqual(len(result["errors"]), 3)
        self.assertFalse(validate_handoff_payload(None)["ok"])


if __name__ == "__main__":
    unittest.main()
