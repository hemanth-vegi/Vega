"""
Security middleware for Vega 2.0.
Enforces Origin verification and transient X-Vega-Token CSRF validation.
"""

import secrets
from bottle import hook, request, response, HTTPError

# Ephemeral session token generated on each application startup
SESSION_TOKEN = secrets.token_urlsafe(32)

ALLOWED_HOSTS = {
    "127.0.0.1:47632",
    "localhost:47632",
    "127.0.0.1",
    "localhost"
}


def get_session_token() -> str:
    """Returns the active ephemeral session token."""
    return SESSION_TOKEN


def apply_security_middleware(app):
    """Applies host/origin checks and CSRF token enforcement to the Bottle app."""

    @app.hook("before_request")
    def validate_request_security():
        # Static files and non-API paths are allowed through
        if not request.path.startswith("/api/"):
            return

        # 1. Host header validation
        host = request.headers.get("Host", "").split(":")[0]
        if host not in {"127.0.0.1", "localhost"}:
            raise HTTPError(403, "Access denied: Invalid Host header.")

        # 2. Origin / Referer validation for Cross-Site Request Forgery
        origin = request.headers.get("Origin")
        if origin:
            # Strip protocol
            clean_origin = origin.replace("http://", "").replace("https://", "").rstrip("/")
            if clean_origin not in ALLOWED_HOSTS:
                raise HTTPError(403, f"Cross-Origin request blocked from {origin}")

        # 3. Exempt read-only endpoints and auth token endpoint
        if request.path in {"/api/auth/token", "/api/events"}:
            return

        # 4. Enforce X-Vega-Token for mutating methods (POST, PUT, DELETE, PATCH)
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            token = request.headers.get("X-Vega-Token")
            if not token or not secrets.compare_digest(token, SESSION_TOKEN):
                raise HTTPError(401, "Unauthorized: Invalid or missing X-Vega-Token header.")

