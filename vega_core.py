"""
Vega's brain.

It understands commands (open apps, reminders, to-dos, weather, news, your VM,
your diary, your documents, your 24-week plan) and runs them directly with
Python. Anything that isn't a command goes to your own Ollama model ("vega").

The AI model only talks. The actions are done by this file, so a small model
like llama3.2:3b is enough.

Everything stays on your laptop. The internet is used only for weather, news
and Wikipedia (free, no account), and for Telegram if you turn it on.
"""

import sys
if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys, "stderr") and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import difflib
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import webbrowser
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.abspath(__file__))
IS_WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
USER_AGENT = "VegaPersonalAssistant/2.0 (personal use)"


def here(*parts):
    return os.path.join(BASE, *parts)


# ---------------------------------------------------------------- settings

DEFAULT_CONFIG = {
    "name": "Vega",
    "model": "vega",                     # your Ollama model (fast; also used to understand commands)
    "chat_model": "auto",                # "auto" = pick the best brain for each question; "" = always "model";
                                         # or a model name like "gemma3:4b" to always use that one for chatting
    "doc_chars": 24000,                  # how much of a document I read (about 8 pages); bigger = slower
    "doc_context": 8192,                 # AI memory size while a document is open
    "pause_on_calls": True,              # don't listen for "Hey Vega" while another app uses the mic (calls)
    "study_plan_reminders": False,       # talk about the 24-week roadmap by itself (greeting, briefing, 9 PM check)
    "close_action": "ask",               # the window's X button: "ask", "quit" or "hide"
    "smart_model": "qwen2.5:7b",          # the thinking brain used by "auto" ("" = best one you have installed)
    "code_model": "qwen2.5:7b",           # used for coding / Linux command questions
    "auto_code_model": True,             # switch to code_model by itself for code questions
    "whisper_model": "small.en",         # speech-to-text; small.en understands Indian accents much better than base.en
    "smart_commands": True,
    "understand_telugu": False,          # hear Telugu / Tenglish speech (turn on in Menu)
    "speak_telugu": False,               # speak answers in Telugu (needs internet)
    "voice_language": "auto",            # "auto" = answer in the language I spoke (Telugu or English), "english", "telugu"
    "telugu_voice": "te-IN-ShrutiNeural",  # or "te-IN-MohanNeural" for a male voice
    "telugu_model": "small",             # "small" (faster) or "medium" (better Telugu, slower)              # when a command isn't clear, ask the AI what you meant
    "wake_model": "base.en",             # model used only to hear "Hey Vega" (base.en copes with accents better than tiny.en)
    "wake_variants": [],                 # how Whisper hears your "Vega" (Menu -> Teach me your "Hey Vega")
    "voice_on": True,                    # read answers out loud
    "wake_word_on": False,               # listen for "Hey Vega" in the background
    "speech_rate": 1,                    # -10 (slow) to 10 (fast)
    "city": "Chennai",                   # default weather place (SRM Kattankulathur)
    "latitude": 12.8231,
    "longitude": 80.0442,
    "vm_name": "hemanth-server1",
    "study_nudge_time": "21:00",
    "morning_briefing_time": "07:30",    # daily briefing on screen and phone ("" = off)
    "internship_keywords": ["devops", "cloud computing", "linux"],         # if today's task isn't done by then, Vega reminds you ("" = off)
    "vboxmanage": "C:\\Program Files\\Oracle\\VirtualBox\\VBoxManage.exe",
    "documents_folder": "documents",
    "telegram_token": "",                # from @BotFather (see the setup guide)
    "telegram_chat_id": "",              # only this chat may talk to Vega
    "browser": "brave",                  # your main web browser: brave, chrome, edge or firefox
    "apps": {
        "notepad": "notepad.exe",
        "calculator": "calc.exe",
        "paint": "mspaint.exe",
        "file explorer": "explorer.exe",
        "explorer": "explorer.exe",
        "files": "explorer.exe",
        "task manager": "taskmgr.exe",
        "settings": "ms-settings:",
        "command prompt": "cmd.exe",
        "cmd": "cmd.exe",
        "powershell": "powershell.exe",
        "terminal": "wt.exe",
        "chrome": "chrome",
        "google chrome": "chrome",
        "brave": "brave",
        "brave browser": "brave",
        "edge": "msedge",
        "vs code": "code",
        "vscode": "code",
        "visual studio code": "code",
        "virtualbox": "C:\\Program Files\\Oracle\\VirtualBox\\VirtualBox.exe",
        "steam": "steam://open/main",
        "spotify": "spotify:",
        "whatsapp": "whatsapp:",
        "camera": "microsoft.windows.camera:",
        "word": "winword",
        "excel": "excel",
        "powerpoint": "powerpnt",
        "snipping tool": "snippingtool.exe"
    },
    "sites": {
        "youtube": "https://www.youtube.com",
        "google": "https://www.google.com",
        "gmail": "https://mail.google.com",
        "github": "https://github.com",
        "linkedin": "https://www.linkedin.com",
        "coursera": "https://www.coursera.org",
        "chatgpt": "https://chatgpt.com",
        "claude": "https://claude.ai",
        "whatsapp web": "https://web.whatsapp.com",
        "instagram": "https://www.instagram.com",
        "netflix": "https://www.netflix.com",
        "maps": "https://maps.google.com",
        "google maps": "https://maps.google.com",
        "odysseus": "http://127.0.0.1:7000",
        "facebook": "https://www.facebook.com",
        "twitter": "https://x.com",
        "x": "https://x.com",
        "telegram": "https://web.telegram.org",
        "telegram web": "https://web.telegram.org",
        "amazon": "https://www.amazon.in",
        "flipkart": "https://www.flipkart.com",
        "google drive": "https://drive.google.com",
        "drive": "https://drive.google.com",
        "google calendar": "https://calendar.google.com",
        "calendar": "https://calendar.google.com",
        "google classroom": "https://classroom.google.com",
        "classroom": "https://classroom.google.com",
        "srm academia": "https://academia.srmist.edu.in",
        "academia": "https://academia.srmist.edu.in",
        "leetcode": "https://leetcode.com",
        "hackerrank": "https://www.hackerrank.com",
        "stack overflow": "https://stackoverflow.com",
        "reddit": "https://www.reddit.com",
        "wikipedia": "https://en.wikipedia.org",
        "outlook": "https://outlook.live.com",
        "spotify web": "https://open.spotify.com",
        "red hat sandbox": "https://developers.redhat.com/developer-sandbox",
        "azure portal": "https://portal.azure.com",
        "oracle cloud": "https://cloud.oracle.com"
    }
}


def read_json(name, default):
    try:
        with open(here(name), "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json(name, data):
    tmp = here(name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, here(name))


def strip_thinking(text):
    """Some models (like qwen3) 'think out loud' inside <think> tags first. Hide that."""
    return re.sub(r"<think>.*?(</think>|$)", "", text or "", flags=re.S).lstrip()


def load_env():
    """Load TELEGRAM_TOKEN and GEMINI_API_KEY from .env into environment variables securely."""
    env_file = here(".env")
    if not os.path.exists(env_file):
        # Fallback to C:\CloudLab\mybot\.env if running from another directory
        env_file = r"C:\CloudLab\mybot\.env"
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        k, v = k.strip(), v.strip()
                        if k in ("TELEGRAM_TOKEN", "GEMINI_API_KEY"):
                            os.environ[k] = v
        except Exception:
            pass


def save_env_value(key, value):
    """Save API keys into .env instead of config.json, preserving .gitignore security."""
    env_file = here(".env")
    if not os.path.exists(env_file) and os.path.exists(r"C:\CloudLab\mybot\.env"):
        env_file = r"C:\CloudLab\mybot\.env"
    current = {}
    if os.path.exists(env_file):
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        current[k.strip()] = v.strip()
        except Exception:
            pass
    current[key] = str(value).strip()
    try:
        with open(env_file, "w", encoding="utf-8") as f:
            for k, v in current.items():
                f.write(f"{k}={v}\n")
    except Exception:
        pass
    os.environ[key] = str(value).strip()
def load_config():
    """Read config.json, filling in anything missing from the defaults and loading keys from .env."""
    load_env()
    user = read_json("config.json", None)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))
    if isinstance(user, dict):
        # If keys accidentally still in config.json, migrate to .env
        if "telegram_token" in user and user["telegram_token"]:
            save_env_value("TELEGRAM_TOKEN", user.pop("telegram_token"))
        if "gemini_api_key" in user and user["gemini_api_key"]:
            save_env_value("GEMINI_API_KEY", user.pop("gemini_api_key"))
        for k, v in user.items():
            if isinstance(v, dict) and isinstance(cfg.get(k), dict):
                cfg[k].update({str(a).lower(): b for a, b in v.items()})
            else:
                cfg[k] = v

    # Keys come strictly from .env / environment
    cfg["telegram_token"] = os.environ.get("TELEGRAM_TOKEN", "")
    cfg["gemini_api_key"] = os.environ.get("GEMINI_API_KEY", "")

    if cfg.get("config_version", 1) < 2:
        if cfg.get("whisper_model") in ("base.en", "tiny.en"):
            cfg["whisper_model"] = "small.en"
        cfg["config_version"] = 2
    if cfg.get("config_version", 1) < 3:
        if cfg.get("wake_model") == "tiny.en":
            cfg["wake_model"] = "base.en"
        cfg["config_version"] = 3
    if cfg.get("config_version", 1) < 4:
        if not cfg.get("chat_model"):
            cfg["chat_model"] = "auto"
        cfg["config_version"] = 4
    if cfg.get("config_version", 1) < 5:
        if cfg.get("wake_model") in ("base.en", "tiny.en"):
            cfg["wake_model"] = "small.en"
        if cfg.get("wake_strictness") in (None, "very_easy"):
            cfg["wake_strictness"] = "easy"
        cfg["config_version"] = 5
    if cfg.get("config_version", 1) < 6:
        if cfg.get("wake_model") == "small.en":
            cfg["wake_model"] = "base.en"
        cfg["config_version"] = 6
    if cfg.get("config_version", 1) < 7:
        if cfg.get("wake_strictness") == "very_easy":
            cfg["wake_strictness"] = "easy"
        cfg.setdefault("wake_only_me", True)
        cfg["config_version"] = 7
    if cfg.get("config_version", 1) < 8:
        if cfg.get("wake_strictness") == "easy":
            cfg["wake_strictness"] = "very_easy"
        cfg["config_version"] = 8
    if cfg.get("config_version", 1) < 9:
        cfg.setdefault("cloud_speech", True)
        cfg["config_version"] = 9

    # Save stripped config.json (never write keys to config.json)
    save_cfg = json.loads(json.dumps(cfg))
    save_cfg.pop("telegram_token", None)
    save_cfg.pop("gemini_api_key", None)
    write_json("config.json", save_cfg)
    return cfg


def save_config_value(key, value):
    """Save a config value. API keys are redirected to .env."""
    if key == "telegram_token":
        save_env_value("TELEGRAM_TOKEN", value)
        return
    if key == "gemini_api_key":
        save_env_value("GEMINI_API_KEY", value)
        return
    cfg = read_json("config.json", {}) or {}
    cfg.pop("telegram_token", None)
    cfg.pop("gemini_api_key", None)
    cfg[key] = value
    write_json("config.json", cfg)


# ---------------------------------------------------------------- helpers

NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
    "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20, "twenty five": 25, "thirty": 30,
    "forty": 40, "forty five": 45, "fifty": 50, "sixty": 60, "ninety": 90,
}


def to_number(word):
    word = word.lower().strip()
    if word.isdigit():
        return int(word)
    return NUMBER_WORDS.get(word)


def words_to_digits(text):
    """'at seven thirty pm' -> 'at 7 30 pm' (only for small number words)."""
    for w in sorted(NUMBER_WORDS, key=len, reverse=True):
        if w in ("a", "an"):
            continue
        text = re.sub(r"\b" + w + r"\b", str(NUMBER_WORDS[w]), text)
    return text


# how speech-to-text often hears "Hey Vega" / "Vega" at the start of a command ("Viveka shut down my system")
WAKE_NAMES = (r"vega|wega|vegas|veega|vaiga|vayga|vaga|weiga|vegah|veiga|baega|bega|vegha|waiga|weega|wiga|viga|vayega|"
              r"vaigha|bhega|beiga|vegaa|wegah|vivek|viveka|vivega|vivegha|vibe|vivi|khelega|kelega|kheloga|hevega|heyvega|evega|avega|hivega|"
              r"rekha|reka|rayka|raika|reikha|sega|smart rekha|smart tracker")


def clean_command(text):
    """Lower-case the text and drop 'hey vega', 'please', etc."""
    t = text.strip().lower()
    t = t.replace("’", "'").replace("‘", "'")
    t = re.sub(r"^(hey|hi|hay|hai|ok|okay|hello)[\s,]+", "", t)
    t = re.sub(r"^(?:he|here|a|e)[\s,]+(?=(?:" + WAKE_NAMES + r")\b)", "", t)
    t = re.sub(r"^(?:" + WAKE_NAMES + r")\b[\s,.!]*", "", t)
    t = re.sub(r"^(please|can you|could you|will you|would you)\s+", "", t)
    t = re.sub(r"^(please)\s+", "", t)
    t = re.sub(r"[\s,]*please$", "", t)
    t = re.sub(r"[?.!]+$", "", t).strip()
    return t


# ---------------------------------------------------------------- speech autocorrect
# Common Indian English accent misheard patterns from Google STT and Whisper.
# Keys are what STT hears; values are what the user actually meant.
# These are applied ONLY to spoken commands, not typed ones.
ACCENT_FIXES = {
    # Wake name phonetic variations in Indian English
    "rekha": "vega", "reka": "vega", "rayka": "vega", "raika": "vega", "reikha": "vega", "sega": "vega",
    "smart rekha": "hey vega", "smart tracker": "hey vega",
    # App names that STT commonly mangles with Indian accents
    "brev": "brave", "breve": "brave", "prior": "brave", "imperial": "brave",
    "braid": "brave", "bravo": "brave", "bravo browser": "brave browser",
    "crome": "chrome", "krome": "chrome", "grome": "chrome",
    "what's app": "whatsapp", "what app": "whatsapp", "watts app": "whatsapp",
    "what sapp": "whatsapp", "watsapp": "whatsapp",
    "you tube": "youtube", "u tube": "youtube", "utube": "youtube",
    "insta": "instagram", "insta gram": "instagram",
    "note pad": "notepad", "not pad": "notepad",
    "power shell": "powershell", "power cell": "powershell",
    "virtual box": "virtualbox", "virtual boss": "virtualbox",
    "vs court": "vs code", "we is code": "vs code", "v s code": "vs code",
    "file explore": "file explorer", "files explore": "file explorer",
    "task manage": "task manager", "taskmanager": "task manager",
    "snipping tools": "snipping tool", "sniping tool": "snipping tool",
    "spot if i": "spotify", "spot a phi": "spotify", "sportify": "spotify",
    "net flicks": "netflix", "net flex": "netflix",
    "corsera": "coursera", "corcera": "coursera", "courserea": "coursera",
    "linked in": "linkedin",
    "git hub": "github", "get hub": "github", "git up": "github",
    "leet code": "leetcode", "lead code": "leetcode", "elite code": "leetcode",
    "hacker rank": "hackerrank", "hacker ring": "hackerrank",
    "stack over flow": "stack overflow",
    "google class room": "google classroom",
    "wiki pedia": "wikipedia",
    "amazone": "amazon",
    "flip cart": "flipkart", "flip kart": "flipkart",
    # Two-word splits that STT introduces
    "oh pen": "open", "o pen": "open", "opan": "open",
    "wall you": "volume", "wall um": "volume",
    "shut down": "shut down", "shutdown": "shut down",
    "screen shot": "screenshot", "screen shorts": "screenshot",
    "such for": "search for",
    "remind her": "remind",
    "remainder": "reminder",
    # Closing apps and tabs mishearings from Whisper / Google STT
    "close them on": "close them all",
    "close them in": "close them all",
    "close the mall": "close them all",
    "close demo": "close them all",
    "close dem all": "close them all",
    "clothes them all": "close them all",
    "time in close": "close",
    "time to close": "close",
    "trying to close": "close",
    # Lab log common mishearings from STT
    "today's black lock": "today's lab log",
    "todays black lock": "today's lab log",
    "today black lock": "today's lab log",
    "black lock": "lab log",
    "black look": "lab log",
    "blacklook": "lab log",
    "block log": "lab log",
    "app lock": "lab log",
    "applock": "lab log",
    "app log": "lab log",
    "applog": "lab log",
    "lag log": "lab log",
    "laglog": "lab log",
    "lap log": "lab log",
    "laplog": "lab log",
    "lab lock": "lab log",
    "lap lock": "lab log",
    "back log": "lab log",
    "backlog": "lab log",
    # Common filler/noise patterns to clean
    "the the": "the", "and and": "and",
}

# Verb corrections: only applied when the word is at the START of a command.
# These are too ambiguous in mid-sentence ("I often play..." shouldn't become "I open play...")
ACCENT_VERB_FIXES = {
    "often": "open", "oven": "open", "upon": "open", "opm": "open", "opn": "open", "opan": "open", "openm": "open",
    "played": "play", "plea": "play",
    "clothes": "close", "clause": "close",
    "called": "call", "coal": "call",
    "massage": "message",
    "remained": "remind",
    "brightens": "brightness", "brightest": "brightness",
}

# Multi-word phrases that are common STT garbage (ambient noise / TV / random pickup)
SPEECH_GARBAGE = re.compile(
    r"^(?:thank you(?: for watching)?|thanks for watching|bye|bye bye|goodbye|"
    r"good night|see you|see you next time|see you later|take care|"
    r"have a (?:good|nice|great) day|stay safe|god bless(?: you)?|"
    r"please subscribe|don't forget to subscribe|hit the bell|smash the like button|"
    r"like and subscribe|subscribe|the end|"
    r"music(?:al)?|background music|music playing|"
    r"i love you(?: too)?|love you(?: too)?|"
    r"the the|and and|"
    r"no problem|never mind|never|alright|all right|"
    r"sorry|excuse me|pardon|"
    r"oh my god|oh my goodness|oh wow|what the|"
    r"done|that's it|that is it|got it|got you|"
    r"well done|good job|nice|perfect|excellent|"
    r"the end|hmm+|um+|uh+|ah+|oh+|mm+|ha+|huh|"
    r"subscribe|like and subscribe|hit the bell|notification|"
    r"microphone (?:link|check|test)|"
    r"you$|yes$|no$|okay$|ok$|"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten)$)$",
    re.IGNORECASE
)


def _phonetic_key(word):
    """Simple phonetic key for fuzzy matching: strip vowels (except first), collapse doubles."""
    if len(word) < 2:
        return word
    key = word[0] + re.sub(r"[aeiou]", "", word[1:])
    return re.sub(r"(.)\1+", r"\1", key)


def autocorrect_speech(text, known_vocab=None):
    """Fix common Indian accent STT errors in spoken text.

    Args:
        text: the cleaned command text (already lowercased by clean_command)
        known_vocab: set/list of known words (app names, site names, contacts) for fuzzy matching

    Returns:
        (corrected_text, list_of_fixes)  where fixes is like [("brev", "brave"), ...]
    """
    if not text:
        return text, []

    fixes = []

    # --- Tier 1: exact phrase/word substitutions from the accent dictionary ---
    corrected = text
    # Try multi-word replacements first (longest match first)
    for wrong, right in sorted(ACCENT_FIXES.items(), key=lambda x: len(x[0]), reverse=True):
        if wrong in corrected:
            before = corrected
            corrected = re.sub(r"(?<![a-z])" + re.escape(wrong) + r"(?![a-z])", right, corrected)
            if corrected != before:
                fixes.append((wrong, right))

    # --- Tier 1.5: verb corrections at the START of the command only ---
    # "often brave" → "open brave", but "I often use brave" stays unchanged
    words = corrected.split()
    if words and words[0] in ACCENT_VERB_FIXES:
        old_verb = words[0]
        words[0] = ACCENT_VERB_FIXES[old_verb]
        fixes.append((old_verb, words[0]))
        corrected = " ".join(words)

    # --- Tier 2: fuzzy matching against known vocabulary ---
    if known_vocab:
        common_english = {
            "the", "and", "for", "with", "from", "that", "this", "what", "what's", "whats",
            "when", "where", "where's", "which", "how", "how's", "who", "who's", "why", "will",
            "have", "been", "being", "some", "any", "all", "just", "like", "very",
            "can", "not", "but", "are", "was", "were", "has", "had",
            "your", "you", "they", "them", "its", "it's", "his", "her", "our",
            "about", "into", "over", "also", "than", "then", "now",
            "out", "new", "one", "two", "set", "get", "let", "put",
            "say", "said", "tell", "told", "take", "give", "make",
            "come", "going", "doing", "here", "there", "there's", "back", "still",
            "in", "to", "at", "by", "on", "up", "down", "off", "of", "is", "am", "me", "my",
            "drink", "eat", "water", "tea", "coffee", "food",
            "minute", "minutes", "min", "mins", "hour", "hours", "hr", "hrs",
            "second", "seconds", "sec", "secs", "day", "days", "task", "tasks",
            "today", "tomorrow", "tonight", "morning", "afternoon", "evening", "night",
            "clock", "time", "date", "weather", "news", "music", "song", "video",
            "check", "run", "do", "done", "see", "show", "read", "write", "send"
        }
        words = corrected.split()
        vocab_set = {v.lower() for v in known_vocab if len(v) >= 3}
        new_words = []
        skip_next = False

        for i, w in enumerate(words):
            if skip_next:
                skip_next = False
                continue

            w_clean = w.replace("'", "")
            if w in vocab_set or w_clean in vocab_set or len(w) < 3 or w in common_english or w_clean in common_english:
                new_words.append(w)
                continue

            # Check if this word + next word form a known vocab entry
            if i + 1 < len(words):
                pair = w + " " + words[i + 1]
                if pair in vocab_set:
                    new_words.append(pair)
                    skip_next = True
                    continue

            # Fuzzy match only if word is NOT common English and similarity is very high (>= 0.82)
            best_match = None
            best_ratio = 0
            for v in vocab_set:
                if abs(len(v) - len(w)) > 2:
                    continue
                ratio = difflib.SequenceMatcher(None, w, v).ratio()
                if ratio > best_ratio:
                    best_ratio = ratio
                    best_match = v

            if best_match and best_ratio >= 0.82 and w != best_match:
                new_words.append(best_match)
                fixes.append((w, best_match))
            else:
                new_words.append(w)

        corrected = " ".join(new_words)

    return corrected, fixes


def fmt_time(dt):
    return dt.strftime("%I:%M %p").lstrip("0")


def fmt_day(dt):
    today = date.today()
    d = dt.date() if isinstance(dt, datetime) else dt
    if d == today:
        return "today"
    if d == today + timedelta(days=1):
        return "tomorrow"
    return d.strftime("%a %d %b")


def http_get(url, params=None, timeout=12, retries=2):
    import requests  # imported here so the rest works even if it's missing
    last_err = None
    for attempt in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout, headers={"User-Agent": USER_AGENT})
            r.raise_for_status()
            return r
        except Exception as e:
            last_err = e
            time.sleep(0.4)
    if last_err:
        raise last_err


def keep_case(part, raw):
    """We match commands in lower case; this gives back the words as you typed them."""
    i = raw.lower().find(part.lower())
    return raw[i:i + len(part)] if part and i >= 0 else part


def reply(text, say=None, **extra):
    """A finished answer: text for the screen, and a (shorter) version to speak."""
    out = {"text": text, "say": say if say is not None else text}
    out.update(extra)
    return out


# ---------------------------------------------------------------- the brain

from vega_control import ControlMixin, tenglish_to_command  # noqa: E402
from vega_lablog import LabLogMixin  # noqa: E402
from vega_tracks import TracksMixin  # noqa: E402
from vega_summaries import SummaryMixin  # noqa: E402


class Vega(ControlMixin, LabLogMixin, TracksMixin, SummaryMixin):
    def __init__(self):
        self.cfg = load_config()
        self.name = self.cfg["name"]
        self.lock = threading.RLock()
        self.history = self._load_history()
        self.diary_mode = False
        self.active_doc = None   # (name, text)
        self.stop_flag = threading.Event()
        os.makedirs(here(self.cfg["documents_folder"]), exist_ok=True)
        os.makedirs(here("diary"), exist_ok=True)

    # ---------------- memory
    def _load_history(self):
        data = read_json("memory.json", [])
        if not isinstance(data, list):
            return []
        return [m for m in data if isinstance(m, dict) and m.get("role") in ("user", "assistant") and m.get("content")]

    def _save_history(self):
        write_json("memory.json", self.history[-20:])

    RECALL_SKIP = set("about after again also always because before being could doing does from give going have here into "
                      "just know like make more much need only other please really should some tell than that their them "
                      "then there these they thing think this those very want what when where which while will with would "
                      "your yours vega hemanth okay yeah".split())

    def recall_old_chats(self, question, limit=3):
        """Find what we said about this topic in older chats (like Claude / ChatGPT memory), so she remembers everything."""
        if not self.cfg.get("recall_chats", True) or not question:
            return ""
        keys = {w for w in re.findall(r"[a-z0-9+#.]{4,}", question.lower()) if w not in self.RECALL_SKIP}
        if len(keys) < 2:
            return ""
        folder = here("chats")
        current = {str(m.get("content", ""))[:200] for m in self.history}
        hits = []
        try:
            files = sorted((os.path.join(folder, f) for f in os.listdir(folder) if f.endswith(".json")),
                           key=os.path.getmtime, reverse=True)[:300]
        except OSError:
            return ""
        for path in files:
            try:
                with open(path, encoding="utf-8") as fh:
                    msgs = json.load(fh).get("messages", [])
            except (OSError, ValueError):
                continue
            for i, m in enumerate(msgs):
                if m.get("who") not in ("you", "phone"):
                    continue
                text = str(m.get("text", ""))
                if text[:200] in current:
                    continue
                words = set(re.findall(r"[a-z0-9+#.]{4,}", text.lower()))
                score = len(keys & words)
                if score >= 2:
                    answer = next((str(x.get("text", "")) for x in msgs[i + 1:i + 3] if x.get("who") == "vega"), "")
                    hits.append((score, str(m.get("t", ""))[:10], text[:200], answer[:300]))
        hits.sort(key=lambda h: h[0], reverse=True)
        return "\n".join(f"- ({d or 'earlier'}) I said: {q}\n  You answered: {a}" for _, d, q, a in hits[:limit])

    def remember_conversation(self, history=None, quiet=False):
        """Really 'learn' from a chat: the brain picks the few facts worth keeping, and they're saved to memory,
        so every future chat knows them. (Vega can't retrain herself; this memory is how she improves.)
        quiet=True: done automatically after a chat, returns the new points (or []) instead of a reply."""
        import ollama
        recent = [m for m in (history if history is not None else self.history)[-24:] if m.get("role") in ("user", "assistant")]
        if quiet and len(recent) < 4:
            return []
        if len(recent) < 2:
            return reply("There's nothing in this chat to remember yet. Or tell me directly: remember that …")
        convo = "\n".join(f"{'Hemanth' if m['role'] == 'user' else 'Vega'}: {str(m['content'])[:600]}" for m in recent)
        known = self.facts()[-40:]
        prompt = (("Already in memory (don't repeat these, only write NEW facts):\n- " + "\n- ".join(known) + "\n\n" if known else "") +
                  "Read this conversation between Hemanth and his assistant Vega. Write the facts about Hemanth that are worth "
                  "remembering for future chats: his goals, plans, preferences, decisions, important things he told Vega, and how he "
                  "wants Vega to behave. 1 to 6 short lines, each starting with '- ', written like 'Hemanth ...'. Skip small talk, "
                  "never include passwords, phone numbers, OTPs or bank details. If nothing is worth keeping, write only NONE.\n\n" + convo)
        try:
            r = ollama.chat(model=self.smart_model(), keep_alive="15m", options={"temperature": 0.2},
                            messages=[{"role": "user", "content": prompt}])
            text = strip_thinking((r["message"]["content"] if isinstance(r, dict) else r.message.content) or "")
        except Exception as e:
            if quiet:
                return []
            return reply(f"I couldn't read the chat right now ({e}). Tell me directly: remember that …")
        # only the list lines ("- Hemanth ..."), never an intro like "Here are the facts:"
        points = [re.sub(r"^\s*(?:[-•*]|\d+[.)])\s*", "", ln).strip() for ln in text.splitlines()
                  if re.match(r"^\s*(?:[-•*]|\d+[.)])\s+\S", ln) and not ln.strip().endswith(":")]
        points = [p for p in points if len(p) > 8 and p.upper() != "NONE" and not re.search(r"\b\d{6,}\b|password|otp\b", p, re.I)][:6]
        if not points:
            if quiet:
                return []
            return reply("I didn't find anything important to keep from this chat. Tell me directly: remember that …")
        facts = self.facts()

        def same(a, b):  # almost the same sentence = already known
            return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio() > 0.8

        new = [p for p in points if not any(same(p, x) for x in facts)]
        facts = facts + new
        if len(facts) > 80:  # keep memory tidy: the oldest automatic notes go first, the ones he asked for stay
            auto = [f for f in facts if f.startswith("Hemanth")]
            for f in auto[:len(facts) - 80]:
                facts.remove(f)
        self.save_facts(facts)
        if quiet:
            return new
        return reply("Saved to my memory from this chat:\n" + "\n".join("- " + p for p in (new or points)) +
                     "\n(I'll use these in every chat. Say “what do you remember” to see all, or “forget …” to remove one.)",
                     f"I saved {len(new or points)} things from this chat to my memory.")

    def facts(self):
        try:
            with open(here("facts.txt"), "r", encoding="utf-8") as f:
                return [line.strip() for line in f if line.strip()]
        except OSError:
            return []

    def save_facts(self, facts):
        with open(here("facts.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(facts) + ("\n" if facts else ""))

    # ---------------- main entry
    # ---------------- learning from corrections ("no, I meant Brave")
    NOT_LEARNABLE = {"open", "call", "play", "search", "remind", "me", "to", "the", "a", "on", "in", "my", "and", "for",
                     "at", "is", "it", "of", "please", "hey", "vega", "start", "stop", "close", "set", "add", "show",
                     "like", "i", "you", "we", "he", "she", "they", "said", "say", "mean", "meant", "not", "no", "yes", "what",
                     "why", "how", "this", "that", "was", "are", "be", "do", "did", "have", "has", "can", "will", "so", "but", "or"}

    def handle(self, text, source="laptop", on_token=None, spoken=False):
        raw = (text or "").strip()
        self._last_raw = raw.lower()
        prev = getattr(self, "prev_user_raw", None)
        if raw and not self.diary_mode and prev:
            fixed = self.correction(clean_command(raw), prev, source, on_token, spoken)
            if fixed is not None:
                return fixed
        if raw:
            self.prev_user_raw = raw
        return self._handle(text, source, on_token, spoken)

    def correction(self, t, prev, source, on_token, spoken):
        """He's correcting what Vega heard or did. Redo it right, and offer to learn the fix."""
        p = clean_command(prev)
        new = old = None
        m = re.match(r"^(?:no|nope|wrong|that'?s wrong|sorry)?[,.!\s]*(?:it'?s|it is|i said|i meant|i mean)?\s*(.+?)[,\s]+not\s+(?:the\s+)?(.+?)(?:[,.!].*)?$", t, re.I)
        if m:
            cand_new, cand_old = m.group(1).strip(" ,."), m.group(2).strip(" ,.")
            norm_cand_new = re.sub(r"[^a-z']+", "", cand_new.lower())
            if norm_cand_new in ("heyvega", "vega", "heyrekha", "rekha", "wake") or not clean_command(cand_new):
                return reply(f"Got it, Hemanth! I understand you were saying “Hey Vega”, not “{cand_old}”. I'm right here listening!")
            if cand_old and re.search(r"\b" + re.escape(cand_old) + r"\b", p) and not re.search(r"\bnot\b", p):
                new, old = cand_new, cand_old
                nw, ow = new.split(), old.split()
                if len(nw) > len(ow) + 1:  # "I call you Vega not Vivek" -> the fix is just "Vega" for "Vivek"
                    new = " ".join(nw[-len(ow):])
                corrected = re.sub(r"\b" + re.escape(old) + r"\b", new, p)
            else:
                m = None
        if not m:
            m = re.match(r"^(?:no|nope|wrong|that'?s wrong|sorry)[,.!\s]+i\s+(?:meant|said|mean|wanted|asked for)\s+(.+)$", t) or \
                re.match(r"^i\s+(?:meant|mean|said)\s+((?:\S+\s*){1,3})$", t) or \
                re.match(r"^(?:wrong|that'?s wrong|that is wrong)[,.!\s]+(.+)$", t)
            if not m:
                if re.match(r"^(?:that'?s wrong|that is wrong|wrong|you got it wrong|not correct)$", t):
                    return reply("Sorry about that. Tell me what you meant, like: no, I meant open Brave. "
                                 "Then I'll do it right and can remember the fix.")
                return None
            x = m.group(1).strip(" ,.")
            xw, pw = x.split(), p.split()
            verbs = ("open", "call", "play", "search", "remind", "start", "stop", "close", "turn", "set", "add", "wake", "chat", "message")
            if len(xw) > 4 and not (xw[0] in verbs and len(xw) <= 8):
                return None  # "I mean I said hey Vega 7 times..." is just talking, not fixing a word
            if len(xw) >= 2 and xw[0] in ("open", "call", "play", "search", "remind", "start", "stop", "close", "turn", "set", "add", "wake", "chat", "message"):
                corrected = x  # a full command: find what changed
                sm = difflib.SequenceMatcher(None, pw, xw)
                for op, a1, a2, b1, b2 in sm.get_opcodes():
                    if op == "replace":
                        old, new = " ".join(pw[a1:a2]), " ".join(xw[b1:b2])
                        break
            else:  # a short fix like "Brave": swap the word it most likely replaces
                cands = [w for w in pw if w not in self.NOT_LEARNABLE and len(w) >= 3]
                if not cands:
                    return None
                best = max(cands, key=lambda w: difflib.SequenceMatcher(None, w, x.split()[0]).ratio())
                old, new = best, x
                corrected = re.sub(r"\b" + re.escape(best) + r"\b", x, p, count=1)
        out = self._handle(corrected, source, on_token, spoken)
        self.prev_user_raw = corrected
        note = f"(Corrected to: “{corrected}”)\n"
        verbs = ("open", "call", "play", "search", "remind", "start", "stop", "close", "turn", "set", "add", "take", "show")
        if old and new and old != new and old not in self.NOT_LEARNABLE and len(old) >= 3 and len(old) <= 40 \
                and len(old.split()) <= 2 and len(new.split()) <= 2 and new.split()[0] not in verbs and old.split()[0] not in verbs \
                and old not in ("screenshot", "screen shot", "news", "weather", "reminder", "reminders") \
                and self.my_words().get(old) != new:
            self.pending_learn = (old, new, time.time() + 300)
            out["text"] = note + out["text"] + f"\n\nShould I remember this fix? When I hear “{old}”, you mean “{new}”. Say “remember it”."
            out["say"] = (out.get("say") or "") + " Should I remember this fix? Say remember it."
        else:
            out["text"] = note + out["text"]
        return out

    def _handle(self, text, source="laptop", on_token=None, spoken=False):
        """
        Understand one message and answer it.
        source: "laptop" or "phone".  on_token(text_so_far) streams AI replies.
        Returns {"text": ..., "say": ...}.
        """
        raw = (text or "").strip()
        if not raw:
            return reply("I didn't catch that.")
        t = clean_command(raw)

        # Fix verb typos like "opm" -> "open" even when typed
        words = t.split()
        if words and words[0] in ACCENT_VERB_FIXES:
            old_verb = words[0]
            words[0] = ACCENT_VERB_FIXES[old_verb]
            t = " ".join(words)
            raw = t

        # --- Speech autocorrect: fix accent / STT errors before processing ---
        if spoken and t:
            # Filter out obvious STT garbage (ambient noise, TV audio, etc.)
            if SPEECH_GARBAGE.match(t):
                return reply("I didn't catch that. Could you say it again?", "I didn't catch that.")
            # Build known vocabulary from apps, sites, contacts
            vocab = set()
            vocab.update(self.cfg.get("apps", {}).keys())
            vocab.update(self.cfg.get("sites", {}).keys())
            for c in self.load_contacts().values():
                vocab.add(c["name"])
            vocab.update(self.my_words().values())
            # Add common command words
            vocab.update({"open", "close", "play", "call", "search", "remind", "reminder",
                          "weather", "news", "volume", "brightness", "screenshot", "mute",
                          "unmute", "pause", "battery", "lock", "shut", "restart", "sleep",
                          "whatsapp", "youtube", "instagram", "spotify", "netflix", "brave",
                          "chrome", "edge", "notepad", "terminal", "powershell", "virtualbox",
                          "google", "gmail", "github", "linkedin", "coursera", "chatgpt",
                          "claude", "reddit", "wikipedia", "amazon", "flipkart", "leetcode",
                          "hackerrank", "telegram", "facebook", "twitter", "steam", "excel",
                          "word", "powerpoint", "camera", "calendar", "classroom", "drive"})
            corrected, fixes = autocorrect_speech(t, vocab)
            if fixes:
                from vega_voice import log_problem
                log_problem('autocorrect: "%s" -> "%s" (fixes: %s)' % (t, corrected, fixes))
                t = corrected
                raw = corrected  # use corrected text as the raw for AI too

        if self.diary_mode:
            out = self.diary_command(t, raw)
            if out:
                return out
            for skill in (self.time_and_date, self.notebook):
                out = skill(t, raw)
                if out:
                    return out
            self.diary_append(raw)
            return self.chat(raw, source, on_token,
                             instruction="Hemanth is writing his diary and just told you this. "
                                         "Listen like a caring friend. Reply in 1 or 2 short sentences "
                                         "and ask one gentle question about his day. Don't give advice unless he asks.",
                             save=False)

        self._source = source
        out = self.check_confirm(t)  # an answer to "Are you sure?"
        if out is not None:
            return out
        out = self.words_command(t, raw)  # teaching her words comes before using them
        if out is not None:
            return out
        # his own words and Telugu / Tenglish phrases -> Vega's English commands
        t2 = tenglish_to_command(self.apply_my_words(t))
        if t2 != t:
            out = self.run_skills(t2, raw, source, on_token, allow_unknown=False)
            if out is not None and not out.get("unknown"):
                return out
        out = self.run_skills(t, raw, source, on_token)
        if out is not None and not out.get("unknown"):
            return out
        unknown = out
        # Not a clear command. If it sounds like one (or speech may have misheard), ask the AI what was meant.
        if self.cfg.get("smart_commands", True) and self.sounds_like_command(t, spoken):
            fixed = self.understand(raw)
            if fixed and fixed.upper() != "CHAT":
                ft = clean_command(fixed)
                if ft and ft != t and self.routed_ok(raw, ft):
                    out = self.run_skills(ft, fixed, source, on_token, allow_unknown=False)
                    if out is not None:
                        out["text"] = f"(I understood: {fixed})\n" + out["text"]
                        return out
        if unknown is not None:
            return unknown
        out = self.chat(raw, source, on_token)
        fact = self.memory_suggestion(raw) if out.get("kind") == "chat" else None
        if fact:
            self.pending_memory = (fact, time.time() + 300)
            out["text"] += f"\n\nShould I remember this about you: “{fact}”? Say “remember it”."
        return out

    def speech_hotwords(self):
        words = ["Vega", "Hemanth", "Brave", "WhatsApp", "YouTube", "Instagram", "Coursera", "Linux", "OpenShift", "Lab Log", "lablog", "tabs", "all apps"]
        words += [c["name"] for c in self.load_contacts().values()][:20]
        words += list(self.my_words().values())[:10]
        return " ".join(dict.fromkeys(words))

    def speech_hint(self):
        """Words Whisper should expect: helps it hear names and app names in an Indian accent."""
        words = ["Hey Vega", "Hemanth", "open Brave", "Google Chrome", "WhatsApp", "Instagram", "YouTube", "Spotify",
                 "Coursera", "PowerShell", "Terminal", "VS Code", "VirtualBox", "Rocky Linux", "OpenShift", "Kubernetes",
                 "Docker", "AWS", "Python", "Linux", "SRM", "Lab Log", "open Lab Log", "today's Lab Log", "close all tabs",
                 "close them all", "remind me", "close", "restart", "shutdown", "mute", "unmute",
                 "volume", "brightness", "screenshot", "weather", "notes", "study plan", "interview", "explain", "code", "to-do list"]
        words += [c["name"] for c in self.load_contacts().values()][:30]
        words += list(self.my_words())[:20]
        return ", ".join(dict.fromkeys(words)) + "."

    ACTION_WORDS = re.compile(r"\b(cheyyi|cheyu|cheppu|pettu|chupinchu|teruvu|open|launch|start|run|play|call|ring|message|text|remind|reminder|alarm|wake|set|add|put|"
                              r"mark|tick|search|google|look up|show|check|turn|lock|stop|shut|close|weather|news|music|song|"
                              r"video|whatsapp|youtube|instagram|app|task|progress|todo|to-?do|diary|internships?|volume|sound|mute|"
                              r"bright|brightness|wifi|wi-fi|bluetooth|screenshot|shut ?down|restart|sleep|file|folder|find|"
                              r"pause|next|skip|browser|penchu|tagginchu|aapu|teruvu|moosey|vetuku|teeyi)\b")

    # Commands that change something need proof in what he actually said, so the AI can't invent them.
    PROOF = [
        (r"^(remind|wake)", r"remind|reminder|alarm|wake|gurthu|gurtu|guruthu|lepu|lepa|lepandi|nidra"),
        (r"^add\b", r"\badd|to-?do|list|pettu|raasu|rasu|note"),
        (r"^mark\b", r"\bmark|done|finish|complete|ayipoy|aipoy|aindi|ayindi|tick"),
        (r"^i finished", r"finish|done|complete|ayipoy|aipoy|aindi|ayindi"),
        (r"^(call|video call)", r"call|ring|phone|kalupu|video"),
        (r"^chat with", r"chat|message|text|whatsapp|msg"),
        (r"^lock", r"\block"),
        (r"^(start|stop) my vm", r"\bvm\b|virtual|server|machine|rocky|linux"),
        (r"^(shut down|restart|put my laptop to sleep)", r"shut|off|band|restart|reboot|sleep|nidra|power"),
        (r"^close\b", r"close|quit|exit|kill|moose|musey|kattu|aapu|band"),
        (r"^turn (on|off) (wifi|bluetooth)", r"wi-?fi|bluetooth|internet|\bnet\b"),
        (r"^take a screenshot", r"screen|shot|capture|photo|snap"),
        (r"^(volume|mute|unmute)", r"volume|sound|loud|quiet|mute|voice|silent"),
        (r"^brightness", r"bright|screen|light|dim"),
        (r"^quiz", r"quiz|test|question|prashn"),
        (r"^open my diary", r"diary|journal"),
    ]

    def routed_ok(self, raw, cmd):
        low = raw.lower()
        m = re.match(r"^(?:open|launch|start)\s+(.+)$", cmd)
        if m:  # the thing to open must sound like something he actually said
            said = re.findall(r"[a-z0-9]+", low)
            wanted = [w for w in re.findall(r"[a-z0-9]+", m.group(1)) if len(w) >= 3 and w not in ("the", "app", "and", "open", "in")]
            if said and wanted and not any(difflib.SequenceMatcher(None, w, x).ratio() >= 0.6 or w in x or x in w
                                           for w in wanted for x in said if len(x) >= 3):
                return False
        m = re.match(r"^(?:video call|call|chat with)\s+(.+?)(?:\s+on\s+(?:whatsapp|phone))?$", cmd)
        if m:  # "call akka" only if he actually said something like "akka"
            said = re.findall(r"[a-z0-9]+", low)
            who = [w for w in re.findall(r"[a-z0-9]+", m.group(1)) if len(w) >= 3]
            if who and not any(difflib.SequenceMatcher(None, w, x).ratio() >= 0.6 for w in who for x in said if len(x) >= 3):
                return False
        for form, proof in self.PROOF:
            if re.match(form, cmd):
                return bool(re.search(proof, low))
        return True

    def sounds_like_command(self, t, spoken):
        words = t.split()
        if not words or len(words) > 14:
            return False
        # "why did you take a screenshot", "don't open that", "only if I say", "I'll share you a screenshot"
        if re.search(r"\b(?:why|don'?t|didn'?t|do not|did not|never|only if|if i|if you|i will|i'll|i said|you did|you took|"
                     r"you opened|you called|i was|i am not|i'm not)\b", t):
            return False
        if re.match(r"^(?:and|but|so|because|also|then|actually|i think|i feel|i know|i have|i had|i can|i want to tell)\b", t):
            return False
        # "you are ...", "I am ...", "it is ..." are statements, not orders
        if re.match(r"^i (?:call|called|name|named) you\b", t):
            return False
        if re.match(r"^(you are|you're|youre|you were|i am|i'm|im|i was|it is|it's|its|that is|that's|this is|he is|she is|we are)\b", t) \
                and not re.search(r"\b(open|play|call|remind|search|start|stop|lock|add)\b", t):
            return False
        if re.match(r"^(who|what|why|how|when|where|explain|tell me|can you explain|is it|are you|do you)\b", t) and not self.ACTION_WORDS.search(t):
            return False
        # Only real orders go to the command-guesser, so a normal sentence that just contains a word like
        # "youtube" or "weather" gets a normal answer, not an action about that one word.
        if re.match(r"^(?:please\s+|hey\s+|ok\s+|okay\s+)?(?:open|launch|start|close|quit|exit|play|pause|resume|stop|call|video call|"
                    r"message|text|chat with|search|find|google|look up|remind|set|add|mark|tick|turn|switch|increase|decrease|"
                    r"lower|raise|mute|unmute|volume|brightness|take|show|read|lock|shut|restart|reboot|put|go to|check|"
                    r"wake|delete|remove|cancel|install|download|send|what'?s|whats|next|skip|unlock)\b", t):
            return len(words) <= 12
        if re.search(r"\b(?:cheyyi|cheyu|chey|pettu|kottu|teeyi|teruvu|moosey|musey|aapu|penchu|tagginchu|chupinchu|vetuku|cheppu)\b", t):
            return len(words) <= 10
        return spoken and len(words) <= 3  # "youtube please", "brave browser"

    def understand(self, raw):
        """Ask the AI to turn a messy or misheard sentence into one exact Vega command (or CHAT)."""
        import ollama
        names = sorted(set(list(self.cfg["apps"]) + list(self.cfg["sites"]) + [a["name"].lower() for a in self.app_index()]))[:150]
        contacts = [c["name"] for c in self.load_contacts().values()][:40]
        prompt = (
            "You turn Hemanth's message into ONE exact command for his assistant program. He may write in English, Telugu or "
            "Tenglish (Telugu in English letters, e.g. 'youtube open cheyyi' = 'open youtube', 'repu 7 ki amma ki call cheyyamani "
            "gurthu cheyyi' = 'remind me at 7 pm tomorrow to call amma'). His messages often come from "
            "speech-to-text with an Indian English accent, so words may be misheard (for example 'Oh, better Apple Music' "
            "means 'open apple music', 'call the car' may mean 'call akka').\n"
            "Reply with JSON only: {\"command\": \"...\"}\n"
            "Command forms (copy the form exactly, fill in the <...>):\n"
            "open <app or website> | open <website> in <browser> | play <song or video> | search google for <words> |\n"
            "remind me at <time> to <thing> | remind me in <n> minutes to <thing> | wake me up at <time> | my reminders |\n"
            "add <item> to my to-do list | my to-do list | mark <item> done |\n"
            "call <contact> on whatsapp | video call <contact> | chat with <contact> |\n"
            "weather | weather tomorrow | weather in <city> | news | tech news | cricket | good morning | internships |\n"
            "what's my task today | I finished today's task | my progress | what did I miss | quiz me |\n"
            "start my vm | stop my vm | battery | laptop status | lock my laptop | open my diary | what time is it |\n"
            "volume up | volume down | volume <n> | mute | unmute | pause | next song | previous song |\n"
            "brightness up | brightness down | brightness <n> | turn on wifi | turn off wifi | turn on bluetooth | turn off bluetooth |\n"
            "take a screenshot | find file <name> | open <downloads/documents/desktop/pictures> folder | close <app> |\n"
            "close all apps | close all other apps except <app> | close all tabs except this one | close this tab |\n"
            "shut down my laptop | restart my laptop | put my laptop to sleep | call <contact> on phone\n"
            f"Apps and websites he has: {', '.join(names)}\n"
            f"His contacts: {', '.join(contacts) or 'none yet'}\n"
            f"His web browser is {self.cfg.get('browser') or 'brave'}. Speech-to-text often writes 'Brave' as 'prior', 'brev' or 'imperial'.\n"
            "Never swap an app for a different one (don't turn Brave into Chrome).\n"
            "If the message is a normal question or conversation (not a request to do something), reply {\"command\": \"CHAT\"}.\n"
            "Never invent a command that is not in the list."
        )
        try:
            router = self.smart_model() if self.cfg.get("one_brain", True) else self.cfg["model"]
            r = ollama.chat(model=router, format="json", keep_alive="15m", options={"temperature": 0},
                            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": raw}])
            content = r["message"]["content"] if isinstance(r, dict) else r.message.content
            clean_json = re.sub(r"^```(?:json)?\s*|\s*```$", "", (content or "").strip(), flags=re.I).strip()
            m = re.search(r"\{.*?\}", clean_json, re.S)
            data = json.loads(m.group(0) if m else clean_json)
            cmd = str(data.get("command", "")).strip()
            return cmd[:200] or None
        except Exception:
            return None

    def listening_command(self, t, raw):
        if re.match(r"^(?:stop|pause|don'?t|do not)\s+(?:listening|hearing me|listen)(?:\s+(?:to me|for now|now))?$|^(?:mic|microphone) off$|^be quiet$", t):
            return reply("Okay, I've stopped listening for “Hey Vega”. Press Mic, or tick “Listen for Hey Vega”, when you need me.",
                         "Okay, I stopped listening.", wake=False)
        if re.match(r"^(?:start|resume)\s+listening(?:\s+again)?$|^listen for hey vega$", t):
            return reply("Okay, I'm listening for “Hey Vega” again.", "Okay, I'm listening again.", wake=True)
        if re.match(r"^(?:are you|can you)?\s*(?:listening|hear me|hearing me|there|still there)\??$", t) or \
           re.match(r"^(?:you there|listening)\??$", t):
            return reply("I'm right here and listening, Hemanth! What can I help you with?",
                         "I'm listening, Hemanth! What's up?")
        return None

    def voice_language(self, t, raw):
        """'speak in English' / 'speak in Telugu' / 'english lo matladu' / 'telugu lo cheppu'."""
        te = re.search(r"^(?:please\s+)?(?:speak|talk|reply|answer|say it|respond)\s+(?:to me\s+)?(?:in|with)\s+telugu(?:\s+(?:voice|now|please|from now on))*$|"
                       r"^(?:switch|change)\s+(?:your\s+)?(?:voice|language)?\s*(?:to\s+)?telugu(?:\s+voice)?$|"
                       r"^telugu\s*(?:lo|lone)\s+(?:matladu|matladandi|cheppu|chepu|maatladu|matlaadu)(?:\s+(?:ra|please))?$|^telugu voice(?: on)?$", t)
        en = re.search(r"^(?:please\s+)?(?:speak|talk|reply|answer|say it|respond)\s+(?:to me\s+)?(?:in|with)\s+english(?:\s+(?:voice|now|please|again|from now on))*$|"
                       r"^(?:switch|change)\s+(?:your\s+)?(?:voice|language)?\s*(?:back\s+)?(?:to\s+)?english(?:\s+voice)?$|"
                       r"^english\s*(?:lo|lone)\s+(?:matladu|matladandi|cheppu|chepu|maatladu|matlaadu)(?:\s+(?:ra|please))?$|"
                       r"^(?:english voice(?: on)?|stop (?:speaking|talking) (?:in )?telugu|telugu voice off)$", t)
        auto = re.search(r"^(?:speak|talk|reply|answer)\s+(?:in\s+)?(?:my|the same|same)\s+language(?:\s+as me)?$|"
                         r"^(?:auto|automatic)\s+(?:voice\s+)?language$|^match my language$|^(?:voice\s+)?language\s+auto$", t)
        if te:
            return reply("Okay, I'll always speak in Telugu now. The text stays in English so you can read it.\n"
                         "Say “match my language” to go back to answering in whichever language you use.",
                         "Okay, I will always speak in Telugu now.", voice_lang="telugu")
        if en:
            return reply("Okay, I'll always speak in English now.\n"
                         "Say “match my language” to go back to answering in whichever language you use.",
                         "Okay, I'll always speak in English now.", voice_lang="english")
        if auto:
            return reply("Okay. If you speak Telugu (or Tenglish), I'll answer in Telugu. If you speak English, I'll answer in English.",
                         "Okay. I'll answer in the language you speak.", voice_lang="auto")
        return None

    def run_skills(self, t, raw, source, on_token, allow_unknown=True):
        """Try every skill. Returns an answer, or None if nothing matched."""
        skills = (self.help, self.remember_it, self.listening_command, self.voice_language, self.words_command, self.lablog_command, self.projects_command, self.summarize, self.briefing, self.internships, self.time_and_date, self.notebook,
                  self.diary_command, self.todo, self.tracking, self.reminders, self.phone_call, self.contacts, self.vm, self.devops_command,
                  self.lock_screen, self.laptop_status, self.documents, self.laptop_control, self.weather, self.news,
                  self.search_and_play, self.sites_command, self.open_thing, self.plan, self.wikipedia)
        self._allow_unknown = allow_unknown
        # Complaints, questions and conditions are not orders: "why did you take a screenshot", "don't lock my laptop",
        # "take a screenshot only if I say", "I'll share you a screenshot". Those go to normal chat, never to an action.
        talking = re.search(r"\b(?:why|didn'?t|did not|don'?t|do not|never|only if|if i|if you|i will|i'll|i said|you did|you took|"
                            r"you opened|you called|you are|you were|should i|shall i)\b", t) and \
            not re.match(r"^(?:cancel|abort|stop|don'?t|do not)\s+(?:the\s+)?(?:shut ?down|shutdown|restart|reboot)$", t)
        actions = {self.phone_call, self.contacts, self.vm, self.devops_command, self.lock_screen, self.laptop_control, self.search_and_play, self.open_thing}
        if talking:
            skills = tuple(sk for sk in skills if sk not in actions)
        # A long normal sentence ("I wanted to go out but the weather ruined my plan and I missed my study task")
        # is talking, not a request: don't answer just one word of it (weather / task / file / battery...).
        request = re.match(r"^(?:please\s+|hey\s+|ok\s+|okay\s+|can you\s+|could you\s+)?(?:what|what'?s|whats|when|where|which|how|is|are|will|"
                           r"do|does|did|can|could|show|tell|give|read|open|check|set|add|remind|play|search|find|start|stop|"
                           r"call|mark|turn|any|list|today'?s|tomorrow'?s)\b", t)
        if len(t.split()) > 9 and not request:
            keyword = {self.weather, self.news, self.internships, self.laptop_status, self.documents, self.plan, self.tracking,
                       self.wikipedia, self.search_and_play, self.sites_command, self.open_thing, self.time_and_date}
            if talking:
                keyword.discard(self.news)  # "don't show me the news..." still reaches the news skill, which says sorry
            skills = tuple(sk for sk in skills if sk not in keyword)
        for skill in skills:
            try:
                out = skill(t, raw)
            except Exception as e:  # a skill failed (no internet, etc.)
                kind = type(e).__name__
                if "Connection" in kind or "Timeout" in kind or "SSL" in kind:
                    return reply("I couldn't reach the internet. Check your Wi-Fi and try again.")
                if isinstance(e, ModuleNotFoundError):
                    return reply(f"A part of me isn't installed ({e.name}). Double-click 'Install Vega.bat' once.",
                                 "A part of me isn't installed. Run Install Vega once.")
                return reply(f"Sorry, that didn't work: {e}", "Sorry, that didn't work. The details are on screen.")
            if out is None:
                continue
            if out.get("unknown") and not getattr(self, "_allow_unknown", True):
                return None
            if out.get("ask"):
                self._force_model = out.get("model")  # summaries always use the smart brain
                try:
                    return self.chat(out["ask"], source, on_token, shown=raw, instruction=out.get("instruction"))
                finally:
                    self._force_model = None
            return out
        return None

    # ---------------- AI chat
    def context_notes(self, source, instruction=None):
        now = datetime.now()
        notes = [f"Right now it is {now.strftime('%A %d %B %Y, %I:%M %p')}."]
        facts = self.facts()
        if facts:
            notes.append("Things I asked you to remember about me:\n- " + "\n- ".join(facts))
        pers = read_json("personal.json", {}) or {}
        if str(pers.get("about", "")).strip():
            notes.append("About me (from my personal settings): " + str(pers["about"]).strip()[:1500])
        if str(pers.get("style", "")).strip():
            notes.append("How I want you to answer (always follow this): " + str(pers["style"]).strip()[:800])
        if self.cfg.get("study_plan_reminders") or re.search(r"\b(study|plan|roadmap|task|lab|week|rhcsa|coursera|linux)\b",
                                                               (instruction or "") + " " + str(getattr(self, "_last_raw", ""))):
            today = self.plan_context()
            if today:
                notes.append(today + " (Only bring this up if I ask about my studies.)")
        if self.active_doc:
            name, text = self.active_doc
            notes.append(f"I opened the document '{name}'. Its text (maybe cut short):\n\"\"\"\n{text}\n\"\"\"")
        notes.append(
            "You are Vega chatting directly with Hemanth. Answer naturally, warmly, and concisely. "
            "Never say 'Sorry, I didn't open Chrome' or apologize about apps unless Hemanth specifically asked you to open an app. "
            "If he says 'are you listening' or greets you, simply confirm warmly that you are listening and ask what he needs.")
        notes.append(
            "How to answer me: like a smart, kind friend who knows a lot. Be correct and direct. Keep it short (2 to 5 sentences) "
            "unless I ask for detail, steps, notes or code. Use simple English. For study or Linux questions, explain step by step "
            "with a small example. My messages usually come from speech-to-text, so a word can be wrong: guess what I most likely "
            "meant; if you really can't tell, ask me one short question. Never comment on being tested, never make up facts, "
            "and if you don't know something, say so. Be honest about yourself: you can't retrain or improve yourself from chats. "
            "You only remember what is saved in your memory (when I say 'remember that …' or 'remember this conversation'), "
            "my word fixes, and the recent messages of this chat. If I ask you to learn from our chats, tell me to say "
            "'remember this conversation'. Speech-to-text often writes 'Claude' (the AI assistant) as 'cloud' or 'clod': "
            "if I say 'a prompt for cloud' or 'ask cloud', I mean Claude. 'Vega' can come out as 'Vivek' or 'we go'.")
        recall = self.recall_old_chats(str(getattr(self, "_last_raw", "")))
        if recall:
            notes.append("From our earlier chats (use only if it helps answer me):\n" + recall)
        if source == "phone":
            notes.append("I'm messaging you from my phone on Telegram, so keep replies short.")
        if getattr(self, "project_note", ""):
            notes.append(self.project_note)
        mw = self.my_words()
        if mw:
            notes.append("My own words and slang: " + "; ".join(f"'{k}' means '{v}'" for k, v in list(mw.items())[:40]) + ".")
        feel = self.emoji_feelings(str(getattr(self, "_last_raw", "")))
        if feel:
            notes.append("The emojis / smileys in my last message show how I feel: " + ", ".join(feel) +
                         ". Notice my mood and reply to it naturally (for example, cheer me up if I'm sad or angry).")
        gloss = self.tenglish_gloss(str(getattr(self, "_last_raw", "")))
        if gloss:
            notes.append("Telugu words in my last message (Tenglish): " + "; ".join(gloss) + ". Use these meanings.")
        notes.append("I sometimes write in Telugu or Tenglish (Telugu in English letters, like 'naaku help cheyyi'). "
                     "Understand it, and always reply in simple English (English letters only, never Telugu script), because I read English best. "
                     + ("Your answers are turned into a Telugu voice by the app, so if I ask, yes, you can speak Telugu." if self.cfg.get("speak_telugu") else ""))
        if instruction:
            notes.append(instruction)
        return "\n\n".join(notes)

    # Common Tenglish (Telugu in English letters) word starts -> meaning, so the AI understands mixed talk
    TENGLISH_GLOSS = [
        ("matlad", "speak / talk"), ("maatlad", "speak / talk"), ("ardham", "understand / meaning"), ("artham", "understand / meaning"),
        ("avthund", "is it happening? / do you (question)"), ("avtund", "is it happening? / do you (question)"),
        ("aithund", "is it happening?"), ("cheppu", "tell"), ("chepp", "tell"), ("cheyyi", "do"), ("cheyali", "have to do"),
        ("chesa", "I did"), ("chest", "doing"), ("enti", "what"), ("emiti", "what"), ("emi", "what"), ("ela", "how"),
        ("enduku", "why"), ("ekkada", "where"), ("eppudu", "when"), ("entha", "how much"), ("evaru", "who"),
        ("nenu", "I"), ("naaku", "to me / I have"), ("naku", "to me / I have"), ("nuvvu", "you"), ("neeku", "to you"),
        ("meeru", "you (respect)"), ("ledu", "no / not there"), ("ledh", "no / not"), ("kaadu", "no / it is not"),
        ("avunu", "yes"), ("sare", "okay"), ("kavali", "want / need"), ("vaddu", "don't want"), ("telusu", "know"),
        ("teliyadu", "don't know"), ("teledu", "don't know"), ("baga", "well / good"), ("bagun", "is good"), ("inka", "more / still"),
        ("ippudu", "now"), ("repu", "tomorrow"), ("ninna", "yesterday"), ("ivala", "today"), ("vinu", "listen"),
        ("chudu", "look / see"), ("chusko", "check / look"), ("nerchuk", "learn"), ("chaduv", "study / read"), ("pani", "work"),
        ("undhi", "is there / have"), ("undi", "is there / have"), ("ra", "(friendly word, like 'bro')"), ("anna", "brother / bro"),
        ("akka", "elder sister"), ("amma", "mother"), ("nanna", "father"), ("em chestunnav", "what are you doing"),
        ("ela unnav", "how are you"), ("thinnava", "did you eat"), ("sorry ra", "sorry bro"),
    ]

    EMOJI_FEEL = [("😂🤣😆😹", "laughing"), ("😀😃😄😁😊☺🙂😸", "happy"), ("😍🥰😘❤💕💖💗💞💓😻♥", "love"),
                  ("😢😭😿💔🥺", "sad / crying"), ("😞😔😟😕🙁☹😩😫", "disappointed / down"), ("😡😠🤬👿💢", "angry"),
                  ("😤", "frustrated"), ("😱😨😰😧", "scared / shocked"), ("😴🥱💤", "tired / sleepy"), ("🤔🧐", "thinking / unsure"),
                  ("😅😬", "awkward / nervous"), ("🙄😒", "annoyed / unimpressed"), ("😎🤩🥳🎉🔥💯✨", "excited / proud"),
                  ("👍👌✅🙌👏💪", "okay / good / agree"), ("👎❌", "no / disagree"), ("🙏", "please / thanks"),
                  ("😮😲🤯", "surprised"), ("🤒🤕😷🤢🤮", "unwell"), ("😇", "innocent / kind"), ("😏😉", "playful / teasing")]
    SMILEYS = [(r":-?\)|:-?\]|\(:", "happy"), (r":-?d\b|xd\b", "laughing"), (r":-?\(|:'-?\(", "sad"), (r"<3", "love"),
               (r";-?\)", "playful / teasing"), (r":-?p\b", "playful"), (r":-?/|:-?\\", "unsure"), (r">:-?\(", "angry")]

    def emoji_feelings(self, text):
        out = []
        for chars, feel in self.EMOJI_FEEL:
            if any(c in text for c in chars) and feel not in out:
                out.append(feel)
        low = (text or "").lower()
        for pattern, feel in self.SMILEYS:
            if re.search(pattern, low) and feel not in out:
                out.append(feel)
        return out[:4]

    def tenglish_gloss(self, text):
        words = re.findall(r"[a-z]+", (text or "").lower())
        out = []
        for stem, meaning in self.TENGLISH_GLOSS:
            if " " in stem:
                if stem in " ".join(words):
                    out.append(f"'{stem}' = {meaning}")
            elif len(stem) <= 3:
                if stem in words:
                    out.append(f"'{stem}' = {meaning}")
            elif any(w.startswith(stem) for w in words):
                w = next(w for w in words if w.startswith(stem))
                out.append(f"'{w}' = {meaning}")
        seen, uniq = set(), []
        for g in out:
            if g.split("=")[0] not in seen:
                seen.add(g.split("=")[0]); uniq.append(g)
        return uniq[:12] if len(uniq) >= 2 else []  # one English-looking word alone isn't Telugu

    # ---------------- which brain answers
    # Programming goes to the coder brain. Linux / admin questions go to the smart brain (it explains better).
    CODE_WORDS = re.compile(r"\b(code|coding|python|java|javascript|typescript|c\+\+|html|css|sql|program|programming|"
                            r"function|class|method|loop|array|traceback|exception|compile|compiler|syntax error|regex|"
                            r"write a script|bash script|shell script|python script|leetcode|algorithm|"
                            r"docker|kubernetes|k8s|openshift|ci/cd|pipeline|aws|ec2|s3|iam|vpc|linux|systemctl|"
                            r"journalctl|cron|bash|shell|git|github|devops|cloud|virtualbox|vm|server|database|"
                            r"api|rest|json|yaml|powershell|cmd)\b", re.I)

    SMART_PREFERENCE = ["qwen2.5:3b", "qwen2.5-coder:3b", "llama3.2:3b", "qwen3:4b"]
    CODE_SIGNS = re.compile(r"```|\bdef |\bimport |#include|\bprintf\(|\bconsole\.log|=>|\};|"
                            r"\.(py|java|js|ts|cpp|html|css|sql)\b|segmentation fault|stack trace|traceback", re.I)
    SMALL_TALK = re.compile(r"^(hi|hello|hey|hii+|good (morning|afternoon|evening|night)|thanks|thank you|ok|okay|"
                            r"bye|how are you|what'?s up|sup|nice|cool|great|lol|haha|yes|no|hmm+|gunnai|good|"
                            r"who are you|what is your name|what's your name|who made you|tell me about yourself|"
                            r"what can you do|can you hear me|are you there|how are you doing)\b", re.I)
    THINK_WORDS = re.compile(r"\b(explain|what is|what are|what's the difference|difference|define|definition|why|how does|"
                             r"how do|how to|describe|compare|advantages|disadvantages|meaning|summari[sz]e|notes on|"
                             r"assignment|essay|question|answer|teach|learn|example|steps|plan|interview|resume|career|"
                             r"fundamentals|concept|theory|history|analy[sz]e|suggest|advice|should i|tell me about|"
                             r"who is|help me with|how can|write a|create a|give me|pros and cons|guide|tips|solve|calculate|"
                             r"troubleshoot|debug|recommend)\b", re.I)

    def installed_models(self, fresh=False):
        import ollama
        now = time.time()
        cache = getattr(self, "_models_cache", None)
        if cache and not fresh and now - cache[0] < 60:
            return cache[1]
        try:
            names = [m.model for m in ollama.list().models]
        except Exception:
            names = cache[1] if cache else []
        self._models_cache = (now, names)
        return names

    def has_model(self, name):
        return bool(name) and any(self._same(n, name) for n in self.installed_models())

    @staticmethod
    def _same(a, b):
        a, b = a.lower(), b.lower()
        return a == b or a == b + ":latest" or b == a + ":latest"

    def smart_model(self):
        """The best 'thinking' brain he has installed (or the one set in settings)."""
        want = (self.cfg.get("smart_model") or "").strip()
        if want and self.has_model(want):
            return want
        for name in self.SMART_PREFERENCE:
            if self.has_model(name):
                return name
        return self.cfg.get("model") or "vega"

    def question_kind(self, text):
        """'code', 'think' (study / explain / long question) or 'quick' (small talk)."""
        t = (text or "").strip()
        if self.CODE_WORDS.search(t) or self.CODE_SIGNS.search(t):
            return "code"
        if self.active_doc or self.THINK_WORDS.search(t):
            return "think"
        if self.SMALL_TALK.search(t):
            return "quick"
        words = len(t.split())
        if words > 10 or (t.endswith("?") and words > 4):
            return "think"
        if words <= 6:
            return "quick"
        return "think"

    def pick_model(self, text):
        """Choose which brain answers: fast Vega for small talk, Qwen 7B for thinking and code."""
        forced = getattr(self, "_force_model", None)
        if forced and self.has_model(forced):
            return forced
        main = self.cfg.get("model") or "vega"
        if self.diary_mode:
            return main
        chosen = (self.cfg.get("chat_model") or "").strip()
        code = (self.cfg.get("code_model") or "").strip()
        kind = self.question_kind(text)
        if kind == "code" and code and self.cfg.get("auto_code_model", True) and self.has_model(code):
            return code
        if chosen.lower() == "auto":
            # Smart switch: fast Vega for small talk and greetings; smart Qwen 7B for study, thinking and code
            if self.cfg.get("one_brain", False):
                return self.smart_model()
            return self.smart_model() if kind in ("think", "code") else main
        return chosen or main

    def personality(self):
        """Vega's personality and system prompt: as clear and correct as ChatGPT or Claude."""
        facts_list = self.facts()
        facts_text = ("- " + "\n- ".join(facts_list)) if facts_list else "None recorded yet."
        my_prompt = str((read_json("personal.json", {}) or {}).get("prompt", "")).strip()[:4000]
        extra = f"\n\nHemanth\'s custom instructions:\n{my_prompt}" if my_prompt else ""
        return (
            "You are Vega, Hemanth\'s personal AI assistant, running directly on his Windows laptop (HP Victus).\n"
            "Your answers must be as clear, intelligent, and accurate as ChatGPT and Claude.\n\n"
            "CORE RULES:\n"
            "1. Tone & Style: Be warm, intelligent, and concise. Give short, direct answers for simple questions. "
            "Provide clear, step-by-step explanations for how-to questions.\n"
            "2. Code: Always put code inside Markdown code blocks with the correct language identifier (e.g., ```python, ```c, ```powershell).\n"
            "3. Honesty: Be completely honest when unsure or when you do not know something. Never invent facts.\n"
            "4. Emojis: Do NOT use emojis unless Hemanth uses them first in his message.\n"
            "5. Windows Environment: You run on Hemanth\'s Windows laptop. NEVER give Linux or terminal commands for him to run on his laptop. "
            "Never tell him to run `kill -9`, `rm -rf`, `sudo`, `cat`, or `grep` for Windows tasks. Use PowerShell commands when needed.\n"
            "6. Unsupported Actions: If Hemanth asks you for a laptop action or automation that you cannot do, reply in exactly one line:\n"
            "\'I can\'t do that yet, Hemanth. Want me to learn it?\'\n"
            "7. Language & Tenglish: If Hemanth speaks or writes in Tenglish (Telugu in English script), reply in natural, friendly Tenglish. "
            "If he writes in English, reply in English.\n"
            "8. Personal Facts: Use these facts about Hemanth naturally in conversation when relevant, without awkwardly reciting them:\n"
            f"{facts_text}{extra}"
        )

    def gemini_chat(self, text, history, on_token=None, system_instruction=None, is_hard_or_code=False):
        """Call Google Gemini REST API. Returns (answer, model_name) on success, or None on failure/offline."""
        import requests
        api_key = self.cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
        if not api_key or self.cfg.get("private_mode", False):
            return None

        # Preferred models verified to work on the user's key
        if is_hard_or_code:
            models = [
                self.cfg.get("cloud_smart_model") or "gemini-3-flash-preview",
                "gemini-3.6-flash",
                "gemini-3.5-flash-lite",
            ]
        else:
            models = [
                self.cfg.get("cloud_model") or "gemini-3.5-flash-lite",
                "gemini-3-flash-preview",
                "gemini-3.1-flash-lite-preview",
            ]

        contents = []
        for m in history:
            role = "user" if m.get("role") == "user" else "model"
            msg_text = m.get("content", "")
            if msg_text:
                clean_text = re.sub(r"^\[\d{1,2}\s+[A-Za-z]{3}\s+\d{4}[^\]]*\]\s*", "", msg_text)
                contents.append({"role": role, "parts": [{"text": clean_text}]})

        contents.append({"role": "user", "parts": [{"text": text}]})

        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": 0.3 if is_hard_or_code else 0.5,
                "maxOutputTokens": 4096,
            }
        }
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        for model in models:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
            try:
                r = requests.post(url, json=payload, timeout=12)
                if r.status_code == 200:
                    data = r.json()
                    cands = data.get("candidates", [])
                    if cands:
                        parts = cands[0].get("content", {}).get("parts", [])
                        if parts:
                            ans = parts[0].get("text", "").strip()
                            if ans:
                                if on_token:
                                    on_token(ans)
                                return ans, model
                elif r.status_code in (429, 503):
                    continue
            except Exception:
                continue
        return None

    def ollama_chat(self, text, history, on_token=None, system_instruction=None, model=None):
        """Call local Ollama model (Qwen 3B / Coder 3B). Streams and returns (answer, model_name)."""
        import ollama
        target_model = model or self.pick_model(text)
        opts = {"num_ctx": int(self.cfg.get("chat_context", 4096) or 4096)}

        msgs = [{"role": "system", "content": system_instruction or self.personality()}]
        for m in history:
            role = m.get("role", "user")
            msg_text = m.get("content", "")
            if msg_text:
                clean_text = re.sub(r"^\[\d{1,2}\s+[A-Za-z]{3}\s+\d{4}[^\]]*\]\s*", "", msg_text)
                msgs.append({"role": role, "content": clean_text})
        msgs.append({"role": "user", "content": text})

        answer = ""
        main_model = self.cfg.get("model") or "qwen2.5:3b"
        active_model = target_model
        try:
            for part in ollama.chat(model=active_model, messages=msgs, stream=True, keep_alive="30m", options=opts):
                piece = part["message"]["content"] if isinstance(part, dict) else part.message.content
                answer += piece or ""
                if on_token:
                    on_token(strip_thinking(answer))
                if self.stop_flag.is_set():
                    answer += " …(stopped)"
                    break
        except Exception as e:
            if active_model != main_model:
                try:
                    active_model = main_model
                    for part in ollama.chat(model=active_model, messages=msgs, stream=True, keep_alive="30m", options=opts):
                        piece = part["message"]["content"] if isinstance(part, dict) else part.message.content
                        answer += piece or ""
                        if on_token:
                            on_token(strip_thinking(answer))
                        if self.stop_flag.is_set():
                            break
                except Exception as e2:
                    return f"I couldn\'t reach Ollama ({e2}). Is the Ollama app running?", active_model
            else:
                return f"I couldn\'t reach Ollama ({e}). Is the Ollama app running?", active_model

        return answer, active_model

    def chat(self, text, source="laptop", on_token=None, shown=None, instruction=None, save=True):
        with self.lock:
            stamp = datetime.now().strftime("%d %b %Y, %I:%M %p")
            keep = 6 if self.active_doc else 14
            self.stop_flag.clear()
            
            # Check if user used emoji
            user_has_emoji = bool(re.search(r"[\U00010000-\U0010ffff]", text))

            # Determine query complexity
            is_hard_or_code = self.question_kind(text) in ("code", "think")
            sys_prompt = self.personality()
            if instruction:
                sys_prompt += f"\n\nSpecial instructions for this answer: {instruction}"

            answer = ""
            model_used = ""
            source_label = "Online"

            # 1. Try Google Gemini if online and not in private mode
            if not self.cfg.get("private_mode", False):
                gemini_res = self.gemini_chat(text, self.history[-keep:], on_token=on_token,
                                              system_instruction=sys_prompt, is_hard_or_code=is_hard_or_code)
                if gemini_res is not None:
                    answer, model_used = gemini_res
                    source_label = "Online"

            # 2. Automatic Fallback to Local Ollama model if Gemini failed, offline, or private mode
            if not answer:
                source_label = "Offline"
                local_model = self.pick_model(text)
                answer, model_used = self.ollama_chat(text, self.history[-keep:], on_token=on_token,
                                                      system_instruction=sys_prompt, model=local_model)

            answer = strip_thinking(answer).strip() or "(no answer)"
            answer = self.no_fake_actions(answer)

            # Strip emoji if user did not use any
            if not user_has_emoji:
                answer = re.sub(r"[\U00010000-\U0010ffff]", "", answer).strip()

            if not answer:
                answer = "I\'m right here and listening, Hemanth! What can I help you with?"

            self.last_model = model_used
            if save:
                self.history.append({"role": "user", "content": f"[{stamp}] {shown or text}"})
                self.history.append({"role": "assistant", "content": answer})
                self._save_history()

            out = reply(answer, answer, kind="chat")
            out["model"] = model_used
            out["source_label"] = source_label
            return out

    FAKE_ACTION = re.compile(
        r"^(?:okay|ok|sure|done|alright|great|fine|yes)\b[^.\n?]{0,40}?\b(?:shut(?:ting)? down|restart(?:ing)?|reboot(?:ing)?|"
        r"turn(?:ing)? off|power(?:ing)? off)\s+(?:your|the|my)\s+(?:laptop|computer|pc|system)\b|"
        r"\bi(?:'m| am| will|'ll|'ve| have)\s+(?:now\s+)?(?:shut(?:ting)? down|shut|restart(?:ing|ed)?|turn(?:ing|ed)? off)\s+"
        r"(?:your|the)\s+(?:laptop|computer|pc|system)\b|"
        r"^(?:shut down|restart|reboot) your (?:laptop|computer|pc|system)\?", re.I)

    def no_fake_actions(self, answer):
        """The chat brain can't touch the laptop. If it pretends it shut it down (or asks 'are you sure?' itself),
        say what really happened, so he isn't left waiting."""
        if self.FAKE_ACTION.search(answer):
            return ("I didn't do that. Only my commands can control the laptop, and I didn't catch one.\n"
                    "Say or type exactly:  shut down my laptop   (or: restart my laptop, sleep)\n"
                    "I'll ask “are you sure?”, then say “yes”.")
        return answer

    def stop(self):
        self.stop_flag.set()

    # ---------------- help, time
    def help(self, t, raw):
        if not re.search(r"^(help|what can you do|what do you do|your commands|show commands|commands)$", t):
            return None
        text = (
            "Here's what I can do. Just say or type it:\n"
            "• Chat: ask me anything, or say 'interview me on my resume'\n"
            "• Talk to me from any app: press Ctrl + Alt + V, then speak (no need to say Hey Vega)\n"
            "• Woke up by mistake? Say or type 'that wasn't for you' and I'll learn the word that fooled me\n"
            "• Time: 'what time is it', 'what's the date'\n"
            "• Reminders: 'remind me at 7 pm to call Amma', 'remind me in 20 minutes to drink water', "
            "'wake me up at 6 am', 'my reminders', 'cancel reminder Amma'\n"
            "• My tasks: 'add submit lab record for tomorrow', 'add gym every day', 'add pay fees on 30 sep', "
            "'my tasks', 'tomorrow's tasks', 'mark gym done', 'delete pay fees'\n"
            "• WhatsApp: 'add contact Amma 98765 43210', 'call Amma on WhatsApp', 'chat with Ravi', 'my contacts'\n"
            "• Laptop: 'open chrome', 'open whatsapp', 'open instagram', 'play lofi music', 'search google for Rocky Linux', "
            "'what can you open', 'add site srm portal https://academia.srmist.edu.in', "
            "'battery', 'laptop status', 'lock my laptop'\n"
            "• Sound & screen: 'volume up', 'volume 40', 'mute', 'pause', 'next song', 'brightness 60', 'take a screenshot'\n"
            "• Wi-Fi & power: 'turn on bluetooth', 'turn off wifi', 'sleep', 'restart', 'shut down my laptop', "
            "'cancel shutdown', 'close chrome' (I ask 'are you sure?' first)\n"
            "• Files: 'open downloads folder', 'find my resume', 'open my lab record', then 'open 1'\n"
            "• Browsers: 'open YouTube in Brave', 'open Claude in Brave', 'open Instagram in Chrome'\n"
            "• Phone call (Phone Link): 'call Akka on phone'\n"
            "• Telugu/Tenglish: 'youtube open cheyyi', 'sound penchu', 'repu podduna 6 ki alarm pettu', 'Akka ki call cheyyi'\n"
            "• Voice language: I answer in the language you speak. Or say 'speak in English', 'speak in Telugu', "
            "'telugu lo matladu', and 'match my language' to go back to automatic\n"
            "• Fix my mistakes: 'no, I meant Brave', 'it's Brave browser, not Imperial browser', then 'remember it'\n"
            "• Teach me your words: 'when I say mama I mean Ravi', 'my words', 'forget word mama'\n"
            "• Lab Log: 'open lab log', 'lab log: installed Rocky and ran df -h', 'step 2 done', 'assess my lab log', "
            "'read my lab log yesterday' (I also review it by myself every evening)\n"
            "• Your VM: 'start my VM', 'stop my VM', 'is my VM running'\n"
            "• DevOps & Cloud: 'docker status', 'docker stats', 'docker restart <container>', 'git status', 'git commit and push <msg>', 'ping my server'\n"
            "• Morning: 'good morning' (weather, task, streak, reminders, news, internships), 'internships'\n"
            "• Web: 'weather', 'weather tomorrow', 'weather in Hyderabad', 'news', 'tech news', 'cricket', "
            "'who is APJ Abdul Kalam', 'tell me about Kubernetes'\n"
            "• Study plan: 'what's my task today', 'tomorrow's task', 'this week', 'quiz me', 'move my plan back 3 days'\n"
            "• Tracking: 'I finished today's task', 'mark week 1 day 3 done', 'undo today', 'my progress', 'what did I miss'\n"
            "• Diary: 'open my diary' (then talk, and say 'close diary'), 'diary: today was good', 'read my diary yesterday'\n"
            "• Summaries: 'summarize my day', 'summarize the news', 'summarize tech news', 'summarize news about ISRO', "
            "'summarize the weather', 'summarize my tasks', 'summarize my diary' (this week), 'summarize my diary today' "
            "(or press Summaries in the sidebar, or Summarize on the News pages)\n"
            "• Documents: put files in the 'documents' folder, then 'list my documents', 'summarize resume', "
            "ask questions, then 'close document'\n"
            "• Notebook: 'remember that my best friend is Ravi', 'what do you remember about me', 'forget Ravi'"
        )
        return reply(text, "Here's everything I can do. It's on screen.")

    def time_and_date(self, t, raw):
        now = datetime.now()
        if re.search(r"\bwhat(?:'s| is)? (?:the )?time\b|\btime (?:is it|now)\b|^time$", t):
            return reply(f"It's {fmt_time(now)}.")
        if re.search(r"what(?:'s| is) (?:the |today'?s )?date|what day is (?:it|today)|^date$|today'?s date", t):
            return reply(f"Today is {now.strftime('%A, %d %B %Y')}.")
        return None

    # ---------------- "Should I remember this?"
    MEMORY_HINT = re.compile(
        r"\b(my\s+(?:exam|exams|birthday|favou?rite\s+\w+|name|college|university|course|branch|department|semester|goal|dream|"
        r"hobby|hobbies|project|mentor|best friend|brother|sister|father|mother|dad|mom|internship|interview|placement|cgpa|"
        r"target|deadline|hometown|native place)\b[^.?!,;\n]{3,90}|"
        r"\bi\s+(?:like|love|prefer|hate|don'?t like|am learning|want to become|want to be|am preparing for|study|live in|am from)\b[^.?!,;\n]{3,70})",
        re.I)

    def memory_suggestion(self, raw):
        """If he told a fact about himself in normal chat, return it (so Vega can ask to remember it)."""
        if raw.strip().endswith("?") or re.match(r"^\s*(what|who|why|how|when|where|is|are|do|does|can|could|should)\b", raw, re.I):
            return None
        m = self.MEMORY_HINT.search(raw)
        if not m:
            return None
        fact = m.group(1).strip().rstrip(".,!")
        if len(fact.split()) < 3 or any(fact.lower() in f.lower() for f in self.facts()):
            return None
        return fact

    def remember_it(self, t, raw):
        # "remember this conversation", "remember what we talked about", "learn from our chat"
        if re.match(r"^(?:please\s+)?(?:remember|save|note|keep|learn from|memorise|memorize)\s+(?:this|our|the|today'?s|that)\s+"
                    r"(?:whole\s+)?(?:conversation|chat|talk|discussion)\b|^remember (?:what we (?:talked|discussed|said)|everything we)\b", t):
            return self.remember_conversation()
        if not re.match(r"^(?:yes,?\s+)?(?:remember|save|note|keep)\s+(?:it|this|that)(?:\s+please)?$|^yes remember$|^haa?n?,? gurthu pettuko$", t):
            return None
        learn = getattr(self, "pending_learn", None)
        if learn and time.time() < learn[2]:
            self.pending_learn = None
            words = self.my_words()
            words[learn[0]] = learn[1]
            write_json("my_words.json", words)
            return reply(f"Learned. From now on, when I hear “{learn[0]}”, I'll understand “{learn[1]}”.\n"
                         "(Say “my words” to see all fixes, or “forget word …” to undo one.)",
                         "Learned. I won't make that mistake again.")
        pending = getattr(self, "pending_memory", None)
        if not pending or time.time() > pending[1]:
            return self.remember_conversation()  # "remember this" with nothing waiting: keep the important points of this chat
        self.pending_memory = None
        facts = self.facts()
        facts.append(pending[0])
        self.save_facts(facts)
        return reply(f"Saved. I'll remember: {pending[0]}.\n(Say “what do you remember about me” to see everything.)",
                     "Saved. I'll remember that.")

    # ---------------- notebook
    def notebook(self, t, raw):
        m = re.match(r"^remember\s+that[\s,:]+(.+)$", raw.strip(), flags=re.I) or \
            re.match(r"^(?:hey\s+)?vega[\s,]+remember\s+that[\s,:]+(.+)$", raw.strip(), flags=re.I)
        if m:
            fact = m.group(1).strip().rstrip(".!?")
            if not fact:
                return reply("What should I remember?")
            facts = self.facts()
            if fact.lower() not in (x.lower() for x in facts):
                facts.append(fact)
                self.save_facts(facts)
            return reply(f"Got it, I'll remember that {fact}.")
        if re.search(r"what do you (remember|know) about me|^what (?:do|did) you remember\??$|^what have you (?:remembered|saved)|^(?:show )?my memories$", t):
            facts = self.facts()
            pers = read_json("personal.json", {}) or {}
            words = self.my_words()
            contacts = [c["name"] for c in self.load_contacts().values()]
            parts = []
            if str(pers.get("about", "")).strip():
                parts.append("From Personalize Vega (about you):\n" + str(pers["about"]).strip())
            if str(pers.get("style", "")).strip():
                parts.append("How you want me to answer:\n" + str(pers["style"]).strip())
            if facts:
                parts.append("Things you asked me to remember:\n- " + "\n- ".join(facts))
            if words:
                parts.append("Your words and fixes: " + ", ".join(f"{k} → {v}" for k, v in list(words.items())[:15]))
            if contacts:
                parts.append("Your contacts: " + ", ".join(contacts[:20]))
            if not parts:
                return reply("I don't know anything about you yet. Fill in Settings and more → Personalize Vega, "
                             "or tell me things by saying: remember that …")
            return reply("Here's what I know about you:\n\n" + "\n\n".join(parts) +
                         "\n\n(Add more with “remember that …”, or edit Settings and more → Personalize Vega.)",
                         "Here's what I know about you. It's on screen.")
        m = re.match(r"^forget(?:\s+about|\s+that)?[\s,:]+(.+)$", t)
        if m and not re.search(r"\b(document|doc|file|pdf|reminder)\b", t):
            word = m.group(1).strip()
            facts = self.facts()
            kept = [x for x in facts if word not in x.lower()]
            removed = len(facts) - len(kept)
            self.save_facts(kept)
            if removed == 0:
                return reply(f"I didn't have anything noted about {word}.")
            return reply(f"Done. I forgot {removed} note{'s' if removed != 1 else ''} about {word}.")
        return None

    # ---------------- reminders
    def parse_when(self, body):
        """Find a time in the text. Returns (datetime or None, text without the time part)."""
        now = datetime.now()
        b = " " + words_to_digits(body.lower()) + " "
        b = b.replace("half an hour", "30 minutes").replace("half hour", "30 minutes")
        b = b.replace("o'clock", "").replace("a.m.", "am").replace("p.m.", "pm").replace("a. m.", "am").replace("p. m.", "pm")

        m = re.search(r"\bin\s+(\d+|a|an)\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?)\b", b)
        if m:
            n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
            unit = m.group(2)
            delta = timedelta(hours=n) if unit.startswith("h") else (timedelta(seconds=n) if unit.startswith("s") else timedelta(minutes=n))
            rest = (b[:m.start()] + " " + b[m.end():])
            return now + delta, rest

        day_offset = 0
        dm = re.search(r"\b(tomorrow|tonight|today|this evening|this morning)\b", b)
        if dm:
            if dm.group(1) == "tomorrow":
                day_offset = 1
            b = b[:dm.start()] + " " + b[dm.end():]

        m = re.search(r"(?:\bat\s+|\bfor\s+|^\s*|\s)(\d{1,2})(?:[:.\s](\d{2}))?\s*(am|pm)\b", b)
        m24 = None if m else re.search(r"\bat\s+(\d{1,2})(?:[:.](\d{2}))?\b", b)
        mm = m or m24
        if not mm:
            return None, body
        hour = int(mm.group(1))
        minute = int(mm.group(2) or 0)
        if hour > 23 or minute > 59:
            return None, body
        ampm = m.group(3) if m else None
        candidates = []
        if ampm:
            h = hour % 12 + (12 if ampm == "pm" else 0)
            candidates = [h]
        elif hour > 12 or hour == 0:
            candidates = [hour]
        else:
            evening = dm and dm.group(1) in ("tonight", "this evening")
            candidates = [hour % 12 + 12] if evening else [hour % 12, hour % 12 + 12]
        base_day = now.date() + timedelta(days=day_offset)
        best = None
        for h in candidates:
            when = datetime.combine(base_day, datetime.min.time()).replace(hour=h, minute=minute)
            if day_offset == 0 and when <= now:
                continue
            best = when if best is None or when < best else best
        if best is None:  # the time already passed today -> tomorrow
            h = candidates[0]
            best = datetime.combine(now.date() + timedelta(days=1), datetime.min.time()).replace(hour=h, minute=minute)
        rest = b[:mm.start()] + " " + b[mm.end():]
        return best, rest

    @staticmethod
    def tidy_reminder_text(rest):
        r = re.sub(r"\s+", " ", rest).strip(" ,.")
        r = re.sub(r"^(to|that|about|me to|me that|me about)\s+", "", r)
        r = re.sub(r"\s+(at|on|for|by)$", "", r)
        return r.strip(" ,.")

    def reminders(self, t, raw):
        items = read_json("reminders.json", [])
        if not isinstance(items, list):
            items = []

        m = re.match(r"^(?:set (?:an |a |my )?alarm|wake me(?: up)?)\s*(?:for|at)?\s*(.*)$", t)
        if m:
            when, _ = self.parse_when("at " + m.group(1) if m.group(1) and not m.group(1).startswith("in ") else m.group(1))
            if not when:
                return reply("What time? Say something like: wake me up at 6 am.")
            return self._add_reminder(items, when, "Alarm: time to wake up!" if "wake" in t else "Alarm")

        m = re.match(r"^remind me\s+(.+)$", t)
        if m:
            when, rest = self.parse_when(m.group(1))
            if not when:
                return reply("When should I remind you? Say it like: remind me at 7 pm to call Amma, "
                             "or remind me in 20 minutes to drink water.",
                             "When should I remind you? Say a time like 7 pm, or in 20 minutes.")
            text = keep_case(self.tidy_reminder_text(rest), raw) or "Reminder"
            return self._add_reminder(items, when, text)

        if re.search(r"^(what are |show |list |read )?(all )?(my )?(reminders|alarms)$|any reminders|do i have (any )?reminders", t):
            upcoming = sorted([r for r in items if not r.get("done")], key=lambda r: r["when"])
            if not upcoming:
                return reply("You have no reminders.")
            lines = [f"{i}. {fmt_day(datetime.fromisoformat(r['when'])).capitalize()} at "
                     f"{fmt_time(datetime.fromisoformat(r['when']))}: {r['text']}" for i, r in enumerate(upcoming, 1)]
            return reply("Your reminders:\n" + "\n".join(lines), f"You have {len(upcoming)} reminder{'s' if len(upcoming) != 1 else ''}. They're on screen.")

        m = re.match(r"^(?:cancel|delete|remove|clear)\s+(all\s+)?(?:my\s+|the\s+)?(?:reminders?|alarms?)(?:\s+(?:about|for|to|called)?\s*(.+))?$", t)
        if m:
            upcoming = [r for r in items if not r.get("done")]
            if m.group(1) or not m.group(2):
                if not m.group(1) and len(upcoming) > 1:
                    return reply("Which one? Say 'cancel reminder' and a word from it, or 'cancel all reminders'.")
                write_json("reminders.json", [r for r in items if r.get("done")])
                return reply(f"Cancelled {len(upcoming)} reminder{'s' if len(upcoming) != 1 else ''}.")
            word = m.group(2).strip()
            hit = [r for r in upcoming if word in r["text"].lower()]
            if not hit:
                return reply(f"I couldn't find a reminder about {word}.")
            ids = {r["id"] for r in hit}
            write_json("reminders.json", [r for r in items if r["id"] not in ids])
            return reply(f"Cancelled: {hit[0]['text']}" + (f" (and {len(hit) - 1} more)" if len(hit) > 1 else ""))
        return None

    def _add_reminder(self, items, when, text):
        items.append({"id": f"r{int(time.time() * 1000)}", "when": when.isoformat(timespec="seconds"),
                      "text": text[:200], "done": False})
        write_json("reminders.json", items[-200:])
        text_show = f"Okay. I'll remind you {fmt_day(when)} at {fmt_time(when)}: {text}"
        return reply(text_show + "\n(I can only remind you while I'm running. Turn on 'Start with Windows' so I'm always on.)",
                     text_show)

    def due_reminders(self):
        """Called every few seconds. Returns reminders that are due and marks them done."""
        items = read_json("reminders.json", [])
        if not isinstance(items, list) or not items:
            return []
        now = datetime.now()
        due = []
        for r in items:
            if r.get("done"):
                continue
            try:
                when = datetime.fromisoformat(r["when"])
            except (KeyError, ValueError):
                r["done"] = True
                continue
            if when <= now:
                r["done"] = True
                late = (now - when).total_seconds() > 120
                due.append({"text": r["text"], "when": when, "late": late})
        if due:
            keep_from = now - timedelta(days=7)
            items = [r for r in items if not r.get("done") or datetime.fromisoformat(r["when"]) > keep_from]
            write_json("reminders.json", items)
        return due

    # ---------------- my tasks (to-do list with dates and repeats; tasks.json)
    WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

    def tasks_data(self):
        data = read_json("tasks.json", None)
        if not isinstance(data, dict):
            old = read_json("todo.json", [])  # bring in the old simple to-do list once
            items = [{"id": f"t{i}{int(time.time())}", "text": str(x), "date": None, "repeat": "none"}
                     for i, x in enumerate(old if isinstance(old, list) else [])]
            data = {"items": items, "done": {}}
            write_json("tasks.json", data)
        data.setdefault("items", [])
        data.setdefault("done", {})
        return data

    def save_tasks(self, data):
        keep = (date.today() - timedelta(days=60)).isoformat()
        data["done"] = {k: v for k, v in data["done"].items() if k >= keep and v}
        write_json("tasks.json", data)

    def task_done(self, data, task, day):
        if task.get("repeat", "none") != "none":
            return task["id"] in data["done"].get(day, [])
        return any(task["id"] in v for v in data["done"].values())

    def task_applies(self, task, d):
        rep = task.get("repeat", "none")
        if rep == "daily":
            return True
        if rep == "weekdays":
            return d.weekday() < 5
        if rep == "weekly":
            start = date.fromisoformat(task["date"]) if task.get("date") else d
            return d.weekday() == start.weekday() and d >= start
        return True

    def tasks_for(self, d):
        """[(task, done, tag)] for day d: repeating tasks, tasks dated d, undated open tasks, and overdue ones (for today)."""
        data = self.tasks_data()
        day, today = d.isoformat(), date.today()
        out = []
        for tk in data["items"]:
            rep = tk.get("repeat", "none")
            if rep != "none":
                if self.task_applies(tk, d):
                    tag = {"daily": "every day", "weekdays": "weekdays"}.get(rep) or \
                        ("every " + self.WEEKDAYS[date.fromisoformat(tk["date"]).weekday()].capitalize() if tk.get("date") else rep)
                    out.append((tk, self.task_done(data, tk, day), tag))
                continue
            done = self.task_done(data, tk, day)
            if not tk.get("date"):
                if d == today and (not done or tk["id"] in data["done"].get(day, [])):
                    out.append((tk, done, ""))
                continue
            td = date.fromisoformat(tk["date"])
            if td == d:
                out.append((tk, done, ""))
            elif d == today and td < d and not done:
                out.append((tk, done, "overdue, " + td.strftime("%a %d %b")))
        return out

    def upcoming_tasks(self, days=7):
        data = self.tasks_data()
        today = date.today()
        out = []
        for tk in data["items"]:
            if tk.get("repeat", "none") == "none" and tk.get("date"):
                td = date.fromisoformat(tk["date"])
                if today < td <= today + timedelta(days=days) and not self.task_done(data, tk, tk["date"]):
                    out.append((td, tk))
        return sorted(out, key=lambda x: x[0])

    def parse_task_when(self, text):
        """Find 'tomorrow', 'on friday', 'on 30 sep', 'every day', 'weekdays', 'every monday'. Returns (date, repeat, rest)."""
        today = date.today()
        t = " " + text + " "

        def cut(m):
            return (t[:m.start()] + " " + t[m.end():]).strip()

        m = re.search(r"\s(?:every\s?day|daily|everyday)\b", t)
        if m:
            return None, "daily", cut(m)
        m = re.search(r"\s(?:every\s+weekday|on\s+weekdays|weekdays|every\s+week\s?day)\b", t)
        if m:
            return None, "weekdays", cut(m)
        m = re.search(r"\s(?:every|each)\s+(" + "|".join(self.WEEKDAYS) + r")\b", t)
        if m:
            wd = self.WEEKDAYS.index(m.group(1))
            return today + timedelta(days=(wd - today.weekday()) % 7), "weekly", cut(m)
        m = re.search(r"\s(?:for\s+|on\s+|by\s+)?(day after tomorrow|tomorrow|today|tonight)\b", t)
        if m:
            off = {"today": 0, "tonight": 0, "tomorrow": 1, "day after tomorrow": 2}[m.group(1)]
            return today + timedelta(days=off), "none", cut(m)
        m = re.search(r"\s(?:on\s+|by\s+|for\s+|this\s+|next\s+)(" + "|".join(self.WEEKDAYS) + r")\b", t)
        if m:
            wd = self.WEEKDAYS.index(m.group(1))
            return today + timedelta(days=(wd - today.weekday()) % 7 or 7), "none", cut(m)
        mon = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*"
        m = re.search(r"\s(?:on\s+|by\s+|for\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?" + mon + r"\b", t) or \
            re.search(r"\s(?:on\s+|by\s+|for\s+)?" + mon + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b", t)
        if m:
            g = m.groups()
            dnum, mname = (int(g[0]), g[1]) if g[0].isdigit() else (int(g[1]), g[0])
            try:
                d = date(today.year, self.MONTHS[mname[:3]], dnum)
                if d < today:
                    d = date(today.year + 1, d.month, d.day)
                return d, "none", cut(m)
            except ValueError:
                pass
        m = re.search(r"\s(?:on\s+|by\s+|for\s+)(\d{1,2})[/-](\d{1,2})\b", t)
        if m:
            try:
                d = date(today.year, int(m.group(2)), int(m.group(1)))
                if d < today:
                    d = date(today.year + 1, d.month, d.day)
                return d, "none", cut(m)
            except ValueError:
                pass
        return None, "none", text.strip()

    def tasks_view(self, d):
        rows = self.tasks_for(d)
        heading = "Today" if d == date.today() else ("Tomorrow" if d == date.today() + timedelta(days=1) else d.strftime("%A"))
        lines = [f"{heading}, {d.strftime('%a %d %b')}"]
        plan = self.load_plan()
        if plan:
            idx = self.track_lesson_on("linux", d)
            pd = self.plan_day(plan, idx) if idx is not None else None
            if pd:
                lines.append(f"Study: {pd['label']}, {pd['title']}" + (" ✓" if self.is_done(idx) else ""))
            for name, od in self.other_study_today(d):
                lines.append(f"{name}: {od['short']}, {od['title']}" + (" ✓" if od.get("done") else ""))
        if rows:
            lines.append("My tasks:")
            for i, (tk, done, tag) in enumerate(rows, 1):
                lines.append(f"  {i}. {'✓' if done else '○'} {tk['text']}" + (f"  ({tag})" if tag else ""))
        else:
            lines.append("My tasks: nothing yet. Add one: add submit lab record for tomorrow")
        if d == date.today():
            up = self.upcoming_tasks()
            if up:
                lines.append("Coming up:")
                lines += [f"  {td.strftime('%a %d %b')}: {tk['text']}" for td, tk in up[:6]]
        left = [tk["text"] for tk, done, _ in rows if not done]
        say = (f"{heading} you have {len(left)} task{'s' if len(left) != 1 else ''} left: " + ", ".join(left[:4]) + ".") if left \
            else f"All your tasks for {heading.lower()} are done." if rows else f"You have no tasks for {heading.lower()}."
        return reply("\n".join(lines), say)

    def find_task(self, target, rows_today):
        target = target.strip().lower()
        if target.isdigit() and 1 <= int(target) <= len(rows_today):
            return rows_today[int(target) - 1][0]
        data = self.tasks_data()
        items = data["items"]
        low = [x["text"].lower() for x in items]
        for i, x in enumerate(low):
            if target == x:
                return items[i]
        for i, x in enumerate(low):
            if len(target) >= 3 and (target in x or x in target):
                return items[i]
        close = difflib.get_close_matches(target, low, n=1, cutoff=0.6)
        return items[low.index(close[0])] if close else None

    def todo(self, t, raw):
        LIST = r"(?:to-?do(?:\s+list)?|todo|to do(?:\s+list)?|list|tasks?|task list)"
        if re.match(r"^add\s+(?:a\s+)?(?:site|website|link|contact)\b", t):
            return None

        # add
        m = re.match(r"^(?:add|put)\s+(?:a\s+)?(?:task\s+)?(.+?)\s+(?:to|on|in)\s+(?:my\s+|the\s+)?" + LIST + r"$", t) or \
            re.match(r"^(?:add\s+(?:a\s+)?task|new task|create (?:a\s+)?task|to-?do|todo)[:\s]+(.+)$", t) or \
            re.match(r"^(?:add|put)\s+(.+?\s(?:tomorrow|today|every\s?day|daily|everyday|weekdays|every \w+day|on \w+day|on \d{1,2}.*|by \w+day|for tomorrow))$", t)
        if m:
            when, rep, text = self.parse_task_when(m.group(1))
            text = keep_case(text.strip(" ,.:"), raw) or "Task"
            text = re.sub(r"\s+(?:to|on|in)\s+(?:my\s+|the\s+)?(?:to-?do(?:\s+list)?|list|tasks?)$", "", text, flags=re.I)
            data = self.tasks_data()
            data["items"].append({"id": "t" + format(int(time.time() * 1000), "x"), "text": text[:200],
                                  "date": when.isoformat() if when else None, "repeat": rep})
            data["items"] = data["items"][-300:]
            self.save_tasks(data)
            when_txt = {"daily": "every day", "weekdays": "on weekdays"}.get(rep) or \
                (f"every {when.strftime('%A')}" if rep == "weekly" else (fmt_day(when) if when else "no date"))
            return reply(f"Added: {text}, {when_txt}.", f"Added {text}, {when_txt}.")

        # show
        if re.search(r"^(?:what(?:'s| is| are)\s+)?(?:on\s+)?(?:my\s+|the\s+)?(?:to-?do(?:\s+list)?|todo|to do(?:\s+list)?|tasks|task list)(?:\s+for)?(?:\s+today)?$", t) or \
                re.search(r"^(?:today'?s|my today'?s) tasks$|^what (?:do i|should i) (?:have to|need to) do(?: today)?$|^my day$|^plan for today$", t):
            return self.tasks_view(date.today())
        if re.search(r"^(?:what(?:'s| is| are)\s+)?(?:my\s+)?(?:tomorrow'?s tasks|tasks (?:for )?tomorrow|to-?do (?:for )?tomorrow)$|^what do i have (?:to do )?tomorrow$", t):
            return self.tasks_view(date.today() + timedelta(days=1))

        # clear
        if re.match(r"^(?:clear|empty|reset)\s+(?:my\s+|the\s+)?" + LIST + r"$", t):
            self.save_tasks({"items": [], "done": {}})
            return reply("All your tasks are cleared.")

        data = self.tasks_data()
        if not data["items"]:
            return None
        rows = self.tasks_for(date.today())

        # delete
        m = re.match(r"^(?:delete|remove|cancel)\s+(?:the\s+|my\s+)?(?:task\s+)?(.+?)(?:\s+(?:from|off)\s+(?:my\s+|the\s+)?" + LIST + r")?$", t)
        if m and not re.search(r"\b(reminder|contact|site|website|document|note)\b", t):
            tk = self.find_task(m.group(1), rows)
            if tk:
                data["items"] = [x for x in data["items"] if x["id"] != tk["id"]]
                for k in data["done"]:
                    data["done"][k] = [x for x in data["done"][k] if x != tk["id"]]
                self.save_tasks(data)
                return reply(f"Deleted: {tk['text']}.")
            return None

        # untick
        m = re.match(r"^(?:untick|uncheck|unmark)\s+(.+)$", t) or re.match(r"^(.+?)\s+(?:is\s+)?not done$", t)
        if m:
            tk = self.find_task(m.group(1), rows)
            if tk:
                for k in data["done"]:
                    data["done"][k] = [x for x in data["done"][k] if x != tk["id"]]
                self.save_tasks(data)
                return reply(f"Okay, {tk['text']} is not done.")
            return None

        # done
        m = re.match(r"^(?:mark|tick|tick off|check off|cross off|finished|finish|done with|done|i finished|i did|i've done|i have done|i completed|completed)\s+(.+?)"
                     r"(?:\s+(?:as\s+)?(?:done|complete|completed|finished|off))?(?:\s+(?:from|on)\s+(?:my\s+|the\s+)?" + LIST + r")?$", t) or \
            re.match(r"^(.+?)\s+(?:is\s+)?(?:done|finished|completed)$", t)
        if m:
            target = m.group(1).strip()
            if re.fullmatch(r"(?:today'?s|yesterday'?s|my|the)?\s*(?:task|study|lesson|studies)|today|yesterday|day\s*\w+|week.*|w\d.*", target):
                return None  # that's about the study plan
            tk = self.find_task(target, rows)
            if not tk:
                return None
            day = date.today().isoformat()
            if tk["id"] not in data["done"].setdefault(day, []):
                data["done"][day].append(tk["id"])
            self.save_tasks(data)
            left = [x["text"] for x, done, _ in self.tasks_for(date.today()) if not done]
            return reply(f"Done: {tk['text']}. " + (f"{len(left)} left today." if left else "Everything for today is done!"),
                         f"Nice. {tk['text']} is done.")
        return None

    # ---------------- laptop: VM, lock, status
    def _vboxmanage(self):
        p = self.cfg.get("vboxmanage", "")
        if p and os.path.exists(p):
            return p
        return shutil.which("VBoxManage") or shutil.which("vboxmanage")

    def vm(self, t, raw):
        VM = r"(?:vm|v\.m\.|virtual machine|server|rocky|rocky linux|linux vm|linux server|lab vm|lab server)"
        
        # 1. Open interactive VM terminal window
        if re.search(r"\b(?:ssh\s+(?:in(?:to)?\s+)?|open\s+(?:the\s+)?(?:ssh\s+|vm\s+)?terminal|connect\s+to\s+)(?:my\s+|the\s+)?" + VM, t, re.I):
            cmd = ["cmd.exe", "/c", "start", "powershell.exe", "-NoExit", "-Command", "ssh -p 2222 hemanth@127.0.0.1"]
            subprocess.Popen(cmd)
            return reply("Opening your Rocky Linux VM terminal window now!", "Opening VM terminal.")

        # 2. Check VM system health / disk / memory
        if re.search(r"\b(?:check\s+(?:my\s+|the\s+)?vm\s+health|vm\s+(?:health|disk|memory|ram|stats))\b", t, re.I):
            try:
                r = subprocess.run(["ssh", "-p", "2222", "-o", "BatchMode=yes", "hemanth@127.0.0.1",
                                    "echo '=== UPTIME ===' && uptime && echo '=== MEMORY ===' && free -h && echo '=== DISK ===' && df -h /"],
                                   capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
                if r.returncode == 0:
                    out = r.stdout.strip()
                    return reply(f"🖥️ **Rocky Linux VM Health:**\n```\n{out}\n```", "Here is your Rocky Linux VM health report.")
                else:
                    return reply("I couldn't reach the VM over SSH. Make sure the VM is running.", "VM SSH is currently unreachable.")
            except Exception as e:
                return reply(f"Error checking VM health: {e}", "Error checking VM.")

        # 3. Execute arbitrary command on the VM
        m_cmd = re.search(r"^(?:(?:run\s+)?on\s+(?:my\s+|the\s+)?vm|vm\s+(?:run|exec|command))\s*[:\-]?\s*(.+)$", t, re.I)
        if m_cmd:
            guest_cmd = m_cmd.group(1).strip()
            if guest_cmd:
                try:
                    r = subprocess.run(["ssh", "-p", "2222", "-o", "BatchMode=yes", "hemanth@127.0.0.1", guest_cmd],
                                       capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
                    out = (r.stdout + r.stderr).strip()
                    if not out:
                        out = "(Command completed successfully with no output)"
                    return reply(f"🖥️ **VM Output (`{guest_cmd}`):**\n```\n{out[:2500]}\n```",
                                 f"Command finished. Output is on screen.")
                except Exception as e:
                    return reply(f"Error executing on VM: {e}", "Error executing command on VM.")

        # 4. VM Lifecycle (VirtualBox start/stop/status)
        start = re.match(r"^(?:please\s+)?(?:start|boot|turn on|power on|open|run|launch)\s+(?:up\s+)?(?:my\s+|the\s+)?" + VM + r"(?:\s+now)?$", t)
        stop = re.match(r"^(?:please\s+)?(?:stop|shut ?down|shutdown|turn off|power off|close)\s+(?:my\s+|the\s+)?" + VM + r"(?:\s+now)?$", t)
        status = re.match(r"^(?:is\s+(?:my\s+|the\s+)?" + VM + r"\s+(?:running|on|up)|(?:my\s+)?" + VM + r" status)\??$", t)
        if not (start or stop or status):
            return None
        vbox = self._vboxmanage()
        name = self.cfg["vm_name"]
        if not vbox:
            return reply("I can't find VirtualBox's VBoxManage.exe. Check the 'vboxmanage' path in config.json.")

        def run(*args):
            r = subprocess.run([vbox, *args], capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)
            return r.returncode, (r.stdout + r.stderr)

        code, out = run("list", "runningvms")
        running = f'"{name}"' in out
        if status:
            return reply(f"Yes, {name} is running." if running else f"No, {name} is off.")
        if start:
            if running:
                return reply(f"{name} is already running.")
            code, out = run("startvm", name, "--type", "gui")
            if code != 0:
                return reply(f"I couldn't start {name}.\n{out.strip()[:400]}", f"I couldn't start {name}. The error is on screen.")
            return reply(f"Starting {name}. Give it about a minute to boot.")
        if not running:
            return reply(f"{name} is already off.")
        code, out = run("controlvm", name, "acpipowerbutton")
        if code != 0:
            return reply(f"I couldn't shut down {name}.\n{out.strip()[:400]}", f"I couldn't shut down {name}.")
        return reply(f"Shutting down {name} safely (like pressing its power button).")

    def devops_command(self, t, raw):
        """DevOps, Docker, Git, and Server/Network automation commands."""
        # 1. Docker commands
        if re.search(r"\bdocker\b", t):
            docker_cmd = shutil.which("docker")
            if not docker_cmd:
                return reply("Docker is not found or not in PATH. Make sure Docker Desktop is running.",
                             "Docker is not found or running on your laptop.")

            # docker ps / containers / status
            if re.search(r"docker\s+(?:status|ps|containers?|list|running)", t) or "what containers are running" in t:
                try:
                    r = subprocess.run([docker_cmd, "ps", "--format", "table {{.Names}}\t{{.Status}}\t{{.Image}}"],
                                       capture_output=True, text=True, timeout=12, creationflags=NO_WINDOW)
                    if r.returncode != 0:
                        return reply(f"Docker status error: {r.stderr.strip()[:200]}")
                    out = r.stdout.strip()
                    if not out or len(out.splitlines()) <= 1:
                        return reply("No Docker containers are currently running.", "No Docker containers are running.")
                    return reply(f"Running Docker containers:\n{out}", f"Found {len(out.splitlines()) - 1} running containers.")
                except Exception as e:
                    return reply(f"Couldn't check Docker containers: {e}")

            # docker stats
            if "docker stats" in t:
                try:
                    r = subprocess.run([docker_cmd, "stats", "--no-stream", "--format", "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}"],
                                       capture_output=True, text=True, timeout=15, creationflags=NO_WINDOW)
                    if r.returncode == 0:
                        return reply(f"Docker Stats:\n{r.stdout.strip()}")
                except Exception as e:
                    return reply(f"Docker stats error: {e}")

            # docker restart <name>
            m_res = re.search(r"docker restart\s+([a-zA-Z0-9_\-]+)", t)
            if m_res:
                cname = m_res.group(1)
                r = subprocess.run([docker_cmd, "restart", cname], capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
                if r.returncode == 0:
                    return reply(f"Restarted Docker container '{cname}'.", f"Restarted container {cname}.")
                return reply(f"Failed to restart container '{cname}': {r.stderr.strip()[:200]}")

            # docker stop <name>
            m_stop = re.search(r"docker stop\s+([a-zA-Z0-9_\-]+)", t)
            if m_stop:
                cname = m_stop.group(1)
                r = subprocess.run([docker_cmd, "stop", cname], capture_output=True, text=True, timeout=25, creationflags=NO_WINDOW)
                if r.returncode == 0:
                    return reply(f"Stopped Docker container '{cname}'.", f"Stopped container {cname}.")
                return reply(f"Failed to stop container '{cname}': {r.stderr.strip()[:200]}")

            # docker start <name>
            m_start = re.search(r"docker start\s+([a-zA-Z0-9_\-]+)", t)
            if m_start:
                cname = m_start.group(1)
                r = subprocess.run([docker_cmd, "start", cname], capture_output=True, text=True, timeout=25, creationflags=NO_WINDOW)
                if r.returncode == 0:
                    return reply(f"Started Docker container '{cname}'.", f"Started container {cname}.")
                return reply(f"Failed to start container '{cname}': {r.stderr.strip()[:200]}")

            return reply("Docker command recognized. You can say 'docker status', 'docker stats', 'docker restart <container>', or 'docker stop <container>'.")

        # 2. Git commands
        if re.search(r"\bgit\b", t) and any(k in t for k in ("status", "commit", "push", "pull", "log", "branch")):
            git_cmd = shutil.which("git")
            if not git_cmd:
                return reply("Git is not found in PATH.")

            # git commit and push
            m_cp = re.search(r"git commit(?:\s+and\s+push)?(?:\s+with\s+message|\s+message)?\s+['\"]?([^'\"]+)['\"]?", t)
            if m_cp and ("commit" in t or "push" in t):
                msg = m_cp.group(1).strip() or "Update from Vega"
                cwd = BASE
                try:
                    subprocess.run([git_cmd, "add", "."], cwd=cwd, capture_output=True, timeout=15, creationflags=NO_WINDOW)
                    r = subprocess.run([git_cmd, "commit", "-m", msg], cwd=cwd, capture_output=True, text=True, timeout=20, creationflags=NO_WINDOW)
                    p = subprocess.run([git_cmd, "push"], cwd=cwd, capture_output=True, text=True, timeout=30, creationflags=NO_WINDOW)
                    return reply(f"Committed ('{msg}') and pushed changes.", "Git commit and push completed.")
                except Exception as e:
                    return reply(f"Git commit/push error: {e}")

            # git status
            if "git status" in t:
                try:
                    r = subprocess.run([git_cmd, "status", "-s"], cwd=BASE, capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
                    st = r.stdout.strip()
                    if not st:
                        return reply("Git status: Working tree is clean.", "Git status: working tree is clean.")
                    return reply(f"Git status:\n{st}")
                except Exception as e:
                    return reply(f"Git status error: {e}")

            # git log
            if "git log" in t:
                try:
                    r = subprocess.run([git_cmd, "log", "-n", "3", "--oneline"], cwd=BASE, capture_output=True, text=True, timeout=10, creationflags=NO_WINDOW)
                    return reply(f"Recent Git commits:\n{r.stdout.strip()}")
                except Exception as e:
                    return reply(f"Git log error: {e}")

        # 3. Server / VM Ping Health Check
        if re.search(r"\b(?:ping|health)\s+(?:my\s+|the\s+)?(?:server|vm|network)\b|^ping (?:hemanth-server1|vm|server)$", t):
            target = "1.1.1.1" if "network" in t else "127.0.0.1"
            try:
                r = subprocess.run(["ping", "-n", "2", target], capture_output=True, text=True, timeout=8, creationflags=NO_WINDOW)
                if r.returncode == 0:
                    return reply("Server ping successful. Host is reachable with normal latency.", "Server ping successful.")
                return reply("Server ping failed. Host is currently unreachable.", "Server ping failed.")
            except Exception as e:
                return reply(f"Ping check failed: {e}")

        return None

    def projects_command(self, t, raw):
        """Projects in the Lab Log (Train Vega, custom projects, etc.)."""
        if re.search(r"\b(?:my\s+|the\s+)?projects?\b", t):
            # "open projects" / "show projects"
            if re.match(r"^(?:please\s+)?(?:open|show|go to)\s+(?:my\s+|the\s+)?projects?$", t):
                out = reply("Opening the Projects tab in your Lab Log.", "Opening your Lab Log Projects.")
                out["open_projects"] = True
                out["open_lablog"] = True
                return out

            # "my projects" / "list projects" / "what projects"
            if re.search(r"\b(?:what|list|show|my)\s+projects?\b|^projects?$", t):
                projs = self.project_list()
                if not projs:
                    return reply("You don't have any projects in your Lab Log yet. Say 'open lab log' and click '+ Add project'.")
                lines = ["Your Lab Log Projects:"]
                for p in projs:
                    st = p["status"].capitalize()
                    icon = "🔥" if p["status"] == "active" else ("✓" if p["status"] == "done" else "📌")
                    lines.append(f"{icon} {p['name']} [{st}]: {p['about'][:95]}")
                lines.append("\nSay 'open projects' to view or start them in the Lab Log.")
                say = f"You have {len(projs)} project{'s' if len(projs) > 1 else ''}: " + ", ".join(p["name"] for p in projs[:3])
                return reply("\n".join(lines), say)

        # "tell me about Train Vega" / "train vega"
        if "train vega" in t:
            p = self.project("train-vega")
            if p:
                lines = [
                    f"Project: {p['name']}",
                    f"About: {p['about']}",
                    "\nDecisions:",
                ]
                for d in p.get("decided", [])[:4]:
                    lines.append(f"• {d}")
                lines.append(f"\nSteps ({len(p.get('steps', []))} sessions):")
                for i, st in enumerate(p.get("steps", [])[:5], 1):
                    lines.append(f"  {i}. {st.get('title')}")
                if len(p.get("steps", [])) > 5:
                    lines.append(f"  ...and {len(p.get('steps', [])) - 5} more sessions")
                lines.append("\nSay 'open projects' to start it in your Lab Log.")
                return reply("\n".join(lines), "Train Vega is a project to fine-tune your own Vega model on Google Colab using Gemma and Qwen as teachers.")

        return None

    def lock_screen(self, t, raw):
        if not re.match(r"^(?:please\s+)?lock\s+(?:my\s+|the\s+)?(?:laptop|computer|pc|screen|system)(?:\s+now)?$", t):
            return None
        if not IS_WINDOWS:
            return reply("Locking only works on Windows.")
        subprocess.Popen(["rundll32.exe", "user32.dll,LockWorkStation"], creationflags=NO_WINDOW)
        return reply("Locking your laptop.")

    def laptop_status(self, t, raw):
        want_batt = re.search(r"\bbattery\b|\bcharging\b|\bcharge\b", t)
        want_all = re.search(r"(laptop|system|computer|pc) (status|health|stats)|how(?:'s| is) my (laptop|computer|pc|system)|\bcpu\b|\bram\b|disk space|storage left|free space", t)
        if not (want_batt or want_all):
            return None
        import psutil
        lines = []
        b = psutil.sensors_battery()
        if b:
            state = "charging" if b.power_plugged else "on battery"
            extra = ""
            if not b.power_plugged and b.secsleft not in (psutil.POWER_TIME_UNLIMITED, psutil.POWER_TIME_UNKNOWN) and b.secsleft > 0:
                extra = f", about {b.secsleft // 3600} h {b.secsleft % 3600 // 60} min left"
            lines.append(f"Battery: {round(b.percent)}% ({state}{extra})")
        if want_all:
            cpu = psutil.cpu_percent(interval=0.5)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("C:\\" if IS_WINDOWS else "/")
            lines.append(f"CPU: {cpu:.0f}% busy")
            lines.append(f"RAM: {mem.used / 2**30:.1f} of {mem.total / 2**30:.1f} GB used ({mem.percent:.0f}%)")
            lines.append(f"Disk C: {disk.free / 2**30:.0f} GB free of {disk.total / 2**30:.0f} GB")
            top = []
            for p in psutil.process_iter(["name", "memory_info"]):
                try:
                    top.append((p.info["memory_info"].rss, p.info["name"]))
                except Exception:
                    pass
            top.sort(reverse=True)
            names = []
            for rss, n in top:
                if n and n not in names:
                    names.append(n)
                if len(names) == 3:
                    break
            if names:
                lines.append("Using the most RAM: " + ", ".join(names))
        if not lines:
            return reply("I couldn't read the battery. This computer may not report it.")
        return reply("\n".join(lines), ". ".join(lines[:3]) + ".")

    # ---------------- web: weather, news, wikipedia
    WMO = {0: "clear sky", 1: "mostly clear", 2: "partly cloudy", 3: "cloudy", 45: "foggy", 48: "foggy",
           51: "light drizzle", 53: "drizzle", 55: "heavy drizzle", 56: "freezing drizzle", 57: "freezing drizzle",
           61: "light rain", 63: "rain", 65: "heavy rain", 66: "freezing rain", 67: "freezing rain",
           71: "light snow", 73: "snow", 75: "heavy snow", 77: "snow grains", 80: "light showers",
           81: "showers", 82: "heavy showers", 85: "snow showers", 86: "snow showers",
           95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail"}

    def weather(self, t, raw):
        if not re.search(r"\bweather\b|\btemperature\b|\bwill it rain\b|\bis it (?:going to )?rain|\bforecast\b|\bhow hot\b|\bhow cold\b|\bumbrella\b", t):
            return None
        tomorrow = "tomorrow" in t
        city, lat, lon = self.cfg["city"], self.cfg["latitude"], self.cfg["longitude"]
        m = re.search(r"\b(?:in|at|for)\s+([a-z][a-z .'-]{1,40}?)(?:\s+(?:today|tomorrow|now|right now|this week))?$", t)
        if m and m.group(1).strip() not in ("today", "tomorrow", "now", "this week") and not m.group(1).startswith(("the ", "this ", "my ")):
            place = m.group(1).strip()
            g = http_get("https://geocoding-api.open-meteo.com/v1/search",
                         {"name": place, "count": 1, "language": "en", "format": "json"}).json()
            res = (g or {}).get("results") or []
            if not res:
                return reply(f"I couldn't find a place called {place}.")
            city = res[0]["name"] + (f", {res[0].get('admin1')}" if res[0].get("admin1") else "")
            lat, lon = res[0]["latitude"], res[0]["longitude"]
        data = http_get("https://api.open-meteo.com/v1/forecast", {
            "latitude": lat, "longitude": lon, "timezone": "auto", "forecast_days": 2,
            "current": "temperature_2m,apparent_temperature,relative_humidity_2m,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max",
        }).json()
        d = data["daily"]
        i = 1 if tomorrow else 0
        day_line = (f"{'Tomorrow' if tomorrow else 'Today'} in {city}: {self.WMO.get(d['weather_code'][i], 'mixed weather')}, "
                    f"{round(d['temperature_2m_min'][i])} to {round(d['temperature_2m_max'][i])}°C, "
                    f"{d['precipitation_probability_max'][i] or 0}% chance of rain.")
        if tomorrow:
            return reply(day_line, day_line.replace("°C", " degrees"))
        c = data["current"]
        now_line = (f"Right now: {round(c['temperature_2m'])}°C (feels like {round(c['apparent_temperature'])}°C), "
                    f"{self.WMO.get(c['weather_code'], 'mixed weather')}, humidity {c['relative_humidity_2m']}%, "
                    f"wind {round(c['wind_speed_10m'])} km/h.")
        rain = d["precipitation_probability_max"][0] or 0
        tip = " Take an umbrella." if rain >= 50 else ""
        return reply(day_line + "\n" + now_line + tip,
                     f"In {city.split(',')[0]} it's {round(c['temperature_2m'])} degrees and {self.WMO.get(c['weather_code'], 'mixed')}. "
                     f"Today {round(d['temperature_2m_min'][0])} to {round(d['temperature_2m_max'][0])} degrees, {rain}% chance of rain.{tip}")

    SKIP_WORDS = {"cricket": r"cricket|\bipl\b|bcci|\bt20|\bodi\b|wicket|innings|test match|cricbuzz|cricinfo|\bicc\b|kohli|"
                             r"rohit sharma|gill\b|bumrah|world cup|asia cup|ranji|run chase|batting|bowling|\bvs\b.*\b(?:1st|2nd|3rd) (?:odi|t20|test)",
                  "sports": r"cricket|football|\bipl\b|\bisl\b|tennis|hockey|olympic|fifa|\bnba\b|match|tournament|world cup",
                  "politics": r"\bbjp\b|congress|election|minister|\bmla\b|\bmp\b|parliament|lok sabha|rajya sabha|modi|rahul gandhi",
                  "movies": r"movie|film|box office|trailer|actor|actress|bollywood|tollywood|\bott\b|netflix|web series",
                  "celebrity": r"actor|actress|celebrity|star kid|bollywood|gossip|wedding|divorce"}

    def news_skips(self):
        return [str(x).lower() for x in (self.cfg.get("news_skip") or [])]

    def fetch_news(self, topic=None, n=6):
        """Latest headlines from Google News (India). Returns [(title, link)].
        Topics he said he isn't interested in ("no more cricket news") are left out, unless he asks for that topic."""
        skips = [k for k in self.news_skips() if not (topic and k in topic.lower())]
        skip_re = re.compile("|".join(self.SKIP_WORDS.get(k, r"\b" + re.escape(k) + r"\b") for k in skips), re.I) if skips else None
        want = n
        if topic:
            url = ("https://news.google.com/rss/search?q=" + urllib.parse.quote(topic + " when:1d") +
                   "&hl=en-IN&gl=IN&ceid=IN:en")
        else:
            url = "https://news.google.com/rss?hl=en-IN&gl=IN&ceid=IN:en"
        root = ET.fromstring(http_get(url).content)
        items = []
        for it in root.iter("item"):
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            if " - " in title:
                title, source = title.rsplit(" - ", 1)
                title = f"{title} ({source})"
            if title and not (skip_re and skip_re.search(title)):
                items.append((title, link))
            if len(items) == want:
                break
        return items

    # ---------------- internships (live search pages, always current, free)
    def internship_links(self):
        words = self.cfg.get("internship_keywords") or ["devops", "cloud computing", "linux"]
        shala = [(f"Internshala: {w} internships", "https://internshala.com/internships/keywords-" + urllib.parse.quote(w.replace(" ", "-")) + "/")
                 for w in words[:3]]
        linkedin = ("LinkedIn: DevOps / cloud intern roles in India, posted this week",
                    "https://www.linkedin.com/jobs/search/?keywords=" + urllib.parse.quote("devops OR cloud intern") +
                    "&location=India&f_TPR=r604800")
        naukri = ("Naukri: DevOps internships", "https://www.naukri.com/devops-internship-jobs")
        return shala[:1] + [linkedin, naukri] + shala[1:]

    def internships(self, t, raw):
        if not re.search(r"\b(internships?|intern jobs?|job openings|fresher jobs)\b", t):
            return None
        links = self.internship_links()
        if getattr(self, "_source", "laptop") == "laptop":
            for _, url in links[:3]:
                webbrowser.open(url)
            head = "Opened the top searches in your browser. All the live searches (updated by the sites every day):"
        else:
            head = "Live internship searches (updated by the sites every day):"
        fresh = self.fresh_internship_news(5)
        if fresh:
            head = "New internship news (last 24 hours):\n" + "\n".join(f"{i}. [{h}]({u})" for i, (h, u) in enumerate(fresh, 1)) + "\n\n" + head
        return reply(head + "\n" + "\n".join(f"- {name}\n  {url}" for name, url in links) +
                     "\nTip: apply to ones asking for Linux, Docker or AWS, and note which skills repeat.",
                     "I opened internship searches in your browser.")

    # ---------------- morning briefing (free: all from your laptop + free sources)
    def morning_briefing(self):
        now = datetime.now()
        lines = [f"Good morning, Hemanth! It's {now.strftime('%A, %d %B')}.", ""]
        say = ["Good morning, Hemanth."]
        try:
            w = self.weather("weather", "weather")
            lines.append("Weather: " + w["text"].split("\n")[0])
            say.append(w["say"].split(" Today")[0].rstrip(".") + ".")
        except Exception:
            lines.append("Weather: couldn't get it (no internet?).")
        plan = self.load_plan() if self.cfg.get("study_plan_reminders") else None
        if plan:
            idx = self.today_index(plan)
            d = self.plan_day(plan, idx)
            if d:
                done = self.is_done(idx)
                cur, _ = self.streaks(idx)
                missed = self.missed(plan, idx)
                lines.append(f"Study: {d['label']}, {d['title']}" + (" (done)" if done else "") +
                             f". Streak: {cur} day{'s' if cur != 1 else ''}." +
                             (f" Carried over: {len(missed)}." if missed else ""))
                say.append(f"Today's task is {d['title']}.")
                lines += self.lab_briefing_lines()
        items = read_json("reminders.json", [])
        today = [r for r in items if isinstance(r, dict) and not r.get("done")
                 and r.get("when", "")[:10] == now.date().isoformat()]
        if today:
            lines.append("Reminders today: " + "; ".join(
                f"{fmt_time(datetime.fromisoformat(r['when']))} {r['text']}" for r in sorted(today, key=lambda r: r['when'])))
            say.append(f"You have {len(today)} reminder{'s' if len(today) != 1 else ''} today.")
        rows = self.tasks_for(now.date())
        if rows:
            lines.append(f"My tasks today ({len(rows)}): " + ", ".join(tk["text"] + (" (overdue)" if tag.startswith("overdue") else "")
                                                               for tk, done, tag in rows[:6]) + ("…" if len(rows) > 6 else ""))
            say.append(f"You have {len(rows)} task{'s' if len(rows) != 1 else ''} today.")
        for title, topic in (("Top news", None), ("Tech news", "technology")):
            try:
                news = self.fetch_news(topic, 3)
            except Exception:
                news = []
            if news:
                lines += ["", title + ":"] + [f"{i}. [{h}]({u})" for i, (h, u) in enumerate(news, 1)]
                if topic is None:
                    say.append("Top story: " + re.sub(r"\s*\([^)]*\)$", "", news[0][0]) + ".")
        fresh = self.fresh_internship_news(3)
        if fresh:
            lines += ["", "New internship news today:"] + [f"{i}. [{h}]({u})" for i, (h, u) in enumerate(fresh, 1)]
            say.append(f"There {'is' if len(fresh) == 1 else 'are'} {len(fresh)} new internship update{'s' if len(fresh) != 1 else ''}.")
        lines += ["", "Internship searches (the sites add new posts every day):"] + [f"- [{n}]({u})" for n, u in self.internship_links()[:3]]
        lines += ["", "Have a good day!"]
        return reply("\n".join(lines), " ".join(say) + " The full briefing is on screen.")

    def briefing(self, t, raw):
        if not re.match(r"^(?:good morning|gm|morning|morning briefing|(?:my |daily |today'?s )?briefing|brief me|what'?s new today|morning update)$", t):
            return None
        out = self.morning_briefing()
        self._mark_briefed()
        return out

    def _mark_briefed(self):
        state = read_json("briefing_state.json", {}) or {}
        state["last"] = date.today().isoformat()
        write_json("briefing_state.json", state)

    def morning_due(self):
        """True once a day, at morning_briefing_time (or the first time Vega runs after it, before noon)."""
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(self.cfg.get("morning_briefing_time") or ""))
        if not m:
            return False
        now = datetime.now()
        if (now.hour, now.minute) < (int(m.group(1)), int(m.group(2))) or now.hour >= 12:
            return False
        state = read_json("briefing_state.json", {}) or {}
        if state.get("last") == date.today().isoformat():
            return False
        self._mark_briefed()
        return True

    def news(self, t, raw):
        cricket = re.search(r"\bcricket\b|\bmatch score\b|\bipl\b", t)
        if not (re.search(r"\bnews\b|\bheadlines\b", t) or cricket or
                re.search(r"\bnot interested in (?:sports?|politics|political|movies?|films?|celebrit)", t)):
            return None
        # "I'm not interested in cricket", "don't show me the news", "why are you showing cricket news again"
        if re.search(r"\b(?:don'?t|dont|do not|stop|no more|not interested|never|hate|enough|nobody asked|didn'?t ask|did not ask|why)\b|"
                     r"^(?:who asked|i said|no\b)", t):
            topic = None
            m = re.search(r"(?:not interested in|no more|don'?t show(?: me)?|dont show(?: me)?|do not show(?: me)?|hate)\s+(?:the\s+|any\s+)?"
                          r"([a-z]+(?: [a-z]+)?)(?:\s+(?:news|headlines|updates?|articles?|stories))?\b", t)
            generic = {"news", "the news", "headlines", "articles", "article", "stories", "updates", "me", "it", "this", "that", "any", "you"}
            if cricket:
                topic = "cricket"
            elif m:
                words_ = [w for w in m.group(1).split() if w not in generic]
                topic = " ".join(words_) or None
                if topic:
                    topic = {"sport": "sports", "movie": "movies", "film": "movies", "films": "movies",
                             "political": "politics"}.get(topic, topic)
            if topic and len(topic.split()) <= 2:
                skips = sorted(set(self.news_skips()) | {topic})
                self.cfg["news_skip"] = skips
                save_config_value("news_skip", skips)
                return reply(f"Sorry about that! No more {topic} news. I'll leave it out of the news and your morning briefing.\n"
                             f"(If you ever want it, say “show {topic} news”. To undo, say “show {topic} news again”.)",
                             f"Sorry! No more {topic} news.")
            return reply("Sorry! I'll only show news when you ask for it, like “show me the news” or “tech news”.",
                         "Sorry! I'll only show news when you ask.")
        # only real requests: "show me the news", "tech news", "any cricket updates", "news about ISRO"
        asked = re.match(r"^(?:please\s+)?(?:show|give|tell|read|get|fetch|open|play)\b.*\b(?:news|headlines)\b", t) or \
            re.match(r"^(?:what'?s|what is|what are|whats|any|latest|today'?s|todays|top|breaking|headlines)\b.*\b(?:news|headlines|score|scores)\b", t) or \
            re.search(r"\bnews (?:about|on|for|from)\b", t) or \
            (len(t.split()) <= 5 and re.search(r"\b(?:news|headlines)$", t)) or \
            re.search(r"\b(?:cricket|ipl)\s+(?:score|scores|update|updates|news|match)\b|\b(?:live|match) score\b", t)
        if not asked:
            return None  # just talking about news: let the normal chat answer
        m = re.match(r"^(?:show|give|tell)(?: me)? ([a-z ]+?) news again$", t)
        if m and m.group(1) in self.news_skips():
            skips = [k for k in self.news_skips() if k != m.group(1)]
            self.cfg["news_skip"] = skips
            save_config_value("news_skip", skips)
            t = f"{m.group(1)} news"
        m = re.search(r"news (?:about|on|for|from)\s+(.+)$", t)
        if m:
            topic = m.group(1).strip()
        else:
            m2 = re.match(r"^(.*?)\s*\b(?:news|headlines)\b", t)
            topic = m2.group(1).strip() if m2 else ""
            filler = r"^(?:(?:give|tell|show|read) me|what'?s|what is|what are|any|some|the|latest|today'?s|todays|top|breaking|me)\b\s*"
            while re.match(filler, topic):
                topic = re.sub(filler, "", topic, count=1)
        topic = {"tech": "technology", "sport": "sports"}.get(topic, topic)
        if topic in ("", "india", "indian"):
            topic = None
        if cricket:
            topic = "cricket"
        items = self.fetch_news(topic)
        if not items:
            return reply("I couldn't find any news right now.")
        head = f"{'Cricket' if cricket else (topic.capitalize() if topic else 'Top')} news:"
        lines = [f"{i}. [{title}]({link})" for i, (title, link) in enumerate(items, 1)]
        say = head + " " + ". ".join(re.sub(r"\s*\([^)]*\)$", "", x[0]) for x in items[:3]) + "."
        note = "\n(Click a headline to open it. For live scores, open one. I only get headlines.)" if cricket else "\n(Click a headline to open it.)"
        return reply(head + "\n" + "\n".join(lines) + note, say)

    def wikipedia(self, t, raw):
        m = re.match(r"^(?:who (?:is|was|are|were)|tell me about|what do you know about|search wikipedia for|wikipedia|wiki)\s+(.+)$", t)
        if not m:
            return None
        q = m.group(1).strip()
        if q in ("you", "yourself", "me", "myself") or q.startswith(("my ", "your ")):
            return None
        try:
            s = http_get("https://en.wikipedia.org/w/api.php", {
                "action": "query", "list": "search", "srsearch": q, "format": "json", "srlimit": 1}).json()
            hits = s.get("query", {}).get("search", [])
            if not hits:
                return None
            title = hits[0]["title"]
            summ = http_get("https://en.wikipedia.org/api/rest_v1/page/summary/" +
                            urllib.parse.quote(title.replace(" ", "_"))).json()
        except Exception:
            return None  # no internet: let the AI answer instead
        extract = (summ.get("extract") or "").strip()
        if not extract:
            return None
        sentences = re.split(r"(?<=[.!?])\s+", extract)
        short = " ".join(sentences[:3])
        link = (summ.get("content_urls", {}).get("desktop", {}) or {}).get("page", "")
        return reply(f"{title}: {short}\n(From Wikipedia: {link})", short)

    # ---------------- open apps / sites, search, play
    def search_and_play(self, t, raw):
        m = re.match(r"^(?:search|look up|google)\s+(?:on\s+)?youtube\s+(?:for\s+)?(.+)$", t) or \
            re.match(r"^play\s+(.+?)(?:\s+on\s+youtube)?$", t)
        if m and not re.search(r"^play\s+(?:a\s+)?game", t):
            q = m.group(1).strip()
            webbrowser.open("https://www.youtube.com/results?search_query=" + urllib.parse.quote(q))
            return reply(f"Opening YouTube for: {q}. Click the first video to play.", f"Opening YouTube for {q}.")
        m = re.match(r"^(?:search|google|look up)(?:\s+google)?(?:\s+for)?\s+(.+)$", t)
        if m:
            q = m.group(1).strip()
            webbrowser.open("https://www.google.com/search?q=" + urllib.parse.quote(q))
            return reply(f"Searching Google for: {q}", f"Searching Google for {q}.")
        return None

    # ---------------- contacts + WhatsApp (opens the chat; you press call yourself)
    def load_contacts(self):
        c = read_json("contacts.json", {})
        return c if isinstance(c, dict) else {}

    def import_vcf(self, path):
        """Import contacts from a .vcf file (exported from iPhone / iCloud / Google Contacts). Keeps existing ones."""
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        raw = re.sub(r"\r?\n[ \t]", "", raw)  # unfold long lines
        contacts = self.load_contacts()
        added = skipped = 0
        for card in re.split(r"BEGIN:VCARD", raw, flags=re.I)[1:]:
            name = ""
            m = re.search(r"^FN[^:\n]*:(.+)$", card, re.M | re.I)
            if m:
                name = m.group(1).strip()
            if not name:
                m = re.search(r"^N[^:\n]*:([^;\n]*);([^;\n]*)", card, re.M | re.I)
                if m:
                    name = (m.group(2) + " " + m.group(1)).strip()
            phones = re.findall(r"^(?:item\d+\.)?TEL[^:\n]*:(.+)$", card, re.M | re.I)
            if not name or not phones:
                skipped += 1
                continue
            name = re.sub(r"\\([,;])", r"\1", name)[:40]
            best = None
            for p in phones:  # prefer an Indian mobile number
                digits = re.sub(r"\D", "", p)
                if digits.startswith("00"):
                    digits = digits[2:]
                if len(digits) == 11 and digits.startswith("0"):
                    digits = digits[1:]
                if len(digits) == 10:
                    digits = "91" + digits
                if 11 <= len(digits) <= 15:
                    best = best or digits
                    if digits.startswith("91") and digits[2] in "6789":
                        best = digits
                        break
            if not best:
                skipped += 1
                continue
            key = name.lower()
            if key in contacts:
                skipped += 1
                continue
            contacts[key] = {"name": name, "phone": best}
            added += 1
        write_json("contacts.json", contacts)
        return reply(f"Imported {added} contact{'s' if added != 1 else ''}"
                     + (f" ({skipped} skipped: no number, or already saved)" if skipped else "") +
                     f". You now have {len(contacts)} contacts.\nTry: call <name>, or chat with <name>. "
                     "(Only people who use WhatsApp can be called on WhatsApp.)",
                     f"Imported {added} contacts.")

    def find_contact(self, name):
        contacts = self.load_contacts()
        key = name.strip().lower()
        key = re.sub(r"^(my|the)\s+", "", key)
        if key in contacts:
            return contacts[key]
        close = difflib.get_close_matches(key, list(contacts), n=1, cutoff=0.75)
        return contacts[close[0]] if close else None

    def contacts(self, t, raw):
        m = re.match(r"^(?:add|save|new)\s+(?:a\s+)?contact\s+(.+?)\s+(\+?\d[\d\s-]{8,})$", t)
        if m:
            name = keep_case(m.group(1).strip(" ,:"), raw)
            digits = re.sub(r"\D", "", m.group(2))
            if len(digits) == 10:
                digits = "91" + digits  # Indian number without country code
            if not 11 <= len(digits) <= 15:
                return reply("That number doesn't look right. Use the full number, like: add contact Amma 98765 43210")
            c = self.load_contacts()
            c[name.lower()] = {"name": name, "phone": digits}
            write_json("contacts.json", c)
            return reply(f"Saved {name}. Now say: call {name} on WhatsApp", f"Saved {name}.")
        m = re.match(r"^(?:remove|delete|forget)\s+contact\s+(.+)$", t)
        if m:
            c = self.load_contacts()
            hit = self.find_contact(m.group(1))
            if not hit:
                return reply(f"I don't have a contact called {m.group(1)}.")
            c.pop(hit["name"].lower(), None)
            write_json("contacts.json", c)
            return reply(f"Removed {hit['name']} from your contacts.")
        if re.match(r"^(?:show |list )?(?:my |all )?contacts$", t):
            c = self.load_contacts()
            if not c:
                return reply("You have no contacts saved. Add one with: add contact Amma 98765 43210")
            return reply("Your contacts:\n" + "\n".join(f"{v['name']}" for v in sorted(c.values(), key=lambda v: v["name"].lower())),
                         f"You have {len(c)} contacts. They're on screen.")
        # "open WhatsApp and call Akka", "go to whatsapp then message Ravi", "on whatsapp call Amma"
        t = re.sub(r"^(?:(?:open|launch|start|go to)\s+whatsapp|on\s+whatsapp|in\s+whatsapp)\s*(?:and|then|to|,)?\s+(?=(?:video\s+|voice\s+)?call|message|text|chat)", "", t)
        m = re.match(r"^(?:(whatsapp|video|voice)\s+)?call\s+(.+?)(?:\s+(?:on|in|through|using)\s+whatsapp)?$", t) or \
            re.match(r"^(?:open\s+)?(?:my\s+)?(?:whatsapp\s+)?chat\s+with\s+(.+)()$", t) or \
            re.match(r"^(?:message|whatsapp|text)\s+(.+)()$", t)
        if not m:
            return None
        name = m.group(2) if m.re.pattern.startswith("^(?:(whatsapp") else m.group(1)
        name = keep_case(name.strip(), raw)
        if re.match(r"^(?:vega|you|yourself|me)\b", name.lower()):  # "call Vega only" = talking about me, not a contact
            return reply("That's me! You don't need to call me: just say “Hey Vega” and then what you want. "
                         "While my window is open, you can just talk.")
        video = t.startswith("video")
        who = self.find_contact(name)
        if not who:
            return reply(f"I don't have {name} in your contacts yet. Add them first:\n"
                         f"add contact {name} 98765 43210   (use their real number)\n"
                         "Numbers stay only on this laptop, in contacts.json.",
                         f"I don't have {name} yet. Add them with: add contact, then the name and number.", unknown=True)
        if not IS_WINDOWS:
            return reply("This only works on Windows with the WhatsApp app.")
        subprocess.Popen(["cmd", "/c", "start", "", f"whatsapp://send?phone={who['phone']}"], creationflags=NO_WINDOW)
        what = "call" if "call" in t else "chat"
        tip = f"Press the {'video' if video else 'phone'} button at the top to call." if what == "call" else "Type your message there."
        return reply(f"Opening your WhatsApp chat with {who['name']}. {tip}\n"
                     "(For your safety I only open the chat. You press call or send yourself.)",
                     f"Opening WhatsApp with {who['name']}. {tip}")

    def sites_command(self, t, raw):
        """Teach Vega new websites: 'add site srm portal https://...'. List them: 'what can you open'."""
        m = re.match(r"^(?:add|save|remember)\s+(?:a\s+|the\s+|my\s+)?(?:website|site|link)\s+(?:called\s+|named\s+)?(.+?)\s+(?:as\s+|at\s+|is\s+|=\s*)?"
                     r"(https?://\S+|www\.\S+|[a-z0-9-]+(?:\.[a-z0-9-]+)+\S*)$", t)
        if m:
            name = m.group(1).strip(" ,:")
            url = keep_case(m.group(2), raw)
            if not url.lower().startswith(("http://", "https://")):
                url = "https://" + url
            cfg = read_json("config.json", {}) or {}
            sites = cfg.get("sites") if isinstance(cfg.get("sites"), dict) else {}
            sites[name] = url
            cfg["sites"] = sites
            write_json("config.json", cfg)
            self.cfg["sites"][name] = url
            return reply(f"Saved. Now say 'open {name}' and I'll open {url}", f"Saved. Now say open {name}.")
        m = re.match(r"^(?:remove|delete|forget)\s+(?:the\s+)?(?:website|site|link)\s+(.+)$", t)
        if m:
            name = m.group(1).strip()
            cfg = read_json("config.json", {}) or {}
            if isinstance(cfg.get("sites"), dict):
                cfg["sites"].pop(name, None)
                write_json("config.json", cfg)
            self.cfg["sites"].pop(name, None)
            return reply(f"Removed {name}.")
        if re.search(r"^(?:what can you open|what (?:apps|sites|websites) (?:can you open|do you know)|(?:list|show) (?:my |all )?(?:apps|sites|websites)|my (?:apps|sites|websites))$", t):
            apps = ", ".join(sorted(set(list(self.cfg["apps"]) + [a["name"] for a in self.app_index()]), key=str.lower))
            sites = ", ".join(sorted(self.cfg["sites"]))
            return reply(f"Apps I can open:\n{apps}\n\nWebsites I can open:\n{sites}\n\n"
                         "Teach me a new website: add site NAME https://address",
                         "I can open lots of apps and websites. The list is on screen.")
        return None

    def app_index(self):
        """Every app in your Start menu (from Windows' Get-StartApps), cached in apps_index.json."""
        data = read_json("apps_index.json", [])
        return [a for a in data if isinstance(a, dict) and a.get("name") and a.get("id")] if isinstance(data, list) else []

    def refresh_app_index(self):
        if not IS_WINDOWS:
            return
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                                "Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress"],
                               capture_output=True, text=True, timeout=60, creationflags=NO_WINDOW)
            items = json.loads(r.stdout or "[]")
            if isinstance(items, dict):
                items = [items]
            apps = [{"name": str(a.get("Name", "")).strip(), "id": str(a.get("AppID", "")).strip()} for a in items]
            apps = [a for a in apps if a["name"] and a["id"] and not re.search(r"uninstall|readme|help|documentation", a["name"], re.I)]
            if apps:
                write_json("apps_index.json", apps)
        except Exception:
            pass

    def find_installed_app(self, name):
        name = name.replace(" plus plus", "++").replace(" sharp", "#")
        apps = self.app_index()
        if not apps:
            return None
        low = {a["name"].lower(): a for a in apps}
        if name in low:
            return low[name]
        starts = [a for n, a in low.items() if n.startswith(name + " ") or n.replace(" ", "") == name.replace(" ", "")]
        if starts:
            return starts[0]
        close = difflib.get_close_matches(name, list(low), n=1, cutoff=0.75)
        if close:
            return low[close[0]]
        contains = [a for n, a in low.items() if len(name) >= 4 and name in n]
        return contains[0] if len(contains) == 1 else None

    # ---------------- browsers
    BROWSERS = {
        "brave": [r"BraveSoftware\Brave-Browser\Application\brave.exe"],
        "chrome": [r"Google\Chrome\Application\chrome.exe"],
        "edge": [r"Microsoft\Edge\Application\msedge.exe"],
        "firefox": [r"Mozilla Firefox\firefox.exe"],
        "opera": [r"Programs\Opera\opera.exe", r"Programs\Opera GX\opera.exe", r"Opera\opera.exe"],
    }
    # words speech-to-text often writes instead of "brave" (Indian accent)
    BRAVE_SOUNDS = {"brave", "braves", "brav", "brev", "breve", "brave's", "bray", "brave-", "prior", "prayer", "brain",
                    "grave", "rave", "brace", "brake", "break", "bread", "imperial", "above", "wave", "braid", "bravo"}

    def browser_key(self, word):
        """'brave' / 'prior' / 'chrome' / 'google chrome' / 'microsoft edge' -> a key of BROWSERS, or None."""
        w = (word or "").strip().lower()
        w = re.sub(r"^(google|microsoft|mozilla)\s+", "", w)
        if w in ("", "web", "internet", "my", "the", "default", "a"):
            return str(self.cfg.get("browser") or "brave").lower()
        if w in self.BROWSERS:
            return w
        if w in self.BRAVE_SOUNDS:
            return "brave"
        close = difflib.get_close_matches(w, list(self.BROWSERS), n=1, cutoff=0.7)
        return close[0] if close else None

    def browser_exe(self, key):
        roots = [os.environ.get(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
        for rel in self.BROWSERS.get(key, []):
            for root in roots:
                if root and os.path.exists(os.path.join(root, rel)):
                    return os.path.join(root, rel)
        return None

    def open_in_browser(self, key, url=None):
        exe = self.browser_exe(key) if IS_WINDOWS else None
        if exe:
            subprocess.Popen([exe] + ([url] if url else []), creationflags=NO_WINDOW)
            return True
        if url:
            webbrowser.open(url)
            return True
        app = self.find_installed_app(key)
        if app and IS_WINDOWS:
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app["id"]], creationflags=NO_WINDOW)
            return True
        return False

    def open_with_browser(self, name):
        """'brave browser', 'prior browser', 'youtube in brave browser', 'youtube imperial browser', 'browser'."""
        name = re.sub(r"[,.]", " ", name)
        name = re.sub(r"\s+", " ", name).strip()
        words = name.split()
        if words and words[-1] == "browser":
            words = words[:-1]
        elif any(w in ("in", "on", "with", "using") for w in words[-3:-1]):
            pass
        else:
            return None
        key = None
        for n in (2, 1):  # "google chrome", "microsoft edge", then one word
            if len(words) >= n:
                k = self.browser_key(" ".join(words[-n:]))
                if k and (n == 1 or words[-2] in ("google", "microsoft", "mozilla")):
                    key, words = k, words[:-n]
                    break
        if key is None:
            key = self.browser_key("")
        while words and words[-1] in ("in", "on", "with", "using", "from", "the", "my", "a"):
            words = words[:-1]
        site = " ".join(words)
        nice = {"brave": "Brave", "chrome": "Chrome", "edge": "Edge", "firefox": "Firefox"}.get(key, key.title())
        sites = self.cfg["sites"]
        if site:
            site = re.sub(r"^(?:the|my|our)\s+", "", site)
            if site in ("lab log", "lablog", "claude lab log"):
                # Open the local Lab Log window instead of external URL
                return self.lablog_command(t, raw) or reply("Opening your Lab Log.")
            else:
                url = sites.get(site)
                if not url:
                    close = difflib.get_close_matches(site, list(sites), n=1, cutoff=0.8)
                    url = sites[close[0]] if close else None
                if not url and re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$", site):
                    url = "https://" + site
                if not url:
                    url = "https://www.google.com/search?q=" + urllib.parse.quote(site)
            self.open_in_browser(key, url)
            return reply(f"Opening {site} in {nice}.")
        if self.open_in_browser(key):
            return reply(f"Opening {nice}.")
        return reply(f"I couldn't find {nice} on this laptop.", unknown=True)

    def open_thing(self, t, raw):
        t = re.sub(r"^open(?=(?:prior|brave|brev|breve|bray|imperial|browser)\b)", "open ", t)
        m = re.match(r"^(?:open|launch|start|run)\s+(?:up\s+)?(?:the\s+|my\s+)?(.+?)(?:\s+(?:app|application|website|site|page))?$", t)
        if not m:
            return None
        name = m.group(1).strip()
        if re.search(r"\bbrowser$", name) or re.search(r"\s(?:in|on|with|using)\s+(brave|chrome|google chrome|edge|microsoft edge|firefox)$", name):
            out = self.open_with_browser(name)
            if out:
                return out
        if re.search(r"\b(diary|journal|document|doc|file|pdf|to-?do|todo|reminders?)\b", name):
            return None
        if re.search(r"\b(lab ?log|lablog|black lock|applock|lag log|lap log|lab lock)\b", name):
            # Always open the local Lab Log window (not the external website)
            return self.lablog_command(t, raw)
        apps, sites = self.cfg["apps"], self.cfg["sites"]
        if name not in apps and name not in sites:
            close = difflib.get_close_matches(name, list(apps) + list(sites), n=1, cutoff=0.8)
            if close:
                name = close[0]
        if name in sites:
            webbrowser.open(sites[name])
            return reply(f"Opening {name}.")
        if name in apps:
            if not IS_WINDOWS:
                return reply("Opening apps only works on Windows.")
            target = apps[name]
            subprocess.Popen(["cmd", "/c", "start", "", target], creationflags=NO_WINDOW)
            return reply(f"Opening {name}.")
        if re.match(r"^[a-z0-9-]+(\.[a-z0-9-]+)+$", name):
            webbrowser.open("https://" + name)
            return reply(f"Opening {name}.")
        app = self.find_installed_app(name)
        if app and IS_WINDOWS:
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + app["id"]], creationflags=NO_WINDOW)
            return reply(f"Opening {app['name']}.")
        if IS_WINDOWS and len(name) >= 3:  # maybe it's one of his files: "open my resume"
            hits = self.search_files(name, limit=8)
            if hits:
                self.last_files = hits
                if len(hits) == 1:
                    return self.open_path(hits[0])
                return self.show_file_results(name, open_single=False)
        return reply(f"I couldn't find an app, website or file called '{name}'. If it's a website, teach me with:  "
                     f"add site {name} https://its-address\n(Say 'what can you open' to see everything I know.)",
                     f"I couldn't find {name}. The way to teach me is on screen.", unknown=True)

    # ---------------- diary
    def diary_file(self, day):
        return here("diary", day.isoformat() + ".txt")

    def diary_append(self, text):
        with open(self.diary_file(date.today()), "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().strftime('%I:%M %p').lstrip('0')}] {text.strip()}\n")

    def diary_command(self, t, raw):
        if re.search(r"\b(close|end|stop|finish|save|exit)\s+(?:my\s+|the\s+)?(diary|journal)\b|that'?s all for (today|the diary|my diary)", t):
            if not self.diary_mode:
                return reply("Your diary isn't open. Say 'open my diary' to start.")
            self.diary_mode = False
            return reply("Saved in your diary for today. Sleep well, Hemanth." if datetime.now().hour >= 20
                         else "Saved in your diary for today.")
        if re.search(r"\b(start|open|begin|write in|write)\s+(?:my\s+|the\s+|in\s+my\s+)?(diary|journal)\b|let'?s write (?:my |in my )?(diary|journal)|^(diary|journal) time$", t):
            self.diary_mode = True
            return reply("Your diary is open. Tell me about your day, as much or as little as you like. "
                         "Everything you say goes into today's page. Say 'close diary' when you're done.",
                         "Your diary is open. Tell me about your day. Say close diary when you're done.")
        m = re.match(r"^(?:diary|journal|dear diary)[:,]?\s+(.+)$", raw.strip(), flags=re.I)
        if m and not re.match(r"^(diary|journal)\s+(time|for|from)\b", t):
            self.diary_append(m.group(1))
            return reply("Added to today's diary.")
        m = re.search(r"\b(?:read|show|open)\s+(?:me\s+)?(?:my\s+|the\s+)?(?:diary|journal)(?:\s+(?:for|from|of|on))?\s*(today|yesterday|\d{4}-\d{2}-\d{2})?$", t) or \
            re.search(r"what did i write (?:in my diary )?(today|yesterday)", t)
        if m:
            which = (m.group(1) or "today")
            day = date.today() - timedelta(days=1) if which == "yesterday" else (
                date.fromisoformat(which) if re.match(r"\d{4}-", which) else date.today())
            try:
                with open(self.diary_file(day), "r", encoding="utf-8") as f:
                    content = f.read().strip()
            except OSError:
                content = ""
            if not content:
                return reply(f"There's nothing in your diary for {fmt_day(day)}.")
            return reply(f"Your diary for {day.strftime('%A %d %B')}:\n{content}", f"Here's your diary for {fmt_day(day)}. It's on screen.")
        return None

    # ---------------- documents
    def doc_folder(self):
        return here(self.cfg["documents_folder"])

    def list_docs(self):
        ok = (".pdf", ".txt", ".md", ".docx", ".csv", ".py", ".sh", ".yaml", ".yml", ".json", ".log")
        try:
            return sorted(f for f in os.listdir(self.doc_folder()) if f.lower().endswith(ok))
        except OSError:
            return []

    def read_document(self, path, limit=None):
        try:
            limit = int(limit or self.cfg.get("doc_chars", 24000))
        except (TypeError, ValueError):
            limit = 24000
        low = path.lower()
        if low.endswith(".pdf"):
            from pypdf import PdfReader
            reader = PdfReader(path)
            text = ""
            for page in reader.pages:
                text += (page.extract_text() or "") + "\n"
                if len(text) > limit:
                    break
        elif low.endswith(".docx"):
            import docx
            d = docx.Document(path)
            parts = [p.text for p in d.paragraphs]
            for table in d.tables:  # resumes often keep text inside tables
                for row in table.rows:
                    cells = []
                    for c in row.cells:
                        v = c.text.strip()
                        if v and v not in cells:
                            cells.append(v)
                    if cells:
                        parts.append(" | ".join(cells))
            text = "\n".join(parts)
        else:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read(limit + 1)
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        cut = len(text) > limit
        return text[:limit], cut

    def load_document(self, path):
        """Open a document (from the documents folder or the Attach button) and summarise it."""
        name = os.path.basename(path)
        text, cut = self.read_document(path)
        if not text:
            return reply(f"I couldn't read any text in {name}. If it's a scanned PDF, it has no text for me to read.")
        self.active_doc = (name, text)
        pages = max(1, round(len(text) / 3000))
        note = (f" It's long, so I read only the first part (about {pages} pages)." if cut else "")
        return {"ask": f"Summarise the document '{name}' for me in 5 short bullet points, then tell me in one line what it's for.",
                "instruction": f"I just opened '{name}'.{note} I may ask questions about it next.", "doc": name}

    def documents(self, t, raw):
        if re.search(r"^(?:list|show|what are)\s+(?:my\s+|the\s+|all\s+)?(?:documents|docs|files|pdfs)$|^what (?:documents|files) do (?:i|you) have", t):
            docs = self.list_docs()
            if not docs:
                return reply(f"Your documents folder is empty. Put files in:\n{self.doc_folder()}\n"
                             "(PDF, Word .docx, .txt, .md). Or use the Attach button.",
                             "Your documents folder is empty. The folder path is on screen.")
            return reply("Your documents:\n" + "\n".join(f"{i}. {d}" for i, d in enumerate(docs, 1)),
                         f"You have {len(docs)} documents. They're on screen.")
        if re.search(r"^(?:close|forget|done with|stop reading)\s+(?:the\s+|my\s+|this\s+)?(?:document|doc|file|pdf)$", t):
            if not self.active_doc:
                return reply("No document is open.")
            name = self.active_doc[0]
            self.active_doc = None
            return reply(f"Closed {name}.")
        m = re.match(r"^(?:summari[sz]e|read|open|load|study)\s+(?:my\s+|the\s+)?(?:document|doc|file|pdf)?\s*(?:called\s+|named\s+)?(.+)$", t)
        if not m:
            return None
        want = m.group(1).strip()
        docs = self.list_docs()
        if not docs:
            return None
        stems = {os.path.splitext(d)[0].lower().replace("_", " ").replace("-", " "): d for d in docs}
        pick = None
        for stem, d in stems.items():
            if want == stem or want in stem or want == d.lower():
                pick = d
                break
        if not pick:
            close = difflib.get_close_matches(want, list(stems), n=1, cutoff=0.6)
            pick = stems[close[0]] if close else None
        if not pick:
            if re.search(r"\b(document|doc|file|pdf)\b", t):
                return reply(f"I couldn't find '{want}' in your documents. Say 'list my documents' to see them.")
            return None
        return self.load_document(os.path.join(self.doc_folder(), pick))

    # ---------------- 24-week plan
    def load_plan(self):
        p = read_json("plan.json", None)
        return p if isinstance(p, dict) and "weeks" in p else None

    def plan_shift(self):
        try:
            return int((read_json("plan_settings.json", {}) or {}).get("shift", 0))
        except (TypeError, ValueError, AttributeError):
            return 0

    def plan_day(self, plan, index):
        """A Linux plan day. Dates follow his study days (Mon-Sat by default, changeable in the Lab Log)."""
        if index < 0 or index >= len(plan["weeks"]) * 7:
            return None
        d = self.track_day("linux", index)
        return {"index": index, "label": d["label"], "topic": d["topic"], "title": d["title"], "steps": d["steps"],
                "date": d["date"], "week": d["week"]}

    def today_index(self, plan):
        return self.track_today("linux")

    def describe_day(self, d, heading):
        lines = [f"{heading}: {d['label']} ({d['topic']}), {d['date'].strftime('%a %d %b')}", f"  {d['title']}"]
        lines += [f"  {n}. {s}" for n, s in enumerate(d["steps"], 1)]
        if d["index"] % 7 == 0:
            lines += [f"  Coursera: {d['week']['coursera']}", f"  YouTube: {d['week']['youtube']}"]
        return "\n".join(lines), f"{heading} is {d['label']}: {d['title']}. It has {len(d['steps'])} steps. They're on screen."

    def plan_context(self):
        plan = self.load_plan()
        if not plan:
            return ""
        d = self.plan_day(plan, self.today_index(plan))
        other = "".join(f" {name} lesson today: {od['title']}." for name, od in self.other_study_today())
        if not d:
            return other.strip()
        rest = "" if d["date"] == date.today() else f" (today is a Linux rest day; this is the next one, on {d['date'].strftime('%a %d %b')})"
        return f"My Linux study task{rest} is {d['label']} ({d['topic']}): {d['title']}. Steps: " + "; ".join(d["steps"]) + "." + other

    def plan(self, t, raw):
        if not re.search(r"\b(task|plan|schedule|study|week|quiz|test me)\b", t):
            return None
        plan = self.load_plan()
        if plan is None:
            if re.search(r"today|tomorrow|this week|quiz me|move my plan|reset my plan", t):
                return reply("I can't find plan.json. Put it in the same folder as vega_app.py.")
            return None
        m = re.search(r"move (?:my |the )?(?:plan|schedule) (back|forward|ahead)(?: by)? (\w+) days?", t)
        if m:
            n = to_number(m.group(2))
            if not n or n > 30:
                return reply("Tell me a number of days between 1 and 30, like: move my plan back 3 days.")
            shift = self.plan_shift() + (n if m.group(1) == "back" else -n)
            write_json("plan_settings.json", {"shift": shift})
            d = self.plan_day(plan, max(0, min(len(plan["weeks"]) * 7 - 1, self.today_index(plan))))
            screen, _ = self.describe_day(d, "Today is now")
            msg = f"Done. Your plan moved {m.group(1)} {n} day{'s' if n != 1 else ''}."
            return reply(msg + "\n" + screen + "\n(Your ticks stay on the same plan days.)",
                         msg + f" Today is now {d['label']}: {d['title']}.")
        if re.search(r"reset (?:my |the )?(?:plan|schedule)", t):
            write_json("plan_settings.json", {"shift": 0})
            return reply("Done. Your plan is back to the original dates.")
        idx = self.today_index(plan)
        if "tomorrow" in t and re.search(r"task|plan|study", t):
            tmr = date.today() + timedelta(days=1)
            ti = self.track_lesson_on("linux", tmr)
            d = self.plan_day(plan, ti) if ti is not None else None
            extra = "".join(f"\n\n{name} tomorrow: {od['title']}" for name, od in self.other_study_today(tmr))
            if not d:
                return reply(("Tomorrow is a Linux rest day." + extra).strip())
            screen, say = self.describe_day(d, "Tomorrow")
            return reply(screen + extra, say)
        if re.search(r"today'?s (task|plan)|(task|plan|study) (for )?today|what (should|do) i study|^my task$|what'?s my task|what is my task", t):
            if idx < 0:
                first = self.plan_day(plan, 0)["date"].strftime("%A %d %B")
                return reply(f"Your plan hasn't started yet. Day 1 is on {first}.")
            d = self.plan_day(plan, idx)
            extra = "".join(f"\n\n{name} today ({od['short']}, about {self.track_info(od['track'])['minutes']} min): {od['title']}"
                            + "".join(f"\n  {n}. {st}" for n, st in enumerate(od["steps"], 1)) + ("\n  Status: done ✓" if od.get("done") else "")
                            for name, od in self.other_study_today())
            if not d:
                return reply(("You've reached the end of the 24-week plan. Well done!" + extra).strip())
            if d["date"] != date.today():
                screen, _ = self.describe_day(d, "Next Linux day")
                return reply("Today is a Linux rest day. Relax, or catch up on a missed day.\n\n" + screen + extra,
                             "Today is a Linux rest day." + (" Your other lessons are on screen." if extra else ""))
            screen, say = self.describe_day(d, "Today")
            if self.is_done(idx):
                return reply(screen + "\nStatus: done ✓" + extra, say + " You've already finished it.")
            return reply(screen + "\nWhen you finish, say: I finished today's task." + extra, say)
        if re.search(r"this week'?s? (plan|tasks?)|^this week$|what'?s (on )?this week|week'?s plan", t):
            w = max(0, min(len(plan["weeks"]) - 1, idx // 7))
            week = plan["weeks"][w]
            lines = [f"Week {w + 1}: {week['topic']}"]
            for n, day in enumerate(week["days"]):
                d = self.plan_day(plan, w * 7 + n)
                i = w * 7 + n
                box = "✓" if self.is_done(i) else ("✗" if i < idx else "·")
                mark = "  ← today" if i == idx else ""
                lines.append(f"  {box} Day {n + 1} ({d['date'].strftime('%a %d %b')}): {day['title']}{mark}")
            return reply("\n".join(lines), f"Week {w + 1} is {week['topic']}. The seven days are on screen.")
        if re.search(r"\bquiz me\b|\btest me\b", t):
            days = [self.plan_day(plan, i) for i in range(max(0, idx - 2), max(1, idx + 1))]
            material = "\n".join(f"- {d['title']}: " + "; ".join(d["steps"]) for d in days if d)
            return {"ask": "Quiz me on what I studied in these plan days:\n" + material +
                           "\nAsk ONE short question at a time and wait for my answer. After I answer, tell me if I'm right "
                           "in one sentence, then ask the next one. Stop after 3 questions. Only ask about the commands and "
                           "ideas listed above. If you're not sure of an answer, say so."}
        return None


    # ---------------- progress tracking (your ticks, kept on this laptop in progress.json)
    def progress_data(self):
        data = read_json("progress.json", None)
        if not isinstance(data, dict):
            data = {"done": {"0": "2026-09-22T22:00:00"}, "nudged": ""}  # Day 1 was finished on 22 Sep
            write_json("progress.json", data)
        if not isinstance(data.get("done"), dict):
            data["done"] = {}
        data.setdefault("nudged", "")
        return data

    def is_done(self, i):
        return str(i) in self.progress_data()["done"]

    def set_done(self, i, on=True):
        p = self.progress_data()
        if on:
            p["done"][str(i)] = datetime.now().isoformat(timespec="seconds")
        else:
            p["done"].pop(str(i), None)
        write_json("progress.json", p)

    def streaks(self, idx):
        done = {int(k) for k in self.progress_data()["done"] if str(k).isdigit()}
        i = idx if idx in done else idx - 1
        current = 0
        while i >= 0 and i in done:
            current += 1
            i -= 1
        best = run = 0
        for j in range(0, max(idx, 0) + 1):
            run = run + 1 if j in done else 0
            best = max(best, run)
        return current, best

    def missed(self, plan, idx):
        total = len(plan["weeks"]) * 7
        return [i for i in range(0, min(idx, total)) if not self.is_done(i)]

    @staticmethod
    def short_label(i):
        return f"W{i // 7 + 1} D{i % 7 + 1}"

    def day_ref(self, t, idx):
        """Which plan day the message means: 'week 1 day 3', 'w1d3', 'day 3' (this week), 'yesterday', else today."""
        t = words_to_digits(t)
        m = re.search(r"\bw(?:eek)?\s*(\d{1,2})\s*,?\s*d(?:ay)?\s*(\d)\b", t)
        if m:
            return (int(m.group(1)) - 1) * 7 + int(m.group(2)) - 1
        m = re.search(r"\bday\s*(\d)\b", t)
        if m:
            return max(idx, 0) // 7 * 7 + int(m.group(1)) - 1
        if "yesterday" in t:
            return idx - 1
        return idx

    def progress_summary(self, plan, idx):
        total = len(plan["weeks"]) * 7
        done = sum(1 for k in self.progress_data()["done"] if str(k).isdigit() and 0 <= int(k) < total)
        cur, best = self.streaks(idx)
        w = max(0, min(len(plan["weeks"]) - 1, idx // 7))
        marks = []
        for n in range(7):
            i = w * 7 + n
            marks.append(f"D{n + 1} " + ("✓" if self.is_done(i) else ("▶" if i == idx else ("✗" if i < idx else "·"))))
        missed = self.missed(plan, idx)
        lines = [f"Progress: {done} of {total} days done ({round(done * 100 / total)}%)",
                 f"Streak: {cur} day{'s' if cur != 1 else ''} (best {best})",
                 f"This week (Week {w + 1}, {plan['weeks'][w]['topic']}):  " + "   ".join(marks)]
        if missed:
            names = ", ".join(f"{self.short_label(i)} {self.plan_day(plan, i)['title']}" for i in missed[:4])
            lines.append(f"Carried over: {len(missed)} day{'s' if len(missed) != 1 else ''} ({names}{'…' if len(missed) > 4 else ''})")
            if len(missed) >= 2:
                lines.append(f"To catch up, say: move my plan back {len(missed)} days")
        else:
            lines.append("Nothing carried over. You're on track.")
        say = (f"You've done {done} of {total} days. Your streak is {cur} day{'s' if cur != 1 else ''}. "
               + (f"{len(missed)} day{'s are' if len(missed) != 1 else ' is'} carried over." if missed else "You're on track."))
        return "\n".join(lines), say

    def tracking(self, t, raw):
        progress_q = re.search(r"^(?:show |what'?s |what is |check )?(?:my )?(?:progress|streak|stats)$|how am i doing|how far (?:am i|have i got)|how many days (?:have i |did i )?(?:done|completed|finished)", t)
        missed_q = re.search(r"what did i miss|missed days|days i missed|carried over|pending (?:tasks|days)|what'?s pending|backlog", t)
        undo = re.search(r"^(?:undo|unmark|untick|uncheck)\b|\bnot done\b|\bisn'?t done\b", t)
        done_verb = re.search(r"^(?:i\s*(?:'ve|have)?\s*(?:just\s+|already\s+)?(?:finished|completed|done|did)|finished|completed|complete|done with|mark|tick(?: off)?|check off)\b", t)
        target = re.search(r"\b(?:today|yesterday|task|study|studies|lesson|day\s*\w+|w\d+\s*d\d|week\s*\w+\s*,?\s*day\s*\w+)\b", t)
        quick = re.match(r"^(?:done|task done|today done|done for today|i'?m done for today|today'?s task (?:is )?done|finished today'?s task)$", t)
        if not (progress_q or missed_q or quick or (undo and target) or (done_verb and target)):
            return None
        if re.search(r"\b(to-?do|todo|to do|reminder|diary|document)\b", t):
            return None
        plan = self.load_plan()
        if plan is None:
            return reply("I can't find plan.json. Put it in the same folder as vega_app.py.")
        idx = self.today_index(plan)
        total = len(plan["weeks"]) * 7
        if progress_q:
            return reply(*self.progress_summary(plan, idx))
        if missed_q:
            missed = self.missed(plan, idx)
            if not missed:
                return reply("You haven't missed anything. Every past day is done.")
            lines = [f"{self.short_label(i)} ({self.plan_day(plan, i)['date'].strftime('%a %d %b')}): {self.plan_day(plan, i)['title']}" for i in missed]
            tip = f"\nTo restart from the first one, say: move my plan back {len(missed)} days" if len(missed) >= 2 else ""
            return reply(f"Carried over ({len(missed)}):\n" + "\n".join(lines) + tip,
                         f"You have {len(missed)} day{'s' if len(missed) != 1 else ''} carried over. They're on screen.")
        i = self.day_ref(t, idx)
        if i < 0 or i >= total:
            return reply("That day isn't in your 24-week plan.")
        d = self.plan_day(plan, i)
        if undo:
            self.set_done(i, False)
            return reply(f"Okay, {d['label']} ({d['title']}) is marked not done.")
        early = i > idx
        already = self.is_done(i)
        self.set_done(i, True)
        cur, _ = self.streaks(idx)
        done = sum(1 for k in self.progress_data()["done"] if str(k).isdigit() and 0 <= int(k) < total)
        missed = self.missed(plan, idx)
        head = f"{d['label']} ({d['title']}) was already done." if already else \
            f"Nice work! {d['label']} ({d['title']}) is done" + (" early." if early else ".")
        extra = f"\nStill carried over: {len(missed)} earlier day{'s' if len(missed) != 1 else ''}. Say 'what did I miss'." if missed else ""
        text = f"{head}\nStreak: {cur} day{'s' if cur != 1 else ''}. {done} of {total} days done ({round(done * 100 / total)}%).{extra}"
        return reply(text, f"{head} Your streak is {cur} day{'s' if cur != 1 else ''}.")

    def study_nudge_due(self):
        """Once a day, at study_nudge_time: the evening check (reviews today's lab log by itself)."""
        m = re.match(r"^(\d{1,2}):(\d{2})$", str(self.cfg.get("study_nudge_time") or ""))
        if not m:
            return None
        now = datetime.now()
        if (now.hour, now.minute) < (int(m.group(1)), int(m.group(2))):
            return None
        p = self.progress_data()
        today = date.today().isoformat()
        if p.get("nudged") == today:
            return None
        p["nudged"] = today
        write_json("progress.json", p)
        if self.cfg.get("study_plan_reminders"):
            return self.evening_check()
        left = [tk["text"] for tk, done, _ in self.tasks_for(date.today()) if not done]
        if not left:
            return None
        return reply("Evening check: tasks still open today: " + ", ".join(left[:6]) +
                     ".\nSay “mark … done” when you finish one.",
                     f"Evening check. You still have {len(left)} task{'s' if len(left) != 1 else ''} open today.")


# ---------------------------------------------------------------- reminder clock

class ReminderClock(threading.Thread):
    """Checks reminders every 5 seconds and calls on_due(text) when one is due."""

    def __init__(self, vega, on_due, on_briefing=None, on_note=None):
        super().__init__(daemon=True)
        self.vega, self.on_due, self.on_briefing, self.on_note = vega, on_due, on_briefing, on_note

    def run(self):
        while True:
            try:
                for r in self.vega.due_reminders():
                    text = r["text"]
                    if r["late"]:
                        text += f"  (missed — it was due {fmt_day(r['when'])} at {fmt_time(r['when'])})"
                    self.on_due(text)
                nudge = self.vega.study_nudge_due()
                if nudge:
                    if isinstance(nudge, dict):
                        (self.on_note or (lambda n: self.on_due(n["text"])))(nudge)
                    else:
                        self.on_due(nudge)
                if self.on_briefing and self.vega.morning_due():
                    self.on_briefing()
            except Exception:
                pass
            time.sleep(5)
