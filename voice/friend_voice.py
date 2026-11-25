"""
voice/friend_voice.py - ₹0 Neural Voice, Persona & Fluency Engine for Vega.
Implements:
1. Multi-voice Neural TTS via rany2/edge-tts with browser SpeechSynthesis fallback.
2. Fast STT via Groq Whisper (whisper-large-v3-turbo) with Web Speech API fallback.
3. Warm Friend (English/Telugu/Tenglish) vs Pro persona system prompts.
4. Interview English Coach phrasing tips (excluded from spoken audio for natural flow).
5. Open-architecture hooks for thewh1teagle/kokoro-onnx offline CPU TTS.
"""
from __future__ import annotations

import os
import re
import io
import time
import base64
import asyncio
import logging
from typing import Dict, Any, Optional, List, Tuple

logger = logging.getLogger("vega.voice.friend_voice")

# Voice Presets - Verified Stable Microsoft Edge Neural Voices
VOICE_PRESETS = {
    "indian_friend": {
        "id": "en-IN-NeerjaNeural",
        "name": "Neerja (Warm Indian English Friend)",
        "language": "en-IN",
        "gender": "Female",
        "persona": "friend",
    },
    "indian_male": {
        "id": "en-IN-PrabhatNeural",
        "name": "Prabhat (Indian English Male)",
        "language": "en-IN",
        "gender": "Male",
        "persona": "friend",
    },
    "telugu_female": {
        "id": "te-IN-ShrutiNeural",
        "name": "Shruti (Playful Telugu/Tenglish Friend)",
        "language": "te-IN",
        "gender": "Female",
        "persona": "friend",
    },
    "telugu_male": {
        "id": "te-IN-MohanNeural",
        "name": "Mohan (Telugu/Tenglish Friend)",
        "language": "te-IN",
        "gender": "Male",
        "persona": "friend",
    },
    "jarvis": {
        "id": "en-GB-RyanNeural",
        "name": "Ryan (Jarvis-Style British Voice)",
        "language": "en-GB",
        "gender": "Male",
        "persona": "pro",
    },
}

DEFAULT_VOICE_ID = "en-IN-NeerjaNeural"

# Recent Spoken Replies Buffer (for Self-Echo Filtering)
_RECENT_SPOKEN_REPLIES: List[Tuple[float, str]] = []

# Disallowed scripts regex (Korean, CJK, Cyrillic, Arabic, Hebrew, Devanagari, Thai, Greek, etc.)
# Strictly permits only Latin (English, digits, accented Latin) and Telugu (\u0C00-\u0C7F).
NON_LATIN_TELUGU_RE = re.compile(
    r"[\uac00-\ud7af"  # Hangul (Korean)
    r"\u1100-\u11ff"  # Hangul Jamo
    r"\u3130-\u318f"  # Hangul Compatibility
    r"\u4e00-\u9fff"  # CJK Ideographs (Chinese, Japanese Kanji)
    r"\u3040-\u30ff"  # Japanese Hiragana/Katakana
    r"\u0400-\u04ff"  # Cyrillic (Russian, etc.)
    r"\u0600-\u06ff"  # Arabic
    r"\u0590-\u05ff"  # Hebrew
    r"\u0900-\u097f"  # Devanagari (Hindi)
    r"\u0e00-\u0e7f"  # Thai
    r"\u0370-\u03ff]" # Greek
)


def has_forbidden_script(text: str) -> bool:
    """Returns True if text contains characters from forbidden non-Latin/non-Telugu scripts."""
    if not text:
        return False
    return bool(NON_LATIN_TELUGU_RE.search(text))


# Multilingual Whisper Silence Hallucination Filter List
WHISPER_SILENCE_HALLUCINATIONS = {
    "",
    "thank you",
    "thank you.",
    "thank you very much.",
    "thank you very much",
    "thank you so much",
    "thanks",
    "thanks.",
    "thanks for watching",
    "thanks for watching!",
    "thanks for watching.",
    "thank you for watching",
    "subtitles by",
    "subtitles by the amara.org community",
    "subtitles by the amara.org community.",
    "gracias",
    "gracias.",
    "muchas gracias",
    "de nada",
    "de nada.",
    "de vega que se les muro",
    "you",
    "you.",
    "bye",
    "bye.",
    "goodbye",
    "goodbye.",
    "good night",
    "silence",
    "[silence]",
    "(silence)",
    "[music]",
    "(music)",
    "music",
    "please subscribe",
    "like and subscribe",
    "subscribe",
    "so",
    "yeah",
    "yeah.",
    "okay",
    "okay.",
    "ok",
}


def is_silence_hallucination(text: str) -> bool:
    """
    Checks if a transcript is a known Whisper silence/hallucination phrase,
    or contains forbidden non-Latin / non-Telugu scripts, or is pure punctuation/single-word noise.
    """
    if not text or not text.strip():
        return True

    # 1. Script check: drop Korean, Spanish/CJK/Cyrillic scripts
    if has_forbidden_script(text):
        return True

    # Normalize text (remove punctuation, lowercase, collapse whitespace)
    norm = re.sub(r"[^\w\s]", " ", text.lower()).strip()
    norm = re.sub(r"\s+", " ", norm)

    if not norm or len(norm) < 2:
        return True

    # 2. Check against known multilingual hallucinations
    if norm in WHISPER_SILENCE_HALLUCINATIONS:
        return True

    # Check substring matches for prominent hallucinations
    substring_hallucinations = (
        "thanks for watching",
        "thank you for watching",
        "subtitles by",
        "amara org",
        "please subscribe",
        "like and subscribe",
        "de vega que se les muro",
    )
    for sub in substring_hallucinations:
        if sub in norm:
            return True

    # 3. Drop single-word non-command noise
    words = norm.split()
    if len(words) == 1:
        allowed_single_words = {
            "yes", "no", "stop", "mute", "unmute", "pause", "resume",
            "next", "back", "play", "news", "weather", "help", "status", "lock", "vega"
        }
        if words[0] not in allowed_single_words:
            return True

    return False


def register_spoken_reply(text: str) -> None:
    """Registers spoken TTS reply text in rolling memory to prevent self-hearing feedback loops."""
    global _RECENT_SPOKEN_REPLIES
    if not text:
        return
    clean = re.sub(r"[^\w\s]", " ", text.lower()).strip()
    clean = re.sub(r"\s+", " ", clean)
    if not clean:
        return
    now = time.time()
    _RECENT_SPOKEN_REPLIES.append((now, clean))
    # Retain only entries from the last 30 seconds, max 10 entries
    _RECENT_SPOKEN_REPLIES = [(t, s) for t, s in _RECENT_SPOKEN_REPLIES if now - t <= 30.0][-10:]


def is_self_echo(transcript: str, window_seconds: float = 8.0, overlap_threshold: float = 0.55) -> bool:
    """
    Checks if a transcript is self-hearing speaker feedback bleed from a recently spoken Vega reply.
    """
    global _RECENT_SPOKEN_REPLIES
    if not transcript:
        return False
    norm_tx = re.sub(r"[^\w\s]", " ", transcript.lower()).strip()
    norm_tx = re.sub(r"\s+", " ", norm_tx)
    if not norm_tx or norm_tx in WHISPER_SILENCE_HALLUCINATIONS:
        return True

    now = time.time()
    tx_words = set(norm_tx.split())
    if not tx_words:
        return True

    for spoken_time, spoken_text in reversed(_RECENT_SPOKEN_REPLIES):
        if now - spoken_time > window_seconds:
            continue
        # Direct substring check
        if norm_tx in spoken_text or spoken_text in norm_tx:
            return True
        # Token overlap ratio
        sp_words = set(spoken_text.split())
        if sp_words:
            overlap = len(tx_words.intersection(sp_words)) / float(len(tx_words))
            if overlap >= overlap_threshold:
                return True
    return False


def get_persona_mode() -> tuple[str, bool]:
    """Returns (persona_mode, english_coach_enabled)."""
    try:
        from career.db import get_career_setting
        mode = get_career_setting("persona_mode", "friend") or "friend"
        coach = get_career_setting("english_coach", "0") in ("1", "true")
        return mode, coach
    except Exception:
        return "friend", False


def set_persona_mode(mode: str, coach: Optional[bool] = None) -> tuple[str, bool]:
    """Persists persona_mode and optional english_coach setting."""
    try:
        from career.db import set_career_setting
        set_career_setting("persona_mode", mode.strip().lower())
        if coach is not None:
            set_career_setting("english_coach", "1" if coach else "0")
        return get_persona_mode()
    except Exception:
        return mode, bool(coach)


_SLEEPING_STATE: bool = False


def is_sleeping() -> bool:
    """Returns whether Vega's voice listener is in Silent / Sleep mode."""
    global _SLEEPING_STATE
    try:
        from career.db import get_career_setting
        val = get_career_setting("voice_sleep_mode", None)
        if val is not None:
            _SLEEPING_STATE = val in ("1", "true")
    except Exception:
        pass
    return _SLEEPING_STATE


def set_sleep_state(sleeping: bool) -> bool:
    """Persists Silent / Sleep mode in career settings store."""
    global _SLEEPING_STATE
    _SLEEPING_STATE = bool(sleeping)
    try:
        from career.db import set_career_setting
        set_career_setting("voice_sleep_mode", "1" if _SLEEPING_STATE else "0")
    except Exception:
        pass
    return _SLEEPING_STATE


set_sleeping = set_sleep_state


def is_sleep_phrase(text: str) -> bool:
    """Detects commands instructing Vega to go silent, enter sleep mode, or stop listening."""
    if not text:
        return False
    t = re.sub(r"[^\w\s]", " ", text.lower()).strip()
    t = re.sub(r"\s+", " ", t)
    sleep_patterns = [
        r"\b(?:go\s+to\s+sleep|(?:go|so|be|stay|no)\s+silent|sleep\s+mode|silent\s+mode)\b",
        r"\b(?:stop\s+listening|stop\s+talking|(?:go|be|stay)\s+quiet|shut\s+up|hush)\b",
        r"\b(?:sleep\s+now|take\s+a\s+nap|mute\s+yourself)\b",
        r"\b(?:good\s+night\s+vega|vega\s+sleep|vega\s+go\s+silent|hey\s+vega\s+go\s+silent)\b",
        r"\b(?:turn\s+off\s+mic|turn\s+off\s+microphone)\b",
    ]
    return any(re.search(pat, t) for pat in sleep_patterns)


def is_hey_vega_phrase(text: str) -> bool:
    """Require the explicit wake phrase while sleep mode is active."""
    if not text:
        return False
    t = re.sub(r"[^\w\s]", " ", text.lower()).strip()
    t = re.sub(r"\s+", " ", t)
    return bool(re.search(r"\bhey\s+vega\b", t))


def is_wake_phrase(text: str) -> bool:
    """Detects phrases intended to awaken Vega from sleep / silent mode."""
    if not text:
        return False
    t = re.sub(r"[^\w\s]", " ", text.lower()).strip()
    t = re.sub(r"\s+", " ", t)
    wake_patterns = [
        r"\b(?:hey\s+vega|vega\s+wake\s+up|wake\s+up\s+vega|wake\s+up)\b",
        r"\b(?:start\s+listening|unmute\s+yourself|are\s+you\s+there)\b",
        r"\b(?:hello\s+vega|hi\s+vega|ok\s+vega|okay\s+vega)\b",
    ]
    return any(re.search(pat, t) for pat in wake_patterns)


def extract_speech_text(response_text: str) -> str:
    """
    Strips 'Interview phrasing tip:' or '[Interview Phrasing Tip]' lines and Markdown
    formatting from text so spoken TTS audio remains short, natural, and conversational (1-3 sentences).
    """
    if not response_text:
        return ""

    lines = []
    for line in response_text.splitlines():
        line_clean = line.strip()
        # Strip leading markdown symbols when checking for tips
        norm_line = re.sub(r"^[*_#`\s]+", "", line_clean)
        # Remove coaching tips from spoken output
        if re.match(r"^(?:\[(?:Interview\s+Phrasing\s+Tip|English\s+Tip)\]|(?:Interview\s+(?:phrasing\s+)?tip|English\s+tip)\s*:)", norm_line, re.IGNORECASE):
            continue
        # Remove code blocks from spoken output (summarize as code)
        if line_clean.startswith("```"):
            continue
        lines.append(line)

    text = "\n".join(lines).strip()
    # Strip markdown emphasis symbols for speech
    text = re.sub(r"[*_~`#]", "", text)
    # Normalize multiple whitespaces
    text = re.sub(r"\s+", " ", text).strip()
    return text


def build_persona_instruction(mode: str = "friend", english_coach: bool = False, context: str = "") -> str:
    """
    Constructs the system prompt for Vega based on the active persona mode and English coach toggle.
    """
    mode_clean = (mode or "friend").strip().lower()

    if mode_clean == "pro" or mode_clean == "work":
        base = (
            "You are Vega, a Professional AI and engineering partner in Work/Pro Mode. "
            "Provide crisp, structured, technical DevOps, Cloud, and Career answers. "
            "Be precise, professional, and efficient. Focus on production best practices, architecture, and code. "
            "Keep technical answers rigorous, clean, and concise."
        )
    else:
        base = (
            "You are Vega, a warm, witty, supportive best friend and DevOps companion. "
            "Speak naturally in English, Telugu, or Tenglish depending on what the user speaks (e.g. friendly greetings like 'Baagunnava mama', 'Enti sangathulu', or encouraging words). "
            "Keep spoken and conversational replies short (1 to 2 crisp, natural sentences). "
            "Do NOT overuse or repeat 'bro' on every sentence; keep responses genuinely warm, grounded, crisp, and natural. "
            "Naturally reference the user's ongoing DevOps, Linux, Docker, Python, and Cloud goals and projects (never mention game development or legacy games)."
        )

    if english_coach:
        base += (
            "\n[ENGLISH COACH ACTIVE]: If the user's message had a grammar slip or could sound sharper and more impressive in a tech interview, "
            "append a single concise line at the very end of your response formatted exactly as:\n"
            "English tip: <sharp 1-line professional alternative> (or Interview phrasing tip: <alternative>)\n"
            "If their phrasing is already strong, do NOT add any tip."
        )

    if context:
        base += f"\nActive Memory Context: {context}"

    return base


async def _synthesize_edge_tts(text: str, voice_id: str) -> bytes:
    """Internal helper to call edge-tts asynchronously with IPv4 enforcement."""
    import socket
    import aiohttp
    import edge_tts  # Lazy import for headless Linux CI parity

    # Force IPv4 TCPConnector to avoid Windows IPv6 connection timeouts to Microsoft Bing edge TTS
    connector = aiohttp.TCPConnector(family=socket.AF_INET)
    communicate = edge_tts.Communicate(text, voice_id, connector=connector, connect_timeout=15)
    audio_buffer = io.BytesIO()
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            audio_buffer.write(chunk["data"])
    return audio_buffer.getvalue()


def synthesize_speech(text: str, voice_name: str = DEFAULT_VOICE_ID, persona_mode: str = "friend") -> Dict[str, Any]:
    """
    Synthesizes clean speech via edge-tts.
    If edge-tts is unavailable, encounters a connection error, or offline mode is active,
    returns a clean fallback flag so frontend speaks seamlessly via browser SpeechSynthesis.
    """
    clean_text = extract_speech_text(text)
    if not clean_text:
        return {"ok": False, "error": "Empty text for speech"}

    # Resolve voice ID from preset or use directly
    voice_id = voice_name
    if voice_name in (DEFAULT_VOICE_ID, "en-IN-NeerjaExpressiveNeural") and persona_mode == "pro":
        voice_id = "en-GB-RyanNeural"
    for key, p in VOICE_PRESETS.items():
        if voice_name == key or voice_name == p["id"]:
            voice_id = p["id"]
            break

    # Normalize any legacy expressive voice to verified stable neural voice
    if voice_id == "en-IN-NeerjaExpressiveNeural":
        voice_id = "en-IN-NeerjaNeural"

    # Determine language tag for browser speech synthesis fallback
    lang_tag = "en-IN"
    if "te-IN" in voice_id:
        lang_tag = "te-IN"
    elif "en-GB" in voice_id:
        lang_tag = "en-GB"

    # Hermetic CI Mode / Offline Check
    if os.environ.get("VEGA_CI_MODE") == "1":
        return {
            "ok": True,
            "fallback": True,
            "use_browser_tts": True,
            "text": clean_text,
            "voice": voice_id,
            "lang": lang_tag,
            "notice": "CI Mode hermetic speech fallback.",
        }

    try:
        from llm.engine import is_offline_or_low_data
        restricted, net_mode = is_offline_or_low_data()
        if restricted:
            return {
                "ok": True,
                "fallback": True,
                "use_browser_tts": True,
                "text": clean_text,
                "voice": voice_id,
                "lang": lang_tag,
                "notice": f"Edge-TTS skipped ({net_mode} active). Falling back to browser SpeechSynthesis.",
            }
    except Exception:
        pass

    try:
        # Attempt Edge-TTS synthesis in a separate event loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            audio_bytes = loop.run_until_complete(_synthesize_edge_tts(clean_text, voice_id))
        finally:
            try:
                loop.close()
            except Exception:
                pass
            asyncio.set_event_loop(None)

        if audio_bytes and len(audio_bytes) > 200:
            audio_b64 = base64.b64encode(audio_bytes).decode("ascii")
            return {
                "ok": True,
                "audio_base64": audio_b64,
                "format": "audio/mp3",
                "voice": voice_id,
                "text": clean_text,
                "use_browser_tts": False,
            }
        else:
            raise ValueError("Edge-TTS returned empty audio stream.")
    except Exception as exc:
        logger.exception("Edge-TTS synthesis failed for voice %s", voice_id)
        notice = sanitize_tts_notice(str(exc))
        return {
            "ok": True,
            "fallback": True,
            "use_browser_tts": True,
            "text": clean_text,
            "voice": voice_id,
            "lang": lang_tag,
            "error": notice,
            "notice": notice,
        }


def sanitize_tts_notice(error: str) -> str:
    """Return a concise UI-safe fallback notice without exposing Edge-TTS request URLs."""
    err_str = str(error or "")
    match = re.search(
        r"\bHTTP(?:\s+Error)?\s*[:=]?\s*(\d{3})\b|\b(\d{3})\s+(?:Client|Server)\s+Error\b",
        err_str,
        re.IGNORECASE,
    )
    status_code = next((code for code in match.groups() if code), None) if match else None
    if status_code == "403" or "403" in err_str:
        return "Edge-TTS unavailable (HTTP 403 — please verify Windows system date & time sync). Using browser voice."
    if status_code:
        return f"Edge-TTS unavailable (HTTP {status_code}). Using browser voice."
    return "Edge-TTS unavailable. Using browser voice."


def transcribe_audio_groq(audio_bytes: bytes, filename: str = "audio.webm") -> Dict[str, Any]:
    """
    Transcribes microphone audio via Groq Whisper (whisper-large-v3-turbo).
    Gracefully falls back to browser SpeechRecognition if offline, unconfigured, or failing.
    """
    # Hermetic CI Mode
    if os.environ.get("VEGA_CI_MODE") == "1":
        return {
            "ok": True,
            "text": "CI Mock: Explain my Kubernetes deployment",
            "language": "en",
            "provider": "groq_whisper_ci_mock",
        }

    if not audio_bytes or len(audio_bytes) < 100:
        return {"ok": False, "fallback_to_browser": True, "error": "Empty audio payload"}

    try:
        from llm.engine import is_offline_or_low_data
        restricted, net_mode = is_offline_or_low_data()
        if restricted:
            return {
                "ok": False,
                "fallback_to_browser": True,
                "error": f"Groq Whisper unavailable in {net_mode}. Using browser SpeechRecognition.",
            }
    except Exception:
        pass

    groq_api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not groq_api_key:
        return {
            "ok": False,
            "fallback_to_browser": True,
            "error": "GROQ_API_KEY not configured. Falling back to browser SpeechRecognition.",
        }

    try:
        import httpx
        url = "https://api.groq.com/openai/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {groq_api_key}"}

        files = {"file": (filename, audio_bytes, "audio/webm")}
        data = {
            "model": "whisper-large-v3-turbo",
            "response_format": "json",
            "temperature": "0.0",
        }

        with httpx.Client(timeout=10.0) as client:
            resp = client.post(url, headers=headers, files=files, data=data)
            if resp.status_code == 200:
                result = resp.json()
                text = (result.get("text") or "").strip()
                if is_silence_hallucination(text):
                    return {
                        "ok": False,
                        "hallucination_filtered": True,
                        "error": f"Silence/script hallucination filtered ('{text[:30]}').",
                    }
                if is_self_echo(text):
                    return {
                        "ok": False,
                        "self_echo_filtered": True,
                        "error": "Self-echo filtered.",
                    }
                return {
                    "ok": True,
                    "text": text,
                    "language": result.get("language", "auto"),
                    "provider": "groq_whisper",
                }
            else:
                return {
                    "ok": False,
                    "fallback_to_browser": True,
                    "error": f"Groq Whisper HTTP {resp.status_code}: {resp.text[:120]}",
                }
    except Exception as exc:
        return {
            "ok": False,
            "fallback_to_browser": True,
            "error": f"Groq Whisper connection error: {exc}",
        }
