"""
Security middleware for Vega 2.0.
Enforces Origin verification, transient X-Vega-Token CSRF validation,
and guaranteed JSON error formatting on all /api/* routes.
"""

import json
import secrets
from bottle import request, response, HTTPError

# Ephemeral session token generated on each application startup
SESSION_TOKEN = secrets.token_urlsafe(32)

ALLOWED_HOSTS = {
    "127.0.0.1:47632",
    "localhost:47632",
    "127.0.0.1",
    "localhost"
}

# Endpoints exempt from mutating X-Vega-Token check
# (Auth handshake, SSE streaming, pre-auth lock screen, and quick voice toggles)
EXEMPT_ROUTES = {
    "/api/auth/token",
    "/api/events",
    "/api/lock/verify",
    "/api/lock/voice_unlock",
    "/api/lock/now",
    "/api/settings/toggle_listening",
    "/api/settings/toggle_speaking",
}


def get_session_token() -> str:
    """Returns the active ephemeral session token."""
    return SESSION_TOKEN


_MIDDLEWARE_APPLIED = False


def apply_security_middleware(app):
    """Applies host/origin checks, CSRF token enforcement, and JSON error handlers."""
    global _MIDDLEWARE_APPLIED
    if _MIDDLEWARE_APPLIED:
        return
    _MIDDLEWARE_APPLIED = True

    # 1. Guaranteed JSON error responses for any /api/ endpoint
    def json_error_handler(err):
        if request.path.startswith("/api/"):
            response.content_type = "application/json; charset=UTF-8"
            err_msg = err.body if (isinstance(err.body, str) and err.body) else f"HTTP {err.status_code}"
            # Avoid double-encoding if err.body is already valid JSON
            try:
                parsed = json.loads(err_msg)
                if isinstance(parsed, dict) and "error" in parsed:
                    return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                pass
            return json.dumps({
                "ok": False,
                "error": err_msg,
                "status": err.status_code
            }, ensure_ascii=False)
        return err.body

    for code in [400, 401, 403, 404, 405, 423, 500]:
        app.error(code)(json_error_handler)

    # 2. Before-request security check
    @app.hook("before_request")
    def validate_request_security():
        # Static files and non-API paths are allowed through
        if not request.path.startswith("/api/"):
            return

        # Host header validation
        host = request.headers.get("Host", "").split(":")[0]
        if host not in {"127.0.0.1", "localhost"}:
            raise HTTPError(403, "Access denied: Invalid Host header.")

        # Origin / Referer validation for Cross-Site Request Forgery
        origin = request.headers.get("Origin")
        if origin:
            clean_origin = origin.replace("http://", "").replace("https://", "").rstrip("/")
            if clean_origin not in ALLOWED_HOSTS:
                raise HTTPError(403, f"Cross-Origin request blocked from {origin}")

        # Exempt read-only endpoints, auth token handshake, and lock screen authentication
        if request.path in EXEMPT_ROUTES:
            return

        # Enforce X-Vega-Token for mutating methods (POST, PUT, DELETE, PATCH)
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            token = request.headers.get("X-Vega-Token")
            if not token or not secrets.compare_digest(token, SESSION_TOKEN):
                raise HTTPError(401, "Unauthorized: Invalid or missing X-Vega-Token header.")


