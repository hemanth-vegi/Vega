"""
Universal Job Description URL & Text Fetcher for Vega v2.2.
Fetches external job postings, strips ads/scripts/nav, handles anti-bot/403 walls gracefully,
and supports direct text or uploaded PDF JD parsing.
"""

import re
from typing import Dict, Any, Optional
import httpx
from bs4 import BeautifulSoup

try:
    from tools.pdf_reader import extract_pdf
except ImportError:
    from tools.pdf_reader import extract_pdf


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def clean_html_content(html: str) -> str:
    """Strips scripts, styles, headers, navigation, footers and returns readable text."""
    soup = BeautifulSoup(html, "html.parser")
    # Remove noise elements
    for element in soup(["script", "style", "nav", "header", "footer", "aside", "svg", "noscript", "iframe"]):
        element.decompose()

    # Extract clean text
    text = soup.get_text(separator="\n")
    # Clean whitespace
    lines = [line.strip() for line in text.splitlines()]
    clean_text = "\n".join(l for l in lines if l)
    # Deduplicate excessive newlines
    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text)
    return clean_text.strip()


def fetch_job_url(url: str, timeout: float = 12.0) -> Dict[str, Any]:
    """
    Fetches job posting text from an HTTP/HTTPS URL.
    Returns clean text, or a graceful prompt to paste the JD if access is blocked.
    """
    clean_url = (url or "").strip()
    if not clean_url:
        return {"error": "Empty URL provided.", "text": "", "url": ""}

    if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
        clean_url = "https://" + clean_url

    try:
        with httpx.Client(headers=HEADERS, follow_redirects=True, timeout=timeout) as client:
            resp = client.get(clean_url)
            if resp.status_code == 200:
                text = clean_html_content(resp.text)
                if len(text) < 60:
                    return {
                        "url": clean_url,
                        "text": "",
                        "error": "Fetched page content was too short or empty. Please copy and paste the job description text directly."
                    }
                return {
                    "url": clean_url,
                    "text": text[:25000],
                    "error": None
                }
            elif resp.status_code in (401, 403, 429, 999):
                return {
                    "url": clean_url,
                    "text": "",
                    "blocked": True,
                    "status_code": resp.status_code,
                    "error": (
                        f"Direct access to this site returned HTTP {resp.status_code} (anti-bot / login wall). "
                        "Please use the 1-Click Send to Vega Brave extension or copy-paste the JD text directly below."
                    )
                }
            else:
                return {
                    "url": clean_url,
                    "text": "",
                    "error": f"Failed to fetch job URL (HTTP {resp.status_code}). Please paste the job description text."
                }
    except Exception as exc:
        return {
            "url": clean_url,
            "text": "",
            "error": f"Network error fetching URL ({exc}). Please copy and paste the job description text directly."
        }


def parse_job_input(url: Optional[str] = None, text: Optional[str] = None, pdf_bytes: Optional[bytes] = None) -> Dict[str, Any]:
    """
    Universal ingest for URL, pasted text, or uploaded PDF.
    Returns normalized dictionary with clean text and source URL.
    """
    if pdf_bytes:
        res = extract_pdf(pdf_bytes)
        if res.get("error"):
            return {"error": res["error"], "text": "", "url": ""}
        return {
            "url": url or "uploaded_jd.pdf",
            "text": res.get("text", ""),
            "error": None
        }

    if text and text.strip():
        return {
            "url": url or "direct_paste",
            "text": text.strip(),
            "error": None
        }

    if url and url.strip():
        return fetch_job_url(url)

    return {"error": "No job URL, text, or PDF provided.", "text": "", "url": ""}
