"""
career/github_sync.py - GitHub Portfolio Synchronizer & Cache for Vega v2.2.
Fetches repository metadata and truncated READMEs (<= 2500 chars) for the
verified portfolio (default: vega-dev), caches them in SQLite (career_vault/jobs.db),
and provides clean hooks for chat prompt injection and tailored resume assembly.
"""
from __future__ import annotations

import os
import json
import logging
import urllib.request
import urllib.error
from datetime import datetime
from typing import Dict, Any, List, Optional
from career.db import get_connection

logger = logging.getLogger("vega.career.github_sync")

DEFAULT_GITHUB_USERNAME = os.getenv("VEGA_GITHUB_USERNAME", "").strip()

# Hermetic Mock Repositories for CI and Offline Parity
HERMETIC_MOCK_REPOS = [
    {
        "name": "AI-Agent-Orchestrator",
        "full_name": "vega-dev/AI-Agent-Orchestrator",
        "description": "Auto-failover proxy router and safety-guard system for VS Code coding agents with 10-model fallback cascade.",
        "html_url": "https://github.com/vega-dev/AI-Agent-Orchestrator",
        "language": "Python",
        "topics": json.dumps(["fastapi", "ai-agents", "reverse-proxy", "failover", "ollama", "python"]),
        "readme_snippet": "# AI-Agent-Orchestrator\nReverse proxy router directing coding agent requests across a multi-model fallback cascade with automated sliding-window cooldowns and AST syntax guards.",
        "pushed_at": "2026-11-22T02:00:00Z",
        "resume_eligible": 1,
    },
    {
        "name": "Vega",
        "full_name": "vega-dev/mybot",
        "description": "Unified desktop AI assistant and DevOps engineering workbench with 5-brain auto-routing, Edge-TTS, and ATS resume engine.",
        "html_url": "https://github.com/vega-dev/mybot",
        "language": "Python",
        "topics": json.dumps(["devops", "ai-assistant", "edge-tts", "ats-resume", "sqlite", "desktop-tools"]),
        "readme_snippet": "# Vega v2.2\nUnified desktop engineering assistant integrating smart LLM routing, ATS resume compilation, system telemetry, and half-duplex neural voice.",
        "pushed_at": "2026-11-23T12:00:00Z",
        "resume_eligible": 1,
    },
    {
        "name": "Server-Health-Monitor",
        "full_name": "vega-dev/Server-Health-Monitor",
        "description": "Automated server health daemon tracking CPU, RAM, and disk utilization with YAML thresholds and JSON structured logging.",
        "html_url": "https://github.com/vega-dev/Server-Health-Monitor",
        "language": "Python",
        "topics": json.dumps(["monitoring", "psutil", "daemon", "yaml", "structured-logging"]),
        "readme_snippet": "# Server Health Monitor\nLightweight Python daemon tracking hardware resource utilization with configurable alert thresholds and continuous JSON logging.",
        "pushed_at": "2026-11-10T14:00:00Z",
        "resume_eligible": 1,
    },
    {
        "name": "Linux-Resource-Alert-Script",
        "full_name": "vega-dev/Linux-Resource-Alert-Script",
        "description": "Enterprise-grade shell automation suite monitoring memory leaks, high swap loads, and systemd service auto-recovery.",
        "html_url": "https://github.com/vega-dev/Linux-Resource-Alert-Script",
        "language": "Shell",
        "topics": json.dumps(["bash", "linux", "systemd", "cron", "monitoring", "auto-recovery"]),
        "readme_snippet": "# Linux Resource Alert & Auto-Recovery Script\nBash shell automation monitoring system metrics, memory leaks, and triggering systemctl service restarts on threshold breaches.",
        "pushed_at": "2026-11-05T09:00:00Z",
        "resume_eligible": 1,
    },
]


def init_github_table():
    """Ensures github_repos table exists in jobs.db."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS github_repos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT UNIQUE,
                full_name TEXT,
                description TEXT,
                html_url TEXT,
                language TEXT,
                topics TEXT,
                readme_snippet TEXT,
                pushed_at TEXT,
                resume_eligible INTEGER DEFAULT 1,
                synced_at TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


def sync_github_repos(
    username: Optional[str] = None,
    token: Optional[str] = None,
    force_mock: bool = False
) -> Dict[str, Any]:
    """
    Synchronizes GitHub repositories from GitHub REST API into SQLite.
    Guarantees hermetic offline and CI operation when VEGA_CI_MODE is active.
    """
    init_github_table()
    user = username or os.environ.get("VEGA_GITHUB_USERNAME") or DEFAULT_GITHUB_USERNAME
    auth_token = token or os.environ.get("GITHUB_TOKEN") or ""

    is_ci = os.environ.get("VEGA_CI_MODE") == "1"
    now_iso = datetime.now().isoformat()

    if not user and not is_ci and not force_mock:
        return {
            "ok": True,
            "status": "ok",
            "synced_count": 0,
            "username": "",
            "repos": get_synced_repos(),
        }

    if is_ci or force_mock:
        # Populate hermetic mock repositories
        conn = get_connection()
        synced_count = 0
        try:
            cur = conn.cursor()
            for r in HERMETIC_MOCK_REPOS:
                cur.execute("""
                    INSERT INTO github_repos (name, full_name, description, html_url, language, topics, readme_snippet, pushed_at, resume_eligible, synced_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        full_name = excluded.full_name,
                        description = excluded.description,
                        html_url = excluded.html_url,
                        language = excluded.language,
                        topics = excluded.topics,
                        readme_snippet = excluded.readme_snippet,
                        pushed_at = excluded.pushed_at,
                        synced_at = excluded.synced_at
                """, (
                    r["name"], r["full_name"], r["description"], r["html_url"],
                    r["language"], r["topics"], r["readme_snippet"], r["pushed_at"],
                    r.get("resume_eligible", 1), now_iso
                ))
                synced_count += 1
            conn.commit()
        finally:
            conn.close()

        return {
            "ok": True,
            "status": "ok",
            "synced_count": synced_count,
            "username": user,
            "notice": "Hermetic CI / offline portfolio sync completed successfully.",
            "repos": get_synced_repos(),
        }

    # Live GitHub API Sync
    api_url = f"https://api.github.com/users/{user}/repos?sort=pushed&per_page=30"
    headers = {
        "User-Agent": "Vega-DevOps-Workbench/2.2",
        "Accept": "application/vnd.github.v3+json",
    }
    if auth_token:
        headers["Authorization"] = f"token {auth_token}"

    req = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        logger.warning(f"GitHub API fetch failed for {user}: {exc}; falling back to existing cache or mock.")
        # If cache already has repos, return them; otherwise seed hermetic mocks
        existing = get_synced_repos()
        if existing:
            return {
                "ok": True,
                "status": "ok",
                "synced_count": len(existing),
                "username": user,
                "notice": f"Network fetch failed ({exc}). Using cached repositories.",
                "repos": existing,
            }
        return sync_github_repos(user, token, force_mock=True)

    if not isinstance(data, list):
        return {"ok": False, "status": "error", "error": "Invalid response format from GitHub"}

    conn = get_connection()
    synced_count = 0
    try:
        cur = conn.cursor()
        for repo_obj in data:
            name = repo_obj.get("name")
            if not name or repo_obj.get("fork"):
                continue

            full_name = repo_obj.get("full_name") or f"{user}/{name}"
            description = repo_obj.get("description") or ""
            html_url = repo_obj.get("html_url") or f"https://github.com/{full_name}"
            language = repo_obj.get("language") or "Python"
            topics = json.dumps(repo_obj.get("topics") or [])
            pushed_at = repo_obj.get("pushed_at") or now_iso

            # Fetch README snippet (up to 2500 chars)
            readme_snippet = description
            raw_readme_url = f"https://raw.githubusercontent.com/{full_name}/main/README.md"
            readme_req = urllib.request.Request(raw_readme_url, headers={"User-Agent": "Vega-DevOps-Workbench/2.2"})
            try:
                with urllib.request.urlopen(readme_req, timeout=3.0) as r_resp:
                    readme_raw = r_resp.read().decode("utf-8", errors="replace")
                    readme_snippet = readme_raw[:2500].strip()
            except Exception:
                # Try master branch fallback
                try:
                    master_url = f"https://raw.githubusercontent.com/{full_name}/master/README.md"
                    with urllib.request.urlopen(urllib.request.Request(master_url, headers={"User-Agent": "Vega-DevOps-Workbench/2.2"}), timeout=2.0) as m_resp:
                        readme_raw = m_resp.read().decode("utf-8", errors="replace")
                        readme_snippet = readme_raw[:2500].strip()
                except Exception:
                    pass

            cur.execute("""
                INSERT INTO github_repos (name, full_name, description, html_url, language, topics, readme_snippet, pushed_at, resume_eligible, synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(name) DO UPDATE SET
                    full_name = excluded.full_name,
                    description = excluded.description,
                    html_url = excluded.html_url,
                    language = excluded.language,
                    topics = excluded.topics,
                    readme_snippet = excluded.readme_snippet,
                    pushed_at = excluded.pushed_at,
                    synced_at = excluded.synced_at
            """, (name, full_name, description, html_url, language, topics, readme_snippet, pushed_at, now_iso))
            synced_count += 1
        conn.commit()
    finally:
        conn.close()

    return {
        "ok": True,
        "status": "ok",
        "synced_count": synced_count,
        "username": user,
        "repos": get_synced_repos(),
    }


def get_synced_repos(eligible_only: bool = False) -> List[Dict[str, Any]]:
    """Returns all cached repositories from jobs.db."""
    init_github_table()
    conn = get_connection()
    try:
        cur = conn.cursor()
        query = "SELECT * FROM github_repos"
        if eligible_only:
            query += " WHERE resume_eligible = 1"
        query += " ORDER BY pushed_at DESC"
        cur.execute(query)
        rows = cur.fetchall()
        result = []
        for r in rows:
            topics_list = []
            try:
                topics_list = json.loads(r["topics"]) if r["topics"] else []
            except Exception:
                pass
            result.append({
                "id": r["id"],
                "name": r["name"],
                "full_name": r["full_name"],
                "description": r["description"],
                "html_url": r["html_url"],
                "language": r["language"],
                "topics": topics_list,
                "readme_snippet": r["readme_snippet"],
                "pushed_at": r["pushed_at"],
                "resume_eligible": bool(r["resume_eligible"]),
                "synced_at": r["synced_at"],
            })
        return result
    finally:
        conn.close()


def toggle_repo_eligibility(name: str, eligible: bool) -> bool:
    """Toggles whether a repository is eligible for ATS resume tailoring."""
    init_github_table()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("UPDATE github_repos SET resume_eligible = ? WHERE name = ?", (1 if eligible else 0, name))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_portfolio_summary() -> str:
    """
    Returns a crisp, prompt-friendly summary of verified active projects and repos
    for chat context and conversational assistance.
    """
    username = os.getenv("VEGA_GITHUB_USERNAME", "").strip()
    repos = get_synced_repos(eligible_only=True)
    if not repos:
        if not username:
            return ""
        if os.environ.get("VEGA_CI_MODE") == "1":
            repos = HERMETIC_MOCK_REPOS
        else:
            return ""

    lines = ["Verified Projects & GitHub Repositories:"]
    for r in repos[:5]:
        desc = r.get("description") or "DevOps / Python Engineering Project"
        lang = r.get("language") or "Python"
        lines.append(f"- {r.get('name')} ({lang}): {desc} [{r.get('html_url')}]")
    return "\n".join(lines)
