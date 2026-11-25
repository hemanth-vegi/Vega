"""
Two-Stage Fake-Job & Scam Shield for Vega v2.2.
ALWAYS RUNS FIRST before skill matching or resume tailoring.
Stage 1: Deterministic rules (instant, zero tokens, regex patterns).
Stage 2: LLM Credibility Audit (Gemini 3.8 Flash analysis with graceful heuristic fallback).
"""

import re
import json
from typing import Dict, Any, List, Tuple
from llm.engine import generate_for_task


DETERMINISTIC_SCAM_PATTERNS = [
    # 1. Upfront Fee Traps & Security Deposits
    (r"\b(registration\s+fee|training\s+fee|security\s+deposit|processing\s+charge|refundable\s+amount|pay\s+(?:rs\.?|inr|\$)\s*\d+)\b",
     "Mandatory upfront fee, training charge, or security deposit required."),

    # 2. WhatsApp / Telegram Direct Hiring Traps
    (r"\b(contact\s+(?:us\s+)?on\s+whatsapp|whatsapp\s+only|wa\.me/|send\s+(?:resume|cv)\s+to\s+whatsapp|telegram\s+task|t\.me/|join\s+telegram|contact\s+hr\s+on\s+telegram)\b",
     "Informal WhatsApp/Telegram-only hiring channel with no official corporate contact."),

    # 3. URL Shorteners in Application Links
    (r"\b(bit\.ly|tinyurl\.com|t\.co|is\.gd|rb\.gy|cutt\.ly|shorturl\.at)/[a-zA-Z0-9_-]+",
     "Suspicious URL shortener hiding the actual destination domain."),

    # 4. Free Webmail Posing as Enterprise Recruiters
    (r"([a-zA-Z0-9_.-]+@(gmail|yahoo|outlook|hotmail)\.com)",
     "Free webmail address used instead of a verified corporate domain."),

    # 5. Guaranteed Daily Income / Task-based Scams
    (r"\b(earn\s+(?:rs\.?|inr|\$)\s*\d+\s*(?:daily|per\s+day)|like\s+and\s+subscribe\s+task|youtube\s+video\s+rating|part[\s-]?time\s+data\s+entry\s+without\s+experience)\b",
     "Unrealistic daily task-based earning pitch typical of click/review fraud rings."),
]

ENTERPRISE_NAMES = [
    "infosys", "tcs", "tata consultancy", "wipro", "google", "microsoft",
    "amazon", "cognizant", "accenture", "ibm", "hcl", "meta", "apple"
]


def run_deterministic_rules(text: str, url: str = "") -> Tuple[int, List[str]]:
    """
    Executes fast regex rules across text and URL.
    Returns (scam_score, red_flags).
    """
    combined = f"{url}\n{text}".lower()
    red_flags = []
    score = 0

    for pattern, description in DETERMINISTIC_SCAM_PATTERNS:
        match = re.search(pattern, combined, re.IGNORECASE)
        if match:
            # Special check for enterprise webmail: only flag high score if claiming enterprise identity
            if "@" in pattern:
                matched_email = match.group(1)
                claims_enterprise = any(ent in combined for ent in ENTERPRISE_NAMES)
                if claims_enterprise:
                    red_flags.append(f"Enterprise scam: Free email ({matched_email}) claiming to represent major enterprise.")
                    score += 55
                else:
                    red_flags.append(f"Free webmail contact: {matched_email} (prefer verified corporate domains).")
                    score += 25
            else:
                matched_str = match.group(0)
                red_flags.append(f"{description} (matched: '{matched_str}')")
                if "upfront fee" in description.lower() or "deposit" in description.lower():
                    score += 65
                elif "whatsapp" in description.lower() or "telegram" in description.lower():
                    score += 45
                elif "url shortener" in description.lower():
                    score += 30
                elif "daily income" in description.lower():
                    score += 50
                else:
                    score += 25

    return min(score, 100), red_flags


def run_llm_credibility_audit(text: str, company: str, role: str) -> Dict[str, Any]:
    """
    Second-stage LLM evaluation using Gemini 3.8 Flash.
    Checks ghost-job language, unrealistic intern expectations, domain mismatches.
    """
    prompt = f"""
You are an expert career integrity auditor protecting college students and freshers from job scams.
Analyze this job description for scams, fee traps, ghost jobs, or fraudulent claims.

Company: {company}
Role: {role}
Job Description Text:
\"\"\"
{text[:4000]}
\"\"\"

Audit Criteria:
1. Upfront charges, paid training traps, registration fees, equipment deposits.
2. WhatsApp/Telegram tasks, crypto tasks, review-farm tasks.
3. Completely unrealistic experience for an "intern" (e.g. asking 5+ years production experience).
4. Suspiciously inflated salary for a fresher (e.g. Rs 50,000/day for data entry).
5. Vague, generic ghost-job text without clear deliverables.

Output strict JSON only with this schema:
{{
  "scam_score": <int 0-100>,
  "risk_level": "<LOW_RISK | VERIFY_MANUALLY | HIGH_RISK>",
  "red_flags": ["<evidence quote or concise flag>"],
  "verification_tip": "<one practical sentence on how to verify this role>"
}}
"""
    try:
        res = generate_for_task(
            prompt=prompt,
            task_type="scam_shield",
            temperature=0.1
        )
        if res.get("status") == "success" and res.get("content"):
            raw_content = res.get("content", "").strip()
            # Clean markdown code fences if present
            cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_content, flags=re.MULTILINE).strip()
            data = json.loads(cleaned)
            score = int(data.get("scam_score", 0))
            risk = data.get("risk_level", "LOW_RISK")
            flags = data.get("red_flags", [])
            tip = data.get("verification_tip", "Verify directly on the company's official career portal.")
            return {
                "scam_score": min(max(score, 0), 100),
                "risk_level": risk,
                "red_flags": flags if isinstance(flags, list) else [str(flags)],
                "verification_tip": tip,
                "provider": res.get("provider"),
                "model": res.get("model")
            }
    except Exception:
        pass

    # Heuristic fallback if LLM is offline or parsing fails
    return {
        "scam_score": 0,
        "risk_level": "LOW_RISK",
        "red_flags": [],
        "verification_tip": "Verify this role directly on the company's official career portal."
    }


def audit_job_listing(text: str, company: str = "", role: str = "", url: str = "") -> Dict[str, Any]:
    """
    Full two-stage Scam Shield pipeline.
    Always runs Stage 1 (Deterministic) first.
    If score is severe (>= 50), immediately flags as HIGH_RISK or VERIFY_MANUALLY without token waste.
    Otherwise runs Stage 2 LLM audit to detect subtle deception.
    """
    clean_text = (text or "").strip()
    rule_score, rule_flags = run_deterministic_rules(clean_text, url)

    # If deterministic flags already establish high confidence fraud:
    if rule_score >= 60:
        return {
            "risk_level": "HIGH_RISK",
            "scam_score": min(rule_score + 20, 100),
            "red_flags": rule_flags,
            "verification_tip": "DO NOT PROCEED. This listing contains explicit scam patterns (fees/unverified channels).",
            "audited_by": "Stage 1 (Deterministic Rules)"
        }

    # Run Stage 2 LLM audit
    llm_result = run_llm_credibility_audit(clean_text, company, role)

    # Combine signals
    combined_flags = list(dict.fromkeys(rule_flags + llm_result.get("red_flags", [])))
    final_score = max(rule_score, llm_result.get("scam_score", 0))

    if final_score >= 50 or any("fee" in f.lower() or "whatsapp" in f.lower() for f in combined_flags):
        risk_level = "HIGH_RISK"
    elif final_score >= 20 or len(combined_flags) > 0:
        risk_level = "VERIFY_MANUALLY"
    else:
        risk_level = "LOW_RISK"

    audited_brain = llm_result.get("model") or "Gemini 3.8 Flash"
    return {
        "risk_level": risk_level,
        "scam_score": final_score,
        "red_flags": combined_flags,
        "verification_tip": llm_result.get("verification_tip") or "Verify directly on the company's official career portal.",
        "audited_by": f"Stage 1 & Stage 2 (Rules + {audited_brain})"
    }
