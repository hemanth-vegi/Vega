"""
Bidirectional PDF Engine - Reader Module for Vega v2.2.
Uses PyMuPDF (fitz) to extract page-by-page reading-order text,
embedded clickable hyperlinks, encrypted PDF handling, scanned/image-only detection,
and strict character caps.
"""

import os
import re
from typing import Dict, Any, List, Union, Tuple

try:
    import pymupdf as fitz
except ImportError:
    try:
        import fitz
    except ImportError:
        fitz = None


def extract_pdf(source: Union[str, bytes], max_chars: int = 30000) -> Dict[str, Any]:
    """
    Extracts text, page count, and clickable hyperlinks from a PDF file or bytes buffer.

    Returns:
        dict: {
            "text": str,
            "pages": int,
            "links": list of {"text": str, "uri": str, "page": int},
            "is_scanned": bool,
            "is_encrypted": bool,
            "truncated": bool,
            "char_count": int,
            "error": Optional[str]
        }
    """
    if fitz is None:
        return {
            "text": "",
            "pages": 0,
            "links": [],
            "is_scanned": False,
            "is_encrypted": False,
            "truncated": False,
            "char_count": 0,
            "error": "PyMuPDF is not installed. Run: pip install pymupdf"
        }

    doc = None
    try:
        if isinstance(source, bytes):
            doc = fitz.open(stream=source, filetype="pdf")
        elif isinstance(source, str):
            if not os.path.exists(source):
                return {
                    "text": "",
                    "pages": 0,
                    "links": [],
                    "is_scanned": False,
                    "is_encrypted": False,
                    "truncated": False,
                    "char_count": 0,
                    "error": f"PDF file not found: {source}"
                }
            doc = fitz.open(source)
        else:
            return {
                "text": "",
                "pages": 0,
                "links": [],
                "is_scanned": False,
                "is_encrypted": False,
                "truncated": False,
                "char_count": 0,
                "error": f"Invalid PDF source type: {type(source)}"
            }

        # Check encryption
        if doc.is_encrypted:
            # Try authenticating with empty password
            auth_ok = doc.authenticate("")
            if not auth_ok:
                return {
                    "text": "",
                    "pages": len(doc),
                    "links": [],
                    "is_scanned": False,
                    "is_encrypted": True,
                    "truncated": False,
                    "char_count": 0,
                    "error": "PDF is encrypted with a password and cannot be read without credentials."
                }

        page_count = len(doc)
        pages_text: List[str] = []
        all_links: List[Dict[str, Any]] = []
        total_chars = 0
        truncated = False

        for page_idx in range(page_count):
            page = doc[page_idx]

            # 1. Extract reading-order text
            page_raw_text = page.get_text("text") or ""
            clean_page_text = page_raw_text.strip()

            # 2. Extract embedded clickable hyperlinks
            links = page.get_links()
            for l in links:
                uri = l.get("uri")
                if uri:
                    anchor = ""
                    rect = l.get("from")
                    if rect:
                        try:
                            anchor = page.get_textbox(rect).strip()
                        except Exception:
                            anchor = ""
                    all_links.append({
                        "text": anchor or uri,
                        "uri": uri,
                        "page": page_idx + 1
                    })

            # Check char limit
            if total_chars + len(clean_page_text) > max_chars:
                allowed = max_chars - total_chars
                if allowed > 0:
                    pages_text.append(f"[Page {page_idx + 1}]\n" + clean_page_text[:allowed])
                truncated = True
                total_chars = max_chars
                break
            else:
                pages_text.append(f"[Page {page_idx + 1}]\n" + clean_page_text)
                total_chars += len(clean_page_text)

        full_text = "\n\n".join(pages_text)
        if truncated:
            full_text += f"\n\n[Notice: Content truncated at {max_chars} character limit]"

        # 3. Detect scanned / image-only PDFs
        # If total text characters across non-empty document < 40 chars
        is_scanned = (page_count > 0 and len(re.sub(r"\[Page \d+\]|\s+", "", full_text)) < 40)

        # Remove duplicate links while preserving order
        unique_links = []
        seen_uris = set()
        for link in all_links:
            key = (link["uri"], link["text"])
            if key not in seen_uris:
                seen_uris.add(key)
                unique_links.append(link)

        return {
            "text": full_text,
            "pages": page_count,
            "links": unique_links,
            "is_scanned": is_scanned,
            "is_encrypted": False,
            "truncated": truncated,
            "char_count": len(full_text),
            "error": None
        }

    except Exception as exc:
        return {
            "text": "",
            "pages": 0,
            "links": [],
            "is_scanned": False,
            "is_encrypted": False,
            "truncated": False,
            "char_count": 0,
            "error": f"Failed to parse PDF: {exc}"
        }
    finally:
        if doc is not None:
            try:
                doc.close()
            except Exception:
                pass


def extract_text_and_links(source: Union[str, bytes]) -> Tuple[str, List[Dict[str, str]]]:
    """Convenience helper returning text and links tuple."""
    res = extract_pdf(source)
    return res.get("text", ""), res.get("links", [])
