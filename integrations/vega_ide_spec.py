"""Pure data contracts for Vega to Vega-IDE feature specifications."""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Mapping, Sequence
from typing import Any


SCHEMA_VERSION = "1.0"
SPEC_STATUSES = frozenset({"drafting", "awaiting_approval", "running", "completed", "failed"})
APPROVAL_TIERS = frozenset({"read_only", "apply_edits"})
SPEC_MODES = frozenset({"plan_only", "plan_and_apply"})
IDE_CONTEXT_FIELDS = ("workspace_root", "active_file", "selected_text", "recent_diagnostics")
README_SPEC_SECTIONS = (
    "Goal",
    "In Scope (This Pass)",
    "Out of Scope / Non-Goals",
    "Target Files & Interfaces",
    "Edge Cases & Guardrails",
    "Done Criteria (Verification)",
)

_TOKEN_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\bgsk_[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-or-v1-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
)
_ENV_LINE = re.compile(r"(?im)^\s*[A-Za-z_][A-Za-z0-9_.-]*\s*=\s*[^\r\n]*$")
_CREDENTIAL_ASSIGNMENT = re.compile(
    r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|secret|client[_ -]?secret)"
    r"(\s*[:=]\s*)[^\s,;]+"
)
_ENV_PATH = re.compile(r"(?i)(?:[A-Z]:[\\/][^\s\"'<>]*?\\\.env(?:\.[^\\/\s\"'<>]+)?|(?:^|[\\/])\.env(?:\.[^\\/\s\"'<>]+)?)")
_WINDOWS_USER_PATH = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\\\s\"'<>]+(?:\\[^\s\"'<>]*)?")
_POSIX_HOME_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:home|Users)/[^/\s\"'<>]+(?:/[^\s\"'<>]*)?")


def is_ide_bridge_enabled() -> bool:
    """Return whether the optional IDE bridge is explicitly enabled."""
    return os.getenv("VEGA_IDE_BRIDGE_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}


def redact_sensitive_text(text: str) -> str:
    """Remove credentials, environment assignments, and personal home paths."""
    if not isinstance(text, str):
        text = "" if text is None else str(text)

    redacted = _ENV_PATH.sub("[REDACTED_ENV_PATH]", text)
    redacted = _WINDOWS_USER_PATH.sub("[REDACTED_USER_PATH]", redacted)
    redacted = _POSIX_HOME_PATH.sub("[REDACTED_USER_PATH]", redacted)
    redacted = _ENV_LINE.sub("[REDACTED_ENV_LINE]", redacted)
    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub("[REDACTED_TOKEN]", redacted)
    return _CREDENTIAL_ASSIGNMENT.sub(r"\1\2[REDACTED]", redacted)


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_sensitive_text(value)
    if isinstance(value, Mapping):
        return {redact_sensitive_text(str(key)): _redact_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_redact_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return redact_sensitive_text(str(value))


def _as_text(value: Any, default: str = "") -> str:
    if value is None:
        value = default
    if isinstance(value, (Mapping, list, tuple, set)):
        return default
    return redact_sensitive_text(str(value))


def _as_items(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        source: Sequence[Any] = value.splitlines()
    elif isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        source = value
    else:
        source = (value,)
    items = []
    for item in source:
        cleaned = _as_text(item).strip()
        if cleaned:
            items.append(cleaned)
    return items


def _safe_context(value: Any) -> dict[str, Any]:
    context = value if isinstance(value, Mapping) else {}
    result = {field: _redact_value(context.get(field, "" if field != "recent_diagnostics" else []))
              for field in IDE_CONTEXT_FIELDS}
    for field in IDE_CONTEXT_FIELDS[:-1]:
        if not isinstance(result[field], str):
            result[field] = _as_text(result[field])
    if not isinstance(result["recent_diagnostics"], (str, list, dict)):
        result["recent_diagnostics"] = _as_text(result["recent_diagnostics"])
    if "ide_online" in context and isinstance(context["ide_online"], bool):
        result["ide_online"] = context["ide_online"]
    return result


def _copy_file_operations(value: Any) -> Any:
    """Keep reviewed patch text exact; the IDE executor applies path and size guards."""
    if value is None:
        return []
    if not isinstance(value, list):
        return value
    return [dict(item) if isinstance(item, Mapping) else item for item in value]


def _validate_file_operations(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return ["file_operations must be a list"]
    if len(value) > 100:
        return ["file_operations must contain no more than 100 operations"]
    errors: list[str] = []

    def check_text(text: Any, label: str) -> None:
        if not isinstance(text, str):
            errors.append(f"{label} must be a string")
        elif len(text.encode("utf-8")) > 1024 * 1024:
            errors.append(f"{label} exceeds the 1 MiB operation limit")

    for index, operation in enumerate(value):
        prefix = f"file_operations[{index}]"
        if not isinstance(operation, Mapping):
            errors.append(f"{prefix} must be an object")
            continue
        op = operation.get("op")
        if op not in {"replace", "insert", "create", "patch"}:
            errors.append(f"{prefix}.op is invalid")
        check_text(operation.get("path"), f"{prefix}.path")
        if op == "create":
            check_text(operation.get("content"), f"{prefix}.content")
        elif op in {"replace", "insert"}:
            check_text(operation.get("search"), f"{prefix}.search")
            check_text(operation.get("replace"), f"{prefix}.replace")
        elif op == "patch":
            changes = operation.get("changes")
            if not isinstance(changes, list) or not changes or len(changes) > 100:
                errors.append(f"{prefix}.changes must be a non-empty list with at most 100 items")
            else:
                for change_index, change in enumerate(changes):
                    change_prefix = f"{prefix}.changes[{change_index}]"
                    if not isinstance(change, Mapping):
                        errors.append(f"{change_prefix} must be an object")
                        continue
                    check_text(change.get("search"), f"{change_prefix}.search")
                    check_text(change.get("replace"), f"{change_prefix}.replace")
    return errors


def _safe_enum(value: Any, choices: frozenset[str], default: str) -> str:
    candidate = value.strip() if isinstance(value, str) else ""
    return candidate if candidate in choices else default


def render_readme_spec(
    title: str,
    goal: str = "",
    in_scope: Any = None,
    out_of_scope: Any = None,
    target_files: Any = None,
    edge_cases: Any = None,
    done_criteria: Any = None,
) -> str:
    """Render a redacted README-style feature spec with stable section names."""
    safe_title = _as_text(title, "Untitled Feature").strip() or "Untitled Feature"
    sections = (
        (README_SPEC_SECTIONS[0], [_as_text(goal).strip()] if _as_text(goal).strip() else []),
        (README_SPEC_SECTIONS[1], _as_items(in_scope)),
        (README_SPEC_SECTIONS[2], _as_items(out_of_scope)),
        (README_SPEC_SECTIONS[3], _as_items(target_files)),
        (README_SPEC_SECTIONS[4], _as_items(edge_cases)),
        (README_SPEC_SECTIONS[5], _as_items(done_criteria)),
    )
    lines = [f"# Feature Spec: {safe_title}"]
    for heading, items in sections:
        lines.extend(("", f"## {heading}"))
        if items:
            lines.extend(f"- {item}" for item in items)
        else:
            lines.append("- Not specified.")
    return redact_sensitive_text("\n".join(lines))


def build_handoff_payload(
    title: str,
    readme_spec_markdown: str,
    transcript_cleanup_note: str = "",
    ide_context: Any = None,
    glossary_terms: Any = None,
    constraints: Any = None,
    approval_tier: str = "apply_edits",
    mode: str = "plan_only",
    status: str = "awaiting_approval",
    spec_id: str | None = None,
    spec_version: int = 1,
    parent_spec_id: str | None = None,
    run_id: str | None = None,
    seq: int = 0,
    file_operations: Any = None,
) -> dict[str, Any]:
    """Build a redacted, approval-pending payload without performing I/O."""
    supplied_constraints = constraints if isinstance(constraints, Mapping) else {}
    safe_constraints = _redact_value(supplied_constraints)
    safe_constraints.update({
        "stay_within_scope": True,
        "require_approval_for_destructive_ops": True,
    })
    safe_version = spec_version if isinstance(spec_version, int) and not isinstance(spec_version, bool) and spec_version > 0 else 1
    safe_seq = seq if isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0 else 0
    safe_spec_id = _as_text(spec_id).strip() or str(uuid.uuid4())
    safe_parent_id = _as_text(parent_spec_id).strip() or None
    safe_run_id = _as_text(run_id).strip() or None

    return {
        "schema_version": SCHEMA_VERSION,
        "spec_id": safe_spec_id,
        "spec_version": safe_version,
        "parent_spec_id": safe_parent_id,
        "run_id": safe_run_id,
        "seq": safe_seq,
        "title": _as_text(title, "Untitled Feature").strip() or "Untitled Feature",
        "status": _safe_enum(status, SPEC_STATUSES, "awaiting_approval"),
        "approved_by_user": False,
        "approval_tier": _safe_enum(approval_tier, APPROVAL_TIERS, "apply_edits"),
        "mode": _safe_enum(mode, SPEC_MODES, "plan_only"),
        "readme_spec_markdown": _as_text(readme_spec_markdown),
        "transcript_cleanup_note": _as_text(transcript_cleanup_note),
        "ide_context": _safe_context(ide_context),
        "glossary_terms": _as_items(glossary_terms),
        "constraints": safe_constraints,
        "file_operations": _copy_file_operations(file_operations),
    }


def validate_handoff_payload(payload: Any) -> dict[str, Any]:
    """Return structured validation errors; malformed input never raises KeyError."""
    if not isinstance(payload, Mapping):
        return {"ok": False, "errors": ["payload must be an object"]}

    required = (
        "schema_version", "spec_id", "spec_version", "parent_spec_id", "run_id", "seq", "title",
        "status", "approved_by_user", "approval_tier", "mode", "readme_spec_markdown",
        "transcript_cleanup_note", "ide_context", "glossary_terms", "constraints",
    )
    errors = [f"missing field: {field}" for field in required if field not in payload]

    if payload.get("schema_version") != SCHEMA_VERSION:
        errors.append("schema_version must be '1.0'")
    if not isinstance(payload.get("spec_id"), str) or not payload.get("spec_id", "").strip():
        errors.append("spec_id must be a non-empty string")
    version = payload.get("spec_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        errors.append("spec_version must be a positive integer")
    seq = payload.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        errors.append("seq must be a non-negative integer")
    for field in ("parent_spec_id", "run_id"):
        value = payload.get(field)
        if value is not None and not isinstance(value, str):
            errors.append(f"{field} must be a string or null")
    if not isinstance(payload.get("title"), str) or not payload.get("title", "").strip():
        errors.append("title must be a non-empty string")
    if not isinstance(payload.get("status"), str) or payload.get("status") not in SPEC_STATUSES:
        errors.append("status is invalid")
    if not isinstance(payload.get("approved_by_user"), bool):
        errors.append("approved_by_user must be a boolean")
    if not isinstance(payload.get("approval_tier"), str) or payload.get("approval_tier") not in APPROVAL_TIERS:
        errors.append("approval_tier is invalid")
    if not isinstance(payload.get("mode"), str) or payload.get("mode") not in SPEC_MODES:
        errors.append("mode is invalid")
    for field in ("readme_spec_markdown", "transcript_cleanup_note"):
        if not isinstance(payload.get(field), str):
            errors.append(f"{field} must be a string")
    context = payload.get("ide_context")
    if not isinstance(context, Mapping):
        errors.append("ide_context must be an object")
    else:
        for field in IDE_CONTEXT_FIELDS:
            if field not in context:
                errors.append(f"ide_context missing field: {field}")
            elif field == "recent_diagnostics":
                if not isinstance(context[field], (str, list, dict)):
                    errors.append("ide_context.recent_diagnostics must be text, a list, or an object")
            elif not isinstance(context[field], str):
                errors.append(f"ide_context.{field} must be a string")
    if not isinstance(payload.get("glossary_terms"), list) or any(
        not isinstance(term, str) for term in payload.get("glossary_terms", [])
    ):
        errors.append("glossary_terms must be a list of strings")
    constraints = payload.get("constraints")
    if not isinstance(constraints, Mapping):
        errors.append("constraints must be an object")
    else:
        if constraints.get("stay_within_scope") is not True:
            errors.append("constraints.stay_within_scope must be true")
        if constraints.get("require_approval_for_destructive_ops") is not True:
            errors.append("constraints.require_approval_for_destructive_ops must be true")
    errors.extend(_validate_file_operations(payload.get("file_operations", [])))
    return {"ok": not errors, "errors": errors}


def bump_spec_version(payload: Any, updated_markdown: str, note: str) -> dict[str, Any]:
    """Create a fresh unapproved revision linked to the prior spec identifier."""
    source = payload if isinstance(payload, Mapping) else {}
    old_id = _as_text(source.get("spec_id")).strip() or None
    old_version = source.get("spec_version", 1)
    if not isinstance(old_version, int) or isinstance(old_version, bool) or old_version < 1:
        old_version = 1
    old_note = _as_text(source.get("transcript_cleanup_note")).strip()
    revision_note = _as_text(note).strip()
    combined_note = "\n".join(part for part in (old_note, revision_note) if part)
    return build_handoff_payload(
        title=source.get("title", "Untitled Feature"),
        readme_spec_markdown=updated_markdown,
        transcript_cleanup_note=combined_note,
        ide_context=source.get("ide_context"),
        glossary_terms=source.get("glossary_terms"),
        constraints=source.get("constraints"),
        approval_tier=source.get("approval_tier", "apply_edits"),
        mode="plan_only",
        status="awaiting_approval",
        spec_version=old_version + 1,
        parent_spec_id=old_id,
        file_operations=source.get("file_operations", []),
    )


def transition_approval(payload: Any, approved: bool, tier: str = "apply_edits") -> dict[str, Any]:
    """Apply an explicit approval decision while preserving safe defaults."""
    source = payload if isinstance(payload, Mapping) else {}
    safe_tier = _safe_enum(tier, APPROVAL_TIERS, "apply_edits")
    result = build_handoff_payload(
        title=source.get("title", "Untitled Feature"),
        readme_spec_markdown=source.get("readme_spec_markdown", ""),
        transcript_cleanup_note=source.get("transcript_cleanup_note", ""),
        ide_context=source.get("ide_context"),
        glossary_terms=source.get("glossary_terms"),
        constraints=source.get("constraints"),
        approval_tier=safe_tier,
        mode="plan_and_apply" if approved is True and safe_tier == "apply_edits" else "plan_only",
        status="awaiting_approval",
        spec_id=source.get("spec_id"),
        spec_version=source.get("spec_version", 1),
        parent_spec_id=source.get("parent_spec_id"),
        run_id=source.get("run_id"),
        seq=source.get("seq", 0),
        file_operations=source.get("file_operations", []),
    )
    result["approved_by_user"] = approved is True
    return result

