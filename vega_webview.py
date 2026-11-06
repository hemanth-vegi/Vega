"""
Vega 2.0 - the ChatGPT-style window (web page in an Edge / Brave "app" window), with everything the
old Vega window had:

  - PIN lock screen at start (and voice unlock: say "Hey Vega"), "lock Vega", auto-lock after no use
  - "Hey Vega" voice commands, the Mic button and Ctrl + Alt + V from any app
  - keeps running after you close the window: the Vega icon by the clock (bottom right) opens her again,
    and so does double-clicking "Start Vega" or saying "Hey Vega, open Vega"
  - reminders, the morning briefing and the evening Lab Log check
  - the full Lab Log page with "Ask Vega about this day" and "How to learn this"
  - Telegram phone link and the Gemini online brain (keys are pasted in Settings and kept in .env)

Only ONE Vega can run at a time (this window or the old one): starting her again just opens her window.
Start:  double-click "Start Vega"   or in PowerShell:  cd C:\\CloudLab\\mybot   then   python vega_webview.py
"""

import os
import re
import sys
import json
import time
import queue
import socket
import logging
import logging.handlers
import threading
import traceback
import subprocess

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Vega 2.0 Next-Gen Architecture Modules
from storage.db import init_db, migrate_from_json, search_messages
from server.middleware import get_session_token, apply_security_middleware
from tools.registry import ToolRegistry
from tools.telemetry import get_hardware_vitals
import tools.system_tools
import tools.vision
import tools.telemetry
import tools.lablog_tools
import tools.dev_media
from core.supervisor import SupervisedTask

IS_WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
LOCK_PORT = 47631   # the same "only one Vega" port the old window uses, so the two never run together
PORT = 47632        # the web page
WEB_DIR = os.path.join(BASE_DIR, "web")
URL = f"http://127.0.0.1:{PORT}"
VERSION = "2026-11-06a"   # also embedded in the page so stale Edge app windows reload updated frontend code

# ---------------------------------------------------------------- log (kept small: it was 18 MB before)
log = logging.getLogger("vega")
log.setLevel(logging.INFO)
try:
    _h = logging.handlers.RotatingFileHandler(os.path.join(BASE_DIR, "debug_vega.log"), maxBytes=2_000_000,
                                              backupCount=1, encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    log.addHandler(_h)
except OSError:
    pass


def log_debug(msg):
    log.info(msg)


def log_error(msg):
    log.error(msg)


# ---------------------------------------------------------------- only one Vega
def claim_single_instance():
    """Returns the lock socket, or None if Vega is already running (then her window is brought up)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if not IS_WINDOWS:  # Linux/Mac: allow a quick restart (on Windows this option would let two Vegas share the port)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(4)
        return s
    except OSError:
        s.close()
        if replace_old_vega():  # an older copy was running in the background: it has quit, start the new one
            return claim_single_instance()
        try:
            socket.create_connection(("127.0.0.1", LOCK_PORT), timeout=2).close()  # "show your window"
        except OSError:
            pass
        return None


def replace_old_vega():
    """A Vega is already running. If it's an OLDER copy (from before an update), ask it to quit, so the new
    files are really used (otherwise the old copy keeps running until the laptop restarts)."""
    import urllib.request
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/api/status", timeout=3) as r:
            running = json.loads(r.read().decode("utf-8") or "{}").get("version", "")
    except Exception:
        return False  # not Vega 2.0 (maybe the old window): leave it alone
    if running == VERSION:
        return False
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{PORT}/api/quit", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).close()
    except Exception:
        pass
    for _ in range(30):  # wait until the old copy has let go of the lock port
        time.sleep(0.5)
        t = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            t.bind(("127.0.0.1", LOCK_PORT))
            t.close()
            return True
        except OSError:
            t.close()
    return False


if __name__ == "__main__":
    INSTANCE = claim_single_instance()
    if INSTANCE is None:
        sys.exit(0)
else:
    INSTANCE = None

log_debug("Vega 2.0 starting...")

import bottle  # noqa: E402
from bottle import route, request, response, static_file  # noqa: E402
from socketserver import ThreadingMixIn  # noqa: E402
from wsgiref.simple_server import WSGIServer, WSGIRequestHandler, make_server  # noqa: E402

from vega_core import Vega, ReminderClock, save_config_value, here, strip_thinking, EXTRA_PROVIDERS  # noqa: E402
from vega_chats import ChatStore  # noqa: E402
import vega_voice  # noqa: E402
from vega_voice import Mouth, Ears, WakeListener, shorten_for_speech, beep  # noqa: E402
import vega_security as sec  # noqa: E402
from vega_lablog import clean_step  # noqa: E402
from vega_popup import Popups  # noqa: E402
from vega_agents import AgentTaskError, AgentTaskManager  # noqa: E402
from vega_practice_lab import PracticeLab  # noqa: E402

bottle.BaseRequest.MEMFILE_MAX = 64 * 1024 * 1024  # attached files and multiple screenshots


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    allow_reuse_address = not IS_WINDOWS


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass


# ---------------------------------------------------------------- Vega herself
vega = Vega()
cfg = vega.cfg
store = ChatStore()
agent_tasks = AgentTaskManager(here("agent_audit.json"), vega=vega)
agent_tasks.preverify_default_local_route()
practice_lab = PracticeLab()

mouth = Mouth(rate=cfg.get("speech_rate", 1.0), voice=cfg.get("voice", "female"))
mouth.on_error = lambda m: notice(m, "warn")
ears = Ears(cfg.get("whisper_model", "small.en"), cfg.get("wake_model", "base.en"))
vega_voice.PLAIN_PHRASES.update(str(p).lower() for p in cfg.get("wake_plain", []) if 1 < len(str(p).split()) <= 4)
vega_voice.BLOCKED.update(str(w).lower() for w in cfg.get("not_wake_words", []) if str(w).lower() != "vega")
vega_voice.LEARNED.update(str(w).lower() for w in cfg.get("wake_variants", []) if vega_voice.good_wake_variant(str(w)))
if cfg.get("telugu_model") in ("small", "medium"):
    ears.set_multilingual_model(cfg["telugu_model"])
ears.cloud = False  # local-only recognition ignores legacy cloud_speech settings
_voice_language = cfg.get("voice_language", "auto")
ears.set_language_mode(_voice_language if _voice_language in ("auto", "english", "telugu") else "auto")
ears.hint = vega.speech_hint()
ears.hotwords = vega.speech_hotwords()
voice = sec.VoiceID()   # ONE voice checker (making a new one each time reloaded the voice model every time)

S = {  # everything the window, the voice and the timers share
    "locked": sec.has_pin(),
    "last_activity": time.time(),
    "last_verified": 0.0,
    "pin_fails": 0,
    "pin_wait_until": 0.0,
    "busy": False,
    "listening": False,          # the Mic button / Ctrl + Alt + V is recording
    "mic_stop": None,
    "focused": False,            # the Vega window is in front (for "just talk" mode)
    "last_spoke": 0.0,
    "pending_briefing": False,
    "pending_pair": None,
    "unlock_clip": None,
    "last_window_open": 0.0,
}

_LOCK_ALLOWED_API = {
    "/api/events", "/api/initial_state", "/api/status",
    "/api/lock/verify", "/api/lock/now", "/api/lock/voice_unlock",
    "/api/quit", "/api/agent/options", "/api/auth/token", "/api/telemetry",
}


@bottle.hook("before_request")
def protect_locked_api():
    if S["locked"] and request.path.startswith("/api/") and request.path not in _LOCK_ALLOWED_API:
        msg = ("Vega is locked. Unlock Vega to use Agent and Practice Lab."
               if request.path.startswith("/api/agent/") or request.path.startswith("/api/practice-lab")
               else "Vega is locked.")
        raise bottle.HTTPResponse(
            json.dumps({"ok": False, "locked": True, "error": msg}),
            status=423,
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )

chats = store.all()
active_chat_id = chats[0]["id"] if chats else store.new()["id"]

# Ensure JSON error handlers and security middleware are active
apply_security_middleware(bottle.default_app())

# ---------------------------------------------------------------- live events to the window (Server-Sent Events)
sse_subscribers = []
sse_lock = threading.Lock()


def broadcast_event(event_type, payload=None):
    with sse_lock:
        for q in list(sse_subscribers):
            try:
                q.put_nowait({"type": event_type, "data": payload or {}})
            except Exception:
                sse_subscribers.remove(q)


def window_open():
    with sse_lock:
        return len(sse_subscribers) > 0


def notice(text, level="muted"):
    """A small grey line in the chat (not saved), like the old window's system messages."""
    broadcast_event("notice", {"text": text, "level": level})


vega.on_notice = lambda text: notice("ℹ️ " + text)


def status(text, badge="ready"):
    broadcast_event("status", {"text": text, "badge": badge})


def in_background():
    """Vega's window is closed, minimized or behind other apps."""
    return not window_open() or not S["focused"]


def pop(title, text, seconds=10, force=False):
    """The small bubble at the bottom-right (only when her window isn't in front, unless force)."""
    if popups is None or not popups.ok:
        return
    if not force and (not in_background() or not cfg.get("bubbles", True)):
        return
    popups.bubble(title, text, seconds)


def lock_msg(text):
    broadcast_event("lock_msg", {"text": text})
    pop("Vega is locked", text, 7)


def ready_text():
    return "Locked" if S["locked"] else ("Ready - just talk" if direct_mode() else "Ready - say “Hey Vega”")


def chat_for(cid):
    """The chat with this id; a chat that isn't saved yet keeps its id, so the window shows what's added to it."""
    chat = store.load(cid) if cid else None
    if not chat:
        chat = store.new()
        if cid:
            chat["id"] = cid
    return chat


def current_chat():
    return chat_for(active_chat_id)


def add_to_chat(who, text):
    chat = current_chat()
    store.add(chat, who, text)
    return chat


# ---------------------------------------------------------------- the window (Edge / Brave / Chrome app window)
def find_browser_exe():
    local = os.environ.get("LOCALAPPDATA", "")
    for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
              os.path.join(local, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
              r"C:\Program Files\Google\Chrome\Application\chrome.exe",
              os.path.join(local, r"Google\Chrome\Application\chrome.exe")):
        if p and os.path.exists(p):
            return p
    return None


def bring_window_to_front():
    """The window is already open (maybe minimized): restore it and put it in front."""
    if not IS_WINDOWS:
        return False
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def each(hwnd, _):
            n = user32.GetWindowTextLengthW(hwnd)
            if 0 < n < 60:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if "Vega" in buf.value and user32.IsWindowVisible(hwnd):
                    found.append(hwnd)
            return True

        user32.EnumWindows(each, 0)
        for hwnd in found[:1]:
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            user32.ShowWindow(hwnd, 5)  # SW_SHOW, including an Edge window that was hidden at startup.
            user32.BringWindowToTop(hwnd)
            user32.keybd_event(0x12, 0, 0, 0)  # a tap of Alt lets Windows move focus to us
            user32.keybd_event(0x12, 0, 2, 0)
            user32.SetForegroundWindow(hwnd)
        return bool(found)
    except Exception as e:
        log_error(f"bring_window_to_front: {e}")
        return False


def _launch_browser_app(browser):
    """Launch Vega in an Edge app window, not as a regular browser tab."""
    args = [f"--app={URL}", "--window-size=1280,840", "--no-first-run", "--no-default-browser-check",
            "--disable-features=Translate"]
    if IS_WINDOWS:
        os.startfile(browser, "open", subprocess.list2cmdline(args), cwd=BASE_DIR, show_cmd=1)
    else:
        subprocess.Popen([browser, *args])


def _fallback_check_window():
    """Restore the app window if Edge starts it minimized; report if it stays hidden."""
    time.sleep(3.5)
    if not bring_window_to_front():
        log_error("Vega's Edge app window did not appear; run Start Vega again to retry.")


_window_launch_lock = threading.Lock()


def open_window():
    """Open Vega or focus an existing window without disturbing its browser profile."""
    if bring_window_to_front():
        return
    with _window_launch_lock:
        if time.time() - S["last_window_open"] < 12:
            return
        S["last_window_open"] = time.time()
    browser = find_browser_exe()
    if not browser:
        log_error("No supported browser executable was found for Vega's app window")
        return
    try:
        _launch_browser_app(browser)
        log_debug(f"window opened with {os.path.basename(browser)}")
        threading.Thread(target=_fallback_check_window, daemon=True).start()
    except Exception as e:
        log_error(f"could not open the window ({type(e).__name__})")


def accept_other_instances():
    """Someone started Vega again (shortcut, Start Vega...): just open the window."""
    while INSTANCE is not None:
        try:
            conn, _ = INSTANCE.accept()
            conn.close()
            open_window()
        except OSError:
            return


# ---------------------------------------------------------------- lock
def lock_now():
    if not sec.has_pin():
        notice("Set a PIN first: Settings → PIN and lock.", "warn")
        return False
    if not S["locked"]:
        S["locked"] = True
        mouth.stop()
        if S["mic_stop"]:
            S["mic_stop"].set()
        log_debug("locked")
    broadcast_event("lock", {"voice_unlock_ready": sec.voice_unlock_ready(), "voice_reason": voice_unlock_reason()})
    status("Locked", "locked")
    return True


def unlock(how=""):
    S["locked"] = False
    S["pin_fails"] = 0
    S["last_verified"] = S["last_activity"] = time.time()
    if how == "voice":
        S["voice_ok_at"] = time.time()
    broadcast_event("unlock", {"how": how})
    status(ready_text(), "ready")
    log_debug(f"unlocked ({how})")
    if S["pending_briefing"]:
        S["pending_briefing"] = False
        threading.Timer(1.5, on_briefing).start()


def idle_lock_loop():
    while True:
        time.sleep(30)
        try:
            mins = sec.lock_minutes()
            timeout_minutes = max(15, mins) if mins > 0 else 0
            if (sec.has_pin() and timeout_minutes > 0 and not S["locked"] and not S["busy"]
                    and time.time() - S["last_activity"] > timeout_minutes * 60):
                lock_now()
        except Exception as e:
            log_error(f"idle lock: {e}")


def touch():
    S["last_activity"] = time.time()


def voice_unlock_reason():
    """Why the lock screen has no voice unlock ("" = it's ready)."""
    if sec.voice_unlock_ready():
        return ""
    if not voice.has_voiceprint():
        return "Voice unlock isn't set up yet: there's no voiceprint (voiceprint.npy) in the mybot folder."
    missing = sec.voice_packages_missing()
    if missing:
        return ("Voice unlock needs " + ", ".join(missing) + ", which aren't installed for the Python that runs Vega. "
                "Double-click INSTALL VOICE UNLOCK.bat in the mybot folder.")
    return "Voice unlock is turned off."


# ---------------------------------------------------------------- voice
def media_playing():
    try:
        from vega_control import is_media_playing
        return is_media_playing()
    except Exception:
        return False


def on_a_call():
    if not cfg.get("pause_on_calls", True):
        return False
    try:
        from vega_control import other_app_using_mic
        return other_app_using_mic()
    except Exception:
        return False


def direct_mode():
    """'Just talk' (no "Hey Vega"): only if he turned it on AND the window is in front AND nothing is playing."""
    return (bool(cfg.get("talk_without_wake", True)) and not in_background() and not S["locked"]
            and bool(cfg.get("wake_word_on", True)) and not media_playing() and not on_a_call())


def wake_busy():
    """True = don't listen for "Hey Vega" right now."""
    if mouth.busy():
        S["last_spoke"] = time.time()
    return bool(S["busy"] or S["listening"] or mouth.busy() or time.time() - S["last_spoke"] < 1.2
                or on_a_call())


def wake_voice_score(audio):
    """How much "Hey Vega" sounds like Hemanth (used by the wake listener to ignore TV, videos and other people)."""
    if not voice.has_voiceprint() or sec.voice_packages_missing():
        return None
    playing = media_playing()
    enforce = bool(cfg.get("speaker_verification_enforce", False))
    if not enforce and not playing:
        return None  # skip the costly speaker model when verification is explicitly disabled
    score = voice.score(audio)
    strong = max(0.24, min(0.28, sec.voice_threshold()))
    low = 0.12
    if playing:  # a video or music is playing: only a clear match with HIS voice counts
        low, enforce = strong, True
    if score >= strong:
        S["last_verified"] = S["voice_ok_at"] = time.time()
    return score, strong, low, enforce


def on_wake(audio=None):
    if S["locked"]:
        if audio is not None and sec.voice_unlock_ready():
            threading.Thread(target=wake_unlock, args=(audio,), daemon=True).start()
        else:  # no voice unlock: show the lock screen so he can type the PIN
            lock_msg("I'm locked. Type your PIN to unlock me.")
            pop("Vega is locked", "Open Vega and type your PIN.", 6)
        return
    mouth.stop()
    status("Yes? I'm listening…", "listening")
    broadcast_event("wake", {})
    pop("Vega", "Yes? I'm listening…", 9)

    def timeout():
        if not S["busy"]:
            status(ready_text(), "ready")
    threading.Timer(11, timeout).start()


def wake_unlock(audio):
    """Locked + "Hey Vega" in your voice = unlocked. Close but not sure: one more "Hey Vega" is joined to it."""
    import numpy as np
    clip, prev = audio, S["unlock_clip"]
    if prev is not None and time.time() - prev[1] < 20:
        clip = np.concatenate([prev[0], audio])
    try:
        score = voice.score(clip)
    except Exception as e:
        lock_msg(f"Voice check didn't work ({e}). Type your PIN.")
        return
    vega_voice.log_problem(f"unlock by Hey Vega: voice {score:.2f} (needs {sec.unlock_threshold():.2f})")
    if not S["locked"]:
        return
    if score >= sec.unlock_threshold():
        S["unlock_clip"] = None
        unlock("voice")
        notice(f"Unlocked by your voice (match {score:.2f}).")
        pop("Vega", "Unlocked. Welcome back! 👋", 4)
        mouth.say("Welcome back, Hemanth.")
    elif score >= sec.voice_threshold() - 0.05:
        S["unlock_clip"] = (clip, time.time())
        lock_msg("Almost sure it's you. Say “Hey Vega” once more.")
    else:
        S["unlock_clip"] = None
        lock_msg("That didn't sound like you. Type your PIN.")


NOT_FOR_YOU = re.compile(r"^(?:hey\s+)?(?:vega\W*)?(?:no\W*)?(?:that|this|it)?\s*(?:was\s*n[o']?t|wasnt|is\s*n[o']?t|isnt|was not|is not)\s+"
                         r"(?:for|to)\s+you\b|^(?:not|wasn'?t)\s+(?:talking\s+)?(?:for|to)\s+you\b|^i\s+(?:was\s*n[o']?t|wasnt|was not|didn'?t|did not)\s+"
                         r"(?:talking to|call|calling|talk to)\s+you\b|^false alarm\b|^wrong wake\b", re.I)
OPEN_SELF = re.compile(r"^(?:please\s+)?(?:open|show|bring up)\s+(?:the\s+|your\s+)?(?:vega|yourself|your window|vega window|vega app|chat)(?:\s+window)?\W*$", re.I)


def not_for_you(text):
    """ "That wasn't for you": she woke by mistake. Stop, and don't wake for that word again."""
    mouth.stop()
    heard, when = getattr(wake, "last_wake", ("", 0.0))
    if "for you" in heard.lower() or "talking" in heard.lower():
        heard, when = getattr(wake, "prev_wake", ("", 0.0))
    word = vega_voice.wake_word_in(heard) if heard and time.time() - when < 600 else ""
    if word and word != "vega" and len(word) >= 3:
        vega_voice.BLOCKED.add(word)
        vega_voice.LEARNED.discard(word)
        blocked = sorted(set(cfg.get("not_wake_words", [])) | {word})[:40]
        cfg["not_wake_words"] = blocked
        save_config_value("not_wake_words", blocked)
        variants = [w for w in cfg.get("wake_variants", []) if w != word]
        cfg["wake_variants"] = variants
        save_config_value("wake_variants", variants)
        msg = f"Sorry! I woke up because I heard “{heard.strip()[:50]}”. I won't wake up for “{word}” again."
    else:
        msg = "Sorry, that wasn't for me. I'll stay quiet."
    notice(msg)
    status(ready_text(), "ready")


def on_voice_command(text, audio=None):
    """Everything said to Vega (after "Hey Vega", in "just talk" mode, or with Ctrl + Alt + V) comes here."""
    text = (text or "").strip()
    if not text:
        return
    vega_voice.log_problem(f"command received: “{text[:80]}” (locked={S['locked']}, voice_only={sec.voice_only()}, busy={S['busy']})")
    if S["busy"]:  # a stuck old answer must not block new voice commands
        vega.stop()
    if S["locked"]:
        if audio is not None and sec.voice_unlock_ready():
            def check():
                try:
                    score = voice.score(audio)
                except Exception as e:
                    lock_msg(f"Voice check didn't work ({e}). Type your PIN.")
                    return
                if score >= sec.unlock_threshold():
                    unlock("voice")
                    notice(f"Unlocked by your voice (match {score:.2f}).")
                    if re.search(r"\b(unlock|it'?s me|it is me|it'?s hemanth)\b", text.lower()):
                        mouth.say("Welcome back, Hemanth.")
                    else:
                        run_command(text, spoken=True)
                else:
                    lock_msg(f"That didn't match your voice closely enough ({score:.2f}, needs "
                             f"{sec.unlock_threshold():.2f}). Type your PIN.")
            threading.Thread(target=check, daemon=True).start()
        else:
            lock_msg("I'm locked. Type your PIN to unlock me.")
            pop("Vega is locked", "Open Vega and type your PIN.", 6)
        return
    if NOT_FOR_YOU.search(text):
        not_for_you(text)
        return
    if sec.voice_only():
        # Fail closed: when voice-only is enabled, no command runs unless verification succeeds.
        if sec.voice_packages_missing():
            status(ready_text(), "ready")
            notice("Voice-only security is enabled, but required voice packages are missing. Install them or disable voice-only in Settings.", "warn")
            pop("Voice check unavailable", "Required voice packages are missing. Install them or disable voice-only security.", 6)
            return
        if not voice.has_voiceprint():
            status(ready_text(), "ready")
            notice("Voice-only security is enabled, but no voiceprint was found. Record your voiceprint in Settings or disable voice-only.", "warn")
            pop("Voiceprint missing", "Record your voiceprint in Settings to use voice-only commands.", 6)
            return
        if audio is None:
            status(ready_text(), "ready")
            notice("Voice-only security is enabled, but no voice audio was captured to verify your identity. Please repeat the command.", "warn")
            pop("Voice verification failed", "No audio received for voice verification. Please try again.", 6)
            return

        def gate():  # "Only obey my voice" is on: check it's Hemanth before doing anything
            status("Checking it's you…", "thinking")
            try:
                score, err = voice.score(audio), None
            except Exception as e:
                score, err = None, str(e)
            if err or score is None:
                status(ready_text(), "ready")
                notice(f"Voice verification failed ({err or 'scoring error'}). Command blocked for security.", "warn")
                pop("Voice check error", f"Could not verify your voice ({err or 'scoring error'}). Please try again.", 6)
                return
            if score >= min(0.24, sec.voice_threshold()) or (time.time() - S["last_verified"] < 300 and score >= 0.12):
                S["last_verified"] = time.time()
                run_command(text, spoken=True)
            else:
                status(ready_text(), "ready")
                notice(f"Heard “{text}”, but that didn't sound like you (voice match {score:.2f}), so I ignored it. "
                       "Say “Hey Vega” and the command together, a little slower.", "warn")
                pop("Vega", "That didn't sound like you, so I ignored it. Say it again.", 6)
        threading.Thread(target=gate, daemon=True).start()
        return

    run_command(text, spoken=True)


def warm_speech_model(name):
    """Warm a cached Whisper model in the background so a language switch does not delay recording."""
    if name in ears._models:
        return

    def load():
        try:
            ears.model(name)
        except Exception as exc:
            log_error(f"offline speech model warm-up failed ({type(exc).__name__})")

    threading.Thread(target=load, daemon=True, name="VegaSpeechWarmup").start()


def set_voice_language(mode, persist=False):
    """Keep local transcription and spoken-response language preferences in sync."""
    if mode not in ("auto", "english", "telugu"):
        return False
    old_model = ears.main_model_name()
    ears.set_language_mode(mode)
    cfg["voice_language"] = mode
    cfg["understand_telugu"] = mode in ("auto", "telugu")
    if persist:
        save_config_value("voice_language", mode)
        save_config_value("understand_telugu", cfg["understand_telugu"])
        if ears.main_model_name() != old_model:
            warm_speech_model(ears.main_model_name())
    return True


def set_voice_model(name, persist=False):
    """Select the cached local multilingual Whisper model used for Auto/Telugu speech."""
    if name not in ("small", "medium"):
        return False
    old_model = ears.main_model_name()
    ears.set_multilingual_model(name)
    cfg["telugu_model"] = name
    if persist:
        save_config_value("telugu_model", name)
        if ears.language_mode in ("auto", "telugu") and ears.main_model_name() != old_model:
            warm_speech_model(ears.main_model_name())
    return True


def after_answer(out, spoken):
    """Things an answer can ask for: lock, open the Lab Log, turn listening on/off, open the window."""
    if out.get("lock_app"):
        threading.Timer(1.2, lock_now).start()
    if out.get("wake") in (True, False):
        set_listening(bool(out["wake"]))
    if out.get("voice_on") in (True, False):
        set_speaking(bool(out["voice_on"]))
    if out.get("voice_lang") in ("auto", "english", "telugu"):
        set_voice_language(out["voice_lang"], persist=True)
    if not window_open() and spoken and (out.get("open_lablog") or len(out.get("text", "")) > 220
                                          or "on screen" in (out.get("say") or "").lower()):
        open_window()  # the answer is meant to be read: show it


def speak(out, prompt_text=None):
    if cfg.get("voice_on", True) and not S["locked"]:
        user_p = (prompt_text or getattr(vega, "_last_user_prompt", "") or "").lower()
        is_explain_query = bool(re.search(
            r"\b(explain(?:ing)?|in your (?:own )?words|full chat|everything|detail|walk me through|break (?:it )?down)\b",
            user_p
        ))
        full = bool(out.get("full_speech")) or is_explain_query
        speech_text = out.get("say") or out.get("text", "")
        language = out.get("voice_lang") or cfg.get("voice_language", "auto")
        if language == "telugu":
            speech_lang = "te"
        elif language == "english":
            speech_lang = "en"
        else:
            typed_telugu = bool(re.search(r"[\u0c00-\u0c7f]", user_p))
            tenglish_terms = vega.tenglish_gloss(user_p)
            speech_lang = "te" if ears.last_lang == "te" or typed_telugu or len(tenglish_terms) >= 2 else "en"
        mouth.say(shorten_for_speech(speech_text, full_speech=full), lang=speech_lang)


def run_command(text, spoken=True):
    """Run a spoken command (in its own thread) and show + speak the answer."""
    def work():
        S["busy"] = True
        touch()
        broadcast_event("voice_input", {"text": text, "chat_id": active_chat_id})
        status("Thinking…", "thinking")
        pop("You said", f"“{text}”\nThinking…", 15)
        try:
            if OPEN_SELF.match(text):
                open_window()
                out = {"text": "Here I am.", "say": "Here I am."}
            else:
                ears.hint, ears.hotwords = vega.speech_hint(), vega.speech_hotwords()
                out = vega.handle(text, source="laptop", spoken=True)
            chat = add_to_chat("you", text)
            store.add(chat, "vega", out.get("text", ""))
            broadcast_event("voice_response", {"text": out.get("text", ""), "source_label": out.get("source_label", "Vega"),
                                               "chat_id": chat["id"], "open_tab": "lablog" if out.get("open_lablog") else None})
            speak(out, prompt_text=text)
            pop("Vega", out.get("text", ""), 12)
            after_answer(out, spoken)
        except Exception as e:
            log_error(f"voice command error: {e}\n{traceback.format_exc()}")
            broadcast_event("voice_response", {"text": f"Something went wrong: {e}", "source_label": "Error",
                                               "chat_id": active_chat_id})
        finally:
            S["busy"] = False
            status(ready_text(), "ready")
    threading.Thread(target=work, daemon=True).start()


def record_and_transcribe():
    """The Mic button / Ctrl + Alt + V: record until he stops talking. Returns (text, audio, error)."""
    if S["listening"]:
        if S["mic_stop"]:
            S["mic_stop"].set()  # pressed again = "I'm done talking"
        return "", None, "already listening"
    S["listening"] = True
    S["mic_stop"] = threading.Event()
    mouth.stop()
    status("Listening… speak now (I stop when you stop)", "listening")
    try:
        if not wake.pause_for_capture():
            return "", None, "The microphone is still in use by Vega's listener. Please try again."
        status(f"Loading offline Whisper model {ears.main_model_name()}…", "thinking")
        ears.model(ears.main_model_name())
        status("Listening… speak now (I stop when you stop)", "listening")
        audio = ears.listen_once(S["mic_stop"])
        if audio is None:
            return "", None, None
        status("Understanding…", "thinking")
        waiting = getattr(vega, "pending_action", None)
        text = ears.transcribe(audio, prompt="Yes. No. Yes, go ahead. No, cancel." if waiting else None)
        return (text or "").strip(), audio, None
    except Exception as e:
        log_error(f"mic error: {e}\n{traceback.format_exc()}")
        return "", None, str(e)
    finally:
        S["listening"] = False
        S["mic_stop"] = None
        wake.resume_after_capture()
        status(ready_text(), "ready")


def hotkey_mic():
    """Ctrl + Alt + V from any app."""
    touch()
    if S["locked"]:
        pop("Vega is locked", "Say “Hey Vega” to unlock with your voice, or open Vega and type your PIN.", 7, force=True)
        return
    if S["listening"]:
        record_and_transcribe()  # stops the recording
        return
    try:
        beep("wake")
    except Exception:
        pass
    pop("Vega", "Listening… speak now.\n(I stop when you stop, or press Ctrl + Alt + V again.)", 10)

    def work():
        text, audio, err = record_and_transcribe()
        if err and err != "already listening":
            notice(f"Microphone problem: {err}", "warn")
            pop("Vega", f"Microphone problem: {err}", 6)
        elif text:
            on_voice_command(text, audio)
        elif not err:
            notice("I didn't hear anything. Press Ctrl + Alt + V and try again, a bit closer.")
            pop("Vega", "I didn't hear anything. Press Ctrl + Alt + V and try again.", 5)
    threading.Thread(target=work, daemon=True).start()


def set_listening(on):
    cfg["wake_word_on"] = on
    save_config_value("wake_word_on", on)
    wake.set_enabled(on)
    broadcast_event("listening", {"on": on})


def set_speaking(on):
    cfg["voice_on"] = bool(on)
    save_config_value("voice_on", bool(on))
    if not on:
        mouth.stop()
    broadcast_event("speaking", {"on": bool(on)})


wake = WakeListener(ears, on_wake=on_wake, on_command=on_voice_command, is_busy=wake_busy,
                    on_error=lambda m: notice(m, "warn"))
wake.strictness = str(cfg.get("wake_strictness", "easy"))
wake.voice_score = wake_voice_score
wake.direct = direct_mode
wake.on_status = lambda t: status(t or ready_text(), "thinking" if t else "ready")


def warm_up_ears():
    """Load the speech models now, not in the middle of the first command."""
    for n in dict.fromkeys([ears.wake_model_name, ears.main_model_name()]):
        try:
            ears.model(n)
            vega_voice.log_problem(f"speech model '{n}' ready")
        except Exception as e:
            vega_voice.log_problem(f"speech model '{n}' failed to load: {e}")
            notice(f"I couldn't load a speech model ({e}). Check your internet.", "warn")


# ---------------------------------------------------------------- shortcuts from any app
def quick_bar():
    """Ctrl + Alt + Space: the small floating box to type to Vega (or her window, if pop-ups can't run)."""
    if popups is not None and popups.ok:
        popups.toggle_quickbar()
    else:
        open_window()
        broadcast_event("focus_input", {})


def quick_submit(text):
    """Something typed in the Quick Bar."""
    text = (text or "").strip()
    if S["locked"]:
        popups.bar_result("Vega is locked. Say “Hey Vega”, or click Open Vega and type your PIN.", "warn")
        return
    if re.fullmatch(r"[A-Za-z0-9]{4,12}", text) and sec.has_pin() and sec.check_pin(text):
        popups.bar_result("That's your PIN, so I didn't save it or send it anywhere.", "warn")
        return
    touch()
    if S["busy"]:
        vega.stop()
    S["busy"] = True
    try:
        if OPEN_SELF.match(text):
            open_window()
            out = {"text": "Opening Vega."}
        else:
            out = vega.handle(text, source="laptop")
        chat = add_to_chat("you", text)
        store.add(chat, "vega", out.get("text", ""))
        broadcast_event("voice_input", {"text": text, "chat_id": chat["id"]})
        broadcast_event("voice_response", {"text": out.get("text", ""), "source_label": out.get("source_label", "Vega"),
                                           "chat_id": chat["id"]})
        popups.bar_result(out.get("text", ""))
        speak(out)
        if out.get("open_lablog"):
            open_window()
            broadcast_event("voice_response", {"text": "", "open_tab": "lablog", "chat_id": "-"})
        after_answer(out, False)
    except Exception as e:
        popups.bar_result(f"Something went wrong: {e}", "warn")
    finally:
        S["busy"] = False
        status(ready_text(), "ready")


def start_global_hotkeys():
    """Ctrl + Alt + V = Mic.  Ctrl + Alt + Space = Quick Bar.  (Not Ctrl + Shift + V: browsers use that to paste.)"""
    if not IS_WINDOWS:
        return

    def loop():
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x4000
            VK_V, VK_SPACE, WM_HOTKEY = 0x56, 0x20, 0x0312
            if not user32.RegisterHotKey(None, 1, MOD_ALT | MOD_CONTROL | MOD_NOREPEAT, VK_V):
                log_debug("Ctrl+Alt+V is already used by another app")
            quickbar_hotkey_id = 2
            if not user32.RegisterHotKey(None, quickbar_hotkey_id, MOD_ALT | MOD_CONTROL | MOD_NOREPEAT, VK_SPACE):
                log_debug("Ctrl+Alt+Space is already used; trying Ctrl+Shift+Space for Quick Bar")
                quickbar_hotkey_id = 3
                if user32.RegisterHotKey(None, quickbar_hotkey_id, MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_SPACE):
                    log_debug("Quick Bar shortcut is Ctrl+Shift+Space")
                else:
                    quickbar_hotkey_id = 0
                    log_debug("Ctrl+Shift+Space is also busy; use Vega's tray menu for Quick Bar")
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    if msg.wParam == 1:
                        hotkey_mic()
                    elif quickbar_hotkey_id and msg.wParam == quickbar_hotkey_id:
                        quick_bar()
        except Exception as e:
            log_error(f"shortcut error: {e}")
    threading.Thread(target=loop, daemon=True).start()


# ---------------------------------------------------------------- reminders, morning briefing, evening check
def on_reminder(text):
    notice(f"Reminder: {text}", "warn")
    broadcast_event("reminder", {"text": text})
    pop("⏰ Reminder", "Unlock Vega to see it." if S["locked"] else text, 30, force=True)
    try:
        beep("reminder")
    except Exception:
        pass
    mouth.say("Hemanth, you have a reminder. Unlock me to see it." if S["locked"] else f"Hemanth, reminder: {text}")
    if phone:
        phone.send(f"Reminder: {text}")


def on_briefing():
    if S["locked"]:
        S["pending_briefing"] = True
        return
    if S["busy"]:
        threading.Timer(30, on_briefing).start()
        return

    def work():
        S["busy"] = True
        try:
            out = vega.morning_briefing()
            chat = add_to_chat("vega", out["text"])
            broadcast_event("voice_response", {"text": out["text"], "source_label": "Briefing", "chat_id": chat["id"]})
            pop("Good morning ☀️", out["text"], 20)
            speak(out)
            if phone:
                phone.send(out["text"])
        except Exception as e:
            log_error(f"briefing: {e}")
        finally:
            S["busy"] = False
            status(ready_text(), "ready")
    threading.Thread(target=work, daemon=True).start()


def on_note(note):
    """Vega speaking up by herself (the evening Lab Log check)."""
    chat = add_to_chat("vega", note["text"])
    broadcast_event("voice_response", {"text": note["text"], "source_label": "Vega", "chat_id": chat["id"]})
    pop("Vega", note["text"], 20)
    if not S["locked"] and cfg.get("voice_on", True):
        mouth.say(shorten_for_speech(note.get("say") or note["text"]))
    if phone:
        phone.send(note["text"])


# ---------------------------------------------------------------- phone (Telegram)
phone = None
popups = None


def phone_event(kind, *data):
    if kind == "phone":
        text_in, text_out = data
        chat = add_to_chat("phone", text_in)
        store.add(chat, "vega", text_out)
        broadcast_event("voice_input", {"text": "📱 " + text_in, "chat_id": chat["id"]})
        broadcast_event("voice_response", {"text": text_out, "source_label": "Phone", "chat_id": chat["id"]})
    elif kind == "status":
        log_debug(f"phone: {data[0]}")
        notice(data[0])
    elif kind == "pair_request":
        chat_id, name = data
        S["pending_pair"] = (chat_id, name)
        open_window()
        broadcast_event("pair_request", {"name": name})


def start_phone():
    global phone
    if phone:
        phone.running = False  # the old link stops after its current check
        phone = None
    if not str(cfg.get("telegram_token") or "").strip():
        return
    try:
        from vega_telegram import TelegramBridge
        phone = TelegramBridge(vega, ears, phone_event)
        phone.start()
        log_debug("phone link started")
    except Exception as e:
        log_error(f"phone link failed: {e}")


# ---------------------------------------------------------------- web routes
def as_json(obj):
    response.content_type = "application/json"
    return json.dumps(obj, ensure_ascii=False)


def body():
    try:
        data = request.json
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


@route("/")
def index():
    try:
        with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
            page = f.read().replace("__VEGA_BUILD_VERSION__", VERSION)
        response.content_type = "text/html; charset=UTF-8"
        response.set_header("Cache-Control", "no-store")
        return page
    except OSError:
        resp = static_file("index.html", root=WEB_DIR)
        resp.set_header("Cache-Control", "no-store")
        return resp


@route("/api/events")
def sse_events():
    response.content_type = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    q = queue.Queue()
    with sse_lock:
        sse_subscribers.append(q)
    try:
        hello = {"time": time.time(), "locked": S["locked"], "voice_unlock_ready": sec.voice_unlock_ready(),
                 "voice_reason": voice_unlock_reason(),
                 "status": ready_text()}
        yield f"data: {json.dumps({'type': 'connected', 'data': hello})}\n\n"
        while True:
            try:
                ev = q.get(timeout=5)
                if S["locked"] and ev.get("type") not in {"lock", "unlock"}:
                    continue
                yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"
    finally:
        with sse_lock:
            if q in sse_subscribers:
                sse_subscribers.remove(q)
        if not window_open():
            S["focused"] = False


@route("/api/initial_state", method="GET")
def initial_state():
    if S["locked"]:
        return as_json({
            "locked": True,
            "has_pin": sec.has_pin(),
            "lock_minutes": sec.lock_minutes(),
            "voice_unlock_ready": sec.voice_unlock_ready(),
            "voice_reason": voice_unlock_reason(),
            "version": VERSION,
            "status": "Locked",
        })
    today_task = ""
    try:
        i = vega.track_today("linux")
        d = vega.track_day("linux", max(i, 0))
        today_task = d["title"] if d else ""
    except Exception:
        pass
    return as_json({
        "active_chat_id": active_chat_id,
        "today_task": today_task,
        "brain_mode": brain_label(),
        "listening_on": bool(cfg.get("wake_word_on", True)),
        "voice_on": bool(cfg.get("voice_on", True)),
        "speech_rate": cfg.get("speech_rate", 1.0),
        "cloud_speech": False,
        "voice_language": ears.language_mode,
        "telugu_model": ears.telugu_model_name,
        "speech_backend": "faster-whisper local CPU int8",
        "speech_model": ears.main_model_name(),
        "speech_model_ready": ears.main_model_name() in ears._models,
        "talk_without_wake": bool(cfg.get("talk_without_wake", True)),
        "startup_enabled": os.path.exists(startup_shortcut_path()),
        "locked": S["locked"],
        "has_pin": sec.has_pin(),
        "lock_minutes": sec.lock_minutes(),
        "voice_unlock_ready": sec.voice_unlock_ready(),
        "voice_reason": voice_unlock_reason(),
        "wake_only_me": bool(cfg.get("speaker_verification_enforce", False)),
        "speaker_verification_enforce": bool(cfg.get("speaker_verification_enforce", False)),
        "private_mode": bool(cfg.get("private_mode", False)),
        "version": VERSION,
        "status": ready_text(),
        "keys": dict({"telegram": bool(cfg.get("telegram_token")), "gemini": bool(cfg.get("gemini_api_key"))},
                     **{n: bool(cfg.get(n + "_api_key")) for n in EXTRA_PROVIDERS}),
    })


_gemini_check = {"t": 0.0, "ok": False}


def brain_label():
    if cfg.get("private_mode", False):
        return "Private (laptop only)"
    key = cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    if not key:
        backups = vega.extra_keys()
        return f"Online ({EXTRA_PROVIDERS[backups[0]]['label']})" if backups else "Offline (laptop)"
    if time.time() - _gemini_check["t"] > 120:
        _gemini_check["t"] = time.time()
        try:
            import requests
            r = requests.get(f"https://generativelanguage.googleapis.com/v1beta/models?key={key}", timeout=4)
            _gemini_check["ok"] = r.status_code == 200
        except Exception:
            _gemini_check["ok"] = False
    if _gemini_check["ok"]:
        return "Online (Gemini)"
    backups = vega.extra_keys()
    return f"Online ({EXTRA_PROVIDERS[backups[0]]['label']})" if backups else "Offline (laptop)"


@route("/api/status", method="GET")
def get_status():
    if S["locked"]:
        return as_json({"locked": True, "status_text": "Locked", "version": VERSION})
    return as_json({"brain_mode": brain_label(), "listening_on": bool(cfg.get("wake_word_on", True)),
                    "voice_on": bool(cfg.get("voice_on", True)), "locked": S["locked"], "status_text": ready_text(),
                    "active_chat_id": active_chat_id, "version": VERSION})


@route("/api/activity", method="POST")
def activity():
    touch()
    data = body()
    if "focused" in data:
        S["focused"] = bool(data["focused"])
    return as_json({"ok": True, "locked": S["locked"]})


# ---- chats
@route("/api/chats", method="GET")
def get_chats():
    return as_json(store.all())


@route("/api/chat/<chat_id>", method="GET")
def get_chat(chat_id):
    global active_chat_id
    chat = store.load(chat_id)
    if chat:
        active_chat_id = chat_id
        vega.history = []
        for m in chat.get("messages", [])[-14:]:
            role = "user" if m.get("who") in ("you", "phone") else "assistant"
            vega.history.append({"role": role, "content": m.get("text", "")})
    return as_json(chat or {})


@route("/api/chat/new", method="POST")
def new_chat():
    global active_chat_id
    chat = store.new()
    active_chat_id = chat["id"]
    vega.history = []
    return as_json(chat)


@route("/api/chat/rename", method="POST")
def rename_chat():
    data = body()
    cid, title = data.get("id"), data.get("title")
    if not isinstance(cid, str) or not isinstance(title, str):
        return as_json({"ok": False, "error": "Chat id and title must be text."})
    chat = store.load(cid)
    if chat:
        chat["title"] = title.strip()[:80] or "Untitled"
        store.save(chat, force=True)
        return as_json({"ok": True, "title": chat["title"]})
    return as_json({"ok": False})


@route("/api/chat/delete", method="POST")
def delete_chat():
    global active_chat_id
    cid = body().get("id")
    if isinstance(cid, str) and cid:
        store.delete(cid)
        if cid == active_chat_id:
            active_chat_id = store.new()["id"]
            vega.history = []
        return as_json({"ok": True})
    return as_json({"ok": False})


@route("/api/chat/pin", method="POST")
def pin_chat():
    data = body()
    cid = data.get("id")
    if isinstance(cid, str) and cid:
        store.set_pinned(cid, bool(data.get("pinned", True)))
        return as_json({"ok": True, "pinned": bool(data.get("pinned", True))})
    return as_json({"ok": False})


@route("/api/regenerate", method="POST")
def regenerate_last():
    cid = body().get("chat_id") or active_chat_id
    if not isinstance(cid, str):
        return as_json({"ok": False})
    chat = store.load(cid)
    if not chat or not chat.get("messages") or S["locked"]:
        return as_json({"ok": False})
    msgs = chat["messages"]
    last = next((m.get("text", "") for m in reversed(msgs) if m.get("who") in ("you", "phone")), "")
    if not last:
        return as_json({"ok": False})
    while msgs and msgs[-1].get("who") not in ("you", "phone"):
        msgs.pop()
    if msgs:
        msgs.pop()  # the question is sent again
    store.save(chat, force=True)
    return as_json({"ok": True, "prompt": last, "chat_id": cid})


# ---- typing and the Mic button
@route("/api/mic/start", method="POST")
def mic_start():
    touch()
    if S["locked"]:
        return as_json({"ok": False, "locked": True, "text": ""})
    if S["busy"]:
        vega.stop()
    text, _audio, err = record_and_transcribe()
    if err == "already listening":
        return as_json({"ok": True, "text": ""})
    if err:
        return as_json({"ok": False, "error": err, "text": ""})
    return as_json({"ok": True, "text": text, "language": ears.last_lang,
                    "model": ears.main_model_name(), "backend": "faster-whisper local CPU int8"})


@route("/api/mic/stop", method="POST")
def mic_stop():
    if S["mic_stop"]:
        S["mic_stop"].set()
    return as_json({"ok": True})


@route("/api/stop", method="POST")
def stop_answer():
    vega.stop()
    mouth.stop()
    return as_json({"ok": True})


def sse_line(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@route("/api/send", method="POST")
def send_message():
    data = body()
    text_value = data.get("text") or ""
    shown_value = data.get("shown") or text_value
    images = data.get("images") or []
    valid_images = isinstance(images, list) and all(
        isinstance(img, dict) and isinstance(img.get("data", ""), str)
        and isinstance(img.get("mime_type", "image/png"), str) for img in images
    )
    if not isinstance(text_value, str) or not isinstance(shown_value, str) or not valid_images:
        response.content_type = "text/event-stream"
        yield sse_line({"done": True, "text": "Message text or attachments have an invalid format.",
                        "source_label": "Error"})
        return
    text = text_value.strip()
    shown = shown_value.strip()
    spoken = bool(data.get("spoken"))
    preferred_provider = data.get("provider") or None
    preferred_model = data.get("model") or None
    if preferred_provider == "auto":
        preferred_provider = None
    if preferred_model == "auto":
        preferred_model = None
    response.content_type = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    if not text and not images:
        yield sse_line({"done": True, "text": "", "source_label": "Vega"})
        return
    touch()
    if S["locked"]:
        lock_now()
        yield sse_line({"done": True, "text": "🔒 Vega is locked. Type your PIN on the lock screen.", "source_label": "Locked",
                        "nosave": True})
        return
    if re.fullmatch(r"[A-Za-z0-9]{4,12}", text) and sec.has_pin() and sec.check_pin(text):
        yield sse_line({"done": True, "nosave": True, "source_label": "Security",
                        "text": "That's your PIN, so I didn't save it or send it anywhere. Type it only on the lock screen."})
        return
    if NOT_FOR_YOU.search(text):
        vega.stop()
        mouth.stop()
        yield sse_line({"done": True, "text": "Okay, I'll stay quiet.", "source_label": "Vega", "nosave": True})
        return
    if S["busy"]:
        vega.stop()
        time.sleep(0.3)

    chat = chat_for(data.get("chat_id") or active_chat_id)
    store.add(chat, "you", shown)
    q = queue.Queue()

    def worker():
        S["busy"] = True
        try:
            if OPEN_SELF.match(text):
                out = {"text": "I'm right here. 🙂", "say": "I'm right here."}
            else:
                ears.hint, ears.hotwords = vega.speech_hint(), vega.speech_hotwords()
                out = vega.handle(text, source="laptop", on_token=lambda full: q.put(("token", full)), spoken=spoken, images=images, preferred_provider=preferred_provider, preferred_model=preferred_model)
            q.put(("done", out))
        except Exception as e:
            log_error(f"vega.handle error: {e}\n{traceback.format_exc()}")
            q.put(("error", str(e)))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    status("Thinking…", "thinking")
    last_ping = time.time()
    try:
        while True:
            try:
                kind, val = q.get(timeout=1)
            except queue.Empty:
                if not t.is_alive() and q.empty():
                    yield sse_line({"done": True, "text": "(no answer)", "source_label": "Vega"})
                    break
                if time.time() - last_ping > 10:  # keep the connection alive while the laptop model loads
                    last_ping = time.time()
                    yield ": thinking\n\n"
                continue
            if kind == "token":
                yield sse_line({"token": True, "full": strip_thinking(str(val)), "source_label": vega_source()})
            elif kind == "done":
                ans = val.get("text", "")
                store.add(chat, "vega", ans)
                speak(val, prompt_text=text)
                after_answer(val, spoken)
                yield sse_line({"done": True, "text": ans, "source_label": val.get("source_label") or "Vega",
                                "model": val.get("model", ""), "open_tab": "lablog" if val.get("open_lablog") else None,
                                "lock": bool(val.get("lock_app"))})
                break
            else:
                store.add(chat, "vega", f"Something went wrong: {val}")
                yield sse_line({"done": True, "text": f"Something went wrong: {val}", "source_label": "Error"})
                break
    finally:
        S["busy"] = False
        status(ready_text(), "ready")


def vega_source():
    if cfg.get("private_mode", False) or not (cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")
                                              or vega.extra_keys()):
        return "Offline"
    return "Online"


# ---- lock
@route("/api/lock/verify", method="POST")
def verify_pin():
    now = time.time()
    if now < S["pin_wait_until"]:
        left = int(S["pin_wait_until"] - now) + 1
        return as_json({"ok": False, "error": f"Too many wrong tries. Wait {left} seconds."})
    pin = str(body().get("pin", "")).strip()
    if sec.has_pin() and sec.check_pin(pin):
        unlock("pin")
        return as_json({"ok": True})
    S["pin_fails"] += 1
    if S["pin_fails"] >= 5:
        S["pin_fails"] = 0
        S["pin_wait_until"] = now + 30
        return as_json({"ok": False, "error": "Wrong PIN 5 times. Wait 30 seconds."})
    return as_json({"ok": False, "error": "Wrong PIN. Try again."})


@route("/api/lock/now", method="POST")
def lock_route():
    ok = lock_now()
    return as_json({"ok": ok, "locked": S["locked"], "voice_unlock_ready": sec.voice_unlock_ready(),
                    "voice_reason": voice_unlock_reason(),
                    "error": "" if ok else "Set a PIN first (Settings → PIN and lock)."})


@route("/api/lock/voice_unlock", method="POST")
def voice_unlock_route():
    if not S["locked"]:
        return as_json({"ok": True})
    if not sec.voice_unlock_ready():
        return as_json({"ok": False, "error": "Voice unlock isn't set up. Type your PIN."})
    S["listening"] = True
    try:
        if not wake.pause_for_capture():
            return as_json({"ok": False, "error": "The microphone is still in use. Please try again."})
        audio = ears.listen_once(wait_seconds=6, silence=1.2)
        if audio is None:
            return as_json({"ok": False, "error": "I didn't hear anything. Try again, or type your PIN."})
        score = voice.score(audio)
        if score >= sec.unlock_threshold():
            unlock("voice")
            mouth.say("Welcome back, Hemanth.")
            return as_json({"ok": True, "score": round(score, 2)})
        return as_json({"ok": False, "error": f"That didn't match your voice closely enough ({score:.2f}, needs "
                                              f"{sec.unlock_threshold():.2f}). Type your PIN."})
    except Exception as e:
        log_error(f"voice unlock: {e}")
        return as_json({"ok": False, "error": f"Voice check didn't work ({e}). Type your PIN."})
    finally:
        S["listening"] = False
        wake.resume_after_capture()


ENROLL = {"clips": []}


@route("/api/voice/enroll_clip", method="POST")
def voice_enroll_clip():
    """Settings → Set up my voice: record one sentence (4 in all), then /api/voice/enroll_finish."""
    if S["locked"]:
        return as_json({"ok": False, "error": "Unlock Vega first."})
    missing = sec.voice_packages_missing()
    if missing:
        return as_json({"ok": False, "error": "Voice setup needs " + ", ".join(missing) +
                        ". Double-click INSTALL VOICE UNLOCK.bat in the mybot folder."})
    if body().get("first"):
        ENROLL["clips"] = []
    S["listening"] = True
    mouth.stop()
    try:
        if not wake.pause_for_capture():
            return as_json({"ok": False, "error": "The microphone is still in use. Please try again."})
        audio = ears.listen_once(wait_seconds=8, silence=1.2, max_seconds=10)
        if audio is None or len(audio) < 16000:
            return as_json({"ok": False, "error": "I didn't hear enough. Say the whole sentence, a bit closer."})
        ENROLL["clips"].append(audio)
        return as_json({"ok": True, "count": len(ENROLL["clips"])})
    except Exception as e:
        log_error(f"voice enroll: {e}")
        return as_json({"ok": False, "error": f"Microphone problem: {e}"})
    finally:
        S["listening"] = False
        wake.resume_after_capture()


@route("/api/voice/enroll_finish", method="POST")
def voice_enroll_finish():
    if S["locked"]:
        return as_json({"ok": False, "error": "Unlock Vega first."})
    try:
        low = voice.enroll(ENROLL["clips"])
        ENROLL["clips"] = []
        s = sec.security()
        s["voice_unlock"] = True
        sec.save_security(s)
        return as_json({"ok": True, "text": f"Your voice is saved (your clips matched each other {low:.2f}). "
                                            "Now “Hey Vega” in your voice unlocks me."})
    except Exception as e:
        return as_json({"ok": False, "error": str(e)})


@route("/api/pin/set", method="POST")
def set_pin_route():
    data = body()
    old, new = str(data.get("old", "")), str(data.get("new", ""))
    if sec.has_pin() and not sec.check_pin(old):
        return as_json({"ok": False, "error": "Your current PIN is wrong."})
    if not re.fullmatch(r"[A-Za-z0-9]{4,12}", new):
        return as_json({"ok": False, "error": "Use 4 to 12 letters or digits, no spaces."})
    sec.set_pin(new)
    return as_json({"ok": True})


@route("/api/settings/lock_minutes", method="POST")
def set_lock_minutes():
    try:
        n = max(0, min(600, int(body().get("minutes", 30))))
    except (TypeError, ValueError):
        n = 30
    sec.set_lock_minutes(n)
    return as_json({"ok": True, "minutes": n})


# ---- keys (pasted in Settings; saved only in .env, never shown again or written to a log)
@route("/api/keys", method="POST")
def save_keys():
    data = body()
    result = {"ok": True}
    tok = str(data.get("telegram_token") or "").strip()
    if tok and not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", tok):
        return as_json({"ok": False, "error": "That Telegram token doesn't look right. Copy it again from @BotFather."})
    key = str(data.get("gemini_api_key") or "").strip()
    if key and (len(key) < 20 or re.search(r"\s", key)):
        return as_json({"ok": False, "error": "That Gemini key doesn't look right. Copy the whole key again."})
    provider_keys = {}
    for name, prov in EXTRA_PROVIDERS.items():
        k2 = str(data.get(name + "_api_key") or "").strip()
        if k2 and (len(k2) < 20 or re.search(r"\s", k2)):
            return as_json({"ok": False, "error": f"That {prov['label']} key doesn't look right. Copy the whole key again."})
        provider_keys[name] = k2
    if tok:
        old = str(cfg.get("telegram_token") or "")
        try:
            save_config_value("telegram_token", tok)
        except OSError as e:
            return as_json({"ok": False, "error": f"Couldn't save the Telegram token: {e}"})
        os.environ["TELEGRAM_TOKEN"] = tok
        cfg["telegram_token"] = tok
        if old.split(":")[0] != tok.split(":")[0]:  # a different bot: the phone must say hi again
            save_config_value("telegram_chat_id", "")
            cfg["telegram_chat_id"] = ""
            result["telegram"] = "Saved. Now send “hi” to your bot from your phone, then click Allow here."
        else:
            result["telegram"] = "Saved. Your phone link is back on."
        start_phone()
    if key:
        try:
            save_config_value("gemini_api_key", key)
        except OSError as e:
            return as_json({"ok": False, "error": f"Couldn't save the Gemini key: {e}"})
        os.environ["GEMINI_API_KEY"] = key
        cfg["gemini_api_key"] = key
        _gemini_check["t"] = 0.0
        try:
            res = vega.gemini_chat("Reply with just the word OK.", [])
        except Exception:
            res = None
        result["gemini"] = (f"Connected ({res[1]}). I'll use Gemini when you're online." if res else
                            "Saved, but Gemini didn't answer. Check the key or your internet.")
    for name, prov in EXTRA_PROVIDERS.items():
        k2 = provider_keys[name]
        if not k2:
            continue
        try:
            save_config_value(name + "_api_key", k2)
        except OSError as e:
            return as_json({"ok": False, "error": f"Couldn't save the {prov['label']} key: {e}"})
        os.environ[prov["env"]] = k2
        cfg[name + "_api_key"] = k2
        vega.cfg[name + "_api_key"] = k2
        vega.__dict__.pop("_pm_cache", None)
        try:
            res = vega.extra_chat("Reply with just the word OK.", [], system_instruction=None, only=name)
        except Exception:
            res = None
        why = (vega.__dict__.get("_why") or {}).get(name, "")
        result[name] = (f"Connected ({res[1]}). I'll use it when Gemini is busy or out of free messages." if res else
                        f"Saved, but {prov['label']} didn't work: {why or 'no answer. Check the key or your internet.'}")
    return as_json(result)


@route("/api/brains", method="GET")
def brains():
    return as_json({"ok": True, "brains": vega.brain_status()})


@route("/api/phone/allow", method="POST")
def phone_allow():
    pending = S["pending_pair"]
    S["pending_pair"] = None
    if not pending or not phone or not body().get("allow"):
        return as_json({"ok": False})
    chat_id, _ = pending
    save_config_value("telegram_chat_id", str(chat_id))
    cfg["telegram_chat_id"] = str(chat_id)
    phone.pair(chat_id)
    return as_json({"ok": True})


# ---- settings
@route("/api/settings/mode", method="POST")
def set_mode():
    mode = body().get("mode", "online")
    cfg["private_mode"] = mode in ("private", "offline")
    save_config_value("private_mode", cfg["private_mode"])
    return as_json({"ok": True, "brain_mode": brain_label()})


SAFE_SETTINGS = {"voice_on", "wake_word_on", "cloud_speech", "speech_rate", "talk_without_wake", "wake_only_me",
                 "speaker_verification_enforce", "wake_strictness", "voice_language", "telugu_model"}


@route("/api/settings/update", method="POST")
def update_setting():
    data = body()
    k, v = data.get("key"), data.get("value")
    if not isinstance(k, str) or k not in SAFE_SETTINGS:
        return as_json({"ok": False})
    if k == "wake_only_me":  # compatibility with older settings pages
        k = "speaker_verification_enforce"
    if k == "cloud_speech":
        if v is not False:
            return as_json({"ok": False, "error": "Online speech recognition is disabled; Vega uses local Whisper."})
        ears.cloud = False
        cfg["cloud_speech"] = False
        save_config_value("cloud_speech", False)
        return as_json({"ok": True, "cloud_speech": False})
    if k == "voice_language":
        if v not in ("auto", "english", "telugu"):
            return as_json({"ok": False, "error": "Choose auto, English, or Telugu speech recognition."})
        set_voice_language(v, persist=True)
        return as_json({"ok": True, "voice_language": ears.language_mode, "speech_model": ears.main_model_name(),
                        "speech_model_ready": ears.main_model_name() in ears._models})
    if k == "telugu_model":
        if v not in ("small", "medium"):
            return as_json({"ok": False, "error": "Choose the cached small or medium multilingual Whisper model."})
        set_voice_model(v, persist=True)
        return as_json({"ok": True, "telugu_model": ears.telugu_model_name, "speech_model": ears.main_model_name(),
                        "speech_model_ready": ears.main_model_name() in ears._models})
    boolean_settings = {"voice_on", "wake_word_on", "cloud_speech", "talk_without_wake",
                        "speaker_verification_enforce"}
    if k in boolean_settings and not isinstance(v, bool):
        return as_json({"ok": False, "error": "This setting must be true or false."})
    if k == "speech_rate":
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0.8 <= v <= 1.5:
            return as_json({"ok": False, "error": "Speech rate must be between 0.8 and 1.5."})
        v = float(v)
    if k == "wake_strictness" and v not in ("strict", "normal", "easy", "very_easy"):
        return as_json({"ok": False, "error": "Choose a supported wake-word sensitivity."})
    if k == "wake_word_on":
        set_listening(bool(v))
        return as_json({"ok": True})
    if k == "voice_on":
        set_speaking(bool(v))
        return as_json({"ok": True})
    if k == "speaker_verification_enforce":
        v = bool(v)
    save_config_value(k, v)
    cfg[k] = v
    if k == "speech_rate":
        mouth.rate = v
    elif k == "voice_on" and not v:
        mouth.stop()
    elif k == "wake_strictness":
        wake.strictness = str(v)
    elif k == "speaker_verification_enforce" and v and not voice.has_voiceprint():
        return as_json({"ok": True, "note": "Set up your voice in the old Vega window first (Settings → Set up my voice)."})
    return as_json({"ok": True})


@route("/api/settings/toggle_listening", method="POST")
def toggle_listening():
    set_listening(not bool(cfg.get("wake_word_on", True)))
    return as_json({"ok": True, "listening_on": bool(cfg.get("wake_word_on", True))})


@route("/api/settings/toggle_speaking", method="POST")
def toggle_speaking():
    new_val = not bool(cfg.get("voice_on", True))
    set_speaking(new_val)
    return as_json({"ok": True, "voice_on": new_val})


@route("/api/settings/wake_only_me", method="POST")
def toggle_wake_only_me():
    val = bool(body().get("wake_only_me", True))
    cfg["speaker_verification_enforce"] = val
    save_config_value("speaker_verification_enforce", val)
    return as_json({"ok": True, "wake_only_me": val})


@route("/api/quickbar/toggle", method="POST")
def quickbar():
    broadcast_event("focus_input", {})  # (this used to call itself again and again: 205,000 times in your log)
    return as_json({"ok": True})


@route("/api/quit", method="POST")
def quit_route():
    def bye():
        time.sleep(0.4)
        quit_vega()
    threading.Thread(target=bye, daemon=True).start()
    return as_json({"ok": True})


def quit_vega():
    log_debug("quit")
    try:
        mouth.stop()
        if tray:
            tray.stop()
    except Exception:
        pass
    os._exit(0)


@route("/api/mic/test", method="POST")
def test_microphone():
    if S["locked"]:
        return as_json({"ok": False, "error": "Unlock Vega first."})
    S["listening"] = True
    try:
        import numpy as np
        import sounddevice as sd
        if not wake.pause_for_capture():
            return as_json({"ok": False, "error": "The microphone is still in use. Please try again."})
        channels = vega_voice.mic_input_channels(sd)
        rec = sd.rec(int(3 * 16000), samplerate=16000, channels=channels, dtype="float32")
        sd.wait()
        rec = vega_voice.mono_from_input(rec)
        rms = float(np.sqrt(np.mean(rec ** 2)))
        level_pct = int(min(100, rms * 500))
        try:
            device_name = str(sd.query_devices(sd.default.device[0]).get("name", "default input"))
        except Exception:
            device_name = "default input"
        vega_voice.log_voice_debug(f"Manual mic test: device='{device_name}', channels={channels}, RMS={rms:.5f}")
        if rms < 0.0005:
            return as_json({"ok": False, "rms": round(rms, 5), "level_pct": level_pct, "heard": "(silence)",
                            "error": f"Almost no sound reached Vega from {device_name}. In Windows Settings > System > Sound > Input, select your microphone and check that its input meter moves. Also check microphone privacy access."})
        text = ears.transcribe(rec)
        return as_json({"ok": True, "rms": round(rms, 5), "level_pct": level_pct, "heard": text or "(silence)"})
    except Exception as e:
        return as_json({"ok": False, "error": str(e)})
    finally:
        S["listening"] = False
        wake.resume_after_capture()


@route("/api/selftest", method="GET")
def self_test():
    """Safe checks only (the old one locked the laptop and opened Brave, WhatsApp and YouTube for real)."""
    results = []

    def check(name, fn):
        try:
            ok = fn()
            results.append({"name": name, "status": "PASS" if ok is not False else "FAIL"})
        except Exception as e:
            results.append({"name": name, "status": "FAIL", "error": str(e)[:200]})

    check("PIN is set", sec.has_pin)
    check("only one Vega running", lambda: INSTANCE is not None)
    check("Hey Vega listener running", lambda: wake.is_alive() and wake.enabled.is_set())
    check("voice unlock ready", sec.voice_unlock_ready)
    check("Gemini (online brain)", lambda: brain_label().startswith("Online"))

    def ollama_ok():
        import ollama
        ollama.list()
    check("Ollama (laptop brain)", ollama_ok)
    check("phone link (Telegram)", lambda: bool(phone and phone.is_alive()))
    check("Lab Log loads", lambda: bool(vega.track_day("linux", max(vega.track_today("linux"), 0))))
    check("commands work", lambda: "It's" in vega.handle("what time is it")["text"])

    def mic():
        import sounddevice as sd
        return sd.query_devices(kind="input") is not None
    check("microphone found", mic)
    return as_json({"ok": True, "results": results})


@route("/api/debug/log", method="POST")
def client_debug_log():
    d = body()
    log_error(f"[page {d.get('type', 'error')}] {str(d.get('message', ''))[:300]} | {str(d.get('stack', ''))[:300]}")
    return as_json({"ok": True})


# ---- news, internships, links, folders, projects
def split_source(title, default):
    if " (" in title and title.endswith(")"):
        i = title.rfind(" (")
        return title[:i].strip(), title[i + 2:-1].strip()
    return title, default


@route("/api/news/tech", method="GET")
def get_tech_news():
    try:
        items = vega.fetch_news("technology OR AI OR cloud computing OR smartphones", 12)
        out = []
        for title, url in items:
            t, src = split_source(title, "Tech news")
            out.append({"title": t, "source": src, "url": url, "time": "Today"})
        return as_json({"ok": True, "items": out})
    except Exception as e:
        return as_json({"ok": False, "error": str(e), "items": []})


@route("/api/news/internships", method="GET")
def get_internships():
    try:
        kw = cfg.get("internship_keywords") or ["devops", "cloud", "linux"]
        items = vega.fetch_news("internship (" + " OR ".join(kw) + ")", 10)
        out = []
        for title, url in items:
            t, src = split_source(title, "Internship news")
            out.append({"title": t, "company": src, "url": url, "badge": "New"})
        for label, url in vega.internship_links():
            out.append({"title": label, "company": "Job portal", "url": url, "badge": "Portal"})
        return as_json({"ok": True, "items": out})
    except Exception as e:
        return as_json({"ok": False, "error": str(e), "items": []})


@route("/api/open_url", method="POST")
def open_url():
    url = str(body().get("url", "")).strip()
    if not re.match(r"^https?://", url):
        return as_json({"ok": False})
    try:
        vega.open_in_browser(vega.browser_key(""), url)
    except Exception:
        import webbrowser
        webbrowser.open(url)
    return as_json({"ok": True})


def open_path(path):
    try:
        if IS_WINDOWS:
            os.startfile(path)
        else:
            import webbrowser
            webbrowser.open("file://" + path)
    except Exception as e:
        log_error(f"open_path {path}: {e}")


@route("/api/open_folder", method="POST")
def open_folder_endpoint():
    which = body().get("folder")
    if which == "diary":
        os.makedirs(here("diary"), exist_ok=True)
        open_path(here("diary"))
    elif which == "docs":
        open_path(vega.doc_folder())
    elif which == "vega":
        open_path(BASE_DIR)
    else:
        return as_json({"ok": False})
    return as_json({"ok": True})


@route("/api/projects", method="GET")
def get_projects():
    return as_json(store.projects())


@route("/api/project/new", method="POST")
def create_project():
    data = body()
    name, notes = data.get("name") or "New project", data.get("notes") or ""
    if not isinstance(name, str) or not isinstance(notes, str):
        return as_json({"ok": False, "error": "Project name and notes must be text."})
    p = store.add_project(name.strip(), notes.strip())
    return as_json({"ok": True, "project": p})


@route("/api/project/<pid>", method="GET")
def get_project_detail(pid):
    return as_json({"project": store.project(pid) or {}, "files": store.project_files(pid)})


@route("/api/project/save_notes", method="POST")
def save_project_notes():
    data = body()
    pid, notes = data.get("id"), data.get("notes", "")
    if isinstance(pid, str) and pid and isinstance(notes, str):
        store.update_project(pid, notes=notes)
        return as_json({"ok": True})
    return as_json({"ok": False})


@route("/api/project/delete", method="POST")
def delete_project():
    pid = body().get("id")
    if isinstance(pid, str) and pid:
        store.delete_project(pid)
        return as_json({"ok": True})
    return as_json({"ok": False})


# ---- Lab Log (the full page, like the old window: every skill, Ask Vega, How to learn this)
QUICK = {
    "QUICK_COMMANDS": ("Commands for today",
                       "List every command (or piece of code) I need for this day's task. For each one give: exactly what to type, "
                       "what it does in one simple line, and what I should see on screen. Put them in the order I will use them."),
    "QUICK_AI": ("How to use AI today",
                 "For this day's task, show me how to use AI to learn faster and do the job better. Give 4 or 5 ready-to-copy "
                 "prompts I can ask an AI (with [blanks] where I paste my own output or errors), and say when to use each one. "
                 "Also tell me one thing I should NOT blindly trust AI for today."),
    "QUICK_EXPLAIN": ("Explain today simply",
                      "Explain what this day is about in very simple words, like to a beginner, and why it matters in a real job. "
                      "Then walk me through the steps one by one."),
    "QUICK_QUIZ": ("Quiz me",
                   "Ask me 5 short quiz questions about this day's task (mostly 'what would you type to...'). "
                   "Put the answers at the very end under 'Answers'."),
}


def lab_track(data):
    tid = str(data.get("track") or "linux")
    return tid if tid in vega.track_ids() else "linux"


def lab_index(tid, raw):
    total = len(vega.track_lessons(tid))
    try:
        i = int(raw)
    except (TypeError, ValueError):
        i = vega.track_today(tid)
    return min(max(i, 0), max(total - 1, 0))


def lab_learn(tid, i, d):
    if tid == "linux":
        day = vega.learn_for(i)
        if not day:
            return None
        return {"course": day.get("course"), "topics": day.get("topics", [])}
    week = vega.track_learn(tid, d["week_no"]) if d else None
    if not week or not week.get("learn"):
        return None
    return {"course": None, "topics": [{"name": f"Week {d['week_no'] + 1}: {week.get('topic', '')}", "read": week["learn"]}]}


def day_context(tid, i):
    d = vega.track_day(tid, i)
    e = vega.track_entry(tid, i)
    name = vega.track_name(tid)
    steps = "\n".join(f"{n}. {clean_step(x)}" for n, x in enumerate(d["steps"], 1)) or "(no steps written)"
    ticked = ", ".join(str(n) for n in sorted(e.get("ticked", []))) or "none yet"
    if tid == "linux":
        learn = vega.learn_for(i) or {}
        topics = "; ".join(t.get("name", "") for t in learn.get("topics", []))
        where = "He practises on a Rocky Linux 9 VM in VirtualBox (hostname server1, user hemanth). "
    else:
        topics = d.get("topic", "")
        where = {"python": "He practises Python 3.11 on Windows (IDLE and PowerShell) and python3 on his Rocky Linux VM. ",
                 "c": "He writes C with vi and compiles with gcc on his Rocky Linux 9 VM (hostname server1, user hemanth). "
                 }.get(tid, "")
    notes = e.get("notes", "").strip()
    return (f"You are Vega, helping Hemanth inside his Lab Log with ONE lesson of his {name} study plan. Everything you say "
            "must be about THIS lesson.\n"
            f"Lesson: {d['label']}" + (f" ({d['topic']})" if d.get("topic") else "") + f"\nTask: {d['title']}\nSteps:\n{steps}\n"
            f"Steps he has ticked: {ticked}\n"
            + (f"Topics: {topics}\n" if topics else "")
            + (f"His notes so far:\n{notes[:1500]}\n" if notes else "")
            + "He is a complete beginner who wants simple, short teaching, not long lessons. " + where +
            "Use simple English, step by step. Show every command or line of code exactly as he should type it, "
            "with what it does and what he should see. If he pastes an error, say the cause in one line, then the fix. "
            "If he asks something unrelated to this lesson, answer in one or two lines and bring him back to it. "
            "Put each command in backticks on its own line, like `ls -la`, and never put ** or other symbols inside "
            "a command. Keep answers short and practical.")


@route("/api/lablog", method="GET")
def get_lablog():
    try:
        tid = lab_track(request.query)
        lessons = vega.track_lessons(tid)
        if not lessons:
            return as_json({"ok": False, "error": "This skill has no lessons yet."})
        today = vega.track_today(tid)
        i = lab_index(tid, request.query.get("day"))
        d = vega.track_day(tid, i)
        e = vega.track_entry(tid, i)
        info = vega.track_info(tid)
        stats = vega.track_stats(tid)
        if tid == "linux":
            wk = d["week"]
            resources = f"Coursera: {wk.get('coursera', '')}   ·   YouTube: {wk.get('youtube', '')}"
        else:
            resources = info.get("about", "")
        when = "TODAY" if i == today else ("PAST DAY" if i < today else "UPCOMING")
        return as_json({
            "ok": True, "track_id": tid, "track_name": info["name"], "total_lessons": len(lessons),
            "today_idx": today, "current_idx": i, "is_today": i == today, "when": when,
            "title": d["title"], "topic": d.get("topic", ""), "label": d["label"],
            "date_str": d["date"].strftime("%A %d %B"), "minutes": info.get("minutes"),
            "resources": resources,
            "steps": [{"num": n, "text": clean_step(s), "raw": s, "done": n in e.get("ticked", [])}
                      for n, s in enumerate(d["steps"], 1)],
            "notes": e.get("notes", ""), "review": e.get("review", ""), "score": e.get("score"),
            "done": vega.track_is_done(tid, i),
            "streak": stats.get("streak", 0), "stats": stats,
            "map": [{"done": vega.track_is_done(tid, n), "past": n < today} for n in range(len(lessons))],
            "map_rows": 7 if tid == "linux" else max(1, min(7, len(info["days"]))),
            "learn": lab_learn(tid, i, d),
            "quick": [[k, v[0]] for k, v in QUICK.items()],
            "tracks": [{"id": t, "name": vega.track_name(t)} for t in vega.track_ids()],
        })
    except Exception as e:
        log_error(f"lab log: {e}\n{traceback.format_exc()}")
        return as_json({"ok": False, "error": str(e)})


@route("/api/lablog/tick", method="POST")
def tick_lablog_step():
    data = body()
    tid = lab_track(data)
    i = lab_index(tid, data.get("day"))
    try:
        all_done = vega.track_tick(tid, i, int(data.get("step", 1)), bool(data.get("done", True)))
        msg = ""
        if all_done and not vega.track_is_done(tid, i):
            vega.track_set_done(tid, i, True)
            msg = "All steps ticked, so this day is marked done. Nice work! 🎉"
        return as_json({"ok": True, "all_done": all_done, "done": vega.track_is_done(tid, i), "message": msg})
    except Exception as e:
        return as_json({"ok": False, "error": str(e)})


def save_lab_notes(tid, i, text):
    e = vega.track_entry(tid, i)
    text = (text or "").strip()
    if text != e.get("notes", "").strip():
        e["notes"] = text
        e.pop("review", None)
        e.pop("score", None)
        vega.save_track_entry(tid, i, e)
        return True
    return False


@route("/api/lablog/save_notes", method="POST")
def save_lablog_notes():
    data = body()
    tid = lab_track(data)
    i = lab_index(tid, data.get("day"))
    try:
        changed = save_lab_notes(tid, i, data.get("notes", ""))
        return as_json({"ok": True, "changed": changed})
    except Exception as e:
        return as_json({"ok": False, "error": str(e)})


@route("/api/lablog/done", method="POST")
def lablog_done():
    data = body()
    tid = lab_track(data)
    i = lab_index(tid, data.get("day"))
    vega.track_set_done(tid, i, bool(data.get("done", True)))
    return as_json({"ok": True, "done": vega.track_is_done(tid, i)})


@route("/api/lablog/assess", method="POST")
def assess_lablog():
    data = body()
    tid = lab_track(data)
    i = lab_index(tid, data.get("day"))
    try:
        if "notes" in data:
            save_lab_notes(tid, i, data.get("notes", ""))
        text, score = vega.assess_lesson(tid, i)
        return as_json({"ok": True, "review": strip_thinking(text).strip(), "score": score})
    except Exception as e:
        log_error(f"assess: {e}")
        return as_json({"ok": False, "error": f"I couldn't review it ({e}). Check your internet, or open the Ollama app."})


@route("/api/lablog/ask", method="POST")
def ask_lablog():
    """'Ask Vega about this day': a small chat that only talks about this lesson (Gemini online, laptop model offline)."""
    data = body()
    tid = lab_track(data)
    i = lab_index(tid, data.get("day"))
    q = str(data.get("question") or "").strip()
    images = data.get("images") or []
    if not q and not images:
        return as_json({"ok": False})
    if S["locked"]:
        return as_json({"ok": False, "error": "Vega is locked."})
    touch()
    shown = q
    if q in QUICK:
        shown, q = QUICK[q]
    msgs = [{"role": "system", "content": day_context(tid, i)}]
    for item in (data.get("history") or [])[-3:]:
        hq = str(item[0])[:1500] if len(item) > 0 else ""
        ha = str(item[1])[:1500] if len(item) > 1 else ""
        msgs += [{"role": "user", "content": hq}, {"role": "assistant", "content": ha}]
    msgs.append({"role": "user", "content": q or "Please inspect the attached screenshot or file for this lesson."})
    try:
        answer = strip_thinking(vega.ask_brain(msgs, temperature=0.4, hard=True, images=images)).strip()
    except Exception as e:
        answer = f"I couldn't answer ({e}). Check your internet, or open the Ollama app."
    return as_json({"ok": True, "shown": shown, "answer": answer or "…"})


# ---- shortcuts
def startup_shortcut_path():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "Vega.lnk")


def desktop_folder():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0])
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")


def make_shortcut(lnk):
    wscript = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "wscript.exe")
    env = dict(os.environ, VEGA_LNK=lnk, VEGA_PYW=wscript, VEGA_SCRIPT=os.path.join(BASE_DIR, "Start Vega.vbs"), VEGA_DIR=BASE_DIR)
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:VEGA_LNK);"
          "$s.TargetPath=$env:VEGA_PYW;$s.Arguments='\"'+$env:VEGA_SCRIPT+'\"';"
          "$s.WorkingDirectory=$env:VEGA_DIR;$s.Description='Vega - personal assistant';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps],
                   env=env, creationflags=NO_WINDOW, timeout=30, check=True)


@route("/api/settings/desktop_shortcut", method="POST")
def create_desktop_shortcut():
    try:
        make_shortcut(os.path.join(desktop_folder(), "Vega.lnk"))
        return as_json({"ok": True})
    except Exception as e:
        return as_json({"ok": False, "error": str(e)})


@route("/api/settings/startup", method=["GET", "POST"])
def settings_startup():
    lnk = startup_shortcut_path()
    if request.method == "POST":
        try:
            if body().get("enabled", True):
                make_shortcut(lnk)
            elif os.path.exists(lnk):
                os.remove(lnk)
        except Exception as e:
            return as_json({"ok": False, "error": str(e)})
    return as_json({"ok": True, "enabled": os.path.exists(lnk)})


# Local-only plan tasks and a disposable in-memory command simulator.
_AGENT_OPTIONS_EMPTY = {
    "local_only": True, "provider": "Ollama", "endpoint": "http://127.0.0.1:11434",
    "provider_modes": ["auto", "local", "free_cloud"],
    "verified_connections": {}, "free_cloud_policy": "",
    "workspace": "", "models": [], "agents": [], "tools": [],
    "recommendations": {}, "action_tools_enabled": False,
    "command_execution_enabled": False, "edit_mode": "preview_then_explicit_approval",
    "source_files": [],
}


@route("/api/agent/options", method="GET")
def agent_options():
    try:
        return as_json(agent_tasks.options())
    except Exception as exc:
        log_error(f"agent options failed: {exc}")
        return as_json(dict(_AGENT_OPTIONS_EMPTY, error=f"Provider status could not be loaded: {type(exc).__name__}. Refresh to retry."))


@route("/api/agent/refresh", method="POST")
def agent_refresh():
    try:
        return as_json(agent_tasks.refresh())
    except Exception as exc:
        log_error(f"agent refresh failed: {exc}")
        return as_json(dict(_AGENT_OPTIONS_EMPTY, error=f"Provider status refresh failed: {type(exc).__name__}. Check that Vega can read its own folder."))


@route("/api/agent/test", method="POST")
def agent_test_connection():
    data = body()
    try:
        result = agent_tasks.test_local_connection(data.get("tool"), data.get("model"))
        return as_json({"ok": True, "connection": result})
    except AgentTaskError as exc:
        response.status = 400
        return as_json({"ok": False, "error": str(exc)})
    except Exception:
        log_error("local agent connection test failed")
        response.status = 500
        return as_json({"ok": False, "error": "The local connection test failed. No task was sent."})


@route("/api/agent/tasks", method=["GET", "POST"])
def agent_task_collection():
    if request.method == "GET":
        return as_json({"tasks": agent_tasks.recent()})
    data = body()
    try:
        task = agent_tasks.start(data.get("category"), data.get("tool"), data.get("model"),
                                 data.get("prompt"), data.get("mode", "read_only"),
                                 data.get("provider_mode", "auto"))
        return as_json({"ok": True, "task": task, "task_id": task["id"]})
    except AgentTaskError as exc:
        response.status = 400
        return as_json({"ok": False, "error": str(exc)})
    except Exception as exc:
        log_error(f"local agent task could not be started: {exc}")
        response.status = 500
        return as_json({"ok": False, "error": f"The local task could not be started ({type(exc).__name__}). No task text or files were sent."})


@route("/api/agent/tasks/<task_id>", method="GET")
def agent_task_status(task_id):
    task = agent_tasks.get(task_id)
    if not task:
        response.status = 404
        return as_json({"ok": False, "error": "Task not found."})
    return as_json({"ok": True, "task": task})


@route("/api/agent/tasks/<task_id>/cancel", method="POST")
def agent_task_cancel(task_id):
    if not agent_tasks.cancel(task_id):
        response.status = 409
        return as_json({"ok": False, "error": "Task is no longer running."})
    return as_json({"ok": True})


@route("/api/agent/tasks/<task_id>/approve", method="POST")
def agent_task_approve(task_id):
    try:
        task = agent_tasks.approve(task_id)
        return as_json({"ok": True, "task": task})
    except AgentTaskError as exc:
        response.status = 409
        return as_json({"ok": False, "error": str(exc)})
    except Exception:
        log_error("approved local edit could not be applied")
        response.status = 500
        return as_json({"ok": False, "error": "The approved edit could not be applied safely."})


@route("/api/agent/browse", method="GET")
def agent_browse():
    """Return the current list of selectable Vega source files without reloading all options."""
    try:
        return as_json({"ok": True, "source_files": agent_tasks.source_inventory()})
    except Exception as exc:
        log_error(f"agent browse failed: {exc}")
        return as_json({"ok": False, "source_files": [], "error": f"Source file list could not be read ({type(exc).__name__})."})


@route("/api/agent/audit", method="GET")
def agent_task_audit():
    try:
        return as_json({"records": agent_tasks.audit()})
    except AgentTaskError as exc:
        response.status = 500
        return as_json({"ok": False, "error": str(exc)})


@route("/api/practice-lab", method="GET")
def practice_lab_state():
    return as_json(practice_lab.state())


@route("/api/practice-lab/command", method="POST")
def practice_lab_command():
    try:
        output = practice_lab.command(body().get("command", ""))
        return as_json({"ok": True, "output": output, "state": practice_lab.state()})
    except ValueError as exc:
        response.status = 400
        return as_json({"ok": False, "error": str(exc), "state": practice_lab.state()})


@route("/api/practice-lab/reset", method="POST")
def practice_lab_reset():
    return as_json({"ok": True, "state": practice_lab.reset()})


# ---------------------------------------------------------------- next-gen API endpoints
@route("/api/auth/token", method="GET")
def get_auth_token():
    return as_json({"ok": True, "token": get_session_token()})


@route("/api/telemetry", method="GET")
def get_telemetry_endpoint():
    return as_json({"ok": True, "data": get_hardware_vitals()})


@route("/api/chats/search", method="GET")
def search_chats_endpoint():
    q = request.query.get("q", "").strip()
    if not q:
        return as_json({"ok": True, "results": []})
    return as_json({"ok": True, "results": search_messages(q)})


@route("/api/tools/execute", method="POST")
def execute_tool_endpoint():
    payload = body()
    name = payload.get("name")
    args = payload.get("args", {})
    return as_json(ToolRegistry.execute_tool(name, args))


@route("/api/tools/confirm", method="POST")
def confirm_tool_endpoint():
    payload = body()
    conf_id = payload.get("confirmation_id")
    approved = payload.get("approved", True)
    return as_json(ToolRegistry.confirm_action(conf_id, approved))


# the page's files (defined LAST: a catch-all defined earlier swallowed /api/chat/<id>, so old chats never opened)
@route("/<filename:path>")
def static_files(filename):
    resp = static_file(filename, root=WEB_DIR)
    resp.set_header("Cache-Control", "no-store")
    return resp


# ---------------------------------------------------------------- the icon by the clock (system tray)
tray = None


def run_tray():
    """Vega's icon by the clock: click = open Vega. Right-click: Open, Lock, Quit. Blocks (runs in the main thread)."""
    global tray
    try:
        import pystray
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        dr = ImageDraw.Draw(img)
        dr.ellipse((2, 2, 62, 62), fill=(79, 135, 255, 255))
        dr.line((18, 18, 32, 48), fill="white", width=7)
        dr.line((46, 18, 32, 48), fill="white", width=7)
        tray = pystray.Icon("Vega", img, "Vega (click to open)", menu=pystray.Menu(
            pystray.MenuItem("Open Vega", lambda *_: open_window(), default=True),
            pystray.MenuItem("Speak answers", lambda *_: set_speaking(not cfg.get("voice_on", True)),
                             checked=lambda *_: bool(cfg.get("voice_on", True))),
            pystray.MenuItem("Quick Bar (Ctrl + Alt + Space)", lambda *_: quick_bar()),
            pystray.MenuItem("Lock Vega", lambda *_: lock_now()),
            pystray.MenuItem("Quit Vega", lambda *_: quit_vega())))
        tray.run()
    except Exception as e:  # no icon: Vega still runs; open her with the Start Vega shortcut
        tray = None
        log_debug(f"no tray icon ({type(e).__name__}: {e})")
    while True:
        time.sleep(3600)


# ---------------------------------------------------------------- start
def main():
    try:
        init_db()
        migrate_from_json()
    except Exception as e:
        log_error(f"Database initialization: {e}")

    apply_security_middleware(bottle.default_app())

    srv = None
    for attempt in range(20):  # just after a restart Windows can hold the port for a few seconds
        try:
            srv = make_server("127.0.0.1", PORT, bottle.default_app(), server_class=ThreadingWSGIServer,
                              handler_class=QuietHandler)
            break
        except OSError as e:
            log_error(f"port {PORT} is busy ({e}), trying again")
            time.sleep(1.5)
    if srv is None:
        print(f"Port {PORT} is busy. Close the other Vega (Task Manager -> python) and try again.")
        sys.exit(1)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    threading.Thread(target=accept_other_instances, daemon=True).start()
    threading.Thread(target=vega.refresh_app_index, daemon=True).start()
    threading.Thread(target=warm_up_ears, daemon=True).start()
    threading.Thread(target=idle_lock_loop, daemon=True).start()

    def _telemetry_stream_loop():
        while True:
            try:
                v = get_hardware_vitals()
                broadcast_event("telemetry", v)
            except Exception:
                pass
            time.sleep(5.0)
    threading.Thread(target=_telemetry_stream_loop, daemon=True, name="TelemetryLoop").start()
    ReminderClock(vega, on_reminder, on_briefing=on_briefing, on_note=on_note).start()
    wake.start()
    wake.set_enabled(bool(cfg.get("wake_word_on", True)))
    global popups
    popups = Popups(on_open=open_window, on_submit=quick_submit, on_mic=hotkey_mic)
    popups.start()
    start_global_hotkeys()
    start_phone()
    log_debug(f"ready on {URL} (locked={S['locked']})")
    open_window()
    run_tray()  # keeps Vega running after the window is closed


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log_error("crash: " + traceback.format_exc())
        with open(os.path.join(BASE_DIR, "vega_error.log"), "a", encoding="utf-8") as f:
            f.write("\n--- Vega 2.0 ---\n" + traceback.format_exc())
        raise
