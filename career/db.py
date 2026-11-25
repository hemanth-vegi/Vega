"""
SQLite Database Layer for Career Workbench in Vega v2.2.
Provides isolated, atomic persistence for job postings, deduplication,
application statuses, tailored resumes, and ATS company watchlists.
Database: career_vault/jobs.db
"""

import os
import json
import sqlite3
import hashlib
from typing import Dict, Any, List, Optional, Tuple


def get_db_path() -> str:
    """Returns absolute path to career_vault/jobs.db."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(base_dir)
    vault_dir = os.path.join(project_root, "career_vault")
    os.makedirs(vault_dir, exist_ok=True)
    return os.path.join(vault_dir, "jobs.db")


def get_connection() -> sqlite3.Connection:
    """Creates a connection to jobs.db with row factory enabled and 15s lock timeout."""
    conn = sqlite3.connect(get_db_path(), timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


# Default Expanded Watchlist (50 tech/cloud/infra companies across Greenhouse, Lever, and Ashby)
DEFAULT_WATCHLIST_COMPANIES: List[Tuple[str, str, str]] = [
    # Greenhouse (24 companies)
    ("Canonical", "greenhouse", "canonical"),
    ("GitLab", "greenhouse", "gitlab"),
    ("Figma", "greenhouse", "figma"),
    ("Docker", "greenhouse", "docker"),
    ("Stripe", "greenhouse", "stripe"),
    ("Cloudflare", "greenhouse", "cloudflare"),
    ("Elastic", "greenhouse", "elastic"),
    ("MongoDB", "greenhouse", "mongodb"),
    ("Automattic", "greenhouse", "automattic"),
    ("Vercel", "greenhouse", "vercel"),
    ("Datadog", "greenhouse", "datadog"),
    ("Mozilla", "greenhouse", "mozilla"),
    ("HashiCorp", "greenhouse", "hashicorp"),
    ("Confluent", "greenhouse", "confluent"),
    ("Twilio", "greenhouse", "twilio"),
    ("GitHub", "greenhouse", "github"),
    ("Atlassian", "greenhouse", "atlassian"),
    ("Okta", "greenhouse", "okta"),
    ("Snowflake", "greenhouse", "snowflake"),
    ("Dynatrace", "greenhouse", "dynatrace"),
    ("Cockroach Labs", "greenhouse", "cockroachlabs"),
    ("Couchbase", "greenhouse", "couchbase"),
    ("Snyk", "greenhouse", "snyk"),
    ("Reddit", "greenhouse", "reddit"),
    # Lever (13 companies)
    ("Postman", "lever", "postman"),
    ("Supabase", "lever", "supabase"),
    ("Palantir", "lever", "palantir"),
    ("Grafana", "lever", "grafana"),
    ("Zapier", "lever", "zapier"),
    ("Checkr", "lever", "checkr"),
    ("Fullstory", "lever", "fullstory"),
    ("Clever", "lever", "clever"),
    ("Sourcegraph", "lever", "sourcegraph"),
    ("Kinsta", "lever", "kinsta"),
    ("Auth0", "lever", "auth0"),
    ("Hotjar", "lever", "hotjar"),
    ("Buffer", "lever", "buffer"),
    # Ashby (13 companies)
    ("Retool", "ashby", "retool"),
    ("Linear", "ashby", "linear"),
    ("Ramp", "ashby", "ramp"),
    ("Cursor", "ashby", "cursor"),
    ("Vanta", "ashby", "vanta"),
    ("Substack", "ashby", "substack"),
    ("Loom", "ashby", "loom"),
    ("Replit", "ashby", "replit"),
    ("Railway", "ashby", "railway"),
    ("Fly.io", "ashby", "flyio"),
    ("Modal", "ashby", "modal"),
    ("Pinecone", "ashby", "pinecone"),
    ("PostHog", "ashby", "posthog"),
]


def init_db():
    """Initializes tables and indexes with deduplication support."""
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS job_postings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                company TEXT,
                role TEXT,
                location TEXT,
                url TEXT UNIQUE,
                raw_text TEXT,
                scam_risk TEXT,
                scam_score INTEGER DEFAULT 0,
                scam_flags_json TEXT,
                match_score INTEGER DEFAULT 0,
                matched_skills_json TEXT,
                missing_skills_json TEXT,
                status TEXT DEFAULT 'New',
                tailored_pdf_path TEXT,
                cover_note TEXT,
                interview_prep_json TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS watchlist_companies (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                company_name TEXT UNIQUE,
                ats_type TEXT,
                board_slug TEXT,
                enabled INTEGER DEFAULT 1
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS gmail_synced_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id TEXT UNIQUE,
                thread_id TEXT,
                subject TEXT,
                sender TEXT,
                received_at DATETIME,
                message_type TEXT,
                action_taken TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS career_settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Seed default career settings
        cur.execute("INSERT OR IGNORE INTO career_settings (key, value) VALUES ('gmail_label', 'Vega-Career')")
        cur.execute("INSERT OR IGNORE INTO career_settings (key, value) VALUES ('calendar_sync_enabled', '0')")

        # Indexes for fast lookup
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_status ON job_postings(status);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_scam_risk ON job_postings(scam_risk);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_jobs_company ON job_postings(company);")
        cur.execute("CREATE INDEX IF NOT EXISTS idx_gmail_msg_id ON gmail_synced_messages(message_id);")

        # Seed expanded watchlist (50 tech/cloud companies across Greenhouse, Lever, and Ashby)
        for c_name, ats, slug in DEFAULT_WATCHLIST_COMPANIES:
            try:
                cur.execute(
                    "INSERT OR IGNORE INTO watchlist_companies (company_name, ats_type, board_slug, enabled) VALUES (?, ?, ?, 1)",
                    (c_name, ats, slug)
                )
            except Exception:
                pass

        conn.commit()
    finally:
        conn.close()


def normalize_job_url(url: str, company: str, role: str) -> str:
    """Normalizes URL or builds deterministic fallback hash identifier for deduplication."""
    clean_url = (url or "").strip()
    if clean_url and (clean_url.startswith("http://") or clean_url.startswith("https://")):
        return clean_url.split("?")[0].rstrip("/")
    # Build unique synthetic URL from company and role
    c = (company or "unknown").strip().lower()
    r = (role or "intern").strip().lower()
    h = hashlib.sha256(f"{c}::{r}".encode("utf-8")).hexdigest()[:12]
    return f"synthetic://{c}/{r}/{h}"


def save_job(job: Dict[str, Any]) -> int:
    """
    Saves or updates a job posting with deduplication.
    Returns the integer id of the posting.
    """
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        url = normalize_job_url(job.get("url", ""), job.get("company", ""), job.get("role", ""))
        scam_flags = job.get("scam_flags") or []
        matched_skills = job.get("matched_skills") or []
        missing_skills = job.get("missing_skills") or []
        interview_prep = job.get("interview_prep") or []

        scam_flags_json = json.dumps(scam_flags, ensure_ascii=False) if isinstance(scam_flags, list) else str(scam_flags)
        matched_json = json.dumps(matched_skills, ensure_ascii=False) if isinstance(matched_skills, list) else str(matched_skills)
        missing_json = json.dumps(missing_skills, ensure_ascii=False) if isinstance(missing_skills, list) else str(missing_skills)
        interview_json = json.dumps(interview_prep, ensure_ascii=False) if isinstance(interview_prep, list) else str(interview_prep)

        # Check existing by URL
        cur.execute("SELECT id, status, tailored_pdf_path, cover_note FROM job_postings WHERE url = ?", (url,))
        existing = cur.fetchone()

        if existing:
            job_id = existing["id"]
            # Preserve existing user-set status unless incoming explicitly updates it
            status = job.get("status") or existing["status"]
            pdf_path = job.get("tailored_pdf_path") or existing["tailored_pdf_path"]
            cover = job.get("cover_note") or existing["cover_note"]

            cur.execute("""
                UPDATE job_postings SET
                    source = COALESCE(?, source),
                    company = COALESCE(?, company),
                    role = COALESCE(?, role),
                    location = COALESCE(?, location),
                    raw_text = COALESCE(?, raw_text),
                    scam_risk = COALESCE(?, scam_risk),
                    scam_score = COALESCE(?, scam_score),
                    scam_flags_json = COALESCE(?, scam_flags_json),
                    match_score = COALESCE(?, match_score),
                    matched_skills_json = COALESCE(?, matched_skills_json),
                    missing_skills_json = COALESCE(?, missing_skills_json),
                    status = ?,
                    tailored_pdf_path = ?,
                    cover_note = ?,
                    interview_prep_json = COALESCE(?, interview_prep_json),
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (
                job.get("source"),
                job.get("company"),
                job.get("role"),
                job.get("location"),
                job.get("raw_text"),
                job.get("scam_risk"),
                job.get("scam_score"),
                scam_flags_json,
                job.get("match_score"),
                matched_json,
                missing_json,
                status,
                pdf_path,
                cover,
                interview_json,
                job_id
            ))
            conn.commit()
            return job_id
        else:
            status = job.get("status", "New")
            cur.execute("""
                INSERT INTO job_postings (
                    source, company, role, location, url, raw_text,
                    scam_risk, scam_score, scam_flags_json,
                    match_score, matched_skills_json, missing_skills_json,
                    status, tailored_pdf_path, cover_note, interview_prep_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.get("source", "Manual"),
                job.get("company", "Unknown"),
                job.get("role", "Opportunity"),
                job.get("location", "India / Remote"),
                url,
                job.get("raw_text", ""),
                job.get("scam_risk", "LOW_RISK"),
                job.get("scam_score", 0),
                scam_flags_json,
                job.get("match_score", 0),
                matched_json,
                missing_json,
                status,
                job.get("tailored_pdf_path", ""),
                job.get("cover_note", ""),
                interview_json
            ))
            conn.commit()
            return cur.lastrowid
    finally:
        conn.close()


def get_job(job_id: int) -> Optional[Dict[str, Any]]:
    """Fetches a single job posting by ID."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM job_postings WHERE id = ?", (job_id,))
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["scam_flags"] = json.loads(d.get("scam_flags_json") or "[]")
        except Exception:
            d["scam_flags"] = []
        try:
            d["matched_skills"] = json.loads(d.get("matched_skills_json") or "[]")
        except Exception:
            d["matched_skills"] = []
        try:
            d["missing_skills"] = json.loads(d.get("missing_skills_json") or "[]")
        except Exception:
            d["missing_skills"] = []
        try:
            d["interview_prep"] = json.loads(d.get("interview_prep_json") or "[]")
        except Exception:
            d["interview_prep"] = []
        return d
    finally:
        conn.close()


def list_jobs(status: Optional[str] = None, scam_risk: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
    """Returns list of job postings filtered by status and/or scam risk."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        query = "SELECT * FROM job_postings WHERE 1=1"
        params = []
        if status:
            query += " AND status = ?"
            params.append(status)
        if scam_risk:
            query += " AND scam_risk = ?"
            params.append(scam_risk)
        query += " ORDER BY match_score DESC, updated_at DESC LIMIT ?"
        params.append(limit)

        cur.execute(query, params)
        rows = cur.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["scam_flags"] = json.loads(d.get("scam_flags_json") or "[]")
            except Exception:
                d["scam_flags"] = []
            try:
                d["matched_skills"] = json.loads(d.get("matched_skills_json") or "[]")
            except Exception:
                d["matched_skills"] = []
            try:
                d["missing_skills"] = json.loads(d.get("missing_skills_json") or "[]")
            except Exception:
                d["missing_skills"] = []
            try:
                d["interview_prep"] = json.loads(d.get("interview_prep_json") or "[]")
            except Exception:
                d["interview_prep"] = []
            results.append(d)
        return results
    finally:
        conn.close()


get_jobs = list_jobs


def update_job_status(job_id: int, status: str, notes: Optional[str] = None, pdf_path: Optional[str] = None) -> bool:
    """Updates status, optional notes, and optional tailored PDF path."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        query = "UPDATE job_postings SET status = ?, updated_at = CURRENT_TIMESTAMP"
        params = [status]
        if notes is not None:
            query += ", cover_note = ?"
            params.append(notes)
        if pdf_path is not None:
            query += ", tailored_pdf_path = ?"
            params.append(pdf_path)
        query += " WHERE id = ?"
        params.append(job_id)

        cur.execute(query, params)
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def delete_job(job_id: int) -> bool:
    """Deletes a job posting by ID."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM job_postings WHERE id = ?", (job_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def get_watchlist() -> List[Dict[str, Any]]:
    """Returns watchlist companies."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM watchlist_companies ORDER BY company_name ASC")
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def add_watchlist_company(company_name: str, ats_type: str, board_slug: str) -> int:
    """Adds a new company to the ATS watchlist."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO watchlist_companies (company_name, ats_type, board_slug, enabled)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(company_name) DO UPDATE SET
                ats_type = excluded.ats_type,
                board_slug = excluded.board_slug,
                enabled = 1
        """, (company_name.strip(), ats_type.strip().lower(), board_slug.strip()))
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_career_metrics() -> Dict[str, int]:
    """Returns application counts by status."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT status, COUNT(*) as cnt FROM job_postings GROUP BY status")
        metrics = {
            "Total": 0,
            "New": 0,
            "Saved": 0,
            "Tailored": 0,
            "Applied": 0,
            "Interview": 0,
            "Rejected": 0,
            "Ignored_Scam": 0
        }
        total = 0
        for row in cur.fetchall():
            s = row["status"]
            cnt = row["cnt"]
            metrics[s] = cnt
            total += cnt
        metrics["Total"] = total
        return metrics
    finally:
        conn.close()


def is_gmail_message_synced(message_id: str) -> bool:
    """Checks whether a Gmail message_id has already been processed."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM gmail_synced_messages WHERE message_id = ?", (message_id,))
        return cur.fetchone() is not None
    finally:
        conn.close()


def record_gmail_synced_message(
    message_id: str,
    thread_id: str = "",
    subject: str = "",
    sender: str = "",
    received_at: str = "",
    message_type: str = "other",
    action_taken: str = ""
) -> bool:
    """Records a processed Gmail message to prevent redundant processing."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT OR REPLACE INTO gmail_synced_messages (
                message_id, thread_id, subject, sender, received_at, message_type, action_taken
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            message_id, thread_id, subject, sender, received_at, message_type, action_taken
        ))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_career_setting(key: str, default: str = "") -> str:
    """Retrieves a configuration value from career_settings."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT value FROM career_settings WHERE key = ?", (key,))
        row = cur.fetchone()
        return row["value"] if row else default
    finally:
        conn.close()


def set_career_setting(key: str, value: str) -> bool:
    """Saves or updates a key-value setting in career_settings."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO career_settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """, (key, value))
        conn.commit()
        return True
    except Exception:
        return False
    finally:
        conn.close()


def get_network_mode() -> str:
    """Returns persistent network mode: 'auto' (default), 'low_data', or 'offline_only'."""
    return get_career_setting("network_mode", "auto")


def set_network_mode(mode: str) -> bool:
    """Persists network mode if valid ('auto', 'low_data', 'offline_only')."""
    valid = ("auto", "low_data", "offline_only")
    clean_mode = str(mode).strip().lower()
    if clean_mode not in valid:
        clean_mode = "auto"
    return set_career_setting("network_mode", clean_mode)


def find_job_by_company(company: str) -> Optional[Dict[str, Any]]:
    """Finds an existing tracked job posting matching the given company name."""
    if not company or not company.strip():
        return None
    comp_clean = company.strip().lower()
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        # 1. Exact match
        cur.execute("SELECT * FROM job_postings WHERE LOWER(company) = ? ORDER BY id DESC LIMIT 1", (comp_clean,))
        row = cur.fetchone()
        if row:
            return dict(row)
        # 2. Substring match
        cur.execute("SELECT * FROM job_postings WHERE LOWER(company) LIKE ? ORDER BY id DESC LIMIT 1", (f"%{comp_clean}%",))
        row = cur.fetchone()
        if row:
            return dict(row)
        return None
    finally:
        conn.close()


def get_gmail_sync_stats() -> Dict[str, Any]:
    """Retrieves sync statistics and configuration status."""
    init_db()
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM gmail_synced_messages")
        total_synced = cur.fetchone()["cnt"]

        label = get_career_setting("gmail_label", "Vega-Career")
        calendar_enabled = get_career_setting("calendar_sync_enabled", "0") == "1"
        last_sync = get_career_setting("last_gmail_sync", "")
        last_status = get_career_setting("last_sync_status", "Never synced")

        return {
            "total_synced": total_synced,
            "label": label,
            "calendar_sync_enabled": calendar_enabled,
            "last_sync": last_sync,
            "last_status": last_status
        }
    finally:
        conn.close()

