"""Loopback end-to-end contract checks for the spoken-spec handoff flow."""

from __future__ import annotations

import json
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from integrations.handoff_dispatcher import apply_status_event, build_debrief, dispatch_approved_spec
from integrations.ide_context_bridge import fetch_ide_context
from integrations.spec_compiler import compile_spoken_spec
from integrations.vega_ide_spec import validate_handoff_payload


class _E2EReceiver(BaseHTTPRequestHandler):
    received = None

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(size) or b"{}")
        type(self).received = payload
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        events = [
            {
                "spec_id": payload["spec_id"], "run_id": payload["run_id"],
                "seq": payload["seq"] + 1, "status": "running",
                "summary_text": "Mock receiver accepted the reviewed spec.", "changed_files": [],
            },
            {
                "spec_id": payload["spec_id"], "run_id": payload["run_id"],
                "seq": payload["seq"] + 2, "status": "completed",
                "summary_text": "Read-only review completed; no files changed.",
                "check_summary": "Mock protocol check passed.", "changed_files": [],
            },
        ]
        for event in events:
            self.wfile.write((json.dumps(event) + "\n").encode("utf-8"))
            self.wfile.flush()

    def log_message(self, _format, *_args):
        return


class VegaIdeHandoffE2ETests(unittest.TestCase):
    def setUp(self):
        _E2EReceiver.received = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _E2EReceiver)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_compile_redact_approve_dispatch_and_debrief(self):
        token = "ghp_" + "a" * 32
        transcript = f"Please add a settings panel and never log Bearer {token} from C:\\Users\\demo\\workspace."
        payload = compile_spoken_spec(
            transcript,
            ide_context={
                "workspace_root": "C:\\Users\\demo\\workspace",
                "active_file": "C:\\Users\\demo\\workspace\\.env",
                "selected_text": f"API_KEY={token}",
                "recent_diagnostics": [{"message": f"Bearer {token}"}],
            },
        )
        payload.update({"approved_by_user": True, "approval_tier": "apply_edits", "mode": "plan_and_apply"})
        self.assertTrue(validate_handoff_payload(payload)["ok"])

        result = dispatch_approved_spec(payload, endpoint=self.endpoint, timeout_sec=3)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["seq"], 3)
        sent = _E2EReceiver.received
        self.assertIsNotNone(sent)
        serialized = json.dumps(sent)
        self.assertNotIn(token, serialized)
        self.assertNotIn("Bearer ", serialized)
        self.assertNotIn("C:\\\\Users\\\\demo", serialized)
        self.assertNotIn("transcript", sent)
        self.assertNotIn("transcript_cleanup_note", sent)
        self.assertEqual(sent["changed_files"] if "changed_files" in sent else [], [])
        self.assertTrue(build_debrief(result, is_silent=False, voice_busy=False)["should_speak"])
        self.assertFalse(build_debrief(result, is_silent=True, voice_busy=False)["should_speak"])
        self.assertFalse(build_debrief(result, is_silent=False, voice_busy=True)["should_speak"])

    def test_offline_receiver_stays_offline_and_unapproved_apply_does_not_send(self):
        payload = compile_spoken_spec("Add a small status panel.")
        payload.update({"approval_tier": "apply_edits", "mode": "plan_and_apply", "approved_by_user": False})
        refused = dispatch_approved_spec(payload, endpoint=self.endpoint)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["reason"], "explicit_approval_required")
        self.assertIsNone(_E2EReceiver.received)

        reservation = socket.socket()
        reservation.bind(("127.0.0.1", 0))
        offline_port = reservation.getsockname()[1]
        reservation.close()
        offline_context = fetch_ide_context(f"http://127.0.0.1:{offline_port}", timeout=0.2)
        self.assertFalse(offline_context["ide_online"])

        payload["approved_by_user"] = True
        result = dispatch_approved_spec(payload, endpoint=f"http://127.0.0.1:{offline_port}", timeout_sec=3)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "receiver_unavailable")

    def test_status_events_ignore_duplicates_stale_and_terminal_rewrites(self):
        state = {}
        running = {"spec_id": "spec-safe", "run_id": "run-safe", "seq": 2, "status": "running"}
        completed = {**running, "seq": 3, "status": "completed"}
        changed = {**running, "seq": 4, "status": "failed", "reason": "late event"}
        self.assertTrue(apply_status_event(state, running))
        self.assertFalse(apply_status_event(state, running))
        self.assertTrue(apply_status_event(state, completed))
        self.assertFalse(apply_status_event(state, changed))
        self.assertEqual(state["spec-safe"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
