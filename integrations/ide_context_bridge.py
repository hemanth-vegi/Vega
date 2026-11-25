"""Read-only, loopback-only context bridge to Vega-IDE."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

from integrations.vega_ide_spec import redact_sensitive_text, transition_approval


DEFAULT_IDE_ENDPOINT = "http://127.0.0.1:47640"
MAX_DIAGNOSTICS_BYTES = 4096
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _empty_context() -> dict[str, Any]:
    return {
        "workspace_root": "",
        "active_file": "",
        "selected_text": "",
        "recent_diagnostics": [],
        "ide_online": False,
    }


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, Mapping):
        return {redact_sensitive_text(str(key)): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return redact_sensitive_text(str(value))


def _cap_diagnostics(value: Any) -> Any:
    cleaned = _safe_value(value)
    if isinstance(cleaned, str):
        encoded = cleaned.encode("utf-8")
        if len(encoded) <= MAX_DIAGNOSTICS_BYTES:
            return cleaned
        return encoded[:MAX_DIAGNOSTICS_BYTES].decode("utf-8", errors="ignore")
    if isinstance(cleaned, list):
        result = []
        for entry in cleaned:
            candidate = result + [entry]
            encoded = json.dumps(candidate, ensure_ascii=False).encode("utf-8")
            if len(encoded) > MAX_DIAGNOSTICS_BYTES:
                break
            result.append(entry)
        return result
    if isinstance(cleaned, dict):
        result = {}
        for key, item in cleaned.items():
            candidate = dict(result)
            candidate[key] = item
            if len(json.dumps(candidate, ensure_ascii=False).encode("utf-8")) > MAX_DIAGNOSTICS_BYTES:
                break
            result[key] = item
        return result
    return []


def _context_url(endpoint: str | None) -> str | None:
    value = endpoint or os.getenv("VEGA_IDE_ENDPOINT", DEFAULT_IDE_ENDPOINT)
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "http"
        or parsed.hostname is None
        or parsed.hostname.lower() not in _LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        return None
    path = parsed.path.rstrip("/")
    if path and path != "/api/vega/context":
        return None
    netloc = parsed.hostname
    if port is not None:
        netloc += f":{port}"
    return f"http://{netloc}/api/vega/context"


def fetch_ide_context(endpoint: str | None = None, timeout: float = 1.5) -> dict[str, Any]:
    """Fetch and redact context from Vega-IDE; fail closed when it is unavailable."""
    url = _context_url(endpoint)
    if not url:
        return _empty_context()
    try:
        timeout_value = float(timeout)
        if timeout_value <= 0:
            timeout_value = 1.5
        timeout_value = min(timeout_value, 5.0)
        request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler())
        with opener.open(request, timeout=timeout_value) as result:
            if result.status != 200:
                return _empty_context()
            raw = result.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            return _empty_context()
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            return _empty_context()
        context = _empty_context()
        for field in ("workspace_root", "active_file", "selected_text"):
            value = payload.get(field, "")
            context[field] = redact_sensitive_text(value) if isinstance(value, str) else ""
        context["recent_diagnostics"] = _cap_diagnostics(payload.get("recent_diagnostics", []))
        context["ide_online"] = True
        return context
    except (OSError, TimeoutError, ValueError, TypeError, json.JSONDecodeError, urllib.error.URLError):
        return _empty_context()
    except Exception:
        return _empty_context()


def gate_spec_approval(payload: Any, approved: bool, approval_tier: str) -> dict[str, Any]:
    """Hold edits and destructive operations until a strict explicit approval."""
    source = payload if isinstance(payload, dict) else {}
    requested_tier = approval_tier if approval_tier in {"read_only", "apply_edits"} else "apply_edits"
    constraints = source.get("constraints") if isinstance(source.get("constraints"), dict) else {}
    destructive = any(source.get(key) is True for key in ("destructive", "destructive_operation"))
    destructive = destructive or constraints.get("destructive_operation") is True
    effective_tier = "apply_edits" if destructive else requested_tier

    if effective_tier == "apply_edits" and approved is not True:
        result = transition_approval(source, approved=False, tier=effective_tier)
        result["approved_by_user"] = False
        result["status"] = "awaiting_approval"
        result["mode"] = "plan_only"
        return result

    result = transition_approval(source, approved=approved is True, tier=effective_tier)
    if effective_tier == "read_only" and approved is not True and not destructive:
        result["approved_by_user"] = False
        result["status"] = "running"
        result["mode"] = "plan_only"
    return result

