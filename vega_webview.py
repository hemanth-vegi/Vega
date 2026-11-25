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
Start:  double-click "Start Vega"   or in PowerShell:  cd <your-vega-folder>   then   python vega_webview.py
"""

import os
import re
import sys
if __name__ == "__main__":
    sys.modules["vega_webview"] = sys.modules["__main__"]
import json
import base64
import time
import queue
import socket
import logging
import logging.handlers
import threading
import traceback
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple

try:
    import winreg
except ImportError:
    winreg = None

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
from tools.telemetry import get_hardware_vitals, record_streaming_metric, set_generating_status

IS_WINDOWS = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WINDOWS else 0
IS_HEADLESS = "--headless" in sys.argv or os.environ.get("HEADLESS") == "1" or os.environ.get("VEGA_HEADLESS") == "1"
LOCK_PORT = 47631   # the same "only one Vega" port the old window uses, so the two never run together
PORT = int(os.environ.get("PORT", 47632))        # the web page
BIND_HOST = os.environ.get("HOST", "0.0.0.0" if IS_HEADLESS else "127.0.0.1")
WEB_DIR = os.path.join(BASE_DIR, "web")
URL = f"http://{BIND_HOST}:{PORT}"
APP_URL = f"http://{BIND_HOST}:{PORT}/?window=vega_standalone"
VERSION = "2026-11-25-v8"   # also embedded in the page so stale Edge app windows reload updated frontend code

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

from vega_core import Vega, ReminderClock, save_config_value, here, strip_thinking, EXTRA_PROVIDERS, clean_command, _coerce_text  # noqa: E402
from vega_chats import ChatStore  # noqa: E402
import vega_voice  # noqa: E402
from vega_voice import Mouth, Ears, WakeListener, shorten_for_speech, beep  # noqa: E402
import vega_security as sec  # noqa: E402
from vega_lablog import clean_step  # noqa: E402
from vega_popup import Popups  # noqa: E402
# Agent Mode purged in v2.1
from vega_practice_lab import PracticeLab  # noqa: E402

bottle.BaseRequest.MEMFILE_MAX = 64 * 1024 * 1024  # attached files and multiple screenshots


class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 128

    def handle_error(self, request, client_address):
        """Silently ignore abrupt client disconnects instead of spamming tracebacks."""
        exc_type, exc_val, _ = sys.exc_info()
        if exc_type in (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, socket.error):
            return
        if isinstance(exc_val, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        if exc_val and any(err in str(exc_val).lower() for err in ("broken pipe", "connection reset", "connection aborted")):
            return
        super().handle_error(request, client_address)


class QuietHandler(WSGIRequestHandler):
    def log_message(self, *args):
        pass

    def handle(self):
        """Handle request while quietly suppressing abrupt client disconnect exceptions."""
        try:
            super().handle()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, socket.error):
            pass
        except Exception as exc:
            if any(err in str(exc).lower() for err in ("broken pipe", "connection reset", "connection aborted")):
                pass
            else:
                raise

    def finish(self):
        """Finish request handling while suppressing socket flush errors on client drop."""
        try:
            super().finish()
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError, socket.error):
            pass
        except Exception:
            pass


# ---------------------------------------------------------------- Vega herself
vega = Vega()
cfg = vega.cfg
store = ChatStore()
# agent_tasks purged in v2.1
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
    "locked": sec.lock_on_start() if hasattr(sec, "lock_on_start") else False,
    "last_activity": time.time(),
    "last_verified": 0.0,
    "unlocked_at": 0.0,
    "unlock_grace_until": 0.0,
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
    "/api/health", "/api/settings/persona",
    "/api/settings/english-coach", "/api/settings/english-coach/",
    "/api/settings/network-mode", "/api/settings/network-mode/",
    "/api/settings/lock_on_start", "/api/settings/lock_on_start/",
    "/api/settings/reset_data", "/api/settings/reset_data/",
    "/api/activity", "/api/activity/",
    "/api/orchestrator/status", "/api/orchestrator/status/",
    "/api/jarvis/status", "/api/jarvis/status/",
    "/api/jarvis/tasks", "/api/jarvis/tasks/",
    "/api/voice/mic-arb", "/api/voice/mic-arb/", "/api/voice/sleep", "/api/voice/sleep/",
    "/api/career/github/sync", "/api/career/github/sync/",
    "/api/career/github/repos", "/api/career/github/repos/",
    "/api/career/github/repo/toggle", "/api/career/github/repo/toggle/",
    "/api/career/resume/analyze-jd", "/api/career/resume/analyze-jd/",
    "/api/career/analyze-jd", "/api/career/analyze-jd/",
    "/api/career/resume/generate-tailored", "/api/career/resume/generate-tailored/",
    "/api/career/resume/generate", "/api/career/resume/generate/",
    "/api/career/generate_resume", "/api/career/generate_resume/",
    "/api/career/tailor", "/api/career/tailor/",
    "/api/career/download_pdf", "/api/career/download_pdf/",
    "/api/career/resume/session/start", "/api/career/resume/session/start/",
    "/api/career/resume/session/by-chat", "/api/career/resume/session/by-chat/",
    "/api/career/resume/session/update", "/api/career/resume/session/update/",
    "/api/career/resume/session/export-pdf", "/api/career/resume/session/export-pdf/",
    "/api/career/resume/session/close", "/api/career/resume/session/close/",
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
    # Reset inactivity timestamp on EVERY active API request when unlocked
    # (chat sends, Career clicks, HUD actions, voice commands, and Settings updates)
    if not S["locked"] and request.path.startswith("/api/") and request.path != "/api/events":
        touch()

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
    if not S["locked"]:
        try:
            from voice.friend_voice import is_sleeping
            if is_sleeping():
                return "Silent — Say “Hey Vega”"
        except Exception:
            pass
        if globals().get("_MIC_OWNER") == "hud":
            return "HUD Voice Active"
    return "Locked" if S["locked"] else ("Ready - just talk" if direct_mode() else "Ready - say “Hey Vega”")


_MIC_OWNER: str = "background"
_RECENT_SPOKEN_MESSAGES: dict[str, tuple[float, str]] = {}
_RECENT_SPOKEN_MESSAGES_LOCK = threading.Lock()


def is_duplicate_spoken_message(text: str, source: str, window_seconds: float = 3.0) -> bool:
    """Drop the same normalized spoken phrase when it crosses voice/chat handlers."""
    normalized = re.sub(r"[^\w\s]", " ", (text or "").casefold())
    normalized = re.sub(r"\s+", " ", normalized).strip()
    if not normalized:
        return False

    now = time.monotonic()
    with _RECENT_SPOKEN_MESSAGES_LOCK:
        expired = [phrase for phrase, (timestamp, _) in _RECENT_SPOKEN_MESSAGES.items()
                   if now - timestamp > window_seconds]
        for phrase in expired:
            del _RECENT_SPOKEN_MESSAGES[phrase]

        previous = _RECENT_SPOKEN_MESSAGES.get(normalized)
        if previous and previous[1] != source and now - previous[0] <= window_seconds:
            return True
        _RECENT_SPOKEN_MESSAGES[normalized] = (now, source)
        return False


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
    pref = (cfg.get("browser") or "").lower() if "cfg" in globals() and isinstance(cfg, dict) else ""
    candidates = []
    if pref == "brave":
        candidates.extend([
            r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
            os.path.join(local, r"BraveSoftware\Brave-Browser\Application\brave.exe")
        ])
    elif pref == "chrome":
        candidates.extend([
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            os.path.join(local, r"Google\Chrome\Application\chrome.exe")
        ])

    candidates.extend([
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.join(local, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        os.path.join(local, r"Google\Chrome\Application\chrome.exe")
    ])

    for p in candidates:
        if p and os.path.exists(p):
            return p
    return None


ISOLATED_USER_DATA_DIR = os.path.abspath(os.path.join(BASE_DIR, "app_profile_v3"))
os.makedirs(ISOLATED_USER_DATA_DIR, exist_ok=True)
_vega_browser_pids = set()


def _track_browser_pid(pid):
    if pid:
        _vega_browser_pids.add(pid)


def is_vega_app_window(title: str, pid: int = 0) -> bool:
    """Strictly verify if a window belongs to Vega's app window and NOT another browser tab."""
    if not title:
        return False
    t = title.strip()
    lower = t.lower()
    # Reject third-party or browser tabs that merely mention Vega in conversations or searches
    for blocked in ("gemini", "google search", "bing", "chatgpt", "claude", "copilot", "github", "youtube", "http://", "https://"):
        if blocked in lower:
            return False
    # Check if process is known to be our isolated browser
    if pid and _vega_browser_pids:
        if pid in _vega_browser_pids:
            return True
        try:
            import psutil
            proc = psutil.Process(pid)
            for parent in proc.parents():
                if parent.pid in _vega_browser_pids:
                    _vega_browser_pids.add(pid)
                    return True
            cmd = " ".join(proc.cmdline() or [])
            if "app_profile_v3" in cmd or "vega_standalone" in cmd:
                _vega_browser_pids.add(pid)
                return True
        except Exception:
            pass
    # Exact Vega window title or standard Chromium app-window title pattern
    if t == "Vega":
        return True
    return bool(re.match(r"^Vega\s*[-–—]\s*(?:Microsoft\s+Edge|Brave|Google\s+Chrome|Edge|Chromium)$", t, re.IGNORECASE))


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
            if not user32.IsWindowVisible(hwnd):
                return True
            n = user32.GetWindowTextLengthW(hwnd)
            if 0 < n < 80:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if is_vega_app_window(title, pid.value):
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


def wait_for_server_ready(host=BIND_HOST, port=PORT, timeout=5.0) -> bool:
    """Ensure the WSGI server is bound and responding over HTTP before launching the browser."""
    import urllib.request
    start = time.time()
    url = f"http://{host}:{port}/"
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(url, timeout=0.5) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            time.sleep(0.05)
    return False


def _launch_browser_app(browser):
    """Launch Vega in an Edge / Brave / Chrome app window with strict profile isolation."""
    wait_for_server_ready(BIND_HOST, PORT, timeout=5.0)
    args = [
        f"--app={APP_URL}",
        f"--user-data-dir={ISOLATED_USER_DATA_DIR}",
        "--new-window",
        "--window-size=1280,840",
        "--class=VegaStandaloneApp",
        "--app-id=VegaLocalStandaloneApp",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-features=Translate",
        "--disable-component-update",
        "--disable-session-crashed-bubble",
        "--enable-media-stream",
        "--autoplay-policy=no-user-gesture-required",
    ]
    if IS_WINDOWS:
        try:
            proc = subprocess.Popen([browser, *args])
            _track_browser_pid(proc.pid)
        except Exception:
            os.startfile(browser, "open", subprocess.list2cmdline(args), cwd=BASE_DIR, show_cmd=1)
    else:
        proc = subprocess.Popen([browser, *args])
        _track_browser_pid(proc.pid)


def _fallback_check_window():
    """Restore the app window if Edge starts it minimized; report if it stays hidden."""
    time.sleep(3.5)
    if not bring_window_to_front():
        log_error("Vega's app window did not appear; opening in default browser fallback.")
        try:
            import webbrowser
            webbrowser.open(APP_URL)
        except Exception:
            pass


_window_launch_lock = threading.Lock()


def open_window():
    """Open Vega or focus an existing window with strict profile isolation."""
    if bring_window_to_front():
        return
    with _window_launch_lock:
        if time.time() - S["last_window_open"] < 4:
            return
        S["last_window_open"] = time.time()
    browser = find_browser_exe()
    if not browser:
        log_error("No supported browser executable found; opening default browser.")
        try:
            import webbrowser
            webbrowser.open(APP_URL)
        except Exception:
            pass
        return
    try:
        _launch_browser_app(browser)
        log_debug(f"window opened with {os.path.basename(browser)} (isolated profile app_profile_v3)")
        threading.Thread(target=_fallback_check_window, daemon=True).start()
    except Exception as e:
        log_error(f"launch window: {e}; falling back to default browser")
        try:
            import webbrowser
            webbrowser.open(APP_URL)
        except Exception:
            pass


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
def lock_now(force=False):
    if not sec.has_pin():
        notice("Set a PIN first: Settings → PIN and lock.", "warn")
        return False
    now = time.time()
    if not force and now < S.get("unlock_grace_until", 0.0):
        log_debug("lock_now suppressed by unlock grace period")
        return False
    S["unlock_grace_until"] = 0.0
    if not S["locked"]:
        S["locked"] = True
        vega.is_locked = True
        mouth.stop()
        if S["mic_stop"]:
            S["mic_stop"].set()
        log_debug("locked")
    broadcast_event("lock", {
        "locked": True,
        "voice_unlock_ready": sec.voice_unlock_ready(),
        "voice_reason": voice_unlock_reason(),
    })
    status("Locked", "locked")
    return True


def unlock(how=""):
    now = time.time()
    S["locked"] = False
    vega.is_locked = False
    S["pin_fails"] = 0
    S["last_verified"] = S["last_activity"] = now
    S["unlocked_at"] = now
    S["unlock_grace_until"] = now + 120.0  # 2-minute unlock grace period
    if how == "voice":
        S["voice_ok_at"] = now
    broadcast_event("unlock", {"how": how})
    status(ready_text(), "ready")
    log_debug(f"unlocked ({how})")
    if S["pending_briefing"]:
        S["pending_briefing"] = False
        threading.Timer(1.5, on_briefing).start()


def idle_lock_loop():
    while True:
        time.sleep(10)
        try:
            mins = sec.lock_minutes()
            timeout_minutes = max(15, mins) if mins > 0 else 0
            now = time.time()
            if (sec.has_pin() and timeout_minutes > 0 and not S["locked"] and not S["busy"]):
                if now < S.get("unlock_grace_until", 0.0):
                    continue
                if now - S.get("last_activity", now) > timeout_minutes * 60:
                    lock_now(force=False)
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
    """'Just talk' (no 'Hey Vega'): only if turned on, window in front, not locked, not playing, not on call, not in HUD/TTS mode, not during/after TTS, and not sleeping."""
    global _MIC_OWNER
    if "_MIC_OWNER" in globals() and _MIC_OWNER in ("hud", "tts"):
        return False
    if mouth.busy() or (time.time() - S.get("last_spoke", 0.0) < 1.0):
        return False
    try:
        from voice.friend_voice import is_sleeping
        if is_sleeping():
            return False
    except Exception:
        pass
    return (bool(cfg.get("talk_without_wake", True)) and not in_background() and not S["locked"]
            and not media_playing() and not on_a_call())


def wake_busy():
    """True = don't listen for "Hey Vega" right now."""
    if mouth.busy():
        S["last_spoke"] = time.time()
    return bool(S["busy"] or S["listening"] or mouth.busy() or time.time() - S["last_spoke"] < 1.2
                or on_a_call())


def wake_voice_score(audio):
    """How much "Hey Vega" sounds like the owner (used by the wake listener to ignore TV, videos and other people)."""
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
    broadcast_event("wake_word_detected", {})
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
        user_name = os.environ.get("VEGA_USER_NAME", "")
        welcome_say = f"Welcome back, {user_name}." if user_name else "Welcome back."
        mouth.say(welcome_say)
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

    if globals().get("_MIC_OWNER") == "hud":
        return

    if is_duplicate_spoken_message(text, "voice"):
        log_debug("duplicate spoken command ignored by voice handler")
        return

    # Check for Silent / Sleep commands and sleep state gate
    try:
        from voice.friend_voice import is_sleeping, is_hey_vega_phrase, is_sleep_phrase, set_sleep_state
        if is_sleep_phrase(text):
            mouth.stop()
            vega.stop()
            set_sleep_state(True)
            status("Silent — Say “Hey Vega”", "ready")
            ack = "Going silent. Say 'Hey Vega' whenever you need me."
            broadcast_event("voice_sleep", {"sleeping": True, "text": ack})
            mouth.say(ack)
            return
        if is_sleeping():
            if is_hey_vega_phrase(text):
                set_sleep_state(False)
                status("Awake and listening", "ready")
                broadcast_event("voice_sleep", {"sleeping": False})
                mouth.say("I'm awake and listening.")
            return  # Sleep mode active: ignore ambient talk
    except Exception:
        pass

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
                    if re.search(r"\b(unlock|it'?s me|it is me)\b", text.lower()):
                        user_name = os.environ.get("VEGA_USER_NAME", "")
                        welcome_say = f"Welcome back, {user_name}." if user_name else "Welcome back."
                        mouth.say(welcome_say)
                    else:
                        run_command(text, spoken=True)
                else:
                    lock_msg(f"That didn't match your voice closely enough ({score:.2f}, needs "
                             f"{sec.unlock_threshold():.2f}). Type your PIN.")
                    mouth.say("I need your PIN to unlock the system first.")
            threading.Thread(target=check, daemon=True).start()
        else:
            lock_msg("I'm locked. Type your PIN to unlock me.")
            pop("Vega is locked", "Open Vega and type your PIN.", 6)
            mouth.say("I need your PIN to unlock the system first.")
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

        def gate():  # "Only obey my voice" is on: check it's the owner before doing anything
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
        threading.Timer(1.2, lambda: lock_now(force=True)).start()
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
        user_p = _coerce_text(prompt_text or getattr(vega, "_last_user_prompt", "") or "").lower()
        is_explain_query = bool(re.search(
            r"\b(explain(?:ing)?|in your (?:own )?words|full chat|everything|detail|walk me through|break (?:it )?down)\b",
            user_p
        ))
        full = bool(out.get("full_speech")) or is_explain_query if isinstance(out, dict) else False
        speech_text = _coerce_text(out.get("say") if isinstance(out, dict) else None) or _coerce_text(out.get("text") if isinstance(out, dict) else out)
        language = (out.get("voice_lang") if isinstance(out, dict) else None) or cfg.get("voice_language", "auto")
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
                chat_model = cfg.get("chat_model") if isinstance(cfg.get("chat_model"), dict) else {}
                provider = chat_model.get("provider") or None
                model = chat_model.get("model") or None
                out = vega.handle(text, source="laptop", spoken=True,
                                  preferred_provider=provider, preferred_model=model, mode="chat")
            chat = add_to_chat("you", text)
            resp_text = _coerce_text(out.get("text", "") if isinstance(out, dict) else out)
            store.add(chat, "vega", resp_text)
            broadcast_event("voice_response", {"text": resp_text, "source_label": out.get("source_label", "Vega") if isinstance(out, dict) else "Vega",
                                               "chat_id": chat["id"], "open_tab": "lablog" if isinstance(out, dict) and out.get("open_lablog") else None})
            speak(out, prompt_text=text)
            pop("Vega", resp_text, 12)
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
vega_voice.subscribe_wake_event(lambda ev: broadcast_event(ev.get("type", "wake_word_detected"), ev.get("data", {})))


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
    user_name = os.environ.get("VEGA_USER_NAME", "")
    rem_prompt = f"{user_name}, you have a reminder. Unlock me to see it." if user_name else "You have a reminder. Unlock me to see it."
    rem_unlocked = f"{user_name}, reminder: {text}" if user_name else f"Reminder: {text}"
    mouth.say(rem_prompt if S["locked"] else rem_unlocked)
    if phone:
        phone.send(f"Reminder: {text}")


def lazy_load_daily_briefing():
    """
    Startup lazy-load check: checks SQLite chats table for today's date.
    If missing, silently fetches the daily hub briefing, writes it to messages & chats, and stops.
    Zero background polling overhead.
    """
    try:
        from storage.db import get_db, save_chat, save_message
        today_date = datetime.now().strftime("%Y-%m-%d")
        today_title_part = datetime.now().strftime("%d %b")
        
        with get_db() as conn:
            row = conn.execute("""
                SELECT id FROM chats 
                WHERE (title LIKE ? OR created_at LIKE ?)
            """, (f"%{today_title_part}%", f"{today_date}%")).fetchone()
            
            if row:
                log_debug(f"Daily briefing already present for today ({today_date}), skipping.")
                return

        log_debug(f"Fetching lazy-load daily briefing for {today_date}...")
        briefing_data = vega.morning_briefing()
        briefing_text = briefing_data.get("text") if isinstance(briefing_data, dict) else str(briefing_data)
        
        if briefing_text:
            chat_id = datetime.now().strftime("%Y%m%d%H%M%S") + "_briefing"
            title = f"Morning briefing, {today_title_part}"
            save_chat(chat_id, title=title, mode="chat")
            save_message(chat_id, "vega", briefing_text, mode="chat")
            log_debug(f"Lazy-load daily briefing successfully stored in chat {chat_id}.")
    except Exception as e:
        log_error(f"lazy_load_daily_briefing error: {e}")


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
        response.set_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        response.set_header("Pragma", "no-cache")
        return page
    except OSError:
        resp = static_file("index.html", root=WEB_DIR)
        resp.set_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        resp.set_header("Pragma", "no-cache")
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
                if S["locked"] and ev.get("type") not in {"lock", "unlock", "status", "lock_msg"}:
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
            "lock_on_start": sec.lock_on_start(),
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
        "listening_on": bool(cfg.get("wake_word_on", True)) or bool(cfg.get("talk_without_wake", True)),
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
        "lock_on_start": sec.lock_on_start(),
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
    data = body() if request.method == "POST" else {}
    chat_mode = data.get("mode") or "chat"
    chat = store.new(mode=chat_mode)
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
        clear_cached_chat_images(cid)
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


# Session-based image memory: remembers the most recent images per chat_id for follow-ups
_RECENT_CHAT_IMAGES: Dict[str, Dict[str, Any]] = {}
_IMAGE_MEMORY_TTL_SECONDS = 1800  # 30 minutes retention


def cache_chat_images(chat_id: str, images: List[Dict[str, Any]]) -> None:
    """Cache recent image attachments for this chat session to allow follow-up questions."""
    if not chat_id or not images:
        return
    _RECENT_CHAT_IMAGES[str(chat_id)] = {
        "images": list(images),
        "timestamp": time.time()
    }


def get_cached_chat_images(chat_id: str) -> List[Dict[str, Any]]:
    """Retrieve unexpired cached image attachments for this chat session."""
    if not chat_id or str(chat_id) not in _RECENT_CHAT_IMAGES:
        return []
    entry = _RECENT_CHAT_IMAGES[str(chat_id)]
    if time.time() - entry.get("timestamp", 0) > _IMAGE_MEMORY_TTL_SECONDS:
        _RECENT_CHAT_IMAGES.pop(str(chat_id), None)
        return []
    return list(entry.get("images", []))


def clear_cached_chat_images(chat_id: str) -> None:
    _RECENT_CHAT_IMAGES.pop(str(chat_id), None)


def parse_chat_attachments(data: dict, current_text: str) -> Tuple[List[Dict[str, Any]], str]:
    """
    Normalizes incoming attachments from:
      - data['images']: list of dicts, strings, or paths
      - data['image_b64'] / data['image']: single base64 string or data URL
      - data['image_path']: single file path
      - data['attachments']: list of attachment dicts (from frontend or API)

    For PDF attachments:
      - Automatically extracts text via tools.pdf_reader.extract_pdf.
      - If searchable text is found, injects the document text into prompt context.
      - If scanned/image-only (or < 50 chars), renders the first 1-3 pages to PNG images
        using PyMuPDF (fitz) and adds them directly to the image attachments list for multimodal vision.

    Returns:
      (images_list, updated_text)
    """
    images_out: List[Dict[str, Any]] = []
    updated_text = (current_text or "").strip()

    raw_items = []
    imgs_field = data.get("images")
    if isinstance(imgs_field, list):
        raw_items.extend(imgs_field)
    elif imgs_field:
        raw_items.append(imgs_field)

    for k in ("image_b64", "image"):
        val = data.get(k)
        if val:
            raw_items.append(val)

    img_path = data.get("image_path")
    if img_path:
        raw_items.append({"path": img_path})

    atts = data.get("attachments")
    if isinstance(atts, list):
        raw_items.extend(atts)
    elif atts:
        raw_items.append(atts)

    for item in raw_items:
        if not item:
            continue
        name = "attachment"
        mime = ""
        raw_data = ""
        file_path = ""

        if isinstance(item, dict):
            name = item.get("name") or "attachment"
            mime = item.get("mime_type") or item.get("mimeType") or ""
            raw_data = item.get("data") or item.get("dataUrl") or item.get("b64") or item.get("image_b64") or ""
            file_path = item.get("path") or item.get("image_path") or item.get("file_path") or ""
        elif isinstance(item, str):
            if os.path.exists(item):
                file_path = item
                name = os.path.basename(item)
            else:
                raw_data = item
        elif isinstance(item, bytes):
            if item.startswith(b"%PDF"):
                file_path = ""
                raw_data = base64.b64encode(item).decode("utf-8")
                mime = "application/pdf"
            else:
                images_out.append({
                    "name": "attachment.png",
                    "mime_type": "image/png",
                    "data": base64.b64encode(item).decode("utf-8")
                })
                continue

        pdf_bytes = None
        if file_path and os.path.exists(file_path):
            try:
                with open(file_path, "rb") as f_in:
                    f_bytes = f_in.read()
                if file_path.lower().endswith(".pdf") or f_bytes.startswith(b"%PDF"):
                    pdf_bytes = f_bytes
                    name = os.path.basename(file_path)
                else:
                    b64_str = base64.b64encode(f_bytes).decode("utf-8")
                    if not mime:
                        ext = os.path.splitext(file_path)[1].lower()
                        mime = "image/jpeg" if ext in (".jpg", ".jpeg") else ("image/webp" if ext == ".webp" else "image/png")
                    images_out.append({
                        "name": name,
                        "mime_type": mime,
                        "data": b64_str
                    })
                    continue
            except Exception as e:
                log_error(f"Failed to read attachment file {file_path}: {e}")
                continue

        if not pdf_bytes and raw_data:
            clean_b64 = raw_data
            if "," in clean_b64:
                header, clean_b64 = clean_b64.split(",", 1)
                if "application/pdf" in header or "pdf" in header:
                    mime = "application/pdf"
                elif "image/" in header:
                    m = re.search(r"image/([a-zA-Z0-9_\-]+)", header)
                    if m:
                        mime = f"image/{m.group(1)}"
            clean_b64 = clean_b64.strip()

            is_pdf = (
                mime == "application/pdf" or
                name.lower().endswith(".pdf") or
                clean_b64.startswith("JVBERi0")
            )

            if is_pdf:
                try:
                    pdf_bytes = base64.b64decode(clean_b64)
                except Exception as b64_err:
                    log_error(f"Failed to decode PDF base64: {b64_err}")
            else:
                if not mime:
                    mime = "image/png"
                images_out.append({
                    "name": name,
                    "mime_type": mime,
                    "data": clean_b64
                })

        # Process PDF attachment if present
        if pdf_bytes:
            try:
                from tools.pdf_reader import extract_pdf
                res = extract_pdf(pdf_bytes)
                is_scanned = bool(res.get("is_scanned", False) or len(res.get("text", "").strip()) < 50)
                if not is_scanned and res.get("text", "").strip():
                    doc_text = res["text"].strip()
                    doc_header = f"[Attached PDF Document: {name} ({res.get('pages', 1)} pages)]\n{doc_text}\n"
                    updated_text = (doc_header + "\n" + updated_text).strip()
                else:
                    # Scanned or image-only PDF: render first 1-3 pages via PyMuPDF fitz
                    try:
                        import pymupdf as fitz
                    except ImportError:
                        try:
                            import fitz
                        except ImportError:
                            fitz = None
                    rendered_count = 0
                    if fitz:
                        try:
                            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                            for p_idx in range(min(3, len(doc))):
                                page = doc[p_idx]
                                pix = page.get_pixmap(dpi=150)
                                png_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
                                images_out.append({
                                    "name": f"{name}_page_{p_idx+1}.png",
                                    "mime_type": "image/png",
                                    "data": png_b64
                                })
                                rendered_count += 1
                            doc.close()
                        except Exception as render_err:
                            log_error(f"Failed to render scanned PDF pages: {render_err}")
                    if rendered_count == 0 and res.get("text"):
                        updated_text = (f"[Attached PDF Document: {name}]\n{res['text']}\n\n" + updated_text).strip()
            except Exception as pe:
                log_error(f"Error extracting PDF attachment {name}: {pe}")

    return images_out, updated_text


def sse_line(obj):
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@route("/api/send", method="POST")
@route("/api/chat", method="POST")
def send_message():
    data = body()
    text_value = _coerce_text(data.get("text") or "")
    shown_value = _coerce_text(data.get("shown") or text_value)
    target_chat_id = str(data.get("chat_id") or active_chat_id)

    if not isinstance(text_value, str) or not isinstance(shown_value, str):
        response.content_type = "text/event-stream"
        yield sse_line({"done": True, "text": "Message text or attachments have an invalid format.",
                        "source_label": "Error"})
        return

    images, text = parse_chat_attachments(data, text_value)
    shown = shown_value.strip()

    # Session-based follow-up image memory:
    # If no new images attached this turn, check if recent images exist for this chat_id
    if images:
        cache_chat_images(target_chat_id, images)
    else:
        cached_imgs = get_cached_chat_images(target_chat_id)
        if cached_imgs:
            from vega_control import is_system_command
            if not is_system_command(clean_command(text)):
                images = list(cached_imgs)

    spoken = bool(data.get("spoken"))
    surface = str(data.get("surface") or "").strip().lower()
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
    if spoken and is_duplicate_spoken_message(text, "chat"):
        yield sse_line({"done": True, "text": "", "source_label": "Duplicate voice input", "nosave": True})
        return
    touch()
    if S["locked"]:
        lock_now(force=True)
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

    # Deterministic Sleep / Wake command interception before LLM
    try:
        from voice.friend_voice import is_sleeping, is_sleep_phrase, is_hey_vega_phrase, set_sleep_state
        if is_sleep_phrase(text) and not images:
            vega.stop()
            mouth.stop()
            set_sleep_state(True)
            ack_msg = "Going silent. Say 'Hey Vega' whenever you need me."
            chat_mode = data.get("mode") or "chat"
            chat = chat_for(data.get("chat_id") or active_chat_id)
            store.add(chat, "you", shown, mode=chat_mode)
            store.add(chat, "vega", ack_msg, mode=chat_mode)
            broadcast_event("voice_sleep", {"sleeping": True, "text": ack_msg})
            if spoken:
                mouth.say(ack_msg)
            status("Silent — Say “Hey Vega”", "ready")
            yield sse_line({"done": True, "text": ack_msg, "source_label": "Vega", "sleeping": True})
            return
        if is_sleeping() and spoken:
            if is_hey_vega_phrase(text):
                set_sleep_state(False)
                ack_msg = "I'm awake and listening."
                chat_mode = data.get("mode") or "chat"
                chat = chat_for(data.get("chat_id") or active_chat_id)
                store.add(chat, "you", shown, mode=chat_mode)
                store.add(chat, "vega", ack_msg, mode=chat_mode)
                broadcast_event("voice_sleep", {"sleeping": False})
                mouth.say(ack_msg)
                status(ready_text(), "ready")
                yield sse_line({"done": True, "text": ack_msg, "source_label": "Vega", "sleeping": False})
                return
            else:
                yield sse_line({"done": True, "text": "", "source_label": "Sleeping", "nosave": True})
                return
    except Exception as exc:
        log_error(f"Sleep phrase check error: {exc}")

    # Jarvis background workspace/DevOps task delegation
    try:
        from core.jarvis_bridge import get_jarvis_bridge
        jb = get_jarvis_bridge()
        jarvis_intent = jb.detect_intent(text) if not images else None
        if jarvis_intent and jarvis_intent.get("is_jarvis_task"):
            reply_msg = jarvis_intent["reply_text"]
            chat_mode = data.get("mode") or "chat"
            chat = chat_for(data.get("chat_id") or active_chat_id)
            store.add(chat, "you", shown, mode=chat_mode)
            store.add(chat, "vega", reply_msg, mode=chat_mode)
            if spoken:
                mouth.say(reply_msg)
            if jarvis_intent.get("needs_clarification"):
                yield sse_line({"done": True, "text": reply_msg, "source_label": "Vega"})
                return
            task = jb.create_task(
                prompt=jarvis_intent["task_prompt"],
                workspace=jarvis_intent["workspace"],
                mode=jarvis_intent["mode"]
            )
            yield sse_line({
                "done": True,
                "text": reply_msg,
                "source_label": "Vega",
                "jarvis_task": task
            })
            return
    except Exception as exc:
        log_error(f"Jarvis intent check error: {exc}")

    if S["busy"]:
        vega.stop()
        time.sleep(0.3)

    chat_mode = data.get("mode") or "chat"
    chat = chat_for(data.get("chat_id") or active_chat_id)
    chat["mode"] = chat_mode
    store.add(chat, "you", shown, mode=chat_mode)

    # Hybrid Brain routing & VRAM pre-flight check
    vram_warning = None
    if preferred_provider in ("ollama", "local") or (not preferred_provider and cfg.get("private_mode", False)):
        from tools.telemetry import check_vram_preflight
        vram_check = check_vram_preflight(1.2)
        if not vram_check["ok"]:
            vram_warning = vram_check["warning"]
            # Fall back to cloud Gemini if available, avoiding local OOM
            if not cfg.get("private_mode", False) and (cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY")):
                preferred_provider = "gemini"

    # Route Work Mode code-editing queries to Gemini (Cloud)
    if chat_mode == "work":
        if not preferred_provider and not cfg.get("private_mode", False):
            preferred_provider = "gemini"

    target_chat_id = data.get("chat_id") or active_chat_id
    active_resume_sess = None
    try:
        from career.resume_session import (
            get_resume_session_by_chat, build_resume_system_instruction,
            extract_and_apply_patch, start_resume_session
        )
        active_resume_sess = get_resume_session_by_chat(target_chat_id)
        if not active_resume_sess and any(phrase in text.lower() for phrase in [
            "build a resume", "build my resume", "make my resume", "create my resume", "tailor my resume", "co-build resume"
        ]):
            m_for = re.search(r"(?:for|at)\s+([A-Za-z0-9_\-\s]+)", text, re.IGNORECASE)
            target_role_or_co = m_for.group(1).strip() if m_for else "DevOps Engineer"
            start_resume_session(role=target_role_or_co, company="", goal=text)
            active_resume_sess = get_resume_session_by_chat(target_chat_id)
    except Exception as e:
        log_error(f"Resume session check error: {e}")

    q = queue.Queue()

    def worker():
        S["busy"] = True
        set_generating_status(True)
        try:
            if OPEN_SELF.match(text):
                out = {"text": "I'm right here. 🙂", "say": "I'm right here."}
            else:
                ears.hint, ears.hotwords = vega.speech_hint(), vega.speech_hotwords()
                actual_prompt = text
                if chat_mode == "work":
                    from llm.engine import AIDER_SYSTEM_INSTRUCTIONS
                    if "SEARCH/REPLACE block format" not in actual_prompt:
                        actual_prompt = f"{AIDER_SYSTEM_INSTRUCTIONS}\n\nTask instructions:\n{text}"
                if active_resume_sess:
                    resume_sys = build_resume_system_instruction(active_resume_sess["session_id"])
                    if resume_sys:
                        actual_prompt = f"{resume_sys}\n\nUser request:\n{actual_prompt}"
                out = vega.handle(actual_prompt, source="laptop", on_token=lambda full: q.put(("token", full)), spoken=spoken, images=images, preferred_provider=preferred_provider, preferred_model=preferred_model, mode=chat_mode)
            q.put(("done", out))
        except Exception as e:
            log_error(f"vega.handle error: {e}\n{traceback.format_exc()}")
            q.put(("error", str(e)))

    t = threading.Thread(target=worker, daemon=True)
    t.start()
    status("Thinking…", "thinking")
    last_ping = time.time()
    stream_start_t = time.time()
    token_count = 0
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
                token_count += 1
                elapsed = max(0.05, time.time() - stream_start_t)
                current_speed = token_count / elapsed
                record_streaming_metric(current_speed, is_generating=True)
                yield sse_line({"token": True, "full": strip_thinking(_coerce_text(val)), "source_label": vega_source(), "tok_s": round(current_speed, 1)})
            elif kind == "done":
                ans = _coerce_text(val.get("text", "") if isinstance(val, dict) else val)
                if isinstance(val, dict):
                    val["text"] = ans
                applied_patch = None
                updated_draft = None
                try:
                    clean_ans, applied_patch = extract_and_apply_patch(ans, target_chat_id)
                    if applied_patch:
                        ans = clean_ans
                        val["text"] = clean_ans
                        sess_now = get_resume_session_by_chat(target_chat_id)
                        if sess_now:
                            updated_draft = sess_now.get("draft")
                            broadcast_event("resume_session_updated", {
                                "session_id": sess_now["session_id"],
                                "patch": applied_patch,
                                "draft": updated_draft
                            })
                except Exception as patch_exc:
                    log_error(f"Resume patch application error: {patch_exc}")

                store.add(chat, "vega", ans, mode=chat_mode)
                if surface != "orb":
                    speak(val, prompt_text=text)
                after_answer(val, spoken)
                from llm.engine import parse_aider_diff
                diff_data = parse_aider_diff(ans) if chat_mode == "work" else None
                yield sse_line({"done": True, "text": ans, "source_label": val.get("source_label") or "Vega",
                                "model": val.get("model", ""), "vram_warning": vram_warning,
                                "diff_proposal": diff_data,
                                "resume_patch": applied_patch,
                                "resume_draft": updated_draft,
                                "open_tab": "lablog" if val.get("open_lablog") else None,
                                "lock": bool(val.get("lock_app"))})
                break
            else:
                store.add(chat, "vega", f"Something went wrong: {val}", mode=chat_mode)
                yield sse_line({"done": True, "text": f"Something went wrong: {val}", "source_label": "Error"})
                break
    finally:
        set_generating_status(False)
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
    ok = lock_now(force=True)
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
            user_name = os.environ.get("VEGA_USER_NAME", "")
            welcome_say = f"Welcome back, {user_name}." if user_name else "Welcome back."
            mouth.say(welcome_say)
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


@route("/api/settings/lock_on_start", method=["GET", "POST", "OPTIONS"])
@route("/api/settings/lock_on_start/", method=["GET", "POST", "OPTIONS"])
def settings_lock_on_start_endpoint():
    if request.method == "OPTIONS":
        return as_json({"ok": True})
    if request.method == "POST":
        data = body() or {}
        val = bool(data.get("lock_on_start", False))
        sec.set_lock_on_start(val)
        return as_json({"ok": True, "lock_on_start": sec.lock_on_start()})
    return as_json({"ok": True, "lock_on_start": sec.lock_on_start()})


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
                 "speaker_verification_enforce", "wake_strictness", "voice_language", "telugu_model", "chat_model"}


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
    if k == "chat_model":
        if not isinstance(v, dict):
            return as_json({"ok": False, "error": "Chat model must include provider and model."})
        provider = str(v.get("provider") or "").strip()
        model = str(v.get("model") or "").strip()
        model_id = str(v.get("id") or "").strip()
        valid_providers = {"auto", "groq", "gemini", "ollama", "local"}
        if provider not in valid_providers or not model or not model_id:
            return as_json({"ok": False, "error": "Unsupported chat model selection."})
        save_config_value("chat_model", v)
        cfg["chat_model"] = v
        broadcast_event("model_changed", {"id": model_id, "provider": provider, "model": model})
        return as_json({"ok": True, "chat_model": v})
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


@route("/api/health", method="GET")
def health_endpoint():
    """Health check endpoint for Docker container and monitoring."""
    return as_json({"status": "ok", "version": VERSION, "headless": IS_HEADLESS})


@route("/api/settings/network-mode", method=["GET", "POST"])
def settings_network_mode_endpoint():
    """Gets or sets persistent network mode: 'auto', 'low_data', or 'offline_only'."""
    from career.db import get_network_mode, set_network_mode
    from llm.engine import is_offline_or_low_data
    if bottle.request.method == "POST":
        data = body() or {}
        mode = data.get("mode") or data.get("network_mode") or "auto"
        set_network_mode(mode)
    current_mode = get_network_mode()
    restricted, effective_state = is_offline_or_low_data()
    return as_json({
        "status": "ok",
        "network_mode": current_mode,
        "restricted": restricted,
        "effective_state": effective_state
    })


@route("/api/settings/reset_data", method=["POST", "OPTIONS"])
@route("/api/settings/reset_data/", method=["POST", "OPTIONS"])
def settings_reset_data_endpoint():
    """Wipes all runtime databases, vaults, memories, voiceprints and logs for clean-slate fresh installs."""
    if bottle.request.method == "OPTIONS":
        return as_json({"ok": True})
    try:
        from scripts.reset_vega_data import reset_all_data
        result = reset_all_data(interactive=False, verbose=False)
        return as_json(result)
    except Exception as e:
        return as_json({"status": "error", "message": str(e)}, code=500)


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
    vm_user = os.environ.get("VEGA_VM_USER", "user")
    if tid == "linux":
        learn = vega.learn_for(i) or {}
        topics = "; ".join(t.get("name", "") for t in learn.get("topics", []))
        where = f"They practise on a Linux VM in VirtualBox (hostname server1, user {vm_user}). "
    else:
        topics = d.get("topic", "")
        where = {"python": "They practise Python 3 on Windows and on the Linux VM. ",
                 "c": f"They write C with vi and compile with gcc on the Linux VM (hostname server1, user {vm_user}). "
                 }.get(tid, "")
    notes = e.get("notes", "").strip()
    user_name = os.environ.get("VEGA_USER_NAME", "the student")
    return (f"You are Vega, helping {user_name} inside the Lab Log with ONE lesson of the {name} study plan. Everything you say "
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
        return as_json({"ok": True, "review": strip_thinking(_coerce_text(text)).strip(), "score": score})
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
        raw_ans = vega.ask_brain(msgs, temperature=0.4, hard=True, images=images)
        answer = strip_thinking(_coerce_text(raw_ans)).strip()
    except Exception as e:
        answer = f"I couldn't answer ({e}). Check your internet, or open the Ollama app."
    return as_json({"ok": True, "shown": shown, "answer": answer or "…"})


# ---- shortcuts
def startup_shortcut_path():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "Vega.lnk")


def desktop_folder():
    if winreg is not None:
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
                return os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0])
        except OSError:
            pass
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


# ---------------------------------------------------------------- Work Mode Workspace Endpoints
WORKSPACE_ROOT = os.path.abspath(BASE_DIR)
WORKSPACE_IGNORE_DIRS = {
    ".git", "__pycache__", ".venv", "venv", "node_modules", ".continue",
    "backup_before_upgrades", "gemini_changes", "Vega-update-3", "Vega-update-37 (1)",
    "app_profile_v3", "app_profile_isolated"
}
WORKSPACE_IGNORE_EXTS = {".pyc", ".pyd", ".exe", ".bin", ".dll", ".so", ".zip", ".tar", ".gz", ".iso"}


def _safe_ws_path(rel_path: str) -> Optional[str]:
    """Ensures requested path is safely resolved inside WORKSPACE_ROOT."""
    if not rel_path:
        return None
    clean = rel_path.replace("\\", "/").lstrip("/")
    full = os.path.abspath(os.path.join(WORKSPACE_ROOT, clean))
    if full == WORKSPACE_ROOT or full.startswith(WORKSPACE_ROOT + os.sep):
        return full
    return None


@route("/api/workspace/tree", method="GET")
def workspace_tree():
    """Return file tree hierarchy of WORKSPACE_ROOT."""
    def build_tree(dir_path, depth=0, max_depth=4):
        items = []
        if depth > max_depth:
            return items
        try:
            with os.scandir(dir_path) as entries:
                for entry in sorted(entries, key=lambda e: (not e.is_dir(), e.name.lower())):
                    if entry.name.startswith(".") and entry.name != ".env":
                        continue
                    if entry.is_dir():
                        if entry.name in WORKSPACE_IGNORE_DIRS:
                            continue
                        rel = os.path.relpath(entry.path, WORKSPACE_ROOT).replace("\\", "/")
                        items.append({
                            "name": entry.name,
                            "path": rel,
                            "type": "directory",
                            "children": build_tree(entry.path, depth + 1, max_depth)
                        })
                    elif entry.is_file():
                        _, ext = os.path.splitext(entry.name)
                        if ext.lower() in WORKSPACE_IGNORE_EXTS:
                            continue
                        rel = os.path.relpath(entry.path, WORKSPACE_ROOT).replace("\\", "/")
                        try:
                            size = entry.stat().st_size
                        except Exception:
                            size = 0
                        items.append({
                            "name": entry.name,
                            "path": rel,
                            "type": "file",
                            "size": size
                        })
        except Exception as e:
            log_error(f"workspace_tree scan error: {e}")
        return items

    tree = build_tree(WORKSPACE_ROOT)
    return as_json({"ok": True, "root": WORKSPACE_ROOT, "tree": tree})


@route("/api/workspace/file", method="GET")
def workspace_file_read():
    """Read contents of a file within the workspace."""
    rel = request.query.get("path", "").strip()
    full = _safe_ws_path(rel)
    if not full or not os.path.isfile(full):
        response.status = 404
        return as_json({"ok": False, "error": f"File not found or access denied: {rel}"})
    try:
        size = os.path.getsize(full)
        if size > 3 * 1024 * 1024:  # 3MB limit for editor
            return as_json({"ok": False, "error": f"File is too large ({round(size / 1024)} KB) to edit directly."})
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return as_json({"ok": True, "path": rel, "content": content, "size": size})
    except Exception as e:
        response.status = 500
        return as_json({"ok": False, "error": f"Failed to read file: {e}"})


@route("/api/workspace/save", method="POST")
def workspace_file_save():
    """Save content to a file within the workspace."""
    data = body()
    rel = str(data.get("path", "")).strip()
    content = data.get("content")
    if not rel or content is None:
        response.status = 400
        return as_json({"ok": False, "error": "Path and content are required."})
    full = _safe_ws_path(rel)
    if not full:
        response.status = 403
        return as_json({"ok": False, "error": "Access denied outside workspace."})
    try:
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(content)
        return as_json({"ok": True, "path": rel, "bytes_written": len(content)})
    except Exception as e:
        response.status = 500
        return as_json({"ok": False, "error": f"Failed to save file: {e}"})


@route("/api/workspace/apply_diff", method="POST")
def workspace_apply_diff():
    """Applies Aider-style fuzzy SEARCH/REPLACE block to a file."""
    data = body()
    rel = str(data.get("path", "")).strip()
    search_block = str(data.get("search", ""))
    replace_block = str(data.get("replace", ""))
    full = _safe_ws_path(rel)
    if not full or not os.path.isfile(full):
        response.status = 404
        return as_json({"ok": False, "error": f"File not found: {rel}"})

    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            original = f.read()

        applied = False
        new_content = original
        strategy = ""

        # 1. Exact match
        if search_block in original:
            new_content = original.replace(search_block, replace_block, 1)
            applied = True
            strategy = "exact"
        else:
            # 2. Normalized newline match
            norm_orig = original.replace("\r\n", "\n")
            norm_search = search_block.replace("\r\n", "\n")
            norm_replace = replace_block.replace("\r\n", "\n")
            if norm_search in norm_orig:
                norm_new = norm_orig.replace(norm_search, norm_replace, 1)
                new_content = norm_new.replace("\n", "\r\n") if "\r\n" in original else norm_new
                applied = True
                strategy = "newline_normalized"
            else:
                # 3. Line-trimmed sliding window
                orig_lines = norm_orig.split("\n")
                search_lines = norm_search.split("\n")
                replace_lines = norm_replace.split("\n")
                search_trimmed = [l.rstrip() for l in search_lines]
                matched_idx = -1
                for i in range(len(orig_lines) - len(search_lines) + 1):
                    window = [orig_lines[i + j].rstrip() for j in range(len(search_lines))]
                    if window == search_trimmed:
                        matched_idx = i
                        break
                if matched_idx != -1:
                    reconstructed = orig_lines[:matched_idx] + replace_lines + orig_lines[matched_idx + len(search_lines):]
                    norm_new = "\n".join(reconstructed)
                    new_content = norm_new.replace("\n", "\r\n") if "\r\n" in original else norm_new
                    applied = True
                    strategy = "line_trimmed"

        if not applied:
            response.status = 400
            return as_json({"ok": False, "applied": False, "error": "Search block could not be matched in target file."})

        with open(full, "w", encoding="utf-8") as f:
            f.write(new_content)

        return as_json({"ok": True, "applied": True, "path": rel, "strategy": strategy, "size": len(new_content)})
    except Exception as e:
        response.status = 500
        return as_json({"ok": False, "error": f"Failed to apply diff: {e}"})


@route("/api/workspace/terminal", method="POST")
def workspace_terminal_exec():
    """Execute command in workspace context with timeout and output capture."""
    data = body()
    cmd = str(data.get("command", "")).strip()
    cwd_rel = str(data.get("cwd", "")).strip()
    if not cmd:
        return as_json({"ok": False, "error": "No command provided."})

    work_dir = _safe_ws_path(cwd_rel) or WORKSPACE_ROOT
    try:
        import shlex
        args = shlex.split(cmd, posix=(os.name != "nt")) if isinstance(cmd, str) else cmd
        proc = subprocess.run(
            args,
            shell=False,
            cwd=work_dir,
            capture_output=True,
            text=True,
            timeout=25.0
        )
        return as_json({
            "ok": True,
            "command": cmd,
            "cwd": work_dir,
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr
        })
    except subprocess.TimeoutExpired:
        return as_json({"ok": False, "command": cmd, "error": "Command timed out after 25 seconds."})
    except Exception as e:
        return as_json({"ok": False, "command": cmd, "error": str(e)})


@route("/api/workspace/parse_diff", method="POST")
def workspace_parse_diff():
    """Extracts target file path, search block, and replace block from text."""
    data = body()
    text = str(data.get("text", ""))
    from llm.engine import parse_aider_diff
    parsed = parse_aider_diff(text)
    return as_json({"ok": True, "diff": parsed})


@route("/api/spec/compile", method="POST")
def spec_compile_endpoint():
    from integrations.vega_ide_spec import is_ide_bridge_enabled

    if not is_ide_bridge_enabled():
        return as_json({"ok": False, "disabled": True})
    try:
        from integrations.spec_compiler import compile_spoken_spec

        data = body()
        payload = compile_spoken_spec(
            transcript=data.get("transcript", ""),
            workspace_root=str(data.get("workspace_root") or WORKSPACE_ROOT),
            title=str(data.get("title") or ""),
            ide_context=data.get("ide_context") if isinstance(data.get("ide_context"), dict) else {},
            approval_tier=str(data.get("approval_tier") or "apply_edits"),
        )
        return as_json({"ok": True, "payload": payload})
    except Exception as exc:
        response.status = 400
        return as_json({"ok": False, "error": str(exc)})


@route("/api/spec/revise", method="POST")
def spec_revise_endpoint():
    from integrations.vega_ide_spec import is_ide_bridge_enabled

    if not is_ide_bridge_enabled():
        return as_json({"ok": False, "disabled": True})
    try:
        from integrations.spec_compiler import revise_spoken_spec
        from integrations.vega_ide_spec import validate_handoff_payload

        data = body()
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        validation = validate_handoff_payload(payload)
        if not validation["ok"]:
            response.status = 400
            return as_json({"ok": False, "errors": validation["errors"]})
        revised = revise_spoken_spec(payload, str(data.get("feedback") or ""))
        return as_json({"ok": True, "payload": revised})
    except Exception as exc:
        response.status = 400
        return as_json({"ok": False, "error": str(exc)})


@route("/api/spec/ide-context", method="GET")
def spec_ide_context_endpoint():
    from integrations.vega_ide_spec import is_ide_bridge_enabled

    if not is_ide_bridge_enabled():
        return as_json({"ok": False, "disabled": True})
    try:
        from integrations.ide_context_bridge import fetch_ide_context

        return as_json(fetch_ide_context())
    except Exception:
        return as_json({
            "workspace_root": "",
            "active_file": "",
            "selected_text": "",
            "recent_diagnostics": [],
            "ide_online": False,
        })


@route("/api/spec/approve", method="POST")
def spec_approve_endpoint():
    from integrations.vega_ide_spec import is_ide_bridge_enabled

    if not is_ide_bridge_enabled():
        return as_json({"ok": False, "disabled": True})
    try:
        from integrations.ide_context_bridge import gate_spec_approval
        from integrations.vega_ide_spec import validate_handoff_payload

        data = body()
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        validation = validate_handoff_payload(payload)
        if not validation["ok"]:
            response.status = 400
            return as_json({"ok": False, "errors": validation["errors"]})
        approved = data.get("approved") is True
        tier = str(data.get("approval_tier") or payload.get("approval_tier") or "apply_edits")
        result = gate_spec_approval(payload, approved=approved, approval_tier=tier)
        return as_json({"ok": True, "payload": result})
    except Exception as exc:
        response.status = 400
        return as_json({"ok": False, "error": str(exc)})


@route("/api/spec/dispatch", method="POST")
def spec_dispatch_endpoint():
    from integrations.vega_ide_spec import is_ide_bridge_enabled

    if not is_ide_bridge_enabled():
        return as_json({"ok": False, "disabled": True})
    try:
        from integrations.handoff_dispatcher import start_dispatch
        from integrations.ide_context_bridge import gate_spec_approval
        from integrations.vega_ide_spec import validate_handoff_payload

        data = body()
        payload = data.get("payload") if isinstance(data.get("payload"), dict) else {}
        validation = validate_handoff_payload(payload)
        if not validation["ok"]:
            response.status = 400
            return as_json({"ok": False, "errors": validation["errors"]})
        approved = payload.get("approved_by_user") is True
        gated = gate_spec_approval(payload, approved=approved, approval_tier=payload.get("approval_tier", "apply_edits"))
        if gated.get("status") == "awaiting_approval":
            return as_json({
                "ok": False,
                "status": "awaiting_approval",
                "reason": "explicit_approval_required",
            })
        if gated.get("approval_tier") == "apply_edits" and gated.get("mode") != "plan_and_apply":
            response.status = 400
            return as_json({"ok": False, "error": "apply_edits requires plan_and_apply mode"})
        initial = start_dispatch(
            gated,
            endpoint=os.getenv("VEGA_IDE_ENDPOINT"),
            on_status=lambda state: broadcast_event("spec_status", state),
        )
        if not initial.get("ok"):
            return as_json(initial)
        return as_json({"ok": True, "status": initial.get("status"), "spec_id": initial.get("spec_id"),
                        "run_id": initial.get("run_id"), "seq": initial.get("seq")})
    except Exception:
        response.status = 400
        return as_json({"ok": False, "error": "Could not start the Vega-IDE handoff."})


@route("/api/spec/cancel", method="POST")
def spec_cancel_endpoint():
    from integrations.vega_ide_spec import is_ide_bridge_enabled

    if not is_ide_bridge_enabled():
        return as_json({"ok": False, "disabled": True})
    try:
        from integrations.handoff_dispatcher import cancel_run

        data = body()
        result = cancel_run(str(data.get("spec_id") or ""), str(data.get("run_id") or ""))
        if result.get("ok"):
            broadcast_event("spec_status", {key: value for key, value in result.items() if key != "ok"})
        return as_json(result)
    except Exception:
        response.status = 400
        return as_json({"ok": False, "error": "Could not cancel the Vega-IDE handoff."})


@route("/api/spec/status/<spec_id>", method="GET")
def spec_status_endpoint(spec_id):
    from integrations.vega_ide_spec import is_ide_bridge_enabled

    if not is_ide_bridge_enabled():
        return as_json({"ok": False, "disabled": True})
    try:
        from integrations.handoff_dispatcher import build_debrief, get_spec_status

        state = get_spec_status(spec_id)
        if not state:
            response.status = 404
            return as_json({"ok": False, "error": "spec_run_not_found"})
        result = {"ok": True, "state": state}
        if state.get("status") in ("completed", "failed"):
            is_silent = not bool(cfg.get("voice_on", True))
            voice_busy = bool(S.get("busy")) or bool(S.get("locked"))
            try:
                from voice.friend_voice import is_sleeping

                is_silent = is_silent or bool(is_sleeping())
            except Exception:
                pass
            result["debrief"] = build_debrief(state, is_silent=is_silent, voice_busy=voice_busy)
        return as_json(result)
    except Exception:
        response.status = 500
        return as_json({"ok": False, "error": "Could not load the Vega-IDE handoff status."})


@route("/api/spec/event", method="POST")
def spec_event_endpoint():
    from integrations.vega_ide_spec import is_ide_bridge_enabled

    if not is_ide_bridge_enabled():
        return as_json({"ok": False, "disabled": True})
    try:
        from integrations.handoff_dispatcher import accept_status_event, get_spec_status

        data = body()
        event = data.get("event") if isinstance(data.get("event"), dict) else data
        if not accept_status_event(event, on_status=lambda state: broadcast_event("spec_status", state)):
            response.status = 409
            return as_json({"ok": False, "error": "stale_or_unknown_spec_event"})
        state = get_spec_status(str(event.get("spec_id") or ""))
        return as_json({"ok": True, "state": state})
    except Exception:
        response.status = 400
        return as_json({"ok": False, "error": "Could not accept the Vega-IDE status event."})


# Backward-compatible agent stubs so nothing breaks
_AGENT_OPTIONS_EMPTY = {
    "providers": {}, "local_status": "Work Mode Active", "default_route": "local",
    "provider_modes": ["auto", "local"], "verified_connections": {}, "workspace": WORKSPACE_ROOT,
    "models": ["qwen2.5-coder:3b", "qwen2.5:3b"], "agents": [], "tools": [], "source_files": []
}

@route("/api/agent/options", method="GET")
def agent_options_compat():
    return as_json(_AGENT_OPTIONS_EMPTY)

@route("/api/agent/refresh", method="POST")
def agent_refresh_compat():
    return as_json(_AGENT_OPTIONS_EMPTY)

@route("/api/agent/tasks", method=["GET", "POST"])
def agent_tasks_compat():
    return as_json({"tasks": []})

@route("/api/agent/browse", method="GET")
def agent_browse_compat():
    return as_json({"ok": True, "source_files": []})


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
    vitals = get_hardware_vitals()
    try:
        from career.db import get_network_mode
        from llm.engine import is_offline_or_low_data
        net_mode = get_network_mode()
        offline = is_offline_or_low_data() or (net_mode == "offline_only")
        vitals["network_mode"] = net_mode
        vitals["offline_mode"] = offline
        vitals["voice_engine"] = "Browser Fallback" if offline else "Edge-TTS (Neural)"
    except Exception:
        vitals["voice_engine"] = "Edge-TTS (Neural)"
    return as_json({"ok": True, "data": vitals})


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


# ---------------------------------------------------------------- CAREER WORKBENCH & PDF API ENDPOINTS
from tools.pdf_reader import extract_pdf
from tools.pdf_builder import build_resume_pdf
from career.db import (
    save_job, get_job, list_jobs,
    update_job_status, get_watchlist, add_watchlist_company,
    get_career_metrics, set_career_setting
)
from career.url_fetcher import parse_job_input
from career.connectors import scan_all_feeds
from career.scam_shield import audit_job_listing
from career.matcher import load_profile, match_job_and_tailor
from career.gmail_sync import get_gmail_status, sync_gmail_label
from career.outreach import draft_outreach_messages

EXPORTS_DIR = os.path.join(BASE_DIR, "career_vault", "exports")
os.makedirs(EXPORTS_DIR, exist_ok=True)
PROFILE_PATH = os.path.join(BASE_DIR, "career_vault", "profile.json")


@route("/api/career/profile", method=["GET", "POST", "OPTIONS"])
@route("/api/career/profile/", method=["GET", "POST", "OPTIONS"])
def career_profile_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        if request.method == "POST":
            data = body()
            if not data or not isinstance(data, dict):
                return as_json({"status": "error", "error": "Invalid profile JSON format."})
            from career.matcher import save_profile
            save_profile(data)
            return as_json({"status": "ok", "message": "Profile updated successfully.", "profile": data})
        else:
            profile = load_profile()
            return as_json({"status": "ok", "profile": profile})
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/career/profile/import_pdf", method=["POST", "OPTIONS"])
@route("/api/career/profile/import_pdf/", method=["POST", "OPTIONS"])
def career_profile_import_pdf_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    if S.get("locked"):
        raise bottle.HTTPResponse(
            json.dumps({"ok": False, "locked": True, "error": "Vega is locked. Unlock Vega to import resume."}),
            status=423,
            headers={"Content-Type": "application/json; charset=UTF-8"},
        )
    try:
        upload = request.files.get("file")
        pdf_bytes = None
        filename = "uploaded_resume.pdf"
        if upload:
            filename = upload.filename
            pdf_bytes = upload.file.read()
        else:
            data = body()
            b64 = data.get("data")
            if b64:
                import base64
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                pdf_bytes = base64.b64decode(b64)
                filename = data.get("filename", filename)

        if not pdf_bytes:
            return as_json({"status": "error", "error": "No PDF file data provided."})

        res = extract_pdf(pdf_bytes)
        if res.get("error"):
            return as_json({"status": "error", "error": res["error"]})

        text = res.get("text", "")
        if res.get("is_scanned") or not text.strip():
            return as_json({
                "status": "error",
                "error": "Uploaded PDF appears to be scanned or image-only without extractable text.",
                "is_scanned": True
            })

        links = res.get("links", [])
        from career.matcher import parse_resume_from_text_and_links, save_profile
        updated_profile = parse_resume_from_text_and_links(text, links)
        save_profile(updated_profile)

        return as_json({
            "status": "ok",
            "message": "Resume PDF imported and parsed successfully.",
            "filename": filename,
            "profile": updated_profile,
            "char_count": len(text),
            "pages": res.get("pages", 1),
            "is_scanned": False,
            "truncated": res.get("truncated", False)
        })
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/career/jobs", method=["GET", "POST", "OPTIONS"])
def career_jobs_list_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        st = request.query.get("status") or None
        risk = request.query.get("scam_risk") or None
        jobs = list_jobs(status=st, scam_risk=risk)
        metrics = get_career_metrics()
        return as_json({"status": "ok", "jobs": jobs, "metrics": metrics})
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc), "jobs": [], "metrics": {}})


@route("/api/career/scan", method=["GET", "POST", "OPTIONS"])
def career_scan_feeds_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body() if request.method == "POST" else {}
        include_adzuna = bool(data.get("include_adzuna", False)) if data else (request.query.get("include_adzuna", "0") in ("1", "true"))
        res = scan_all_feeds(include_adzuna=include_adzuna)
        if res.get("status") == "already_running":
            all_jobs = list_jobs()
            metrics = get_career_metrics()
            return as_json({
                "status": "already_running",
                "notice": res.get("notice", "A feed scan is already in progress."),
                "jobs": all_jobs,
                "metrics": metrics
            })
        discovered = res.get("jobs", [])
        saved_count = 0
        for dj in discovered:
            audit = audit_job_listing(dj.get("raw_text", ""), company=dj.get("company", ""), role=dj.get("role", ""), url=dj.get("url", ""))
            dj["scam_risk"] = audit.get("risk_level", "LOW_RISK")
            dj["scam_score"] = audit.get("scam_score", 0)
            dj["scam_flags"] = audit.get("red_flags", [])

            if dj["scam_risk"] == "HIGH_RISK":
                dj["status"] = "Ignored_Scam"
                dj["match_score"] = 0
                dj["matched_skills"] = []
                dj["missing_skills"] = []
            else:
                match = match_job_and_tailor(dj.get("raw_text", ""), company=dj.get("company", ""), role=dj.get("role", ""))
                dj["match_score"] = match.get("match_score", 0)
                dj["matched_skills"] = match.get("matched_skills", [])
                dj["missing_skills"] = match.get("missing_skills", [])
                dj["cover_note"] = match.get("cover_note", "")
                dj["interview_prep"] = match.get("interview_prep", [])
                dj["status"] = "New"

            save_job(dj)
            saved_count += 1

        all_jobs = list_jobs()
        metrics = get_career_metrics()
        return as_json({
            "status": "ok",
            "saved_count": saved_count,
            "total_discovered": res.get("total_discovered", 0),
            "notice": res.get("notice", ""),
            "jobs": all_jobs,
            "metrics": metrics
        })
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/career/analyze", method=["POST", "OPTIONS"])
def career_analyze_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body() or {}
        url = data.get("url")
        raw_text = data.get("text")
        company = data.get("company", "")
        role = data.get("role", "")

        parsed = parse_job_input(url=url, text=raw_text)
        if parsed.get("error"):
            return as_json({
                "status": "error",
                "error": parsed["error"],
                "blocked": parsed.get("blocked", False),
                "status_code": parsed.get("status_code")
            })

        text = parsed.get("text", "")
        final_url = parsed.get("url") or url or ""

        if not company or not role:
            lines = [l.strip() for l in text.splitlines() if l.strip()]
            if lines and not role:
                role = lines[0][:60]
            if len(lines) > 1 and not company:
                company = lines[1][:50]
        company = company or "Target Company"
        role = role or "Software Engineer / Intern"

        # 1. RUN SCAM SHIELD FIRST
        audit = audit_job_listing(text, company=company, role=role, url=final_url)
        risk = audit.get("risk_level", "LOW_RISK")
        score = audit.get("scam_score", 0)
        flags = audit.get("red_flags", [])

        job_record = {
            "source": "Direct Check",
            "company": company,
            "role": role,
            "location": "India / Remote",
            "url": final_url,
            "raw_text": text,
            "scam_risk": risk,
            "scam_score": score,
            "scam_flags": flags,
            "status": "Ignored_Scam" if risk == "HIGH_RISK" else "New"
        }

        # 2. RUN MATCHER IF NOT HIGH_RISK
        if risk != "HIGH_RISK":
            match = match_job_and_tailor(text, company=company, role=role)
            job_record["match_score"] = match.get("match_score", 0)
            job_record["matched_skills"] = match.get("matched_skills", [])
            job_record["missing_skills"] = match.get("missing_skills", [])
            job_record["cover_note"] = match.get("cover_note", "")
            job_record["interview_prep"] = match.get("interview_prep", [])
            job_record["tailored_resume_data"] = match.get("tailored_resume_data", {})
        else:
            job_record["match_score"] = 0
            job_record["matched_skills"] = []
            job_record["missing_skills"] = []

        job_id = save_job(job_record)
        saved = get_job(job_id)
        if risk != "HIGH_RISK":
            saved["tailored_resume_data"] = match.get("tailored_resume_data", {})
        saved["blocked"] = parsed.get("blocked", False)

        return as_json({"status": "ok", "job": saved})
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/career/ingest", method=["POST", "OPTIONS"])
def career_ingest_endpoint():
    origin = request.headers.get("Origin", "")
    allowed_prefixes = ("chrome-extension://", "http://127.0.0.1", "http://localhost")
    if origin and any(origin.startswith(prefix) for prefix in allowed_prefixes):
        response.set_header("Access-Control-Allow-Origin", origin)
        response.set_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        response.set_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
    elif not origin:
        pass
    else:
        if request.method == "OPTIONS":
            response.status = 403
            return as_json({"status": "error", "error": "Cross-Origin request blocked."})

    if request.method == "OPTIONS":
        response.status = 200
        return ""

    try:
        data = body() or {}
        url = data.get("url", "")
        title = data.get("title", "")
        company = data.get("company", "")
        location = data.get("location", "India / Remote")
        raw_text = data.get("text", "") or data.get("html", "")
        source = data.get("source", "Brave Extension")

        if not raw_text and not url:
            return as_json({"status": "error", "error": "No job content or URL provided."})

        if not raw_text and url:
            parsed = parse_job_input(url=url)
            raw_text = parsed.get("text", "")
            if not company:
                company = parsed.get("company", "")
            if not title:
                title = parsed.get("role", "")

        company = (company or "Target Company").strip()
        role = (title or "Software Engineer / Intern").strip()

        # 1. RUN SCAM SHIELD FIRST
        audit = audit_job_listing(raw_text, company=company, role=role, url=url)
        risk = audit.get("risk_level", "LOW_RISK")
        score = audit.get("scam_score", 0)
        flags = audit.get("red_flags", [])

        job_record = {
            "source": source,
            "company": company,
            "role": role,
            "location": location or "India / Remote",
            "url": url,
            "raw_text": raw_text,
            "scam_risk": risk,
            "scam_score": score,
            "scam_flags": flags,
            "status": "Ignored_Scam" if risk == "HIGH_RISK" else "New"
        }

        # 2. RUN MATCHER IF NOT HIGH_RISK
        if risk != "HIGH_RISK":
            match = match_job_and_tailor(raw_text, company=company, role=role)
            job_record["match_score"] = match.get("match_score", 0)
            job_record["matched_skills"] = match.get("matched_skills", [])
            job_record["missing_skills"] = match.get("missing_skills", [])
            job_record["cover_note"] = match.get("cover_note", "")
            job_record["interview_prep"] = match.get("interview_prep", [])
            job_record["tailored_resume_data"] = match.get("tailored_resume_data", {})
        else:
            job_record["match_score"] = 0
            job_record["matched_skills"] = []
            job_record["missing_skills"] = []

        job_id = save_job(job_record)
        saved = get_job(job_id)
        return as_json({"status": "ok", "job": saved})
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/career/generate_resume", method=["GET", "POST", "OPTIONS"])
@route("/api/career/generate_resume/", method=["GET", "POST", "OPTIONS"])
@route("/api/career/resume/generate", method=["GET", "POST", "OPTIONS"])
@route("/api/career/resume/generate/", method=["GET", "POST", "OPTIONS"])
@route("/api/career/tailor", method=["GET", "POST", "OPTIONS"])
@route("/api/career/tailor/", method=["GET", "POST", "OPTIONS"])
def career_generate_resume_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body()
        job_id = data.get("job_id")
        resume_data = data.get("resume_data")

        if not resume_data and job_id:
            job = get_job(int(job_id))
            if not job:
                return as_json({"status": "error", "error": f"Job ID {job_id} not found."})
            match = match_job_and_tailor(job.get("raw_text", ""), company=job.get("company", ""), role=job.get("role", ""))
            resume_data = match.get("tailored_resume_data", {})

        if not resume_data:
            profile = load_profile()
            resume_data = {
                "name": profile.get("name", ""),
                "headline": profile.get("headline", ""),
                "phone": profile.get("phone", ""),
                "email": profile.get("email", ""),
                "location": profile.get("location", ""),
                "links": profile.get("links", {}),
                "summary": profile.get("summary", ""),
                "education": profile.get("education", []),
                "skills": profile.get("verified_skills", {}),
                "projects": profile.get("verified_projects", []),
                "certifications": profile.get("verified_certifications", [])
            }

        pdf_path = build_resume_pdf(resume_data)
        filename = os.path.basename(pdf_path)

        if job_id:
            update_job_status(int(job_id), "Tailored", pdf_path=pdf_path)

        return as_json({
            "status": "ok",
            "pdf_path": pdf_path,
            "filename": filename,
            "download_url": f"/api/career/download_pdf/{filename}"
        })
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/career/download_pdf", method=["GET", "POST", "OPTIONS"])
@route("/api/career/download_pdf/", method=["GET", "POST", "OPTIONS"])
@route("/api/career/download_pdf/<filename:path>", method=["GET", "POST", "OPTIONS"])
def career_download_pdf_endpoint(filename=""):
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    target_file = filename or request.query.get("file") or ""
    if not target_file:
        return as_json({"ok": False, "status": "error", "error": "No filename specified"})
    try:
        resp = static_file(target_file, root=EXPORTS_DIR, download=True)
        resp.set_header("Cache-Control", "no-store")
        return resp
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": f"Failed to serve PDF: {exc}"})


@route("/api/career/update_status", method=["POST", "OPTIONS"])
def career_update_status_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body()
        job_id = data.get("job_id")
        status = data.get("status")
        notes = data.get("notes")
        if not job_id or not status:
            return as_json({"status": "error", "error": "job_id and status are required."})
        ok = update_job_status(int(job_id), status, notes=notes)
        metrics = get_career_metrics()
        return as_json({"status": "ok" if ok else "not_found", "metrics": metrics})
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/career/watchlist", method=["GET", "POST", "OPTIONS"])
def career_watchlist_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        if request.method == "POST":
            data = body()
            c_name = data.get("company_name", "").strip()
            ats = data.get("ats_type", "greenhouse").strip().lower()
            slug = data.get("board_slug", "").strip()
            if not c_name or not slug:
                return as_json({"status": "error", "error": "company_name and board_slug are required."})
            add_watchlist_company(c_name, ats, slug)
            return as_json({"status": "ok", "watchlist": get_watchlist()})
        else:
            return as_json({"status": "ok", "watchlist": get_watchlist()})
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/career/gmail/status", method=["GET", "OPTIONS"])
def career_gmail_status_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        status_data = get_gmail_status()
        return as_json({"status": "ok", "gmail": status_data})
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/career/gmail/sync", method=["POST", "OPTIONS"])
def career_gmail_sync_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body() or {}
        interactive = bool(data.get("interactive", False))
        res = sync_gmail_label(force_interactive=interactive)
        # Also return updated jobs and metrics
        all_jobs = list_jobs()
        metrics = get_career_metrics()
        res["jobs"] = all_jobs
        res["metrics"] = metrics
        return as_json(res)
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/career/gmail/config", method=["POST", "OPTIONS"])
def career_gmail_config_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body() or {}
        label = data.get("label", "").strip()
        calendar_enabled = data.get("calendar_sync_enabled")

        if label:
            set_career_setting("gmail_label", label)
        if calendar_enabled is not None:
            set_career_setting("calendar_sync_enabled", "1" if calendar_enabled else "0")

        return as_json({
            "status": "ok",
            "gmail": get_gmail_status()
        })
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/career/draft-outreach", method=["POST", "OPTIONS"])
def career_draft_outreach_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body() or {}
        job_id = data.get("job_id")
        style = data.get("style", "all")
        if not job_id:
            return as_json({"status": "error", "error": "job_id is required."})

        res = draft_outreach_messages(int(job_id), style=style)
        return as_json(res)
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/career/github/sync", method=["POST", "OPTIONS"])
@route("/api/career/github/sync/", method=["POST", "OPTIONS"])
def career_github_sync_endpoint():
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from career.github_sync import sync_github_repos
        data = body() or {}
        user = data.get("username")
        token = data.get("token")
        res = sync_github_repos(username=user, token=token)
        return as_json(res)
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": str(exc)})


@route("/api/career/github/repos", method=["GET", "OPTIONS"])
@route("/api/career/github/repos/", method=["GET", "OPTIONS"])
def career_github_repos_endpoint():
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from career.github_sync import get_synced_repos
        eligible_only = request.query.get("eligible") in ("1", "true")
        repos = get_synced_repos(eligible_only=eligible_only)
        return as_json({"ok": True, "status": "ok", "repos": repos, "total": len(repos)})
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": str(exc)})


@route("/api/career/github/repo/toggle", method=["POST", "OPTIONS"])
@route("/api/career/github/repo/toggle/", method=["POST", "OPTIONS"])
def career_github_repo_toggle_endpoint():
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from career.github_sync import toggle_repo_eligibility
        data = body() or {}
        name = data.get("name")
        eligible = bool(data.get("eligible", True))
        if not name:
            return as_json({"ok": False, "status": "error", "error": "name is required"})
        success = toggle_repo_eligibility(name, eligible)
        return as_json({"ok": success, "status": "ok" if success else "error"})
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": str(exc)})


@route("/api/career/resume/analyze-jd", method=["GET", "POST", "OPTIONS"])
@route("/api/career/resume/analyze-jd/", method=["GET", "POST", "OPTIONS"])
@route("/api/career/analyze-jd", method=["GET", "POST", "OPTIONS"])
@route("/api/career/analyze-jd/", method=["GET", "POST", "OPTIONS"])
def career_resume_analyze_jd_endpoint():
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from career.matcher import analyze_jd
        data = body() or {}
        jd_text = data.get("jd_text") or data.get("text") or ""
        company = data.get("company", "")
        role = data.get("role", "")
        if not jd_text:
            return as_json({"ok": False, "status": "error", "error": "jd_text is required"})
        res = analyze_jd(jd_text=jd_text, company=company, role=role)
        return as_json(res)
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": str(exc)})


@route("/api/career/resume/generate-tailored", method=["GET", "POST", "OPTIONS"])
@route("/api/career/resume/generate-tailored/", method=["GET", "POST", "OPTIONS"])
@route("/api/career/resume/tailor", method=["GET", "POST", "OPTIONS"])
@route("/api/career/resume/tailor/", method=["GET", "POST", "OPTIONS"])
def career_resume_generate_tailored_endpoint():
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from career.matcher import generate_tailored_resume
        data = body() or {}
        jd_text = data.get("jd_text") or data.get("text") or ""
        company = data.get("company", "")
        role = data.get("role", "")
        skill_decisions = data.get("skill_decisions") or {}
        selected_projects = data.get("selected_project_names") or data.get("projects") or []
        res = generate_tailored_resume(
            jd_text=jd_text,
            company=company,
            role=role,
            skill_decisions=skill_decisions,
            selected_project_names=selected_projects
        )
        job_id = data.get("job_id")
        if job_id and res.get("pdf_path"):
            try:
                update_job_status(int(job_id), "Tailored", pdf_path=res["pdf_path"])
            except Exception:
                pass
        return as_json(res)
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": str(exc)})



@route("/api/career/resume/session/start", method=["POST", "OPTIONS"])
@route("/api/career/resume/session/start/", method=["POST", "OPTIONS"])
def career_resume_session_start_endpoint():
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from career.resume_session import start_resume_session
        data = body() or {}
        res = start_resume_session(
            job_id=data.get("job_id"),
            company=data.get("company", ""),
            role=data.get("role", ""),
            jd_text=data.get("jd_text", ""),
            goal=data.get("goal", "")
        )
        return as_json(res)
    except Exception as exc:
        log_error(f"Error starting resume session: {exc}")
        return as_json({"ok": False, "status": "error", "error": str(exc)})


@route("/api/career/resume/session/by-chat/<chat_id>", method=["GET", "OPTIONS"])
@route("/api/career/resume/session/by-chat/<chat_id>/", method=["GET", "OPTIONS"])
def career_resume_session_by_chat_endpoint(chat_id):
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from career.resume_session import get_resume_session_by_chat
        sess = get_resume_session_by_chat(chat_id)
        return as_json({"ok": True, "session": sess, "draft": sess["draft"] if sess else None})
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": str(exc)})


@route("/api/career/resume/session/<session_id>/update", method=["POST", "OPTIONS"])
@route("/api/career/resume/session/<session_id>/update/", method=["POST", "OPTIONS"])
def career_resume_session_update_endpoint(session_id):
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from career.resume_session import update_resume_draft
        data = body() or {}
        updates = data.get("updates") or data
        updated = update_resume_draft(session_id, updates)
        if not updated:
            return as_json({"ok": False, "error": f"Session {session_id} not found."})
        broadcast_event("resume_session_updated", {
            "session_id": session_id,
            "draft": updated["draft"]
        })
        return as_json({"ok": True, "session": updated, "draft": updated["draft"]})
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": str(exc)})


@route("/api/career/resume/session/<session_id>/export-pdf", method=["GET", "POST", "OPTIONS"])
@route("/api/career/resume/session/<session_id>/export-pdf/", method=["GET", "POST", "OPTIONS"])
def career_resume_session_export_pdf_endpoint(session_id):
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from career.resume_session import export_resume_session_pdf
        res = export_resume_session_pdf(session_id)
        return as_json(res)
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": str(exc)})


@route("/api/career/resume/session/<session_id>/close", method=["POST", "OPTIONS"])
@route("/api/career/resume/session/<session_id>/close/", method=["POST", "OPTIONS"])
def career_resume_session_close_endpoint(session_id):
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from career.resume_session import close_resume_session
        closed = close_resume_session(session_id)
        broadcast_event("resume_session_closed", {"session_id": session_id})
        return as_json({"ok": True, "session": closed})
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": str(exc)})


@route("/api/chat/upload_pdf", method=["POST", "OPTIONS"])
@route("/api/chat/upload_pdf/", method=["POST", "OPTIONS"])
def chat_upload_pdf_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        upload = request.files.get("file")
        pdf_bytes = None
        filename = "document.pdf"
        if upload:
            filename = upload.filename
            pdf_bytes = upload.file.read()
        else:
            data = body()
            b64 = data.get("data")
            if b64:
                import base64
                if "," in b64:
                    b64 = b64.split(",", 1)[1]
                pdf_bytes = base64.b64decode(b64)
                filename = data.get("filename", filename)

        if not pdf_bytes:
            return as_json({"status": "error", "error": "No PDF data provided."})

        res = extract_pdf(pdf_bytes)
        if res.get("error"):
            return as_json({"status": "error", "error": res["error"]})

        rendered_images = []
        is_scanned = bool(res.get("is_scanned", False) or len(res.get("text", "").strip()) < 50)
        if is_scanned:
            try:
                import pymupdf as fitz
            except ImportError:
                try:
                    import fitz
                except ImportError:
                    fitz = None
            if fitz:
                try:
                    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
                    for p_idx in range(min(3, len(doc))):
                        page = doc[p_idx]
                        pix = page.get_pixmap(dpi=150)
                        png_b64 = base64.b64encode(pix.tobytes("png")).decode("utf-8")
                        rendered_images.append({
                            "name": f"{filename}_page_{p_idx+1}.png",
                            "mime_type": "image/png",
                            "data": f"data:image/png;base64,{png_b64}"
                        })
                    doc.close()
                except Exception as render_err:
                    log_error(f"Error rendering scanned PDF pages: {render_err}")

        return as_json({
            "status": "ok",
            "filename": filename,
            "text": res.get("text", ""),
            "links": res.get("links", []),
            "pages": res.get("pages", 1),
            "is_scanned": is_scanned,
            "is_encrypted": res.get("is_encrypted", False),
            "truncated": res.get("truncated", False),
            "char_count": res.get("char_count", 0),
            "images": rendered_images
        })
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


# ---------------------------------------------------------------- Phase 3: Lab Drills, Health, Flashcards & Mock Interviewer
from lab.drills import generate_drill, grade_drill_solution, get_drill_history
from lab.health import get_full_lab_health
from lab.study_capture import process_study_notes, get_flashcards, review_flashcard
from career.interviewer import start_interview_session, submit_interview_answer, finish_interview_session, get_active_interview

@route("/api/lab/drill/generate", method="POST")
def lab_drill_generate_endpoint():
    try:
        data = body() or {}
        topic = data.get("topic", "Dockerfile")
        res = generate_drill(topic=topic)
        return as_json(res)
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/lab/drill/grade", method="POST")
def lab_drill_grade_endpoint():
    try:
        data = body() or {}
        drill_id = data.get("drill_id")
        solution = data.get("user_solution", "")
        if not drill_id:
            return as_json({"status": "error", "error": "drill_id is required."})
        res = grade_drill_solution(drill_id=drill_id, user_solution=solution)
        return as_json(res)
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/lab/drill/history", method="GET")
def lab_drill_history_endpoint():
    try:
        topic = request.query.get("topic")
        history = get_drill_history(topic=topic)
        return as_json({"status": "ok", "drills": history})
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/lab/health", method="GET")
def lab_health_endpoint():
    try:
        health = get_full_lab_health()
        return as_json(health)
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/notes/process-study", method="POST")
def notes_process_study_endpoint():
    try:
        data = body() or {}
        text = data.get("text", "")
        topic = data.get("topic", "General")
        res = process_study_notes(text=text, topic=topic)
        return as_json(res)
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/lab/flashcards", method="GET")
def lab_flashcards_endpoint():
    try:
        topic = request.query.get("topic")
        mastered_raw = request.query.get("mastered")
        mastered = int(mastered_raw) if mastered_raw is not None and mastered_raw != "" else None
        cards = get_flashcards(topic=topic, mastered=mastered)
        return as_json({"status": "ok", "flashcards": cards, "total": len(cards)})
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/lab/flashcards/review", method="POST")
def lab_flashcards_review_endpoint():
    try:
        data = body() or {}
        card_id = data.get("card_id")
        mastered = bool(data.get("mastered", False))
        if card_id is None:
            return as_json({"status": "error", "error": "card_id is required."})
        res = review_flashcard(card_id=int(card_id), mastered=mastered)
        return as_json(res)
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/voice/mic-arb", method=["GET", "POST", "OPTIONS"])
@route("/api/voice/mic-arb/", method=["GET", "POST", "OPTIONS"])
def voice_mic_arb_endpoint():
    """
    Arbitrates microphone ownership between HUD live visualizer/MediaRecorder and
    the background 'Hey Vega' wake listener to eliminate audio contention and double-listening.
    Supports owners: 'hud', 'chat', 'speaking', 'tts', 'background', 'idle', 'release'.
    """
    global _MIC_OWNER
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        if request.method == "POST":
            data = body() or {}
            owner = str(data.get("owner") or "background").strip().lower()
            if owner in ("hud", "chat", "speaking", "tts"):
                _MIC_OWNER = owner
                if "wake" in globals() and wake:
                    try:
                        wake.pause_for_capture(timeout=2.0)
                    except Exception:
                        pass
                status("HUD Voice Active" if owner in ("hud", "chat") else ready_text(), "ready")
                return as_json({"ok": True, "owner": _MIC_OWNER})
            elif owner in ("background", "idle", "release"):
                _MIC_OWNER = "background"
                if "wake" in globals() and wake:
                    try:
                        wake.resume_after_capture()
                    except Exception:
                        pass
                status(ready_text(), "ready")
                return as_json({"ok": True, "owner": _MIC_OWNER})
            status(ready_text(), "ready")
            return as_json({"ok": True, "owner": _MIC_OWNER})
        return as_json({"ok": True, "owner": _MIC_OWNER})
    except Exception as exc:
        return as_json({"ok": False, "error": str(exc), "owner": _MIC_OWNER})


@route("/api/interview/start", method=["POST", "OPTIONS"])
def interview_start_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body() or {}
        job_id = data.get("job_id")
        track = data.get("track")
        res = start_interview_session(job_id=job_id, track=track)
        return as_json(res)
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/interview/answer", method=["POST", "OPTIONS"])
def interview_answer_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body() or {}
        session_id = data.get("session_id")
        answer_text = data.get("answer", "")
        if not session_id:
            return as_json({"status": "error", "error": "session_id is required."})
        res = submit_interview_answer(session_id=session_id, answer_text=answer_text)
        return as_json(res)
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/interview/finish", method=["POST", "OPTIONS"])
def interview_finish_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body() or {}
        session_id = data.get("session_id")
        if not session_id:
            return as_json({"status": "error", "error": "session_id is required."})
        res = finish_interview_session(session_id=session_id)
        return as_json(res)
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/interview/active", method=["GET", "OPTIONS"])
def interview_active_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        session_id = request.query.get("session_id")
        res = get_active_interview(session_id=session_id)
        return as_json(res)
    except Exception as exc:
        return as_json({"status": "error", "error": str(exc)})


@route("/api/settings/persona", method=["GET", "POST", "OPTIONS"])
def settings_persona_endpoint():
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from voice.friend_voice import VOICE_PRESETS
        from career.db import get_career_setting, set_career_setting
        if request.method == "POST":
            data = body() or {}
            if "persona_mode" in data:
                set_career_setting("persona_mode", str(data["persona_mode"]).strip().lower())
            if "english_coach" in data or "english_coach_enabled" in data:
                coach_val = data.get("english_coach_enabled", data.get("english_coach"))
                set_career_setting("english_coach", "1" if coach_val else "0")
            if "tts_voice" in data:
                set_career_setting("tts_voice", str(data["tts_voice"]).strip())

        mode = get_career_setting("persona_mode", "friend")
        coach = get_career_setting("english_coach", "0") in ("1", "true")
        voice = get_career_setting("tts_voice", "en-IN-NeerjaNeural")
        if voice == "en-IN-NeerjaExpressiveNeural":
            voice = "en-IN-NeerjaNeural"
        return as_json({
            "ok": True,
            "status": "ok",
            "persona_mode": mode,
            "english_coach": coach,
            "english_coach_enabled": coach,
            "tts_voice": voice,
            "presets": VOICE_PRESETS,
        })
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": str(exc)})


@route("/api/settings/english-coach", method=["GET", "POST", "OPTIONS"])
@route("/api/settings/english-coach/", method=["GET", "POST", "OPTIONS"])
def settings_english_coach_endpoint():
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from career.db import get_career_setting, set_career_setting
        if request.method == "POST":
            data = body() or {}
            val = data.get("enabled", data.get("english_coach_enabled", data.get("english_coach", True)))
            set_career_setting("english_coach", "1" if val else "0")
        coach = get_career_setting("english_coach", "0") in ("1", "true")
        return as_json({
            "ok": True,
            "status": "ok",
            "english_coach": coach,
            "english_coach_enabled": coach,
        })
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": str(exc)})


@route("/api/orchestrator/status", method=["GET", "OPTIONS"])
@route("/api/orchestrator/status/", method=["GET", "OPTIONS"])
def orchestrator_status_endpoint():
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        from llm.engine import get_orchestrator_status
        st = get_orchestrator_status(timeout=1.0)
        return as_json(st)
    except Exception as exc:
        return as_json({"ok": False, "status": "error", "error": str(exc)})


# ---------------------------------------------------------------- JARVIS BACKGROUND TASK RUNNER
try:
    from core.jarvis_bridge import get_jarvis_bridge, set_broadcast_callback, set_spoken_callback
    set_broadcast_callback(broadcast_event)
    def _jarvis_spoken_cb(txt):
        try:
            from voice.friend_voice import is_sleeping
            if not is_sleeping():
                mouth.say(txt)
        except Exception:
            pass
    set_spoken_callback(_jarvis_spoken_cb)
except Exception as _jb_err:
    log_error(f"Jarvis bridge init error: {_jb_err}")


@route("/api/jarvis/status", method=["GET", "OPTIONS"])
@route("/api/jarvis/status/", method=["GET", "OPTIONS"])
def jarvis_status_endpoint():
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        jb = get_jarvis_bridge()
        return as_json(jb.get_status())
    except Exception as exc:
        return as_json({"ok": False, "error": str(exc), "available": False})


@route("/api/jarvis/tasks", method=["GET", "POST", "OPTIONS"])
@route("/api/jarvis/tasks/", method=["GET", "POST", "OPTIONS"])
def jarvis_tasks_endpoint():
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    jb = get_jarvis_bridge()
    if request.method == "GET":
        return as_json({"ok": True, "tasks": jb.list_recent_tasks()})
    try:
        data = body() or {}
        prompt = (data.get("prompt") or data.get("task") or "").strip()
        workspace = (data.get("workspace") or data.get("project_dir") or "").strip()
        mode = (data.get("mode") or "read_only").strip().lower()
        if not prompt:
            return as_json({"ok": False, "error": "Task prompt cannot be empty."})
        resolved_ws, _ = jb.resolve_workspace(workspace)
        task = jb.create_task(prompt=prompt, workspace=resolved_ws, mode=mode)
        return as_json({"ok": True, "task": task})
    except Exception as exc:
        return as_json({"ok": False, "error": str(exc)})


@route("/api/jarvis/tasks/<task_id>/approve", method=["POST", "OPTIONS"])
@route("/api/jarvis/tasks/<task_id>/approve/", method=["POST", "OPTIONS"])
def jarvis_approve_endpoint(task_id):
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        jb = get_jarvis_bridge()
        updated = jb.approve_task(task_id)
        if not updated:
            return as_json({"ok": False, "error": f"Task not found: {task_id}"})
        return as_json({"ok": True, "task": updated})
    except Exception as exc:
        return as_json({"ok": False, "error": str(exc)})


@route("/api/jarvis/tasks/<task_id>/cancel", method=["POST", "OPTIONS"])
@route("/api/jarvis/tasks/<task_id>/cancel/", method=["POST", "OPTIONS"])
def jarvis_cancel_endpoint(task_id):
    if request.method == "OPTIONS":
        return as_json({"ok": True, "status": "ok"})
    try:
        jb = get_jarvis_bridge()
        updated = jb.cancel_task(task_id)
        if not updated:
            return as_json({"ok": False, "error": f"Task not found: {task_id}"})
        return as_json({"ok": True, "task": updated})
    except Exception as exc:
        return as_json({"ok": False, "error": str(exc)})


@route("/api/voice/speak", method=["POST", "OPTIONS"])
def voice_speak_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body() or {}
        text = data.get("text", "")
        from career.db import get_career_setting
        voice_id = data.get("voice") or get_career_setting("tts_voice", "en-IN-NeerjaNeural")
        if voice_id == "en-IN-NeerjaExpressiveNeural":
            voice_id = "en-IN-NeerjaNeural"
        from voice.friend_voice import synthesize_speech, register_spoken_reply, extract_speech_text
        clean = extract_speech_text(text)
        register_spoken_reply(clean)
        res = synthesize_speech(text, voice_name=voice_id)
        return as_json(res)
    except Exception as exc:
        log_error(f"voice speak endpoint error: {exc}\n{traceback.format_exc()}")
        from voice.friend_voice import sanitize_tts_notice
        notice = sanitize_tts_notice(str(exc))
        return as_json({"ok": False, "fallback": True, "use_browser_tts": True, "error": notice,
                        "notice": notice, "text": (body() or {}).get("text", "")})


@route("/api/voice/transcribe", method=["POST", "OPTIONS"])
def voice_transcribe_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        upload = request.files.get("audio") or request.files.get("file")
        audio_bytes = b""
        filename = "audio.webm"
        if upload:
            audio_bytes = upload.file.read()
            filename = upload.filename or "audio.webm"
        else:
            raw = request.body.read()
            if raw:
                audio_bytes = raw
        from voice.friend_voice import (
            transcribe_audio_groq, is_self_echo, is_sleep_phrase, is_sleeping, is_hey_vega_phrase, set_sleep_state
        )
        res = transcribe_audio_groq(audio_bytes, filename=filename)
        if res.get("ok") and res.get("text"):
            tx = res["text"]
            if is_self_echo(tx):
                return as_json({"ok": False, "self_echo_filtered": True, "error": "Self-echo detected and ignored."})
            if is_sleep_phrase(tx):
                set_sleep_state(True)
                mouth.stop()
                vega.stop()
                ack = "Going silent. Say 'Hey Vega' whenever you need me."
                broadcast_event("voice_sleep", {"sleeping": True, "text": ack})
                mouth.say(ack)
                return as_json({"ok": True, "text": tx, "sleeping": True, "ack": ack, "intercepted": True})
            if is_sleeping():
                if is_hey_vega_phrase(tx):
                    set_sleep_state(False)
                    broadcast_event("voice_sleep", {"sleeping": False})
                    ack = "I'm awake and listening."
                    mouth.say(ack)
                    return as_json({"ok": True, "text": tx, "waking": True, "ack": ack})
                else:
                    return as_json({"ok": False, "sleeping": True, "error": "Silent mode active: ignoring ambient talk without wake phrase."})
        return as_json(res)
    except Exception as exc:
        return as_json({"ok": False, "fallback_to_browser": True, "error": str(exc)})


@route("/api/voice/sleep", method=["GET", "POST", "OPTIONS"])
def voice_sleep_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        from voice.friend_voice import is_sleeping, set_sleep_state
        if request.method == "POST":
            data = body() or {}
            sleeping = data.get("sleeping")
            if sleeping is None:
                new_state = set_sleep_state(not is_sleeping())
            else:
                new_state = set_sleep_state(bool(sleeping))
            if new_state:
                mouth.stop()
                vega.stop()
                ack = "Going silent. Say 'Hey Vega' whenever you need me."
                status("Silent — Say “Hey Vega”", "ready")
            else:
                ack = "I'm awake and listening."
                status(ready_text(), "ready")
            broadcast_event("voice_sleep", {"sleeping": new_state, "text": ack})
            return as_json({"ok": True, "sleeping": new_state, "ack": ack})
        return as_json({"ok": True, "sleeping": is_sleeping()})
    except Exception as exc:
        return as_json({"ok": False, "error": str(exc), "sleeping": False})


@route("/api/voice/listen-local", method=["POST", "OPTIONS"])
def voice_listen_local_endpoint():
    """
    Fallback for when browser MediaRecorder / getUserMedia is unavailable or fails.
    Captures one utterance directly from local microphone via sounddevice + Whisper/Groq.
    """
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    if os.environ.get("VEGA_CI_MODE") == "1":
        return as_json({
            "ok": True,
            "text": "CI Mock: Local microphone captured command.",
            "source": "local_mic_ci_mock"
        })

    try:
        if S.get("listening"):
            return as_json({"ok": False, "error": "Microphone is already actively recording."})
        S["listening"] = True
        mouth.stop()
        try:
            if "wake" in globals() and wake:
                if not wake.pause_for_capture(timeout=2.5):
                    return as_json({"ok": False, "error": "Microphone is busy in background listener. Please retry."})
            audio = ears.listen_once(wait_seconds=6, silence=1.2, max_seconds=10)
            if audio is None or len(audio) < 1600:
                return as_json({"ok": False, "error": "No speech detected from microphone."})
            text = ears.transcribe_command(audio)
            return as_json({"ok": True, "text": _coerce_text(text).strip(), "source": "local_mic"})
        finally:
            S["listening"] = False
            if "wake" in globals() and wake:
                wake.resume_after_capture()
    except Exception as exc:
        return as_json({"ok": False, "error": f"Local listen error: {exc}"})


@route("/api/workspace/explain-clipboard", method=["POST", "OPTIONS"])
def workspace_explain_clipboard_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        from career.db import get_career_setting
        mode = get_career_setting("persona_mode", "friend")
        from tools.workspace_bridge import explain_clipboard
        res = explain_clipboard(persona=mode)
        return as_json(res)
    except Exception as exc:
        return as_json({"ok": False, "error": str(exc)})


@route("/api/workspace/look-at-screen", method=["POST", "OPTIONS"])
def workspace_look_at_screen_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body() or {}
        prompt = data.get("prompt") or "What is on my screen? Help me understand and fix any visible errors."
        from tools.workspace_bridge import look_at_screen
        res = look_at_screen(prompt=prompt)
        return as_json(res)
    except Exception as exc:
        return as_json({"ok": False, "error": str(exc)})


@route("/api/workspace/action", method=["POST", "OPTIONS"])
def workspace_action_endpoint():
    if request.method == "OPTIONS":
        return as_json({"status": "ok"})
    try:
        data = body() or {}
        action = data.get("action", "")
        params = data.get("params") or {}
        from tools.workspace_bridge import execute_workspace_action
        res = execute_workspace_action(action=action, params=params)
        return as_json(res)
    except Exception as exc:
        return as_json({"ok": False, "error": str(exc)})


# the page's files (defined LAST: a catch-all defined earlier swallowed /api/chat/<id>, so old chats never opened)
@route("/web/<filename:path>")
@route("/<filename:re:(?!api/).*>")
def static_files(filename):
    if filename.startswith("api/") or filename == "api":
        raise bottle.HTTPError(404, "Not Found")
    resp = static_file(filename, root=WEB_DIR)
    clean_name = filename.split("?")[0].lower()
    if clean_name.endswith(".js"):
        resp.set_header("Content-Type", "application/javascript; charset=utf-8")
    elif clean_name.endswith(".css"):
        resp.set_header("Content-Type", "text/css; charset=utf-8")
    elif clean_name.endswith(".html"):
        resp.set_header("Content-Type", "text/html; charset=utf-8")
    resp.set_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
    resp.set_header("Pragma", "no-cache")
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
            pystray.MenuItem("Lock Vega", lambda *_: lock_now(force=True)),
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
            srv = make_server(BIND_HOST, PORT, bottle.default_app(), server_class=ThreadingWSGIServer,
                              handler_class=QuietHandler)
            break
        except OSError as e:
            log_error(f"port {PORT} on {BIND_HOST} is busy ({e}), trying again")
            time.sleep(1.5)
    if srv is None:
        print(f"Port {PORT} on {BIND_HOST} is busy. Close the other Vega (Task Manager -> python) and try again.")
        sys.exit(1)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    wait_for_server_ready(BIND_HOST, PORT, timeout=5.0)
    threading.Thread(target=accept_other_instances, daemon=True).start()
    threading.Thread(target=vega.refresh_app_index, daemon=True).start()

    def _telemetry_stream_loop():
        while True:
            try:
                v = get_hardware_vitals()
                broadcast_event("telemetry", v)
            except Exception:
                pass
            time.sleep(1.5)
    threading.Thread(target=_telemetry_stream_loop, daemon=True, name="TelemetryLoop").start()
    threading.Thread(target=lazy_load_daily_briefing, daemon=True, name="LazyBriefingStartup").start()

    if IS_HEADLESS:
        log_debug(f"ready headless on http://{BIND_HOST}:{PORT} (locked={S['locked']})")
        print(f"Vega headless server running on http://{BIND_HOST}:{PORT}")
        while True:
            time.sleep(3600)
    else:
        threading.Thread(target=warm_up_ears, daemon=True).start()
        threading.Thread(target=idle_lock_loop, daemon=True).start()
        ReminderClock(vega, on_reminder, on_note=on_note).start()
        wake.start()
        wake.set_enabled(bool(cfg.get("wake_word_on", True)) or bool(cfg.get("talk_without_wake", True)))
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
