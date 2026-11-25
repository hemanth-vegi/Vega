"""
Vega v2.2 — Voice + HUD 10-Minute Mock Technical Interviewer.
Grounds technical and behavioral interview questions directly in:
1. Candidate's verified Profile Vault projects (career_vault/profile.json).
2. Selected Job Posting requirements (career_vault/jobs.db).
Delivers turn-by-turn spoken/typed answers, constructive verbal feedback,
and pushes weak topics & scorecards directly to today's Lab Log.
"""

import os
import re
import json
import uuid
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger("vega.career.interviewer")

from storage.db import get_db, init_db
from llm.engine import generate_for_task
from career.db import get_job
from career.matcher import load_profile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(BASE_DIR)


def _get_fallback_questions(role: str, company: str, project_names: List[str]) -> List[Dict[str, Any]]:
    """Deterministic fallback questions grounded in candidate's verified projects."""
    p1 = project_names[0] if project_names else "Server Health Monitor & Linux Resource Alert System"
    p2 = project_names[1] if len(project_names) > 1 else "Automated Cloud CI/CD & Docker Deployment Pipeline"

    return [
        {
            "id": 1,
            "type": "Introduction & Role Fit",
            "question": f"Welcome! Could you briefly introduce yourself and share why you are interested in this {role} opportunity at {company}?"
        },
        {
            "id": 2,
            "type": "Core Technical Principles",
            "question": "In a production Linux & Docker environment, how do you diagnose and troubleshoot a container that unexpectedly crashes with an Out-Of-Memory (OOMKilled) exit code?"
        },
        {
            "id": 3,
            "type": "Profile Project Deep-Dive",
            "question": f"In your project '{p1}', what technical challenges did you face when capturing live resource telemetry, and how did you prevent monitoring overhead from degrading system performance?"
        },
        {
            "id": 4,
            "type": "Scenario & Troubleshooting",
            "question": "Suppose a CI/CD pipeline fails during a multi-stage Docker build due to a cache invalidation issue or layer mismatch. What steps do you take to optimize the Dockerfile and ensure reproducible builds?"
        },
        {
            "id": 5,
            "type": "Architecture & Engineering Trade-offs",
            "question": f"Considering your experience with {p2} or cloud architectures, what trade-offs do you prioritize between container isolation, deployment speed, and resource efficiency?"
        }
    ]


def start_interview_session(job_id: Optional[int] = None, track: Optional[str] = None) -> Dict[str, Any]:
    """
    Initializes a 5-question mock interview session.
    Retrieves job specifics if job_id is provided; grounds questions in Profile Vault.
    """
    init_db()
    role = "DevOps & Cloud Engineer"
    company = "Target Cloud Tech"
    jd_text = ""

    if job_id:
        try:
            job = get_job(int(job_id))
            if job:
                role = job.get("role") or role
                company = job.get("company") or company
                jd_text = job.get("raw_text") or ""
        except Exception as exc:
            logger.warning(f"Could not load job #{job_id}: {exc}")

    if not track:
        track = "DevOps & Cloud Engineering"

    # Load candidate's verified projects and skills
    profile = load_profile()
    verified_projects = profile.get("verified_projects", [])
    project_names = [p.get("title", "") for p in verified_projects if isinstance(p, dict) and p.get("title")]

    questions: List[Dict[str, Any]] = []

    # Attempt LLM question formulation
    prompt = f"""You are a Principal Technical Interviewer conducting a rigorous 10-minute mock interview for:
Role: {role}
Company: {company}
Track: {track}

Candidate's Verified Profile Projects:
{json.dumps(project_names, indent=2)}

Job Description Context:
{jd_text[:2000] if jd_text else "Focus on Linux system administration, Docker containerization, CI/CD pipelines, and Python automation."}

Formulate exactly 5 targeted, high-impact interview questions:
1. Warm-up / Introduction & Motivation for {company}.
2. Core Technical competency (Linux, Docker, or Cloud fundamentals).
3. Project Deep-Dive strictly referencing one of the candidate's actual projects ({', '.join(project_names) if project_names else 'Server Health Monitor'}).
4. Scenario / Incident Troubleshooting (production triage).
5. Architecture / Trade-offs & Best Practices.

Return ONLY a JSON array of 5 objects, each with:
- "id": integer 1-5
- "type": string category
- "question": string question text (crisp, professional, 1-2 sentences)
"""
    try:
        llm_res = generate_for_task(prompt=prompt, task_type="fast")
        raw = llm_res.get("content", "") if isinstance(llm_res, dict) else str(llm_res)
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        match = re.search(r"\[.*\]", cleaned, re.DOTALL)
        if match:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list) and len(parsed) >= 5:
                for idx, q in enumerate(parsed[:5]):
                    questions.append({
                        "id": idx + 1,
                        "type": q.get("type", "Technical Question"),
                        "question": q.get("question", "").strip()
                    })
    except Exception as exc:
        logger.warning(f"LLM question generation fallback triggered: {exc}")

    if len(questions) < 5:
        questions = _get_fallback_questions(role, company, project_names)

    session_id = uuid.uuid4().hex[:12]

    with get_db() as conn:
        conn.execute("""
            INSERT INTO mock_interviews (
                session_id, job_id, role, company, track,
                questions_json, current_question_idx, answers_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, 0, '[]', 'in_progress')
        """, (session_id, job_id, role, company, track, json.dumps(questions)))

    return {
        "status": "ok",
        "session_id": session_id,
        "job_id": job_id,
        "role": role,
        "company": company,
        "track": track,
        "total_questions": len(questions),
        "current_question_idx": 0,
        "current_question": questions[0],
        "is_complete": False
    }


def submit_interview_answer(session_id: str, answer_text: str) -> Dict[str, Any]:
    """
    Evaluates candidate's answer to the active question.
    Provides immediate constructive feedback, updates score, and advances to the next question.
    """
    init_db()
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM mock_interviews WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        if not row:
            return {"status": "error", "error": f"Session {session_id} not found."}

        if row["status"] == "completed":
            return {
                "status": "ok",
                "session_id": session_id,
                "is_complete": True,
                "message": "Interview session is already completed."
            }

        questions = json.loads(row["questions_json"] or "[]")
        answers = json.loads(row["answers_json"] or "[]")
        curr_idx = row["current_question_idx"]

        if curr_idx >= len(questions):
            return finish_interview_session(session_id)

        current_q = questions[curr_idx]

    # Evaluate answer with LLM or fallback
    eval_prompt = f"""You are an expert technical interviewer evaluating a candidate's answer:
Question Category: {current_q.get('type')}
Question: {current_q.get('question')}
Candidate's Answer: {answer_text.strip() if answer_text.strip() else "(Candidate passed / gave no answer)"}

Evaluate the response concisely:
1. "score": Integer from 1 to 10 (10 being exceptional).
2. "feedback": 1 to 2 conversational, constructive sentences suitable for speaking out loud. Be specific on what was good and what was missing.
3. "strengths": 1 key positive point.
4. "improvement": 1 key improvement tip.
5. "phrasing_tip": A concise 1-line professional phrasing or terminology polish for tech interviews (or empty string if already strong).

Return ONLY a valid JSON object with keys "score", "feedback", "strengths", "improvement", "phrasing_tip".
"""
    score = 7
    feedback = "Good response. Be sure to highlight specific metrics and root cause isolation techniques."
    strengths = "Clear structured thought process."
    improvement = "Add concrete technical tooling examples."
    phrasing_tip = ""

    try:
        llm_eval = generate_for_task(prompt=eval_prompt, task_type="fast")
        raw_eval = llm_eval.get("content", "") if isinstance(llm_eval, dict) else str(llm_eval)
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw_eval.strip())
        cleaned = re.sub(r"\s*```$", "", cleaned)
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            p_eval = json.loads(match.group(0))
            score = max(1, min(10, int(p_eval.get("score", 7))))
            feedback = str(p_eval.get("feedback", feedback)).strip()
            strengths = str(p_eval.get("strengths", strengths)).strip()
            improvement = str(p_eval.get("improvement", improvement)).strip()
            phrasing_tip = str(p_eval.get("phrasing_tip", "")).strip()
    except Exception as exc:
        logger.warning(f"Answer evaluation LLM error: {exc}")

    answer_record = {
        "question_id": current_q.get("id"),
        "question": current_q.get("question"),
        "answer": answer_text.strip(),
        "score": score,
        "feedback": feedback,
        "strengths": strengths,
        "improvement": improvement,
        "phrasing_tip": phrasing_tip
    }
    answers.append(answer_record)
    next_idx = curr_idx + 1

    with get_db() as conn:
        conn.execute("""
            UPDATE mock_interviews
            SET answers_json = ?, current_question_idx = ?, updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
        """, (json.dumps(answers), next_idx, session_id))

    if next_idx >= len(questions):
        finish_res = finish_interview_session(session_id)
        return {
            "status": "ok",
            "session_id": session_id,
            "feedback": feedback,
            "phrasing_tip": phrasing_tip,
            "score": score,
            "next_question": None,
            "current_question_idx": next_idx,
            "is_complete": True,
            "scorecard": finish_res.get("scorecard")
        }

    return {
        "status": "ok",
        "session_id": session_id,
        "feedback": feedback,
        "phrasing_tip": phrasing_tip,
        "score": score,
        "next_question": questions[next_idx],
        "current_question_idx": next_idx,
        "is_complete": False
    }


def finish_interview_session(session_id: str) -> Dict[str, Any]:
    """
    Finalizes interview session, computes comprehensive scorecard,
    and pushes weak areas and performance summary to today's Lab Log.
    """
    init_db()
    with get_db() as conn:
        cur = conn.execute("SELECT * FROM mock_interviews WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        if not row:
            return {"status": "error", "error": f"Session {session_id} not found."}

        answers = json.loads(row["answers_json"] or "[]")
        role = row["role"] or "Technical Candidate"
        company = row["company"] or "Target Company"

    # Compute Scorecard
    total_score = sum(a.get("score", 7) for a in answers)
    max_score = len(answers) * 10 if answers else 50
    overall_percentage = round((total_score / max_score) * 100) if max_score > 0 else 70

    weak_topics: List[str] = []
    strengths_list: List[str] = []

    for a in answers:
        if a.get("score", 10) <= 6 and a.get("improvement"):
            weak_topics.append(a.get("improvement"))
        elif a.get("strengths"):
            strengths_list.append(a.get("strengths"))

    if not weak_topics:
        weak_topics.append("Deepen knowledge on advanced container networking & stateful deployments.")

    scorecard = {
        "overall_score": overall_percentage,
        "letter_grade": "A" if overall_percentage >= 85 else "B" if overall_percentage >= 70 else "C",
        "total_questions_answered": len(answers),
        "strengths": strengths_list[:3],
        "weak_topics": weak_topics[:3],
        "summary": f"Completed 5-question mock technical interview for {role} at {company} with {overall_percentage}% score."
    }

    with get_db() as conn:
        conn.execute("""
            UPDATE mock_interviews
            SET status = 'completed', scorecard_json = ?, updated_at = CURRENT_TIMESTAMP
            WHERE session_id = ?
        """, (json.dumps(scorecard), session_id))

    # Push weak topics & scorecard directly to Lab Log
    try:
        from tools.lablog_tools import lablog_add_note
        log_msg = f"[Mock Interview: {role} @ {company}] Score: {overall_percentage}%. Focus areas: {'; '.join(scorecard['weak_topics'])}"
        lablog_add_note(log_msg, track="linux")
    except Exception as exc:
        logger.warning(f"Could not push to Lab Log: {exc}")

    return {
        "status": "ok",
        "session_id": session_id,
        "scorecard": scorecard
    }


def get_active_interview(session_id: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves current active interview session status."""
    init_db()
    with get_db() as conn:
        if session_id:
            cur = conn.execute("SELECT * FROM mock_interviews WHERE session_id = ?", (session_id,))
        else:
            cur = conn.execute("SELECT * FROM mock_interviews WHERE status = 'in_progress' ORDER BY id DESC LIMIT 1")

        row = cur.fetchone()
        if not row:
            return {"status": "none", "active": False}

        questions = json.loads(row["questions_json"] or "[]")
        idx = row["current_question_idx"]
        curr_q = questions[idx] if idx < len(questions) else None

        return {
            "status": "ok",
            "active": row["status"] == "in_progress",
            "session_id": row["session_id"],
            "job_id": row["job_id"],
            "role": row["role"],
            "company": row["company"],
            "track": row["track"],
            "total_questions": len(questions),
            "current_question_idx": idx,
            "current_question": curr_q,
            "scorecard": json.loads(row["scorecard_json"]) if row["scorecard_json"] else None
        }
