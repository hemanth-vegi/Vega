"""
Vega v2.2 — Read-Only Gmail Label Synchronizer & Application Update Tracker.
Strict Privacy Guarantees:
1. Scope restricted strictly to 'https://www.googleapis.com/auth/gmail.readonly' (NEVER write/delete/send).
2. Syncs ONLY from a single configured user label (default: 'Vega-Career'). NEVER scans general inbox.
3. Optional Google Calendar integration ('https://www.googleapis.com/auth/calendar.events') is strictly opt-in.
4. Credentials & tokens kept strictly inside gitignored career_vault/. Zero leaked secrets.
5. Graceful offline / unconfigured state when libraries or credentials.json are missing.
"""

import os
import re
import json
import base64
import logging
import threading
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, Tuple

logger = logging.getLogger("vega.career.gmail")

# Safe import of Google client libraries
try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    GOOGLE_CLIENT_AVAILABLE = True
except ImportError:
    GOOGLE_CLIENT_AVAILABLE = False

from career.db import (
    is_gmail_message_synced,
    record_gmail_synced_message,
    get_career_setting,
    set_career_setting,
    find_job_by_company,
    update_job_status,
    save_job
)
from career.scam_shield import audit_job_listing
from career.matcher import match_job_and_tailor

# Directory paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)
VAULT_DIR = os.path.join(PROJECT_ROOT, "career_vault")
os.makedirs(VAULT_DIR, exist_ok=True)

# Strict Read-Only Gmail Scope
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

_gmail_sync_lock = threading.Lock()


def get_credentials_path() -> Optional[str]:
    """Finds credentials.json in career_vault/ or project root."""
    paths = [
        os.path.join(VAULT_DIR, "credentials.json"),
        os.path.join(PROJECT_ROOT, "credentials.json")
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    return None


def get_token_path() -> str:
    """Returns token storage path inside career_vault/."""
    return os.path.join(VAULT_DIR, "token.json")


def is_gmail_configured() -> bool:
    """Checks if Google client is available and credentials.json is present."""
    if not GOOGLE_CLIENT_AVAILABLE:
        return False
    return get_credentials_path() is not None


def is_gmail_authenticated() -> bool:
    """Checks whether an existing OAuth token is present and valid/refreshable."""
    token_p = get_token_path()
    if not os.path.exists(token_p):
        return False
    try:
        creds = Credentials.from_authorized_user_file(token_p, GMAIL_SCOPES)
        return creds and (creds.valid or creds.refresh_token is not None)
    except Exception:
        return False


def get_gmail_status() -> Dict[str, Any]:
    """Returns current Gmail integration status, label name, and setup guidance."""
    creds_p = get_credentials_path()
    token_p = get_token_path()
    label = get_career_setting("gmail_label", "Vega-Career")
    calendar_enabled = get_career_setting("calendar_sync_enabled", "0") == "1"
    last_sync = get_career_setting("last_gmail_sync", "")
    last_status = get_career_setting("last_sync_status", "Never synced")

    if not GOOGLE_CLIENT_AVAILABLE:
        return {
            "status": "library_missing",
            "configured": False,
            "connected": False,
            "label": label,
            "calendar_sync_enabled": calendar_enabled,
            "last_sync": last_sync,
            "last_status": "google-api-python-client not installed.",
            "setup_guide_needed": True
        }

    if not creds_p:
        return {
            "status": "credentials_missing",
            "configured": False,
            "connected": False,
            "label": label,
            "calendar_sync_enabled": calendar_enabled,
            "last_sync": last_sync,
            "last_status": "credentials.json missing in career_vault/.",
            "setup_guide_needed": True
        }

    is_authed = is_gmail_authenticated()
    return {
        "status": "connected" if is_authed else "needs_authorization",
        "configured": True,
        "connected": is_authed,
        "label": label,
        "calendar_sync_enabled": calendar_enabled,
        "last_sync": last_sync,
        "last_status": last_status,
        "setup_guide_needed": not is_authed
    }


def get_authenticated_services(interactive: bool = False) -> Tuple[Optional[Any], Optional[Any]]:
    """Builds Gmail and optional Calendar services with strict read-only/event scopes."""
    if not GOOGLE_CLIENT_AVAILABLE:
        return None, None

    creds_p = get_credentials_path()
    token_p = get_token_path()
    if not creds_p:
        return None, None

    calendar_enabled = get_career_setting("calendar_sync_enabled", "0") == "1"
    required_scopes = list(GMAIL_SCOPES)
    if calendar_enabled:
        required_scopes.extend(CALENDAR_SCOPES)

    creds = None
    if os.path.exists(token_p):
        try:
            creds = Credentials.from_authorized_user_file(token_p, required_scopes)
        except Exception as exc:
            logger.warning(f"Failed to load token file: {exc}")

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception as exc:
                logger.warning(f"Failed to refresh OAuth token: {exc}")
                creds = None

        if not creds and interactive:
            try:
                flow = InstalledAppFlow.from_client_secrets_file(creds_p, required_scopes)
                creds = flow.run_local_server(port=0)
                with open(token_p, "w", encoding="utf-8") as token_file:
                    token_file.write(creds.to_json())
            except Exception as exc:
                logger.error(f"Interactive OAuth flow failed: {exc}")
                return None, None

    if not creds or not creds.valid:
        return None, None

    gmail_service = build("gmail", "v1", credentials=creds, cache_discovery=False)
    calendar_service = None
    if calendar_enabled and any(s in creds.scopes for s in CALENDAR_SCOPES):
        try:
            calendar_service = build("calendar", "v3", credentials=creds, cache_discovery=False)
        except Exception as exc:
            logger.warning(f"Calendar service initialization failed: {exc}")

    return gmail_service, calendar_service


def add_career_reminder(when_dt: datetime, text: str) -> bool:
    """Adds a scheduled deadline reminder to Vega's reminders.json."""
    try:
        reminders_path = os.path.join(PROJECT_ROOT, "reminders.json")
        items = []
        if os.path.exists(reminders_path):
            with open(reminders_path, "r", encoding="utf-8") as f:
                items = json.load(f)
            if not isinstance(items, list):
                items = []

        # Check for duplicate text and timestamp
        when_str = when_dt.isoformat(timespec="seconds")
        if any(r.get("text") == text and r.get("when") == when_str for r in items):
            return False

        items.append({
            "id": f"r{int(datetime.now().timestamp() * 1000)}",
            "when": when_str,
            "text": text[:200],
            "done": False,
            "source": "Vega-Career-Gmail"
        })

        with open(reminders_path, "w", encoding="utf-8") as f:
            json.dump(items[-200:], f, indent=2)
        return True
    except Exception as exc:
        logger.warning(f"Could not write career reminder: {exc}")
        return False


def create_calendar_event(
    calendar_service,
    summary: str,
    description: str,
    start_dt: datetime,
    duration_hours: int = 1
) -> Optional[str]:
    """Creates a calendar event if calendar_service is provided (opt-in)."""
    if not calendar_service:
        return None
    try:
        end_dt = start_dt + timedelta(hours=duration_hours)
        event = {
            "summary": summary,
            "description": description,
            "start": {"dateTime": start_dt.isoformat(), "timeZone": "Asia/Kolkata"},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": "Asia/Kolkata"},
            "reminders": {
                "useDefault": False,
                "overrides": [
                    {"method": "popup", "minutes": 60},
                    {"method": "popup", "minutes": 15}
                ]
            }
        }
        res = calendar_service.events().insert(calendarId="primary", body=event).execute()
        return res.get("htmlLink")
    except Exception as exc:
        logger.warning(f"Failed to create Google Calendar event: {exc}")
        return None


# ==============================================================================
# DETERMINISTIC EMAIL PARSERS (Alerts, Application Status, Deadlines)
# ==============================================================================

JOB_ALERT_DOMAINS = [
    "linkedin.com", "naukri.com", "wellfound.com", "hirist.com",
    "unstop.com", "indeed.com", "glassdoor.com", "internshala.com"
]

JOB_URL_PATTERNS = [
    re.compile(r"https?://(?:www\.)?linkedin\.com/comm/jobs/view/(\d+)[^\s\"'<>]*", re.I),
    re.compile(r"https?://(?:www\.)?linkedin\.com/jobs/view/(\d+)[^\s\"'<>]*", re.I),
    re.compile(r"https?://(?:www\.)?naukri\.com/job-listings-[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?wellfound\.com/jobs/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?unstop\.com/jobs/[^\s\"'<>]+", re.I),
    re.compile(r"https?://(?:www\.)?hirist\.com/j/[^\s\"'<>]+", re.I),
    re.compile(r"https?://boards\.greenhouse\.io/[^\s\"'<>]+\/jobs\/\d+", re.I),
    re.compile(r"https?://jobs\.lever\.co/[^\s\"'<>]+\/[a-f0-9\-]+", re.I),
    re.compile(r"https?://jobs\.ashbyhq\.com/[^\s\"'<>]+\/[a-f0-9\-]+", re.I),
]

STATUS_KEYWORDS = [
    ("Online Assessment", [
        r"\bonline assessment\b", r"\bcoding test\b", r"\bhackerrank\b",
        r"\bcodesignal\b", r"\btest link\b", r"\bassessment deadline\b",
        r"\bcomplete your assessment\b", r"\bhacker earth\b", r"\bglider\b"
    ]),
    ("Interview", [
        r"\binterview scheduled\b", r"\binterview invitation\b", r"\binvite you to interview\b",
        r"\btechnical interview\b", r"\bspeaking with the team\b", r"\bgoogle meet\b",
        r"\bzoom interview\b", r"\bhiring manager interview\b", r"\bround 1 interview\b",
        r"\bfinal round\b"
    ]),
    ("Offer", [
        r"\boffer letter\b", r"\bjob offer\b", r"\bpleased to offer\b",
        r"\bcongratulations on the offer\b", r"\bwelcome to the team\b"
    ]),
    ("Rejected", [
        r"\bunfortunately\b", r"\bnot moving forward\b", r"\bdecided to pursue other candidates\b",
        r"\bimpressive background, but\b", r"\bposition has been filled\b",
        r"\bnot selected at this time\b", r"\bnot be proceeding\b"
    ]),
    ("Applied", [
        r"\bapplication received\b", r"\bthank you for applying\b",
        r"\bwe received your application\b", r"\bapplication submitted\b",
        r"\bsuccessfully applied\b"
    ])
]

DEADLINE_REGEX = re.compile(
    r"\b(?:due(?: date)?|due by|deadline|complete (?:[a-zA-Z]+\s+)*(?:before|by)|before|by|on)\s*:?\s*"
    r"((?:(?:Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)[,]?\s+)?"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2}(?:st|nd|rd|th)?(?:,?\s*\d{4})?"
    r"(?:\s+(?:at\s+)?\d{1,2}(?::\d{2})?\s*(?:am|pm|AM|PM)?)?)",
    re.IGNORECASE
)


def extract_body_text_from_payload(payload: Dict[str, Any]) -> str:
    """Extracts decoded text content recursively from Gmail message payload."""
    parts = payload.get("parts", [])
    text_content = ""

    if "body" in payload and payload["body"].get("data"):
        try:
            data = payload["body"]["data"]
            decoded = base64.urlsafe_b64decode(data.encode("UTF-8")).decode("UTF-8", errors="ignore")
            text_content += decoded + " "
        except Exception:
            pass

    for part in parts:
        mime_type = part.get("mimeType", "")
        if mime_type == "text/plain" and part.get("body", {}).get("data"):
            try:
                data = part["body"]["data"]
                decoded = base64.urlsafe_b64decode(data.encode("UTF-8")).decode("UTF-8", errors="ignore")
                text_content += decoded + " "
            except Exception:
                pass
        elif mime_type == "text/html" and not text_content and part.get("body", {}).get("data"):
            try:
                data = part["body"]["data"]
                decoded = base64.urlsafe_b64decode(data.encode("UTF-8")).decode("UTF-8", errors="ignore")
                clean = re.sub(r"<[^>]+>", " ", decoded)
                text_content += clean + " "
            except Exception:
                pass
        elif "parts" in part:
            text_content += extract_body_text_from_payload(part) + " "

    return text_content.strip()


def parse_email_message(msg_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extracts normalized subject, sender, date, body, detected status, and job alerts."""
    headers = {h["name"].lower(): h["value"] for h in msg_payload.get("payload", {}).get("headers", [])}
    subject = headers.get("subject", "")
    sender = headers.get("from", "")
    date_str = headers.get("date", "")

    body_text = extract_body_text_from_payload(msg_payload.get("payload", {}))
    combined_text = f"{subject}\n{body_text}"

    # 1. Detect if it's a job alert email
    is_job_alert = False
    extracted_job_urls = []
    sender_lower = sender.lower()
    if any(dom in sender_lower for dom in JOB_ALERT_DOMAINS) or "job alert" in subject.lower() or "jobs for you" in subject.lower():
        is_job_alert = True
        for pat in JOB_URL_PATTERNS:
            for match in pat.findall(combined_text):
                full_url = match if isinstance(match, str) and match.startswith("http") else None
                if not full_url:
                    # reconstruct if captured ID
                    if "linkedin.com" in sender_lower:
                        full_url = f"https://www.linkedin.com/jobs/view/{match}"
                if full_url and full_url not in extracted_job_urls:
                    extracted_job_urls.append(full_url)

    # 2. Detect Application Status Updates
    detected_status = None
    for status_label, patterns in STATUS_KEYWORDS:
        for pat in patterns:
            if re.search(pat, combined_text, re.IGNORECASE):
                detected_status = status_label
                break
        if detected_status:
            break

    # 3. Detect Company Name
    detected_company = ""
    # Try finding company from domain (e.g., recruiter@figma.com -> Figma)
    email_match = re.search(r"@([a-zA-Z0-9\-]+)\.([a-zA-Z]{2,})", sender)
    if email_match:
        domain_name = email_match.group(1).lower()
        if domain_name not in ["gmail", "yahoo", "outlook", "hotmail", "proton", "linkedin", "naukri"]:
            detected_company = domain_name.capitalize()

    if not detected_company:
        # Fallback heuristic: check if subject mentions company
        subj_clean = re.sub(r"^(Re|Fwd):\s*", "", subject, flags=re.I)
        parts = subj_clean.split(" - ")
        if len(parts) > 1:
            detected_company = parts[0].strip()

    # 4. Detect Deadlines & Dates
    detected_deadline = None
    deadline_match = DEADLINE_REGEX.search(combined_text)
    if deadline_match:
        raw_deadline_str = deadline_match.group(1).strip()
        detected_deadline = raw_deadline_str

    return {
        "subject": subject,
        "sender": sender,
        "date_str": date_str,
        "body_text": body_text[:15000],
        "is_job_alert": is_job_alert,
        "job_urls": extracted_job_urls,
        "detected_status": detected_status,
        "detected_company": detected_company,
        "detected_deadline": detected_deadline
    }


def sync_gmail_label(force_interactive: bool = False) -> Dict[str, Any]:
    """
    Main sync routine:
    1. Acquires concurrency lock.
    2. Fetches recent messages strictly from the configured label.
    3. Deduplicates via SQLite message_id.
    4. Ingests job alerts & updates application statuses.
    5. Schedules reminders for detected OA/interview deadlines.
    """
    if not _gmail_sync_lock.acquire(blocking=False):
        return {
            "status": "already_running",
            "notice": "A Gmail synchronization is already active in the background."
        }

    try:
        from llm.engine import is_offline_or_low_data
        restricted, net_mode = is_offline_or_low_data()
        if restricted:
            return {
                "status": "skipped_offline",
                "synced_count": 0,
                "new_jobs_added": 0,
                "apps_updated": 0,
                "deadlines_created": 0,
                "network_mode": net_mode,
                "notice": f"Gmail synchronization skipped ({net_mode} active)."
            }

        if not GOOGLE_CLIENT_AVAILABLE:
            return {
                "status": "error",
                "error": "Google client libraries are not installed.",
                "notice": "Install google-api-python-client & google-auth-oauthlib to use Gmail sync."
            }

        creds_p = get_credentials_path()
        if not creds_p:
            return {
                "status": "error",
                "error": "credentials.json is not placed in career_vault/.",
                "notice": "Place credentials.json in career_vault/ to authenticate."
            }

        gmail_service, calendar_service = get_authenticated_services(interactive=force_interactive)
        if not gmail_service:
            return {
                "status": "needs_auth",
                "notice": "Authentication required. Click 'Connect Gmail' or run interactive authorization."
            }

        label_name = get_career_setting("gmail_label", "Vega-Career")
        calendar_enabled = get_career_setting("calendar_sync_enabled", "0") == "1"

        # 1. Resolve label ID
        labels_resp = gmail_service.users().labels().list(userId="me").execute()
        user_labels = labels_resp.get("labels", [])
        target_label_id = None
        for lbl in user_labels:
            if lbl.get("name", "").lower() == label_name.lower():
                target_label_id = lbl.get("id")
                break

        if not target_label_id:
            notice = f"Label '{label_name}' does not exist in your Gmail account yet. Create it in Gmail to sync."
            set_career_setting("last_sync_status", notice)
            return {
                "status": "label_not_found",
                "label": label_name,
                "notice": notice,
                "synced_count": 0
            }

        # 2. List messages strictly inside target label
        msgs_resp = gmail_service.users().messages().list(
            userId="me",
            labelIds=[target_label_id],
            maxResults=30
        ).execute()
        messages = msgs_resp.get("messages", [])

        synced_count = 0
        alerts_ingested = 0
        status_updates = 0
        deadlines_scheduled = 0

        for m_stub in messages:
            msg_id = m_stub["id"]
            if is_gmail_message_synced(msg_id):
                continue

            try:
                msg_full = gmail_service.users().messages().get(
                    userId="me",
                    id=msg_id,
                    format="full"
                ).execute()

                parsed = parse_email_message(msg_full)
                action_notes = []

                # A. Handle Job Alerts
                if parsed["is_job_alert"] and parsed["job_urls"]:
                    for j_url in parsed["job_urls"][:5]:  # limit to top 5 per email
                        source_label = f"Gmail Alert ({parsed['detected_company'] or 'Feed'})"
                        audit = audit_job_listing(parsed["body_text"], company=parsed["detected_company"], url=j_url)
                        risk = audit.get("risk_level", "LOW_RISK")

                        new_job = {
                            "source": source_label,
                            "company": parsed["detected_company"] or "Target Company",
                            "role": parsed["subject"][:60],
                            "location": "India / Remote",
                            "url": j_url,
                            "raw_text": parsed["body_text"],
                            "scam_risk": risk,
                            "scam_score": audit.get("scam_score", 0),
                            "scam_flags": audit.get("red_flags", []),
                            "status": "Ignored_Scam" if risk == "HIGH_RISK" else "New"
                        }
                        if risk != "HIGH_RISK":
                            match = match_job_and_tailor(parsed["body_text"], company=new_job["company"], role=new_job["role"])
                            new_job["match_score"] = match.get("match_score", 0)
                            new_job["matched_skills"] = match.get("matched_skills", [])
                            new_job["missing_skills"] = match.get("missing_skills", [])
                            new_job["cover_note"] = match.get("cover_note", "")

                        save_job(new_job)
                        alerts_ingested += 1
                    action_notes.append(f"Ingested {len(parsed['job_urls'])} alert URLs")

                # B. Handle Application Status Update
                if parsed["detected_status"] and parsed["detected_company"]:
                    existing_job = find_job_by_company(parsed["detected_company"])
                    if existing_job:
                        update_job_status(
                            existing_job["id"],
                            parsed["detected_status"],
                            notes=f"Auto-updated from Gmail: {parsed['subject'][:80]}"
                        )
                        status_updates += 1
                        action_notes.append(f"Updated {parsed['detected_company']} to {parsed['detected_status']}")

                # C. Handle Deadlines & Reminders
                if parsed["detected_deadline"]:
                    reminder_text = f"OA/Interview Deadline: {parsed['detected_company'] or 'Role'} — {parsed['subject'][:50]}"
                    # Default reminder in 2 days at 10:00 AM if parse fails
                    now = datetime.now()
                    due_dt = now + timedelta(days=2)
                    due_dt = due_dt.replace(hour=10, minute=0, second=0)

                    added = add_career_reminder(due_dt, reminder_text)
                    if added:
                        deadlines_scheduled += 1
                        action_notes.append("Added to Vega Reminders")

                    if calendar_enabled and calendar_service:
                        create_calendar_event(
                            calendar_service,
                            summary=reminder_text,
                            description=f"Automated career deadline synced from Gmail.\n\nSubject: {parsed['subject']}\nFrom: {parsed['sender']}",
                            start_dt=due_dt
                        )
                        action_notes.append("Added to Google Calendar")

                action_summary = "; ".join(action_notes) if action_notes else "Processed (No action needed)"
                record_gmail_synced_message(
                    message_id=msg_id,
                    thread_id=msg_full.get("threadId", ""),
                    subject=parsed["subject"],
                    sender=parsed["sender"],
                    received_at=parsed["date_str"],
                    message_type="job_alert" if parsed["is_job_alert"] else ("application_update" if parsed["detected_status"] else "other"),
                    action_taken=action_summary
                )
                synced_count += 1
            except Exception as item_err:
                logger.warning(f"Error processing email message {msg_id}: {item_err}")
                continue

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        set_career_setting("last_gmail_sync", now_str)
        result_notice = f"Synced {synced_count} emails: {alerts_ingested} alerts ingested, {status_updates} statuses updated, {deadlines_scheduled} deadlines scheduled."
        set_career_setting("last_sync_status", result_notice)

        return {
            "status": "ok",
            "synced_count": synced_count,
            "alerts_ingested": alerts_ingested,
            "status_updates": status_updates,
            "deadlines_scheduled": deadlines_scheduled,
            "last_sync": now_str,
            "notice": result_notice
        }
    except Exception as exc:
        logger.error(f"Gmail sync failed: {exc}")
        err_msg = f"Sync failed: {str(exc)}"
        set_career_setting("last_sync_status", err_msg)
        return {
            "status": "error",
            "error": str(exc),
            "notice": err_msg
        }
    finally:
        _gmail_sync_lock.release()
