"""Safe source-only glossary extraction and spoken identifier normalization."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any


SOURCE_SUFFIXES = frozenset({".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css"})
SKIP_DIRECTORIES = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env", "career_vault",
    "vscode-win32-x64", "vega-data", "dist", "build", ".ruff_cache", "logs", "chats",
    "memory", "diary", "documents", "contacts", "reminders", "progress", "lab log",
    "browser_profiles", "browser-profile",
})
SKIP_FILE_NAMES = frozenset({"config.json", "security.json", "chat_history.json", "memory.json"})
MAX_SOURCE_FILES = 1200
ACTIVE_SOURCE_DIRECTORIES = frozenset({
    "browser_extension", "career", "core", "integrations", "llm", "scripts", "server",
    "storage", "tests", "tools", "voice", "web",
})
_ENV_ACCESS = re.compile(
    r"(?:os\.(?:getenv|environ\.get)\s*\(\s*|process\.env\.)['\"]?([A-Z][A-Z0-9_]{2,})",
)
_CLI_FLAG = re.compile(r"(?<![\w-])--[a-zA-Z][a-zA-Z0-9-]{1,80}")
_JS_SYMBOL = re.compile(r"(?m)^\s*(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)")
_JS_BINDING = re.compile(r"(?m)^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)")
_FILLERS = re.compile(r"(?i)\b(?:um+|uh+|erm+|hmm+|you know|I mean)\b[, ]*")
_INSTEAD_OF = re.compile(r"(?i)\s+instead of\s+[^,;.!?]+")
_CORRECTION = re.compile(r"(?i)\b(?:actually|correction|rather|wait)\b[,:]?\s*")
_ACTION_START = re.compile(r"(?i)^(use|choose|select|switch to|make it|set|call|name)\b")


def _add_term(terms: dict[str, str], value: Any) -> None:
    if not isinstance(value, str):
        return
    candidate = value.strip().strip("'\"`.,;:(){}[]")
    if len(candidate) < 2 or len(candidate) > 100 or "\n" in candidate:
        return
    if not re.search(r"[A-Za-z]", candidate) or re.fullmatch(r"[A-Z]{1,2}", candidate):
        return
    terms.setdefault(candidate.casefold(), candidate)


def _add_path_terms(terms: dict[str, str], relative_path: Path) -> None:
    for part in relative_path.parts:
        if part.startswith("."):
            continue
        _add_term(terms, part)
        _add_term(terms, Path(part).stem)


def _source_paths(root: Path):
    root_path = root.resolve()
    skipped = {item.casefold() for item in SKIP_DIRECTORIES}
    yielded = 0
    for current, directories, filenames in os.walk(root, topdown=True, followlinks=False):
        is_root = Path(current).resolve() == root_path
        directories[:] = sorted(
            name for name in directories
            if name.casefold() not in skipped
            and not name.startswith(".")
            and not name.casefold().startswith(("vega-update-", "vega_2_fix"))
            and "backup" not in name.casefold()
            and "archive" not in name.casefold()
            and "profile" not in name.casefold()
            and "cache" not in name.casefold()
            and (not is_root or name.casefold() in ACTIVE_SOURCE_DIRECTORIES)
        )
        current_path = Path(current)
        for filename in sorted(filenames):
            path = current_path / filename
            if (
                filename == ".env"
                or filename.lower().startswith(".env.")
                or filename.casefold() in SKIP_FILE_NAMES
            ):
                continue
            if path.suffix.lower() in SOURCE_SUFFIXES:
                yield path
                yielded += 1
                if yielded >= MAX_SOURCE_FILES:
                    return


def build_repo_glossary(workspace_root: str, max_terms: int = 250) -> list[str]:
    """Collect identifiers from source paths and text files without executing code."""
    try:
        root = Path(workspace_root).expanduser().resolve()
        limit = max(0, min(int(max_terms), 5000))
    except (OSError, TypeError, ValueError):
        return []
    if not root.is_dir() or limit == 0:
        return []

    terms: dict[str, str] = {}
    try:
        for file_path in _source_paths(root):
            relative_path = file_path.relative_to(root)
            _add_path_terms(terms, relative_path)
            try:
                if file_path.stat().st_size > 512_000:
                    continue
                source = file_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "\x00" in source:
                continue

            if file_path.suffix.lower() == ".py":
                try:
                    tree = ast.parse(source)
                except (SyntaxError, ValueError):
                    tree = None
                if tree is not None:
                    for node in ast.walk(tree):
                        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                            _add_term(terms, node.name)
                        elif isinstance(node, ast.arg):
                            _add_term(terms, node.arg)
            else:
                for match in _JS_SYMBOL.finditer(source):
                    _add_term(terms, match.group(1))
                for match in _JS_BINDING.finditer(source):
                    _add_term(terms, match.group(1))

            for match in _ENV_ACCESS.finditer(source):
                _add_term(terms, match.group(1))
            for match in _CLI_FLAG.finditer(source):
                _add_term(terms, match.group(0))
    except OSError:
        return []

    return sorted(terms.values(), key=lambda item: (item.casefold(), item))[:limit]


def _honor_self_correction(text: str) -> tuple[str, bool]:
    match = _CORRECTION.search(text)
    if not match:
        return text, False
    prefix = text[:match.start()].rstrip(" ,;—–-")
    replacement = _INSTEAD_OF.sub("", text[match.end():]).strip(" ,;—–-")
    if not replacement:
        return text, False

    if prefix and _ACTION_START.match(replacement):
        prior_parts = re.split(r"(?<=[,;.!?])\s+", prefix)
        prior_clause = prior_parts[-1].strip(" ,;.!?—–-")
        if _ACTION_START.match(prior_clause):
            prefix = " ".join(prior_parts[:-1]).strip(" ,;.!?—–-")
    cleaned = " ".join(part for part in (prefix, replacement) if part)
    return cleaned, cleaned != text


def _normalize_terms(text: str, glossary: list[str]) -> tuple[str, list[str]]:
    aliases: dict[str, str] = {}
    canonical_terms = {term.casefold(): term for term in glossary if isinstance(term, str)}
    fixed = {
        "pi compile": "py_compile",
        "py compile": "py_compile",
        "sql lite": "SQLite",
        "sqlite lite": "SQLite",
        "s s e": "SSE",
        "sse": "SSE",
    }
    for alias, canonical in fixed.items():
        aliases[alias.casefold()] = canonical_terms.get(canonical.casefold(), canonical)

    for term in glossary:
        if not isinstance(term, str) or not term.strip():
            continue
        canonical = term.strip()
        variants = {canonical}
        if "_" in canonical:
            variants.add(re.sub(r"_+", " ", canonical))
        if re.search(r"[a-z][A-Z]", canonical):
            variants.add(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", canonical))
        for alias in variants:
            aliases.setdefault(alias.casefold(), canonical)

    matches: list[str] = []
    normalized = text
    for alias, canonical in sorted(aliases.items(), key=lambda item: (-len(item[0]), item[0])):
        pattern = re.compile(rf"(?<!\w){re.escape(alias)}(?!\w)", re.IGNORECASE)

        def replace(match: re.Match[str]) -> str:
            if canonical not in matches:
                matches.append(canonical)
            return canonical

        normalized = pattern.sub(replace, normalized)
    return normalized, matches


def normalize_spoken_transcript(transcript: str, glossary: list[str]) -> dict[str, Any]:
    """Remove common fillers, honor corrections, and canonicalize technical terms."""
    raw_text = transcript if isinstance(transcript, str) else str(transcript or "")
    without_fillers, filler_count = _FILLERS.subn("", raw_text)
    corrected, correction_applied = _honor_self_correction(without_fillers)
    cleaned = re.sub(r"\s+", " ", corrected).strip(" ,;\t\r\n")
    normalized, matches = _normalize_terms(cleaned, glossary if isinstance(glossary, list) else [])
    notes = []
    if filler_count:
        notes.append("Removed spoken fillers.")
    if correction_applied:
        notes.append("Applied the latest self-correction.")
    if matches:
        notes.append("Normalized technical terms: " + ", ".join(matches) + ".")
    if not notes:
        notes.append("No transcript cleanup was needed.")
    return {
        "cleaned_text": normalized,
        "glossary_matches": matches,
        "transcript_cleanup_note": " ".join(notes),
    }

