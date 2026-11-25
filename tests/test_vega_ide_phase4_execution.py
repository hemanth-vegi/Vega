"""Hermetic dispatch, sequence, cancellation, and voice debrief tests."""

import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from integrations.handoff_dispatcher import (
    apply_status_event,
    build_debrief,
    cancel_run,
    dispatch_approved_spec,
    get_spec_status,
    start_dispatch,
)
from integrations.spec_compiler import compile_spoken_spec


class _ReceiverHandler(BaseHTTPRequestHandler):
    scenario = "complete"
    cancel_event = threading.Event()
    received_payload = None

    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        data = json.loads(self.rfile.read(size) or b"{}")
        if self.path == "/api/vega/cancel":
            type(self).cancel_event.set()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
            return
        if self.path != "/api/vega/handoff":
            self.send_response(404)
            self.end_headers()
            return

        type(self).received_payload = data
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        running = {
            "spec_id": data["spec_id"], "run_id": data["run_id"], "seq": data["seq"] + 1,
            "status": "running", "summary_text": "Receiver accepted approved work.", "changed_files": [],
        }
        self.wfile.write((json.dumps(running) + "\n").encode("utf-8"))
        self.wfile.flush()
        if type(self).scenario == "cancel":
            type(self).cancel_event.wait(3)
            final_status = "failed"
            reason = "cancelled_by_user"
            summary = "Cancelled by user."
        elif type(self).scenario == "timeout":
            time.sleep(0.5)
            final_status = "completed"
            reason = ""
            summary = "This late event should be ignored."
        else:
            final_status = "completed"
            reason = ""
            summary = "Read-only handoff completed; no files changed."
        final = {
            "spec_id": data["spec_id"], "run_id": data["run_id"], "seq": data["seq"] + 2,
            "status": final_status, "summary_text": summary, "changed_files": [],
        }
        if reason:
            final["reason"] = reason
        try:
            self.wfile.write((json.dumps(final) + "\n").encode("utf-8"))
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def log_message(self, _format, *_args):
        return


class HandoffDispatcherTests(unittest.TestCase):
    def setUp(self):
        _ReceiverHandler.scenario = "complete"
        _ReceiverHandler.cancel_event = threading.Event()
        _ReceiverHandler.received_payload = None
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _ReceiverHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def make_payload(self, approved=True):
        payload = compile_spoken_spec("Inspect the project and summarize the affected interfaces.")
        payload["approved_by_user"] = approved
        payload["approval_tier"] = "apply_edits"
        payload["mode"] = "plan_and_apply"
        payload["transcript_cleanup_note"] = "raw transcript must not be forwarded"
        payload["raw_chat_history"] = "never forward this field"
        return payload

    def test_dispatch_refuses_unapproved_edits(self):
        result = dispatch_approved_spec(self.make_payload(approved=False), endpoint=self.endpoint)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "awaiting_approval")
        self.assertIsNone(_ReceiverHandler.received_payload)

    def test_dispatch_sends_only_distilled_redacted_contract_and_applies_ordered_events(self):
        payload = self.make_payload()
        result = dispatch_approved_spec(payload, endpoint=self.endpoint)
        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertGreaterEqual(result["seq"], 3)
        sent = _ReceiverHandler.received_payload
        self.assertNotIn("transcript_cleanup_note", sent)
        self.assertNotIn("raw_chat_history", sent)
        self.assertEqual(sent["approval_tier"], "apply_edits")

        state = {}
        event = {"spec_id": "spec", "run_id": "run", "seq": 2, "status": "running"}
        self.assertTrue(apply_status_event(state, event))
        self.assertFalse(apply_status_event(state, event))
        stale = {**event, "seq": 1, "status": "drafting"}
        self.assertFalse(apply_status_event(state, stale))
        self.assertEqual(state["spec"]["seq"], 2)

    def test_timeout_sets_failed_state(self):
        _ReceiverHandler.scenario = "timeout"
        result = dispatch_approved_spec(self.make_payload(), endpoint=self.endpoint, timeout_sec=0.1)
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["reason"], "timeout")

    def test_cancel_waits_for_receiver_rollback_and_debrief_stays_silent_when_busy(self):
        _ReceiverHandler.scenario = "cancel"
        payload = self.make_payload()
        initial = start_dispatch(payload, endpoint=self.endpoint, timeout_sec=3)
        self.assertTrue(initial["ok"])
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            state = get_spec_status(initial["spec_id"])
            if state and state.get("seq", 0) >= 2:
                break
            time.sleep(0.02)
        cancelled = cancel_run(initial["spec_id"], initial["run_id"], endpoint=self.endpoint)
        self.assertTrue(cancelled["ok"])
        self.assertEqual(cancelled["status"], "running")
        self.assertEqual(cancelled["reason"], "cancellation_requested")
        deadline = time.monotonic() + 2
        final = get_spec_status(initial["spec_id"])
        while time.monotonic() < deadline and final and final.get("status") != "failed":
            time.sleep(0.02)
            final = get_spec_status(initial["spec_id"])
        self.assertEqual(final["status"], "failed")
        self.assertEqual(final["reason"], "cancelled_by_user")
        self.assertFalse(build_debrief(final, is_silent=False, voice_busy=True)["should_speak"])
        self.assertFalse(build_debrief(final, is_silent=True, voice_busy=False)["should_speak"])


if __name__ == "__main__":
    unittest.main()

