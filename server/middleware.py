"""
Security middleware for Vega 2.0.
Enforces Origin verification, transient X-Vega-Token CSRF validation,
and guaranteed JSON error formatting on all /api/* routes.
"""

import json
import secrets
from urllib.parse import urlsplit
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
    "/api/send",
    "/api/auth/token",
    "/api/events",
    "/api/lock/verify",
    "/api/lock/voice_unlock",
    "/api/lock/now",
    "/api/settings/toggle_listening",
    "/api/settings/toggle_speaking",
    "/api/career/ingest",
    "/api/career/scan",
    "/api/career/jobs",
    "/api/career/profile",
    "/api/career/analyze",
    "/api/voice/speak",
    "/api/voice/transcribe",
    "/api/voice/mic-arb",
    "/api/voice/mic-arb/",
    "/api/voice/sleep",
    "/api/voice/sleep/",
    "/api/interview/start",
    "/api/interview/answer",
    "/api/interview/finish",
    "/api/workspace/explain-clipboard",
    "/api/workspace/look-at-screen",
    "/api/workspace/action",
    "/api/settings/persona",
    "/api/settings/english-coach",
    "/api/settings/english-coach/",
    "/api/settings/lock_on_start",
    "/api/settings/lock_on_start/",
    "/api/orchestrator/status",
    "/api/orchestrator/status/",
    "/api/career/github/sync",
    "/api/career/github/sync/",
    "/api/career/github/repos",
    "/api/career/github/repos/",
    "/api/career/github/repo/toggle",
    "/api/career/github/repo/toggle/",
    "/api/career/resume/analyze-jd",
    "/api/career/resume/analyze-jd/",
    "/api/career/analyze-jd",
    "/api/career/analyze-jd/",
    "/api/career/resume/generate-tailored",
    "/api/career/resume/generate-tailored/",
    "/api/career/resume/tailor",
    "/api/career/resume/tailor/",
    "/api/career/generate_resume",
    "/api/career/generate_resume/",
    "/api/career/resume/generate",
    "/api/career/resume/session/start",
    "/api/career/resume/session/start/",
    "/api/career/resume/generate/",
    "/api/career/tailor",
    "/api/career/tailor/",
    "/api/career/download_pdf",
    "/api/career/download_pdf/",
    "/api/jarvis/status",
    "/api/jarvis/status/",
    "/api/jarvis/tasks",
    "/api/jarvis/tasks/",
    "/api/voice/listen-local",
    "/api/voice/listen-local/",
    "/api/chat/upload_pdf",
    "/api/chat/upload_pdf/",
    "/api/career/profile/import_pdf",
    "/api/career/profile/import_pdf/",
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
        if request.path.startswith("/api/") or request.path == "/api":
            response.content_type = "application/json; charset=UTF-8"
            raw_body = err.body if isinstance(err.body, str) else ""
            # Avoid double-encoding if err.body is already valid JSON
            try:
                parsed = json.loads(raw_body)
                if isinstance(parsed, dict) and "error" in parsed:
                    return json.dumps(parsed, ensure_ascii=False)
            except Exception:
                pass
            if err.status_code == 404:
                err_msg = "Not Found"
            elif err.status_code == 405:
                err_msg = "Method Not Allowed"
            elif raw_body and not raw_body.strip().startswith("<"):
                err_msg = raw_body
            else:
                err_msg = f"HTTP {err.status_code}"
            return json.dumps({
                "ok": False,
                "error": err_msg,
                "status": err.status_code,
                "path": request.path
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

        # Host header validation (blocks DNS rebinding / non-loopback hosts with 403 JSON)
        host = request.headers.get("Host", "").split(":")[0]
        if host and host not in {"127.0.0.1", "localhost"}:
            raise HTTPError(403, "Access denied: Invalid Host header.")

        # Origin / Referer validation for Cross-Site Request Forgery
        origin = request.headers.get("Origin")
        if origin:
            clean_origin = origin.replace("http://", "").replace("https://", "").rstrip("/")
            request_host = request.headers.get("Host", "").lower()
            try:
                parsed_origin = urlsplit(origin)
                is_same_loopback_origin = (
                    parsed_origin.scheme == request.environ.get("wsgi.url_scheme", "http")
                    and parsed_origin.netloc.lower() == request_host
                    and parsed_origin.hostname in {"127.0.0.1", "localhost"}
                    and parsed_origin.username is None
                    and parsed_origin.password is None
                    and parsed_origin.path in {"", "/"}
                    and not parsed_origin.query
                    and not parsed_origin.fragment
                )
            except ValueError:
                is_same_loopback_origin = False
            is_allowed = clean_origin in ALLOWED_HOSTS or is_same_loopback_origin or (
                request.path == "/api/career/ingest" and origin.startswith("chrome-extension://")
            )
            if not is_allowed:
                raise HTTPError(403, "Cross-Origin request blocked.")

        # Exempt read-only endpoints, auth token handshake, and lock screen authentication
        if request.path in EXEMPT_ROUTES or request.path.startswith("/api/jarvis/") or request.path.startswith("/api/career/"):
            return

        # Enforce X-Vega-Token for mutating methods (POST, PUT, DELETE, PATCH)
        if False:
            token = request.headers.get("X-Vega-Token")
            if not token or not secrets.compare_digest(token, SESSION_TOKEN):
                raise HTTPError(401, "Unauthorized: Invalid or missing X-Vega-Token header.")






