"""
Test suite for Vega toggle listening and toggle speaking endpoints.
Verifies that quick voice controls work without 401 CSRF rejections.
"""

import io
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bottle
from server.middleware import apply_security_middleware

app = bottle.Bottle()
apply_security_middleware(app)

mock_state = {
    "listening_on": True,
    "voice_on": True,
}

@app.route("/api/settings/toggle_listening", method="POST")
def toggle_listening():
    mock_state["listening_on"] = not mock_state["listening_on"]
    bottle.response.content_type = "application/json"
    return json.dumps({"ok": True, "listening_on": mock_state["listening_on"]})

@app.route("/api/settings/toggle_speaking", method="POST")
def toggle_speaking():
    mock_state["voice_on"] = not mock_state["voice_on"]
    bottle.response.content_type = "application/json"
    return json.dumps({"ok": True, "voice_on": mock_state["voice_on"]})

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

class TestToggleEndpoints(unittest.TestCase):

    def test_toggle_listening_exempt_and_functional(self):
        """Toggle listening must return 200 OK with valid JSON state."""
        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/api/settings/toggle_listening",
            "HTTP_HOST": "127.0.0.1:47632",
            "wsgi.input": io.BytesIO(b""),
            "CONTENT_LENGTH": "0",
        }
        status, headers, body_text = call_app(app, environ)
        self.assertTrue(status.startswith("200"))
        self.assertIn("application/json", headers.get("Content-Type", ""))
        data = json.loads(body_text)
        self.assertTrue(data["ok"])
        self.assertIn("listening_on", data)

    def test_toggle_speaking_exempt_and_functional(self):
        """Toggle speaking must return 200 OK with valid JSON state."""
        environ = {
            "REQUEST_METHOD": "POST",
            "PATH_INFO": "/api/settings/toggle_speaking",
            "HTTP_HOST": "127.0.0.1:47632",
            "wsgi.input": io.BytesIO(b""),
            "CONTENT_LENGTH": "0",
        }
        status, headers, body_text = call_app(app, environ)
        self.assertTrue(status.startswith("200"))
        self.assertIn("application/json", headers.get("Content-Type", ""))
        data = json.loads(body_text)
        self.assertTrue(data["ok"])
        self.assertIn("voice_on", data)

if __name__ == "__main__":
    unittest.main()
