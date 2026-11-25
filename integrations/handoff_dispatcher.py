"""Approved loopback handoff transport and idempotent run state."""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Mapping, MutableMapping
from typing import Any, Callable
from urllib.parse import urlsplit

from integrations.vega_ide_spec import IDE_CONTEXT_FIELDS, redact_sensitive_text


_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_STATUS_VALUES = frozenset({"drafting", "awaiting_approval", "running", "completed", "failed"})
_LOCK = threading.RLock()
_RUNS: dict[str, dict[str, Any]] = {}
_RUN_ENDPOINTS: dict[str, str] = {}
_CANCELLED_RUNS: set[str] = set()
_WORKERS: dict[str, threading.Thread] = {}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _safe_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return redact_sensitive_text(value)


def _safe_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, Mapping):
        return {redact_sensitive_text(str(key)): _safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_sensitive_text(str(value))


def _receiver_url(endpoint: str | None, route: str) -> str | None:
    value = endpoint or "http://127.0.0.1:47640"
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
    if parsed.path and parsed.path not in ("/", "/api/vega/context"):
        return None
    hostname = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
    netloc = hostname + (f":{port}" if port is not None else "")
    return f"http://{netloc}{route}"


def _notify(on_status: Callable[[dict[str, Any]], Any] | None, state: dict[str, Any]) -> None:
    if on_status is None:
        return
    try:
        on_status(dict(state))
    except Exception:
        return


def apply_status_event(state_store: MutableMapping[str, Any], event: Any) -> bool:
    """Store the newest event for a run; duplicate and stale sequence numbers are ignored."""
    if not isinstance(state_store, MutableMapping) or not isinstance(event, Mapping):
        return False
    spec_id = event.get("spec_id")
    run_id = event.get("run_id")
    status = event.get("status")
    seq = event.get("seq")
    if not isinstance(spec_id, str) or not spec_id.strip() or not isinstance(run_id, str) or not run_id.strip():
        return False
    if not isinstance(status, str) or status not in _STATUS_VALUES:
        return False
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        return False

    safe_event: dict[str, Any] = {
        "spec_id": redact_sensitive_text(spec_id),
        "run_id": redact_sensitive_text(run_id),
        "seq": seq,
        "status": status,
    }
    for field in ("reason", "summary_text", "check_summary"):
        if field in event:
            safe_event[field] = _safe_text(event.get(field))
    changed_files = event.get("changed_files")
    if isinstance(changed_files, list):
        safe_event["changed_files"] = [_safe_text(item) for item in changed_files if isinstance(item, str)][:100]
    attempted_files = event.get("attempted_files")
    if isinstance(attempted_files, list):
        safe_event["attempted_files"] = [_safe_text(item) for item in attempted_files if isinstance(item, str)][:100]
    checks = event.get("checks")
    if isinstance(checks, list):
        safe_checks = []
        for check in checks[:100]:
            if not isinstance(check, Mapping):
                continue
            item = {}
            for field in ("file", "check", "status", "diagnostic"):
                if isinstance(check.get(field), str):
                    item[field] = _safe_text(check[field])[:2000]
            if isinstance(check.get("exit_code"), int) and not isinstance(check.get("exit_code"), bool):
                item["exit_code"] = check["exit_code"]
            if isinstance(check.get("files"), list):
                item["files"] = [_safe_text(path) for path in check["files"] if isinstance(path, str)][:100]
            safe_checks.append(item)
        safe_event["checks"] = safe_checks
    if isinstance(event.get("rolled_back"), bool):
        safe_event["rolled_back"] = event["rolled_back"]
    if "finished_at" in event and isinstance(event.get("finished_at"), (str, int, float)):
        safe_event["finished_at"] = _safe_value(event["finished_at"])

    with _LOCK:
        current = state_store.get(safe_event["spec_id"])
        if isinstance(current, Mapping) and current.get("run_id") == safe_event["run_id"]:
            if current.get("status") in ("completed", "failed"):
                return False
            current_seq = current.get("seq", 0)
            if isinstance(current_seq, int) and seq <= current_seq:
                return False
            merged = dict(current)
            merged.update(safe_event)
            safe_event = merged
        state_store[safe_event["spec_id"]] = safe_event
        return True


def get_spec_status(spec_id: str) -> dict[str, Any] | None:
    with _LOCK:
        state = _RUNS.get(_safe_text(spec_id))
        return dict(state) if isinstance(state, dict) else None


def accept_status_event(event: Any, on_status: Callable[[dict[str, Any]], Any] | None = None) -> bool:
    """Accept a receiver event only for the currently tracked spec/run pair."""
    if not isinstance(event, Mapping):
        return False
    spec_id = _safe_text(event.get("spec_id"))
    run_id = _safe_text(event.get("run_id"))
    current = get_spec_status(spec_id)
    if not current or current.get("run_id") != run_id:
        return False
    if not apply_status_event(_RUNS, event):
        return False
    state = get_spec_status(spec_id)
    if state:
        _notify(on_status, state)
    return state is not None


def _accept_receiver_record(
    store: MutableMapping[str, Any],
    record: Any,
    expected_spec_id: str,
    expected_run_id: str,
    on_status: Callable[[dict[str, Any]], Any] | None,
) -> int:
    if not isinstance(record, Mapping):
        return 0
    records = record.get("events")
    if isinstance(records, list):
        return sum(
            _accept_receiver_record(store, item, expected_spec_id, expected_run_id, on_status)
            for item in records
        )
    event = dict(record)
    event.setdefault("spec_id", expected_spec_id)
    event.setdefault("run_id", expected_run_id)
    if event.get("spec_id") != expected_spec_id or event.get("run_id") != expected_run_id:
        return 0
    accepted = apply_status_event(store, event)
    if accepted:
        state = store.get(expected_spec_id)
        if isinstance(state, dict):
            _notify(on_status, state)
        return 1
    return 0


def _next_failure(spec_id: str, run_id: str, reason: str, summary: str) -> dict[str, Any]:
    current = get_spec_status(spec_id) or {}
    seq = current.get("seq", 0)
    seq = seq + 1 if isinstance(seq, int) and not isinstance(seq, bool) else 1
    event = {
        "spec_id": spec_id,
        "run_id": run_id,
        "seq": seq,
        "status": "failed",
        "reason": reason,
        "summary_text": summary,
        "changed_files": [],
        "checks": [],
        "rolled_back": False,
    }
    apply_status_event(_RUNS, event)
    return get_spec_status(spec_id) or event


def dispatch_approved_spec(
    payload: dict,
    endpoint: str | None = None,
    timeout_sec: float = 120.0,
    *,
    run_id: str | None = None,
    on_status: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Send the approved contract to the loopback IDE receiver without rewriting patch text."""
    source = payload if isinstance(payload, Mapping) else {}
    tier = source.get("approval_tier")
    read_only_dry_run = tier == "read_only" and source.get("mode") == "plan_only"
    if source.get("approved_by_user") is not True and not read_only_dry_run:
        return {
            "ok": False,
            "spec_id": _safe_text(source.get("spec_id")),
            "status": "awaiting_approval",
            "reason": "explicit_approval_required",
        }

    spec_id = _safe_text(source.get("spec_id"))
    markdown = _safe_text(source.get("readme_spec_markdown"))
    if not spec_id or not markdown:
        return {"ok": False, "status": "failed", "reason": "invalid_spec_payload"}
    target_url = _receiver_url(endpoint or os.getenv("VEGA_IDE_ENDPOINT"), "/api/vega/handoff")
    if not target_url:
        return {"ok": False, "spec_id": spec_id, "status": "failed", "reason": "loopback_endpoint_required"}
    try:
        timeout_value = min(max(float(timeout_sec), 0.1), 300.0)
    except (TypeError, ValueError):
        timeout_value = 120.0

    current_run_id = _safe_text(run_id) or str(uuid.uuid4())
    with _LOCK:
        existing = _RUNS.get(spec_id)
        if not isinstance(existing, Mapping) or existing.get("run_id") != current_run_id:
            initial = {
                "spec_id": spec_id,
                "run_id": current_run_id,
                "seq": 1,
                "status": "running",
                "summary_text": "Approved handoff dispatched to Vega-IDE.",
                "changed_files": [],
            }
            _RUNS[spec_id] = initial
            _RUN_ENDPOINTS[current_run_id] = endpoint or "http://127.0.0.1:47640"
            _CANCELLED_RUNS.discard(current_run_id)
        else:
            initial = dict(existing)
    _notify(on_status, initial)

    version = source.get("spec_version", 1)
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        version = 1
    sequence = initial.get("seq", 1)
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 1:
        sequence = 1
    raw_context = source.get("ide_context") if isinstance(source.get("ide_context"), Mapping) else {}
    context = {field: _safe_value(raw_context.get(field, "" if field != "recent_diagnostics" else []))
               for field in IDE_CONTEXT_FIELDS}
    handoff = {
        "schema_version": "1.0",
        "spec_id": spec_id,
        "spec_version": version,
        "run_id": current_run_id,
        "seq": sequence,
        "title": _safe_text(source.get("title")),
        "status": "running",
        "approved_by_user": source.get("approved_by_user") is True,
        "approval_tier": tier if tier in ("read_only", "apply_edits") else "apply_edits",
        "mode": source.get("mode") if source.get("mode") in ("plan_only", "plan_and_apply") else "plan_only",
        "readme_spec_markdown": markdown,
        "ide_context": context,
        "glossary_terms": [_safe_text(term) for term in source.get("glossary_terms", [])
                           if isinstance(term, str)][:250]
        if isinstance(source.get("glossary_terms"), list) else [],
        "constraints": _safe_value(source.get("constraints", {}))
        if isinstance(source.get("constraints"), Mapping) else {},
        "file_operations": source.get("file_operations", [])
        if isinstance(source.get("file_operations", []), list) else [],
    }
    request = urllib.request.Request(
        target_url,
        data=json.dumps(handoff, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler())
    received = 0
    deadline = time.monotonic() + timeout_value
    try:
        with opener.open(request, timeout=timeout_value) as response:
            if response.status != 200:
                return _next_failure(spec_id, current_run_id, "receiver_http_error", f"Receiver returned HTTP {response.status}.")
            while True:
                if time.monotonic() >= deadline:
                    raise TimeoutError("handoff timed out")
                line = response.readline()
                if not line:
                    break
                try:
                    record = json.loads(line.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(record, Mapping) and record.get("ok") is False and "status" not in record:
                    return _next_failure(
                        spec_id, current_run_id, _safe_text(record.get("reason")) or "receiver_rejected",
                        _safe_text(record.get("error")) or "Vega-IDE rejected the handoff.",
                    )
                received += _accept_receiver_record(_RUNS, record, spec_id, current_run_id, on_status)
    except urllib.error.HTTPError as exc:
        return _next_failure(spec_id, current_run_id, "receiver_http_error", f"Receiver returned HTTP {exc.code}.")
    except (TimeoutError, OSError, urllib.error.URLError) as exc:
        reason = "timeout" if isinstance(exc, (TimeoutError, socket.timeout)) or "timed out" in str(exc).lower() else "receiver_unavailable"
        summary = "Vega-IDE handoff timed out." if reason == "timeout" else "Vega-IDE receiver is unavailable."
        state = _next_failure(spec_id, current_run_id, reason, summary)
        return {"ok": False, **state, "events_received": received}
    except Exception:
        state = _next_failure(spec_id, current_run_id, "transport_error", "Vega-IDE handoff failed safely.")
        return {"ok": False, **state, "events_received": received}
    finally:
        with _LOCK:
            if current_run_id not in _WORKERS:
                _RUN_ENDPOINTS.pop(current_run_id, None)
                _CANCELLED_RUNS.discard(current_run_id)

    state = get_spec_status(spec_id) or {}
    if state.get("run_id") != current_run_id:
        return {"ok": False, "spec_id": spec_id, "run_id": current_run_id, "status": "failed", "reason": "run_state_missing"}
    if state.get("status") not in ("completed", "failed"):
        if current_run_id in _CANCELLED_RUNS:
            state = _next_failure(
                spec_id, current_run_id, "cancelled_by_user",
                "Vega-IDE closed before confirming cancellation rollback."
            )
        else:
            state = _next_failure(spec_id, current_run_id, "receiver_closed_early", "Vega-IDE closed the handoff before completing.")
    return {"ok": state.get("status") == "completed", **state, "events_received": received}


def start_dispatch(
    payload: dict,
    endpoint: str | None = None,
    timeout_sec: float = 120.0,
    on_status: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Start a handoff on a background thread and return its initial run identity."""
    source = payload if isinstance(payload, Mapping) else {}
    tier = source.get("approval_tier")
    dry_run = tier == "read_only" and source.get("mode") == "plan_only"
    if source.get("approved_by_user") is not True and not dry_run:
        return {"ok": False, "status": "awaiting_approval", "reason": "explicit_approval_required"}
    spec_id = _safe_text(source.get("spec_id"))
    if not spec_id:
        return {"ok": False, "status": "failed", "reason": "invalid_spec_payload"}
    run_id = str(uuid.uuid4())
    initial = {
        "spec_id": spec_id,
        "run_id": run_id,
        "seq": 1,
        "status": "running",
        "summary_text": "Approved handoff dispatched to Vega-IDE.",
        "changed_files": [],
    }
    with _LOCK:
        _RUNS[spec_id] = initial
        _RUN_ENDPOINTS[run_id] = endpoint or os.getenv("VEGA_IDE_ENDPOINT") or "http://127.0.0.1:47640"
        _CANCELLED_RUNS.discard(run_id)
    _notify(on_status, initial)

    def worker() -> None:
        try:
            dispatch_approved_spec(
                source,
                endpoint=endpoint,
                timeout_sec=timeout_sec,
                run_id=run_id,
                on_status=on_status,
            )
        finally:
            with _LOCK:
                _WORKERS.pop(run_id, None)
                _RUN_ENDPOINTS.pop(run_id, None)
                _CANCELLED_RUNS.discard(run_id)

    thread = threading.Thread(target=worker, name=f"VegaHandoff-{run_id[:8]}", daemon=True)
    with _LOCK:
        _WORKERS[run_id] = thread
    thread.start()
    return {"ok": True, **initial}


def cancel_run(spec_id: str, run_id: str, endpoint: str | None = None) -> dict[str, Any]:
    """Request receiver cancellation and wait for its rollback result asynchronously."""
    safe_spec_id = _safe_text(spec_id)
    safe_run_id = _safe_text(run_id)
    with _LOCK:
        current = _RUNS.get(safe_spec_id)
        if not isinstance(current, dict) or current.get("run_id") != safe_run_id:
            return {"ok": False, "reason": "run_not_found"}
        if current.get("status") in ("completed", "failed"):
            return {"ok": False, "reason": "run_terminal", **current}
        _CANCELLED_RUNS.add(safe_run_id)
        cancel_endpoint = endpoint or _RUN_ENDPOINTS.get(safe_run_id)
        event = {
            "spec_id": safe_spec_id,
            "run_id": safe_run_id,
            "seq": int(current.get("seq", 0)) + 1,
            "status": "running",
            "reason": "cancellation_requested",
            "summary_text": "Cancellation requested; waiting for Vega-IDE rollback confirmation.",
            "changed_files": current.get("changed_files", []),
            "checks": current.get("checks", []),
            "rolled_back": False,
        }
        apply_status_event(_RUNS, event)
        state = dict(_RUNS.get(safe_spec_id, event))

    cancel_url = _receiver_url(cancel_endpoint, "/api/vega/cancel")
    if cancel_url:
        try:
            body = json.dumps({"spec_id": safe_spec_id, "run_id": safe_run_id}).encode("utf-8")
            request = urllib.request.Request(
                cancel_url,
                data=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirectHandler())
            with opener.open(request, timeout=2.0) as response:
                response.read(8192)
        except Exception:
            pass
    return {"ok": True, **state, "cancelling": True}


def build_debrief(result: Mapping[str, Any], is_silent: bool, voice_busy: bool) -> dict[str, Any]:
    """Create a short safe completion summary and a voice-eligibility decision."""
    source = result if isinstance(result, Mapping) else {}
    summary = source.get("summary_text") or source.get("check_summary") or source.get("reason")
    if not isinstance(summary, str) or not summary.strip():
        status = source.get("status") if isinstance(source.get("status"), str) else "unknown"
        summary = f"Vega-IDE handoff {status}."
    summary = redact_sensitive_text(summary).strip()
    return {
        "summary_text": summary[:500],
        "should_speak": not bool(is_silent) and not bool(voice_busy),
    }

