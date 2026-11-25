"""Compile spoken requests into approval-pending README feature specifications."""

from __future__ import annotations

import re
from typing import Any

from integrations.repo_glossary import build_repo_glossary, normalize_spoken_transcript
from integrations.vega_ide_spec import (
    README_SPEC_SECTIONS,
    build_handoff_payload,
    bump_spec_version,
    redact_sensitive_text,
    render_readme_spec,
    validate_handoff_payload,
)


def _derive_title(text: str) -> str:
    candidate = re.split(r"[.!?\n]", text, maxsplit=1)[0].strip()
    candidate = re.sub(r"(?i)^(please\s+)?(?:add|build|create|fix|make|implement|update)\s+", "", candidate)
    words = candidate.split()[:8]
    return " ".join(words).strip(" ,;:") or "Feature Request"


def _scope_parts(text: str) -> tuple[list[str], list[str]]:
    cleaned = text.strip()
    out_scope: list[str] = []
    marker = re.search(r"(?i)\b(?:out of scope|non[- ]?goals?)\s*:?\s*(.+)$", cleaned)
    if marker:
        out_scope = [marker.group(1).strip(" .;,")]
        cleaned = cleaned[:marker.start()].strip(" .;,")
    in_match = re.search(r"(?i)\bin scope\s*:?\s*(.+?)(?=\b(?:out of scope|non[- ]?goals?)\b|$)", cleaned)
    if in_match:
        cleaned = in_match.group(1).strip(" .;,")
    return ([cleaned] if cleaned else []), out_scope


def _split_spec(markdown: str) -> tuple[str, dict[str, list[str]]]:
    title = "Feature Request"
    sections: dict[str, list[str]] = {heading: [] for heading in README_SPEC_SECTIONS}
    current: str | None = None
    for line in (markdown or "").splitlines():
        if line.startswith("# Feature Spec:"):
            title = line.partition(":")[2].strip() or title
        elif line.startswith("## "):
            current = line[3:].strip()
        elif current in sections and line.strip().startswith("-"):
            item = line.strip()[1:].strip()
            if item and item != "Not specified.":
                sections[current].append(item)
        elif current == "Goal" and line.strip():
            sections[current].append(line.strip())
    return title, sections


def compile_spoken_spec(
    transcript: str,
    workspace_root: str = "",
    title: str = "",
    ide_context: dict | None = None,
    approval_tier: str = "apply_edits",
) -> dict[str, Any]:
    """Create a validated draft payload; this function never writes or runs commands."""
    source_text = transcript if isinstance(transcript, str) else str(transcript or "")
    glossary = build_repo_glossary(workspace_root) if workspace_root else []
    normalized = normalize_spoken_transcript(source_text, glossary)
    cleaned_text = normalized["cleaned_text"]
    in_scope, out_scope = _scope_parts(cleaned_text)
    safe_title = redact_sensitive_text(title.strip()) if isinstance(title, str) and title.strip() else _derive_title(cleaned_text)
    markdown = render_readme_spec(
        safe_title,
        goal=cleaned_text,
        in_scope=in_scope,
        out_of_scope=out_scope or ["Anything not explicitly listed in In Scope (This Pass)."],
        target_files=["Determine exact files and interfaces after reviewing the approved scope."],
        edge_cases=["Keep changes within scope and redact secrets from any IDE context."],
        done_criteria=["Run the focused verification checks listed in the approved spec."],
    )
    payload = build_handoff_payload(
        title=safe_title,
        readme_spec_markdown=markdown,
        transcript_cleanup_note=normalized["transcript_cleanup_note"],
        ide_context=ide_context or {
            "workspace_root": workspace_root,
            "active_file": "",
            "selected_text": "",
            "recent_diagnostics": [],
        },
        glossary_terms=normalized["glossary_matches"],
        approval_tier=approval_tier,
        mode="plan_only",
        status="awaiting_approval",
    )
    validation = validate_handoff_payload(payload)
    if not validation["ok"]:
        payload["validation_errors"] = validation["errors"]
    return payload


def revise_spoken_spec(payload: dict, feedback: str) -> dict[str, Any]:
    """Apply a textual revision to one scope section and create a new draft version."""
    source = payload if isinstance(payload, dict) else {}
    original_markdown = source.get("readme_spec_markdown", "")
    if not isinstance(original_markdown, str):
        original_markdown = ""
    glossary = source.get("glossary_terms", [])
    normalized = normalize_spoken_transcript(feedback, glossary if isinstance(glossary, list) else [])
    revision = normalized["cleaned_text"].strip()
    spec_title, sections = _split_spec(original_markdown)
    if revision:
        target = (
            README_SPEC_SECTIONS[2]
            if re.search(r"(?i)\b(out of scope|non[- ]?goal)\b", revision)
            else README_SPEC_SECTIONS[1]
        )
        revision = re.sub(r"(?i)^.*?\b(?:out of scope|non[- ]?goal)\s*:?[ ]*", "", revision).strip()
        if revision:
            sections[target].append(revision)
    updated_markdown = render_readme_spec(
        spec_title,
        goal="\n".join(sections[README_SPEC_SECTIONS[0]]),
        in_scope=sections[README_SPEC_SECTIONS[1]],
        out_of_scope=sections[README_SPEC_SECTIONS[2]],
        target_files=sections[README_SPEC_SECTIONS[3]],
        edge_cases=sections[README_SPEC_SECTIONS[4]],
        done_criteria=sections[README_SPEC_SECTIONS[5]],
    )
    return bump_spec_version(source, updated_markdown, normalized["transcript_cleanup_note"])

