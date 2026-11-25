"""
Test suite for Vega lock screen error handling and JSON responses.
Verifies that /api/* errors never return HTML <!DOCTYPE> pages.
"""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bottle
from server.middleware import apply_security_middleware

# Initialize bottle app
app = bottle.Bottle()
apply_security_middleware(app)

@app.route("/api/lock/verify", method="POST")
def mock_verify():
    data = bottle.request.json or {}
    if data.get("pin") == "1234":
        bottle.response.content_type = "application/json"
        return json.dumps({"ok": True})
    bottle.response.content_type = "application/json"
    return json.dumps({"ok": False, "error": "Wrong PIN. Try again."})

@app.route("/api/crash_test", method="GET")
def crash():
    raise bottle.HTTPError(500, "Simulated server failure.")

def call_app(app, environ):
    status_and_headers = []
    def start_response(status, headers, exc_info=None):
        status_and_headers.append((status, headers))
    environ["wsgi.errors"] = io.StringIO()
    body_iter = app(environ, start_response)
    body = "".join(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk for chunk in body_iter)
    status_str = status_and_headers[0][0] if status_and_headers else "200 OK"
    headers_dict = dict(status_and_headers[0][1]) if status_and_headers else {}
    return status_str, headers_dict, body

class TestLockJsonErrorHandling(unittest.TestCase):

    def test_verify_pin_without_token_returns_json(self):
        """Pre-auth lock endpoint must be exempt from X-Vega-Token requirement."""
        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/api/lock/verify",
            "HTTP_HOST": "127.0.0.1:47632",
            "CONTENT_TYPE": "application/json",
            "wsgi.input": io.BytesIO(b'{"pin": "9999"}'),
            "CONTENT_LENGTH": str(len(b'{"pin": "9999"}')),
        }
        status, headers, body_text = call_app(app, environ)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        data = json.loads(body_text)
        self.assertFalse(data["ok"])
        self.assertEqual(data["error"], "Wrong PIN. Try again.")

    def test_404_on_api_returns_json_not_html(self):
        """Any 404 on /api/* must return JSON, never HTML <!DOCTYPE."""
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/api/nonexistent_route",
            "HTTP_HOST": "127.0.0.1:47632",
            "wsgi.input": io.BytesIO(b""),
            "CONTENT_LENGTH": "0",
        }
        status, headers, body_text = call_app(app, environ)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertNotIn("<!DOCTYPE", body_text)
        data = json.loads(body_text)
        self.assertFalse(data["ok"])
        self.assertEqual(data["status"], 404)

    def test_403_invalid_host_returns_json_not_html(self):
        """Host mismatch on /api/* must return JSON, never HTML."""
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/api/lock/verify",
            "HTTP_HOST": "evil-attacker.com",
            "wsgi.input": io.BytesIO(b""),
            "CONTENT_LENGTH": "0",
        }
        status, headers, body_text = call_app(app, environ)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertNotIn("<!DOCTYPE", body_text)
        data = json.loads(body_text)
        self.assertFalse(data["ok"])
        self.assertEqual(data["status"], 403)

    def test_500_on_api_returns_json_not_html(self):
        """Any 500 on /api/* must return JSON, never HTML <!DOCTYPE."""
        environ = {
            "REQUEST_METHOD": "GET",
            "PATH_INFO": "/api/crash_test",
            "HTTP_HOST": "127.0.0.1:47632",
            "wsgi.input": io.BytesIO(b""),
            "CONTENT_LENGTH": "0",
        }
        status, headers, body_text = call_app(app, environ)
        self.assertIn("application/json", headers.get("Content-Type", ""))
        self.assertNotIn("<!DOCTYPE", body_text)
        data = json.loads(body_text)
        self.assertFalse(data["ok"])
        self.assertEqual(data["status"], 500)
        self.assertIn("Simulated server failure", data["error"])

if __name__ == "__main__":
    unittest.main()

