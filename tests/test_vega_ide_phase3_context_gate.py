"""Tests for read-only IDE context and two-tier approval gating."""

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from integrations.ide_context_bridge import fetch_ide_context
from integrations.spec_compiler import compile_spoken_spec
from integrations.ide_context_bridge import gate_spec_approval


class _ContextHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/api/vega/context":
            self.send_response(404)
            self.end_headers()
            return
        workspace_root = "\\".join(("C:", "Users", "example", "workspace"))
        body = json.dumps({
            "workspace_root": workspace_root,
            "active_file": workspace_root + r"\.env",
            "selected_text": "GROQ_API_KEY=gsk_" + "a" * 30,
            "recent_diagnostics": [{"message": "Bearer " + "b" * 40}],
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format, *_args):
        return


class VegaIDEContextGateTests(unittest.TestCase):
    def test_offline_ide_returns_safe_empty_context(self):
        context = fetch_ide_context("http://127.0.0.1:1", timeout=0.1)
        self.assertFalse(context["ide_online"])
        self.assertEqual(context["workspace_root"], "")
        self.assertEqual(context["recent_diagnostics"], [])

    def test_loopback_context_is_redacted_before_return(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), _ContextHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            context = fetch_ide_context(f"http://127.0.0.1:{server.server_port}", timeout=1.0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        text = json.dumps(context)
        self.assertTrue(context["ide_online"])
        self.assertNotIn("\\Users\\example", text)
        self.assertNotIn(".env", context["active_file"])
        self.assertNotIn("gsk_", text)
        self.assertNotIn("Bearer b", text)

    def test_unapproved_edits_are_held_and_explicit_approval_flips(self):
        payload = compile_spoken_spec("Add a safe edit flow.", title="Safe edit flow")
        held = gate_spec_approval(payload, approved=False, approval_tier="apply_edits")
        self.assertFalse(held["approved_by_user"])
        self.assertEqual(held["status"], "awaiting_approval")

        approved = gate_spec_approval(payload, approved=True, approval_tier="apply_edits")
        self.assertTrue(approved["approved_by_user"])
        self.assertEqual(approved["mode"], "plan_and_apply")

    def test_read_only_dry_run_can_proceed_without_edit_approval(self):
        payload = compile_spoken_spec("Inspect the current routes.")
        result = gate_spec_approval(payload, approved=False, approval_tier="read_only")
        self.assertFalse(result["approved_by_user"])
        self.assertEqual(result["status"], "running")
        self.assertEqual(result["mode"], "plan_only")


if __name__ == "__main__":
    unittest.main()

