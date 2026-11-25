"""
Multi-Platform Job Discovery Connectors for Vega v2.2.
Fetches fresher & internship roles from public ATS feeds (Greenhouse, Lever, Ashby)
and open remote developer feeds (Remotive, RemoteOK) with strict fault isolation.
Zero login scraping / zero bot submission strictly enforced.
"""

import os
import re
import threading
import logging
from typing import Dict, Any, List
import httpx
from bs4 import BeautifulSoup
from career.db import get_watchlist, DEFAULT_WATCHLIST_COMPANIES

__all__ = [
    "DEFAULT_WATCHLIST_COMPANIES",
    "get_watchlist",
    "is_role_relevant",
    "strip_html",
    "parse_ashby_payload",
    "parse_arbeitnow_payload",
    "fetch_greenhouse_board",
    "fetch_lever_postings",
    "fetch_ashby_board",
    "fetch_remotive_jobs",
    "fetch_remoteok_jobs",
    "fetch_arbeitnow_jobs",
    "fetch_adzuna_india_jobs",
    "is_scan_running",
    "scan_all_feeds",
]

logger = logging.getLogger("vega.career.connectors")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) VegaCareerScanner/2.2",
    "Accept": "application/json, text/plain, */*",
}

RELEVANT_KEYWORDS = [
    "intern", "internship", "fresher", "junior", "graduate",
    "devops", "cloud", "python", "sde", "software engineer",
    "automation", "linux", "systems", "associate", "developer",
    "sre", "platform", "entry"
]


def is_role_relevant(title: str, location_or_text: str = "") -> bool:
    """Checks whether a job title matches target tracks (DevOps, Cloud, SRE, Linux, Python, Platform, Intern, Junior, Entry)."""
    t = (title or "").lower()
    extra = (location_or_text or "").lower()
    combined = f"{t} {extra}"

    # Prioritize target roles
    has_keyword = any(k in combined for k in RELEVANT_KEYWORDS)
    # Exclude senior / lead / principal / staff unless it's a false match
    is_senior = any(s in t for s in ["senior", "sr.", "principal", "staff", "director", "vp", "lead "])
    return has_keyword and not is_senior


def strip_html(html_str: str) -> str:
    """Helper to convert HTML job description into plain text."""
    if not html_str:
        return ""
    try:
        soup = BeautifulSoup(html_str, "html.parser")
        return soup.get_text(separator="\n").strip()
    except Exception as exc:
        logger.debug(f"strip_html parser fallback triggered: {exc}")
        return re.sub(r"<[^>]+>", " ", html_str).strip()


def fetch_greenhouse_board(board_slug: str, company_name: str, timeout: float = 8.0) -> List[Dict[str, Any]]:
    """Fetches public postings from Greenhouse: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs?content=true"
    jobs = []
    try:
        with httpx.Client(headers=HEADERS, timeout=timeout) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            raw_jobs = data.get("jobs", [])
            for rj in raw_jobs:
                title = rj.get("title", "")
                loc_obj = rj.get("location", {})
                location = loc_obj.get("name", "Remote / Various") if isinstance(loc_obj, dict) else str(loc_obj)

                if is_role_relevant(title, location):
                    apply_url = rj.get("absolute_url") or f"https://boards.greenhouse.io/{board_slug}/jobs/{rj.get('id')}"
                    content_html = rj.get("content", "")
                    clean_jd = strip_html(content_html)
                    jobs.append({
                        "source": f"Greenhouse ({company_name})",
                        "company": company_name,
                        "role": title,
                        "location": location,
                        "url": apply_url,
                        "raw_text": clean_jd[:15000]
                    })
    except Exception as exc:
        logger.debug(f"Greenhouse fetch error for {company_name}: {exc}")
    return jobs


def fetch_lever_postings(board_slug: str, company_name: str, timeout: float = 8.0) -> List[Dict[str, Any]]:
    """Fetches public postings from Lever: https://api.lever.co/v0/postings/{slug}?mode=json."""
    url = f"https://api.lever.co/v0/postings/{board_slug}?mode=json"
    jobs = []
    try:
        with httpx.Client(headers=HEADERS, timeout=timeout) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []
            raw_jobs = resp.json()
            if not isinstance(raw_jobs, list):
                return []
            for rj in raw_jobs:
                title = rj.get("text", "")
                categories = rj.get("categories", {})
                location = categories.get("location", "Remote / Various") if isinstance(categories, dict) else "Various"

                if is_role_relevant(title, location):
                    apply_url = rj.get("hostedUrl") or rj.get("applyUrl") or f"https://jobs.lever.co/{board_slug}"
                    description = rj.get("descriptionPlain") or strip_html(rj.get("description", ""))
                    jobs.append({
                        "source": f"Lever ({company_name})",
                        "company": company_name,
                        "role": title,
                        "location": location,
                        "url": apply_url,
                        "raw_text": description[:15000]
                    })
    except Exception as e:
        logger.warning(f"Lever fetch error for {company_name} ({board_slug}): {e}")
    return jobs


def parse_ashby_payload(data: Dict[str, Any], board_slug: str, company_name: str) -> List[Dict[str, Any]]:
    """
    Parses raw Ashby JSON response into standardized job objects.
    Useful for unit testing with zero network overhead.
    """
    jobs = []
    if not isinstance(data, dict):
        return jobs
    raw_jobs = data.get("jobs", [])
    if not isinstance(raw_jobs, list):
        return jobs

    for rj in raw_jobs:
        title = rj.get("title", "")
        loc = rj.get("location")
        if not loc:
            addr = rj.get("address", {})
            if isinstance(addr, dict):
                loc = addr.get("postalAddress", {}).get("addressLocality")
        if not loc:
            loc = "Remote" if rj.get("isRemote") else "Various"

        if is_role_relevant(title, str(loc)):
            apply_url = rj.get("applyUrl") or rj.get("jobUrl") or f"https://jobs.ashbyhq.com/{board_slug}/{rj.get('id', '')}"
            description = rj.get("descriptionPlain") or strip_html(rj.get("descriptionHtml", ""))
            emp_type = rj.get("employmentType", "")
            is_remote = bool(rj.get("isRemote") or (rj.get("workplaceType") or "").lower() == "remote")

            jobs.append({
                "source": f"Ashby ({company_name})",
                "company": company_name,
                "role": title,
                "location": str(loc),
                "url": apply_url,
                "raw_text": description[:15000],
                "employment_type": emp_type,
                "is_remote": is_remote,
            })
    return jobs


def fetch_ashby_board(board_slug: str, company_name: str, timeout: float = 8.0) -> List[Dict[str, Any]]:
    """Fetches public postings from Ashby: https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board_slug}?includeCompensation=true"
    try:
        with httpx.Client(headers=HEADERS, timeout=timeout) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            return parse_ashby_payload(data, board_slug, company_name)
    except Exception as e:
        logger.warning(f"Ashby fetch error for {company_name} ({board_slug}): {e}")
        return []


def fetch_remotive_jobs(timeout: float = 8.0) -> List[Dict[str, Any]]:
    """Fetches public remote developer and DevOps roles from Remotive API."""
    url = "https://remotive.com/api/remote-jobs?category=devops-sysadmin&limit=20"
    jobs = []
    try:
        with httpx.Client(headers=HEADERS, timeout=timeout) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            for rj in data.get("jobs", []):
                title = rj.get("title", "")
                loc = rj.get("candidate_required_location") or "Remote"
                if is_role_relevant(title, loc):
                    company = rj.get("company_name", "Tech Company")
                    desc = strip_html(rj.get("description", ""))
                    jobs.append({
                        "source": "Remotive (DevOps)",
                        "company": company,
                        "role": title,
                        "location": loc,
                        "url": rj.get("url", ""),
                        "raw_text": desc[:15000]
                    })
    except Exception as e:
        logger.warning(f"Remotive fetch error: {e}")
    return jobs


def fetch_remoteok_jobs(timeout: float = 8.0) -> List[Dict[str, Any]]:
    """Fetches open remote jobs from RemoteOK API."""
    url = "https://remoteok.com/api?tag=devops"
    jobs = []
    try:
        with httpx.Client(headers=HEADERS, timeout=timeout) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            if not isinstance(data, list):
                return []
            for rj in data:
                if not isinstance(rj, dict) or not rj.get("position"):
                    continue
                title = rj.get("position", "")
                loc = rj.get("location") or "Remote"
                if is_role_relevant(title, loc):
                    company = rj.get("company", "Tech Company")
                    desc = strip_html(rj.get("description", ""))
                    jobs.append({
                        "source": "RemoteOK (DevOps)",
                        "company": company,
                        "role": title,
                        "location": loc,
                        "url": rj.get("url") or f"https://remoteok.com/remote-jobs/{rj.get('id', '')}",
                        "raw_text": desc[:15000]
                    })
    except Exception as e:
        logger.warning(f"RemoteOK fetch error: {e}")
    return jobs


def parse_arbeitnow_payload(data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Parses raw Arbeitnow JSON response (https://www.arbeitnow.com/api/job-board-api)
    into standardized job objects. Maps title, company_name, location, remote, url,
    description (cleanly stripped HTML), tags, and created_at.
    Useful for hermetic unit testing.
    """
    jobs = []
    if not isinstance(data, dict):
        return jobs
    items = data.get("data", [])
    if not isinstance(items, list):
        return jobs

    for rj in items:
        if not isinstance(rj, dict):
            continue
        title = (rj.get("title") or "").strip()
        if not title:
            continue

        tags = rj.get("tags") or []
        tags_str = " ".join(tags) if isinstance(tags, list) else str(tags)
        loc = rj.get("location") or ("Remote" if rj.get("remote") else "Worldwide")

        # Check relevance against title + tags
        check_text = f"{title} {tags_str}"
        if not is_role_relevant(title, check_text):
            continue

        company = rj.get("company_name") or "Tech Company"
        raw_desc = rj.get("description", "")
        clean_desc = strip_html(raw_desc)
        is_remote = bool(rj.get("remote", False))
        url = rj.get("url", "")
        created_at = rj.get("created_at")

        jobs.append({
            "source": f"Arbeitnow ({company})",
            "company": company,
            "role": title,
            "location": str(loc),
            "is_remote": is_remote,
            "url": url,
            "raw_text": clean_desc[:15000],
            "tags": tags if isinstance(tags, list) else [],
            "created_at": created_at,
        })
    return jobs


def fetch_arbeitnow_jobs(timeout: float = 8.0) -> List[Dict[str, Any]]:
    """
    Fetches open tech/DevOps/remote jobs from Arbeitnow public API:
    https://www.arbeitnow.com/api/job-board-api (Zero Auth)
    """
    url = "https://www.arbeitnow.com/api/job-board-api"
    try:
        with httpx.Client(headers=HEADERS, timeout=timeout) as client:
            resp = client.get(url)
            if resp.status_code != 200:
                logger.warning(f"Arbeitnow returned HTTP {resp.status_code}")
                return []
            data = resp.json()
            return parse_arbeitnow_payload(data)
    except Exception as exc:
        logger.warning(f"Arbeitnow fetch error: {exc}")
        return []


def fetch_adzuna_india_jobs(query: str = "DevOps intern", location: str = "India", timeout: float = 8.0) -> Dict[str, Any]:
    """
    Queries Adzuna India API: https://api.adzuna.com/v1/api/jobs/in/search/1
    Requires ADZUNA_APP_ID and ADZUNA_APP_KEY in .env.
    NEVER auto-called in background; only on manual UI request.
    """
    app_id = os.getenv("ADZUNA_APP_ID", "").strip()
    app_key = os.getenv("ADZUNA_APP_KEY", "").strip()

    if not app_id or not app_key:
        return {
            "jobs": [],
            "notice": "Adzuna keys not configured. (Add ADZUNA_APP_ID and ADZUNA_APP_KEY to .env for live Adzuna search)"
        }

    url = "https://api.adzuna.com/v1/api/jobs/in/search/1"
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "results_per_page": 10,
        "what": query,
        "where": location,
        "content-type": "application/json"
    }

    jobs = []
    try:
        with httpx.Client(headers=HEADERS, timeout=timeout) as client:
            resp = client.get(url, params=params)
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", [])
                for item in results:
                    title = item.get("title", "")
                    company_obj = item.get("company", {})
                    company = company_obj.get("display_name", "Unknown Company") if isinstance(company_obj, dict) else str(company_obj)
                    loc_obj = item.get("location", {})
                    loc_name = loc_obj.get("display_name", location) if isinstance(loc_obj, dict) else location
                    apply_url = item.get("redirect_url") or item.get("url", "")
                    description = item.get("description", "")

                    jobs.append({
                        "source": "Adzuna India",
                        "company": company,
                        "role": title,
                        "location": loc_name,
                        "url": apply_url,
                        "raw_text": description[:10000]
                    })
                return {"jobs": jobs, "notice": f"Found {len(jobs)} jobs via Adzuna India."}
            else:
                return {"jobs": [], "notice": f"Adzuna returned HTTP {resp.status_code}."}
    except Exception as exc:
        return {"jobs": [], "notice": f"Adzuna fetch error: {exc}"}


# Concurrency Lock preventing overlapping background/manual scans
_scan_lock = threading.Lock()


def is_scan_running() -> bool:
    """Returns True if a feed scan is currently in progress."""
    return _scan_lock.locked()


def scan_all_feeds(include_adzuna: bool = False) -> Dict[str, Any]:
    """
    Orchestrates discovery across all enabled watchlist companies (Greenhouse, Lever, Ashby)
    and open developer feeds (Remotive, RemoteOK) with strict per-board fault isolation.
    Only queries Adzuna if include_adzuna=True (Quota Protection).
    Uses non-blocking concurrency lock to avoid duplicate scans.
    """
    acquired = _scan_lock.acquire(blocking=False)
    if not acquired:
        return {
            "status": "already_running",
            "jobs": [],
            "total_discovered": 0,
            "source_breakdown": {},
            "notice": "A feed scan is already running in the background."
        }

    try:
        from llm.engine import is_offline_or_low_data
        from career.db import get_jobs
        restricted, net_mode = is_offline_or_low_data()
        if restricted:
            cached_jobs = get_jobs(limit=100)
            return {
                "status": "cached",
                "jobs": cached_jobs,
                "total_discovered": len(cached_jobs),
                "source_breakdown": {"cached_sqlite": len(cached_jobs)},
                "network_mode": net_mode,
                "notice": f"Remote feed scan skipped ({net_mode} active). Serving {len(cached_jobs)} cached jobs from SQLite."
            }

        watchlist = get_watchlist()
        all_jobs: List[Dict[str, Any]] = []
        stats: Dict[str, int] = {}

        for comp in watchlist:
            if not comp.get("enabled", 1):
                continue
            c_name = comp.get("company_name", "")
            ats = comp.get("ats_type", "").lower()
            slug = comp.get("board_slug", "")

            found = []
            try:
                if ats == "greenhouse":
                    found = fetch_greenhouse_board(slug, c_name)
                elif ats == "lever":
                    found = fetch_lever_postings(slug, c_name)
                elif ats == "ashby":
                    found = fetch_ashby_board(slug, c_name)
            except Exception as exc:
                logger.warning(f"Error fetching {c_name} ({ats}): {exc}")
                found = []

            if found:
                all_jobs.extend(found)
                stats[c_name] = len(found)

        # Open public boards (Remotive & RemoteOK)
        try:
            remotive_jobs = fetch_remotive_jobs()
            if remotive_jobs:
                all_jobs.extend(remotive_jobs)
                stats["Remotive"] = len(remotive_jobs)
        except Exception as exc:
            logger.warning(f"Remotive error: {exc}")

        try:
            remoteok_jobs = fetch_remoteok_jobs()
            if remoteok_jobs:
                all_jobs.extend(remoteok_jobs)
                stats["RemoteOK"] = len(remoteok_jobs)
        except Exception as exc:
            logger.warning(f"RemoteOK error: {exc}")

        try:
            arbeitnow_jobs = fetch_arbeitnow_jobs()
            if arbeitnow_jobs:
                all_jobs.extend(arbeitnow_jobs)
                stats["Arbeitnow"] = len(arbeitnow_jobs)
        except Exception as exc:
            logger.warning(f"Arbeitnow error: {exc}")

        # Optional Adzuna scan (STRICTLY when include_adzuna=True)
        adzuna_notice = ""
        if include_adzuna:
            try:
                adzuna_res = fetch_adzuna_india_jobs("Python DevOps Intern", "India")
                adzuna_jobs = adzuna_res.get("jobs", [])
                if adzuna_jobs:
                    all_jobs.extend(adzuna_jobs)
                    stats["Adzuna India"] = len(adzuna_jobs)
                adzuna_notice = adzuna_res.get("notice", "")
            except Exception as exc:
                logger.warning(f"Adzuna scan error: {exc}")
                adzuna_notice = f"Adzuna error: {exc}"

        return {
            "status": "ok",
            "jobs": all_jobs,
            "total_discovered": len(all_jobs),
            "source_breakdown": stats,
            "notice": adzuna_notice or f"Scanned {len(watchlist)} companies and open feeds."
        }
    finally:
        _scan_lock.release()
