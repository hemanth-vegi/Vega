"""
Vega v2.2 — Lecture & Study Capture Engine.
Transforms lecture recordings, AI meeting notes, and technical articles into:
1. Structured concept summaries & key takeaways.
2. Q&A flashcards stored in SQLite (study_flashcards table).
3. Actionable deadlines and study milestones scheduled into Vega Reminders.
"""

import os
import re
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional

logger = logging.getLogger("vega.lab.study_capture")

from storage.db import get_db, init_db
from llm.engine import generate_for_task
from career.gmail_sync import add_career_reminder, DEADLINE_REGEX

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def process_study_notes(text: str, topic: str = "General") -> Dict[str, Any]:
    """
    Processes lecture notes or audio transcripts:
    - Extracts structured summary.
    - Generates Q&A flashcards and saves them to SQLite.
    - Identifies assignment/study deadlines and schedules reminders.
    """
    if not text or not text.strip():
        return {
            "status": "error",
            "error": "Empty text provided for study notes.",
            "summary": "",
            "flashcards": [],
            "deadlines": []
        }

    clean_text = text.strip()
    topic = topic.strip() if topic else "General"

    # LLM Prompt to extract summary, flashcards, and deadlines
    prompt = f"""You are Vega's Academic & Technical Study Assistant.
Analyze the following lecture notes or technical transcript on the topic '{topic}'.
Extract three things in valid JSON format:
1. "summary": A concise bullet-point summary of the core concepts, principles, and key takeaways (3-6 bullet points).
2. "flashcards": A list of 3 to 6 high-yield technical Q&A flashcards to test recall.
   Each flashcard MUST have:
   - "question": Clear, direct question testing a key concept.
   - "answer": Precise, correct answer.
   - "explanation": Brief explanation or context (1-2 sentences).
   - "difficulty": "easy", "medium", or "hard".
3. "deadlines": Any assignments, homework, project submission dates, or exam dates mentioned.
   Each deadline item MUST have:
   - "text": Description of the task or milestone.
   - "days_from_now": Integer estimate of days until due (e.g. 3 for next week or 2 for upcoming, default 3).

Return ONLY the raw JSON object with keys "summary", "flashcards", "deadlines". No markdown fences or commentary.

TEXT TO ANALYZE:
{clean_text[:6000]}
"""

    summary_bullets: List[str] = []
    flashcards_data: List[Dict[str, Any]] = []
    deadlines_data: List[Dict[str, Any]] = []

    try:
        llm_res = generate_for_task(prompt=prompt, task_type="reasoning")
        raw_response = llm_res.get("content", "") if isinstance(llm_res, dict) else str(llm_res)
        parsed = None
        # Try cleaning JSON fences
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_response.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))

        if isinstance(parsed, dict):
            summary_raw = parsed.get("summary")
            if isinstance(summary_raw, list):
                summary_bullets = [str(b).strip() for b in summary_raw if str(b).strip()]
            elif isinstance(summary_raw, str):
                summary_bullets = [b.strip() for b in summary_raw.split("\n") if b.strip()]

            if isinstance(parsed.get("flashcards"), list):
                flashcards_data = parsed.get("flashcards", [])

            if isinstance(parsed.get("deadlines"), list):
                deadlines_data = parsed.get("deadlines", [])
    except Exception as exc:
        logger.warning(f"LLM study capture extraction error: {exc}")

    # Fallback heuristics if LLM failed or returned empty
    if not summary_bullets:
        lines = [line.strip("- *").strip() for line in clean_text.splitlines() if line.strip()]
        summary_bullets = lines[:5] if lines else ["Review lecture notes and key technical principles."]

    if not flashcards_data:
        # Heuristic flashcard generation based on topic and content lines
        sentences = [s.strip() for s in re.split(r"[.\n]", clean_text) if len(s.strip()) > 20]
        if sentences:
            flashcards_data.append({
                "question": f"What is the primary objective of {topic}?",
                "answer": sentences[0],
                "explanation": "Core definition from the lecture notes.",
                "difficulty": "medium"
            })
        if len(sentences) > 1:
            flashcards_data.append({
                "question": f"Key technical concept mentioned in {topic}:",
                "answer": sentences[1],
                "explanation": "Foundational takeaway.",
                "difficulty": "medium"
            })

    # Heuristic regex deadline check if not captured by LLM
    if not deadlines_data:
        deadline_match = DEADLINE_REGEX.search(clean_text)
        if deadline_match:
            deadlines_data.append({
                "text": f"Study Task: {topic} — {deadline_match.group(1).strip()}",
                "days_from_now": 2
            })

    # 1. Save Flashcards to SQLite
    init_db()
    saved_cards: List[Dict[str, Any]] = []
    with get_db() as conn:
        for fc in flashcards_data:
            q = str(fc.get("question", "")).strip()
            a = str(fc.get("answer", "")).strip()
            exp = str(fc.get("explanation", "")).strip()
            diff = str(fc.get("difficulty", "medium")).lower()
            if diff not in ["easy", "medium", "hard"]:
                diff = "medium"

            if not q or not a:
                continue

            cur = conn.execute("""
                INSERT INTO study_flashcards (topic, question, answer, explanation, difficulty)
                VALUES (?, ?, ?, ?, ?)
            """, (topic, q, a, exp, diff))
            saved_cards.append({
                "id": cur.lastrowid,
                "topic": topic,
                "question": q,
                "answer": a,
                "explanation": exp,
                "difficulty": diff,
                "mastered": 0,
                "review_count": 0
            })

    # 2. Schedule Deadlines to reminders.json
    scheduled_reminders: List[str] = []
    now = datetime.now()
    for dl in deadlines_data:
        dl_text = str(dl.get("text", "")).strip()
        if not dl_text:
            continue
        days = int(dl.get("days_from_now", 3))
        due_dt = (now + timedelta(days=days)).replace(hour=10, minute=0, second=0)
        reminder_msg = f"Study Milestone ({topic}): {dl_text}"
        added = add_career_reminder(due_dt, reminder_msg)
        if added:
            scheduled_reminders.append(reminder_msg)

    summary_text = "\n".join(f"- {b}" if not b.startswith("-") else b for b in summary_bullets)

    return {
        "status": "ok",
        "topic": topic,
        "summary": summary_text,
        "flashcards_count": len(saved_cards),
        "flashcards": saved_cards,
        "deadlines_count": len(scheduled_reminders),
        "deadlines": scheduled_reminders
    }


def get_flashcards(topic: Optional[str] = None, mastered: Optional[int] = None) -> List[Dict[str, Any]]:
    """Retrieves flashcards from SQLite study_flashcards."""
    init_db()
    with get_db() as conn:
        query = "SELECT id, topic, question, answer, explanation, difficulty, mastered, review_count, last_reviewed, created_at FROM study_flashcards"
        params: List[Any] = []
        conditions: List[str] = []

        if topic and topic.strip() and topic.strip().lower() != "all":
            conditions.append("LOWER(topic) = LOWER(?)")
            params.append(topic.strip())

        if mastered is not None:
            conditions.append("mastered = ?")
            params.append(1 if mastered else 0)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY mastered ASC, id DESC"
        cur = conn.execute(query, params)
        rows = cur.fetchall()

        cards = []
        for r in rows:
            cards.append({
                "id": r["id"],
                "topic": r["topic"],
                "question": r["question"],
                "answer": r["answer"],
                "explanation": r["explanation"] or "",
                "difficulty": r["difficulty"] or "medium",
                "mastered": bool(r["mastered"]),
                "review_count": r["review_count"] or 0,
                "last_reviewed": r["last_reviewed"] or "",
                "created_at": r["created_at"] or ""
            })
        return cards


def review_flashcard(card_id: int, mastered: bool) -> Dict[str, Any]:
    """Updates mastered status and review count for a flashcard."""
    init_db()
    with get_db() as conn:
        cur = conn.execute("""
            UPDATE study_flashcards
            SET mastered = ?,
                review_count = review_count + 1,
                last_reviewed = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (1 if mastered else 0, card_id))

        if cur.rowcount == 0:
            return {"status": "error", "error": f"Flashcard #{card_id} not found."}

        return {
            "status": "ok",
            "card_id": card_id,
            "mastered": bool(mastered)
        }
