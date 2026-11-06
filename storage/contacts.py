"""
Contact management and fuzzy matching for Vega 2.0.
Supports Google Contacts CSV import, JSON contacts, and fuzzy phone/name resolution.
"""

import os
import csv
import json
import re
import difflib
from storage.db import get_db, init_db

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def normalize_phone(phone: str) -> str:
    """Strip whitespace, dashes, and parentheses from a phone number."""
    if not phone:
        return ""
    cleaned = re.sub(r"[^\d+]", "", phone.strip())
    # If standard 10-digit Indian number without country code, prepend +91
    if len(cleaned) == 10 and not cleaned.startswith("+"):
        cleaned = "+91" + cleaned
    return cleaned


def add_contact(name: str, phone: str, email: str = "", notes: str = ""):
    """Add or update a contact in the database."""
    init_db()
    norm_name = name.strip().lower()
    norm_phone = normalize_phone(phone)
    with get_db() as conn:
        conn.execute("""
            INSERT INTO contacts (name, normalized_name, phone, email, notes)
            VALUES (?, ?, ?, ?, ?)
        """, (name.strip(), norm_name, norm_phone, email.strip(), notes.strip()))


def find_contact_fuzzy(query: str, cutoff: float = 0.5):
    """
    Fuzzy match a contact name against the database.
    Returns (best_match_contact_dict, score) or (None, 0.0).
    """
    init_db()
    norm_query = query.strip().lower()
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM contacts").fetchall()

    if not rows:
        return None, 0.0

    contacts_map = {row["normalized_name"]: dict(row) for row in rows}
    names = list(contacts_map.keys())

    # Exact substring match first
    for n in names:
        if norm_query in n or n in norm_query:
            return contacts_map[n], 1.0

    matches = difflib.get_close_matches(norm_query, names, n=1, cutoff=cutoff)
    if matches:
        best_name = matches[0]
        # Calculate approximate similarity score
        ratio = difflib.SequenceMatcher(None, norm_query, best_name).ratio()
        return contacts_map[best_name], ratio

    return None, 0.0


def import_contacts_csv(csv_path: str) -> int:
    """Imports contacts from Google Contacts or standard CSV export."""
    init_db()
    imported = 0
    if not os.path.isfile(csv_path):
        return 0

    with open(csv_path, "r", encoding="utf-8", errors="ignore") as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Common CSV column headers for names and phones
            name = row.get("Name") or row.get("Full Name") or ""
            if not name:
                first = row.get("First Name") or row.get("Given Name") or ""
                last = row.get("Last Name") or row.get("Family Name") or ""
                name = f"{first} {last}".strip()
            
            phone = (
                row.get("Phone 1 - Value") or 
                row.get("Phone") or 
                row.get("Mobile") or 
                row.get("Phone Number") or ""
            )
            email = (
                row.get("E-mail 1 - Value") or 
                row.get("Email") or 
                row.get("E-mail") or ""
            )

            if name and phone:
                add_contact(name, phone, email)
                imported += 1

    return imported


def list_contacts(limit: int = 100):
    """Retrieve all contacts sorted by name."""
    init_db()
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM contacts ORDER BY name ASC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

