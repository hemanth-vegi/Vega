import os
import sys
import json
import time
import queue
import logging
import datetime
import threading
import traceback
import subprocess
import urllib.parse
from datetime import date, timedelta

# Ensure UTF-8 standard on Windows
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Debug Logger setup
DEBUG_LOG_FILE = os.path.join(BASE_DIR, "debug_vega.log")
logging.basicConfig(
    filename=DEBUG_LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    encoding="utf-8"
)

def log_debug(msg):
    try:
        logging.info(msg)
        print(f"[DEBUG] {msg}")
    except Exception:
        pass

def log_error(msg):
    try:
        logging.error(msg)
        print(f"[ERROR] {msg}")
    except Exception:
        pass

log_debug("Vega 2.0 webview server initializing...")

from vega_core import (
    Vega, save_config_value, read_json, write_json, here
)

def open_path(path):
    try:
        if os.name == "nt":
            os.startfile(path)
        else:
            import webbrowser
            webbrowser.open("file://" + path)
    except Exception as e:
        log_error(f"open_path error for {path}: {e}")

from vega_chats import ChatStore
import vega_voice
from vega_voice import Mouth, Ears, WakeListener, shorten_for_speech, beep
import bottle
from bottle import route, run, request, response, static_file, HTTPResponse
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIServer, make_server

class ThreadingWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True

class ThreadedServer(bottle.ServerAdapter):
    def run(self, handler):
        server = make_server(self.host, self.port, handler, server_class=ThreadingWSGIServer)
        server.serve_forever()

PORT = 47632
WEB_DIR = os.path.join(BASE_DIR, "web")

# Core Vega instances
vega = Vega()
store = ChatStore()
cfg = vega.cfg

mouth = Mouth(rate=cfg.get("speech_rate", 1.0), voice=cfg.get("voice", "female"))
ears = Ears(cfg.get("whisper_model", "small.en"), cfg.get("wake_model", "base.en"))
ears.telugu = bool(cfg.get("understand_telugu", False))
ears.cloud = bool(cfg.get("cloud_speech", True))
ears.hint = vega.speech_hint()
ears.hotwords = vega.speech_hotwords()

# Event broadcast queue for Server-Sent Events (SSE)
sse_subscribers = []
sse_lock = threading.Lock()

def broadcast_event(event_type, payload):
    with sse_lock:
        dead = []
        for q in sse_subscribers:
            try:
                q.put_nowait({"type": event_type, "data": payload})
            except Exception:
                dead.append(q)
        for d in dead:
            if d in sse_subscribers:
                sse_subscribers.remove(d)

active_chat_id = None
chats = store.all()
if chats:
    active_chat_id = chats[0]["id"]
else:
    c = store.new()
    active_chat_id = c["id"]

# Current listening & front state
is_direct_talking = True
is_busy = False

def check_is_busy():
    return is_busy or mouth.busy()

# Wake listener callback handlers
def on_wake_detected(audio=None):
    log_debug("Wake word 'Hey Vega' detected!")
    try:
        beep("wake")
    except Exception:
        pass
    broadcast_event("wake", {"status": "Yes? I'm listening...", "time": time.time()})

def on_voice_command(text, audio=None):
    global is_busy
    if not text or not text.strip():
        return
    log_debug(f"Voice command received: '{text}'")
    broadcast_event("voice_input", {"text": text, "chat_id": active_chat_id})

    # Execute through vega.handle
    is_busy = True
    broadcast_event("status", {"text": "Thinking...", "badge": "thinking"})

    def run_voice_task():
        global is_busy
        try:
            chat = store.load(active_chat_id) or store.new()
            store.add(chat, "you", text)

            out = vega.handle(text, source="laptop", spoken=True)
            ans = out.get("text", "")
            say_text = out.get("say") or ans
            src_label = out.get("source_label", getattr(vega, "last_source_label", "Online"))
            open_tab = "lablog" if out.get("open_lablog") else None

            store.add(chat, "vega", ans)

            broadcast_event("voice_response", {
                "text": ans,
                "say": say_text,
                "source_label": src_label,
                "chat_id": active_chat_id,
                "open_tab": open_tab
            })

            if vega.cfg.get("voice_on", True):
                broadcast_event("status", {"text": "Speaking...", "badge": "speaking"})
                mouth.say(shorten_for_speech(say_text))

        except Exception as e:
            log_error(f"Error handling voice command: {e}\n{traceback.format_exc()}")
            broadcast_event("voice_response", {
                "text": f"Error: {e}",
                "source_label": "Offline",
                "chat_id": active_chat_id
            })
        finally:
            is_busy = False
            broadcast_event("status", {"text": "Ready - just talk", "badge": "ready"})

    threading.Thread(target=run_voice_task, daemon=True).start()

# Wake listener thread initialization
wake = WakeListener(
    ears=ears,
    on_wake=on_wake_detected,
    on_command=on_voice_command,
    is_busy=check_is_busy,
    on_error=lambda err: log_error(f"Wake listener error: {err}")
)
wake.strictness = str(cfg.get("wake_strictness", "easy"))
wake.direct = lambda: is_direct_talking

try:
    wake.start()
    wake.set_enabled(bool(cfg.get("wake_word_on", True)))
    log_debug(f"WakeListener started. Enabled: {bool(cfg.get('wake_word_on', True))}")
except Exception as e:
    log_error(f"Failed to start WakeListener: {e}")

# Telegram Phone Bridge
tg_bridge = None
if os.environ.get("TELEGRAM_TOKEN") or cfg.get("telegram_token"):
    try:
        from vega_telegram import TelegramBridge
        def on_telegram_event(kind, *data):
            if kind == "phone":
                text_in, text_out = data
                chat = store.load(active_chat_id) or store.new()
                store.add(chat, "phone", text_in)
                store.add(chat, "vega", text_out)
                broadcast_event("voice_response", {
                    "text": text_out,
                    "say": text_out,
                    "source_label": "Phone",
                    "chat_id": active_chat_id
                })
            elif kind == "status":
                log_debug(f"Telegram status: {data[0]}")
        tg_bridge = TelegramBridge(vega, ears, on_telegram_event)
        tg_bridge.start()
        log_debug("Telegram bridge started for phone control")
    except Exception as e:
        log_error(f"Failed to start Telegram bridge: {e}")

# Global Hotkeys (Ctrl+Alt+V for Mic, Ctrl+Shift+V for QuickBar)
def start_global_hotkeys():
    if os.name != "nt":
        return
    def hotkey_loop():
        try:
            import ctypes
            from ctypes import wintypes
            user32 = ctypes.windll.user32
            MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT, VK_V, WM_HOTKEY = 0x1, 0x2, 0x4, 0x4000, 0x56, 0x0312
            user32.RegisterHotKey(None, 1, MOD_ALT | MOD_CONTROL | MOD_NOREPEAT, VK_V)
            user32.RegisterHotKey(None, 2, MOD_SHIFT | MOD_CONTROL | MOD_NOREPEAT, VK_V)
            log_debug("Registered global shortcuts: Ctrl+Alt+V (Mic) and Ctrl+Shift+V (Quick Bar)")
            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY:
                    if msg.wParam == 1:
                        log_debug("Hotkey Ctrl+Alt+V pressed -> triggering Mic dictation")
                        broadcast_event("hotkey_mic", {})
                    elif msg.wParam == 2:
                        log_debug("Hotkey Ctrl+Shift+V pressed -> toggling Quick Bar")
                        broadcast_event("toggle_quickbar", {})
        except Exception as e:
            log_error(f"Global hotkey thread error: {e}")
    threading.Thread(target=hotkey_loop, daemon=True).start()

start_global_hotkeys()

# Static Web Routes
@route("/")
def index():
    return static_file("index.html", root=WEB_DIR)

@route("/<filename:path>")
def static_files(filename):
    return static_file(filename, root=WEB_DIR)

# SSE Real-time Events Stream
@route("/api/events")
def sse_events():
    response.content_type = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"

    q = queue.Queue()
    with sse_lock:
        sse_subscribers.append(q)

    # Initial ping
    yield f"data: {json.dumps({'type': 'connected', 'data': {'time': time.time()}})}\n\n"

    try:
        while True:
            try:
                ev = q.get(timeout=25)
                yield f"data: {json.dumps(ev)}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"
    except GeneratorExit:
        pass
    finally:
        with sse_lock:
            if q in sse_subscribers:
                sse_subscribers.remove(q)

# Status & Connectivity Check Endpoint
@route("/api/status", method="GET")
def get_system_status():
    response.content_type = "application/json"
    is_private = bool(vega.cfg.get("private_mode", False))

    # Test real Gemini connectivity
    gemini_online = False
    api_key = vega.cfg.get("gemini_api_key") or os.environ.get("GEMINI_API_KEY", "")
    if api_key and not is_private:
        try:
            import requests
            url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}"
            r = requests.get(url, timeout=3)
            gemini_online = (r.status_code == 200)
        except Exception:
            gemini_online = False

    listening_on = bool(vega.cfg.get("wake_word_on", True))
    voice_on = bool(vega.cfg.get("voice_on", True))

    brain_mode_label = "Private Mode" if is_private else ("Online (Gemini)" if gemini_online else "Offline (local)")

    return json.dumps({
        "gemini_online": gemini_online,
        "private_mode": is_private,
        "brain_mode": brain_mode_label,
        "listening_on": listening_on,
        "voice_on": voice_on,
        "status_text": "Ready - just talk",
        "active_chat_id": active_chat_id
    })

# Chat Endpoints
@route("/api/chats", method="GET")
def get_chats():
    response.content_type = "application/json"
    return json.dumps(store.all())

@route("/api/chat/<chat_id>", method="GET")
def get_chat(chat_id):
    response.content_type = "application/json"
    global active_chat_id
    active_chat_id = chat_id
    chat = store.load(chat_id)
    if chat:
        # Sync Vega memory history
        vega.history = []
        for m in chat.get("messages", [])[-14:]:
            role = "user" if m.get("who") in ("you", "phone") else "assistant"
            vega.history.append({"role": role, "content": m.get("text", "")})
    return json.dumps(chat or {})

@route("/api/chat/new", method="POST")
def new_chat():
    response.content_type = "application/json"
    global active_chat_id
    chat = store.new()
    active_chat_id = chat["id"]
    vega.history = []
    store.save(chat, force=True)
    return json.dumps(chat)

@route("/api/chat/rename", method="POST")
def rename_chat():
    data = request.json or {}
    cid = data.get("id")
    title = data.get("title", "").strip() or "Untitled"
    chat = store.load(cid)
    if chat:
        chat["title"] = title
        store.save(chat)
        return json.dumps({"ok": True, "title": title})
    return json.dumps({"ok": False})

@route("/api/chat/delete", method="POST")
def delete_chat():
    data = request.json or {}
    cid = data.get("id")
    if cid:
        store.delete(cid)
        return json.dumps({"ok": True})
    return json.dumps({"ok": False})

@route("/api/chat/pin", method="POST")
def pin_chat():
    data = request.json or {}
    cid = data.get("id")
    pinned = bool(data.get("pinned", True))
    if cid:
        store.set_pinned(cid, pinned)
        return json.dumps({"ok": True, "pinned": pinned})
    return json.dumps({"ok": False})

# Regenerate Last Assistant Message
@route("/api/regenerate", method="POST")
def regenerate_last():
    data = request.json or {}
    cid = data.get("chat_id") or active_chat_id
    chat = store.load(cid)
    if not chat or not chat.get("messages"):
        return json.dumps({"ok": False})

    # Find last user prompt
    messages = chat.get("messages", [])
    last_user_text = ""
    for m in reversed(messages):
        if m.get("who") in ("you", "phone"):
            last_user_text = m.get("text", "")
            break

    if not last_user_text:
        return json.dumps({"ok": False})

    # Remove last assistant response if present
    if messages and messages[-1].get("who") not in ("you", "phone"):
        messages.pop()
        store.save(chat)

    return json.dumps({"ok": True, "prompt": last_user_text, "chat_id": cid})

# Mic Speech-to-Text Endpoints
@route("/api/mic/start", method="POST")
def mic_start():
    response.content_type = "application/json"
    log_debug("Mic recording requested...")
    broadcast_event("status", {"text": "Listening...", "badge": "listening"})
    try:
        # Record one complete utterance using ears.listen_once
        audio = ears.listen_once(timeout=8, max_len=18)
        if audio is None:
            broadcast_event("status", {"text": "Ready - just talk", "badge": "ready"})
            return json.dumps({"ok": True, "text": ""})

        # Transcribe using Google Speech when online, Whisper when offline
        text = ears.transcribe(audio)
        log_debug(f"Mic transcribed text: '{text}'")
        broadcast_event("status", {"text": "Ready - just talk", "badge": "ready"})
        return json.dumps({"ok": True, "text": text or ""})
    except Exception as e:
        log_error(f"Mic error: {e}\n{traceback.format_exc()}")
        broadcast_event("status", {"text": "Ready - just talk", "badge": "ready"})
        return json.dumps({"ok": False, "error": str(e), "text": ""})

@route("/api/mic/stop", method="POST")
def mic_stop():
    return json.dumps({"ok": True})

# Stop Generation
@route("/api/stop", method="POST")
def stop_stream():
    vega.stop()
    mouth.stop()
    return json.dumps({"ok": True})

# Send Message with Server-Sent Events Streaming
@route("/api/send", method="POST")
def send_message():
    data = request.json or {}
    text = data.get("text", "").strip()
    cid = data.get("chat_id") or active_chat_id

    if not text:
        return ""

    chat = store.load(cid) or store.new()
    store.add(chat, "you", text)

    response.content_type = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"

    q = queue.Queue()

    def on_token(chunk):
        q.put(("token", chunk))

    def worker():
        try:
            out = vega.handle(text, source="laptop", on_token=on_token)
            q.put(("done", out))
        except Exception as e:
            log_error(f"vega.handle error: {e}\n{traceback.format_exc()}")
            q.put(("error", str(e)))

    threading.Thread(target=worker, daemon=True).start()

    accumulated = ""
    while True:
        try:
            item = q.get(timeout=35)
            kind, val = item
            if kind == "token":
                accumulated = val if isinstance(val, str) else str(val)
                src = getattr(vega, "last_source_label", "Online")
                yield f"data: {json.dumps({'token': ' ', 'full': accumulated, 'source_label': src})}\n\n"
            elif kind == "done":
                ans = val.get("text", accumulated)
                src = val.get("source_label", getattr(vega, "last_source_label", "Online"))
                model_used = val.get("model", "")
                open_tab = "lablog" if val.get("open_lablog") else None
                store.add(chat, "vega", ans)

                if vega.cfg.get("voice_on", True):
                    say_text = shorten_for_speech(val.get("say") or ans)
                    threading.Thread(target=mouth.say, args=(say_text,), daemon=True).start()

                yield f"data: {json.dumps({'done': True, 'text': ans, 'source_label': src, 'model': model_used, 'open_tab': open_tab})}\n\n"
                break
            elif kind == "error":
                store.add(chat, "vega", f"Error: {val}")
                yield f"data: {json.dumps({'done': True, 'text': f'Error: {val}', 'source_label': 'Offline', 'model': 'error'})}\n\n"
                break
        except queue.Empty:
            break

# Real Lab Log Endpoints
@route("/api/lablog", method="GET")
def get_lablog():
    response.content_type = "application/json"
    tid = request.query.get("track", "linux")
    try:
        lessons = vega.track_lessons(tid)
        today_idx = vega.track_today(tid)
        idx = min(max(today_idx, 0), max(len(lessons) - 1, 0)) if lessons else 0
        lesson = lessons[idx] if lessons and 0 <= idx < len(lessons) else {}

        # Read actual ticks, notes, reviews from lablog.json
        lab_data = read_json("lablog.json", {}) or {}
        days_dict = lab_data.get("days", {})
        day_entry = days_dict.get(str(idx), {})

        ticked_indices = set(day_entry.get("ticked", []))
        raw_steps = lesson.get("steps", [])
        steps = []
        for i, s in enumerate(raw_steps):
            steps.append({
                "num": i + 1,
                "text": s,
                "done": (i + 1) in ticked_indices
            })

        return json.dumps({
            "ok": True,
            "track_id": tid,
            "track_name": vega.track_name(tid),
            "total_lessons": len(lessons),
            "today_idx": idx,
            "title": lesson.get("title", "No milestone scheduled"),
            "topic": lesson.get("topic", ""),
            "week": lesson.get("week", 1),
            "label": lesson.get("label", f"Day {idx + 1}"),
            "steps": steps,
            "notes": day_entry.get("notes", ""),
            "review": day_entry.get("review", ""),
            "score": day_entry.get("score"),
            "tracks": [{"id": t, "name": vega.track_name(t)} for t in vega.track_ids()]
        })
    except Exception as e:
        log_error(f"Error loading lablog: {e}\n{traceback.format_exc()}")
        return json.dumps({"ok": False, "error": str(e)})

@route("/api/lablog/tick", method="POST")
def tick_lablog_step():
    data = request.json or {}
    tid = data.get("track", "linux")
    step_num = int(data.get("step", 1))
    done = bool(data.get("done", True))
    try:
        idx = vega.track_today(tid)
        all_done = vega.tick_step(idx, step_num, done)
        return json.dumps({"ok": True, "step": step_num, "done": done, "all_done": all_done})
    except Exception as e:
        log_error(f"Error ticking lablog step: {e}")
        return json.dumps({"ok": False, "error": str(e)})

@route("/api/lablog/save_notes", method="POST")
def save_lablog_notes():
    data = request.json or {}
    tid = data.get("track", "linux")
    notes = data.get("notes", "").strip()
    try:
        idx = vega.track_today(tid)
        vega.add_lab_note(idx, notes)
        return json.dumps({"ok": True})
    except Exception as e:
        log_error(f"Error saving lablog notes: {e}")
        return json.dumps({"ok": False, "error": str(e)})

@route("/api/lablog/assess", method="POST")
def assess_lablog():
    data = request.json or {}
    tid = data.get("track", "linux")
    try:
        idx = vega.track_today(tid)
        review_text, score = vega.assess_day(idx)
        return json.dumps({"ok": True, "review": review_text, "score": score})
    except Exception as e:
        log_error(f"Error assessing lablog: {e}")
        return json.dumps({"ok": False, "error": str(e)})

# Live Tech News & Internships
@route("/api/news/tech", method="GET")
def get_tech_news():
    response.content_type = "application/json"
    try:
        items = vega.fetch_news("technology OR AI OR cloud computing OR smartphones", 12)
        formatted = []
        for title, url in items:
            # Extract company / publication in parentheses
            source = "Tech News"
            clean_title = title
            if " (" in title and title.endswith(")"):
                idx = title.rfind(" (")
                clean_title = title[:idx].strip()
                source = title[idx + 2:-1].strip()
            formatted.append({
                "title": clean_title,
                "source": source,
                "url": url,
                "time": "Today"
            })
        return json.dumps({"ok": True, "items": formatted})
    except Exception as e:
        log_error(f"Error fetching tech news: {e}")
        return json.dumps({"ok": False, "error": str(e), "items": []})

@route("/api/news/internships", method="GET")
def get_internships():
    response.content_type = "application/json"
    try:
        query = "internship OR intern AND (devops OR cloud OR python OR linux OR software)"
        news_items = vega.fetch_news(query, 10)
        links = vega.internship_links()
        formatted = []

        # Add live news postings
        for title, url in news_items:
            source = "Internship Opening"
            clean_title = title
            if " (" in title and title.endswith(")"):
                idx = title.rfind(" (")
                clean_title = title[:idx].strip()
                source = title[idx + 2:-1].strip()
            formatted.append({
                "title": clean_title,
                "company": source,
                "url": url,
                "badge": "New"
            })

        # Add primary career portals
        for label, url in links:
            formatted.append({
                "title": label,
                "company": "Direct Portal",
                "url": url,
                "badge": "Portal"
            })

        return json.dumps({"ok": True, "items": formatted})
    except Exception as e:
        log_error(f"Error fetching internships: {e}")
        return json.dumps({"ok": False, "error": str(e), "items": []})

# Open URL in Brave Browser
@route("/api/open_url", method="POST")
def open_url():
    data = request.json or {}
    url = data.get("url", "").strip()
    if url:
        log_debug(f"Opening URL in Brave: {url}")
        vega.open_in_browser("brave", url)
        return json.dumps({"ok": True})
    return json.dumps({"ok": False})

# Open Windows Folders (Diary / Docs)
@route("/api/open_folder", method="POST")
def open_folder_endpoint():
    data = request.json or {}
    folder_type = data.get("folder")
    if folder_type == "diary":
        open_path(here("diary"))
        return json.dumps({"ok": True})
    elif folder_type == "docs":
        open_path(vega.doc_folder())
        return json.dumps({"ok": True})
    return json.dumps({"ok": False})

# Projects API
@route("/api/projects", method="GET")
def get_projects():
    response.content_type = "application/json"
    return json.dumps(store.projects())

@route("/api/project/new", method="POST")
def create_project():
    data = request.json or {}
    name = data.get("name", "").strip() or "New Project"
    notes = data.get("notes", "").strip()
    p = store.add_project(name, notes)
    return json.dumps({"ok": True, "project": p})

@route("/api/project/<pid>", method="GET")
def get_project_detail(pid):
    response.content_type = "application/json"
    p = store.project(pid)
    files = store.project_files(pid)
    return json.dumps({"project": p or {}, "files": files})

@route("/api/project/save_notes", method="POST")
def save_project_notes():
    data = request.json or {}
    pid = data.get("id")
    notes = data.get("notes", "")
    if pid:
        store.update_project(pid, notes=notes)
        return json.dumps({"ok": True})
    return json.dumps({"ok": False})

@route("/api/project/delete", method="POST")
def delete_project():
    data = request.json or {}
    pid = data.get("id")
    if pid:
        store.delete_project(pid)
        return json.dumps({"ok": True})
    return json.dumps({"ok": False})

# Settings Endpoints
@route("/api/settings/mode", method="POST")
def set_mode():
    data = request.json or {}
    mode = data.get("mode", "online")
    if mode in ("private", "offline"):
        vega.cfg["private_mode"] = True
        save_config_value("private_mode", True)
    else:
        vega.cfg["private_mode"] = False
        save_config_value("private_mode", False)
    return json.dumps({"ok": True, "mode": mode})

@route("/api/settings/update", method="POST")
def update_setting():
    data = request.json or {}
    k = data.get("key")
    v = data.get("value")
    if k:
        vega.cfg[k] = v
        save_config_value(k, v)
        if k == "speech_rate":
            mouth.rate = float(v)
        elif k == "wake_word_on":
            wake.set_enabled(bool(v))
        elif k == "cloud_speech":
            ears.cloud = bool(v)
    return json.dumps({"ok": True})

@route("/api/settings/toggle_listening", method="POST")
def toggle_listening():
    cur = bool(vega.cfg.get("wake_word_on", True))
    new_state = not cur
    vega.cfg["wake_word_on"] = new_state
    save_config_value("wake_word_on", new_state)
    wake.set_enabled(new_state)
    log_debug(f"Toggled microphone listening to: {new_state}")
    return json.dumps({"ok": True, "listening_on": new_state})

# Floating Quick Bar Endpoint
@route("/api/quickbar/toggle", method="POST")
def toggle_quickbar():
    # Trigger floating Quick Bar or popup
    log_debug("Quick Bar toggle triggered")
    broadcast_event("toggle_quickbar", {})
    return json.dumps({"ok": True})

def desktop_folder():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            return os.path.expandvars(winreg.QueryValueEx(k, "Desktop")[0])
    except Exception:
        return os.path.join(os.path.expanduser("~"), "Desktop")

def startup_shortcut_path():
    return os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup", "Vega.lnk")

def make_shortcut(lnk):
    wscript = os.path.join(os.environ.get("SystemRoot", "C:\\Windows"), "System32", "wscript.exe")
    vbs_script = os.path.join(BASE_DIR, "Start Vega.vbs")
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{wscript}';$s.Arguments='\"{vbs_script}\"';"
          f"$s.WorkingDirectory='{BASE_DIR}';$s.Description='Vega - personal assistant';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps],
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, timeout=30, check=True)

@route("/api/settings/desktop_shortcut", method="POST")
def create_desktop_shortcut():
    try:
        lnk = os.path.join(desktop_folder(), "Vega.lnk")
        make_shortcut(lnk)
        return json.dumps({"ok": True})
    except Exception as e:
        log_error(f"Failed to create desktop shortcut: {e}")
        return json.dumps({"ok": False, "error": str(e)})

@route("/api/settings/startup", method=["GET", "POST"])
def settings_startup():
    lnk = startup_shortcut_path()
    if request.method == "POST":
        data = request.json or {}
        enabled = bool(data.get("enabled", True))
        try:
            if enabled:
                make_shortcut(lnk)
            else:
                if os.path.exists(lnk):
                    os.remove(lnk)
            return json.dumps({"ok": True, "enabled": enabled})
        except Exception as e:
            log_error(f"Failed to toggle startup shortcut: {e}")
            return json.dumps({"ok": False, "error": str(e)})
    else:
        return json.dumps({"ok": True, "enabled": os.path.exists(lnk)})

# Initial State
@route("/api/initial_state", method="GET")
def initial_state():
    response.content_type = "application/json"
    today_task = "No active task scheduled for today."
    try:
        lessons = vega.track_lessons("linux")
        today_idx = vega.track_today("linux")
        if lessons and 0 <= today_idx < len(lessons):
            today_task = lessons[today_idx].get("title", today_task)
    except Exception:
        pass

    is_private = bool(vega.cfg.get("private_mode", False))
    mode = "private" if is_private else ("online" if os.environ.get("GEMINI_API_KEY") else "offline")

    return json.dumps({
        "active_chat_id": active_chat_id,
        "today_task": today_task,
        "brain_mode": mode,
        "listening_on": bool(vega.cfg.get("wake_word_on", True)),
        "voice_on": bool(vega.cfg.get("voice_on", True)),
        "speech_rate": vega.cfg.get("speech_rate", 1.0),
        "startup_enabled": os.path.exists(startup_shortcut_path())
    })

# JavaScript Error & Debug Logging Endpoint
@route("/api/debug/log", method="POST")
def client_debug_log():
    data = request.json or {}
    err_type = data.get("type", "client_error")
    msg = data.get("message", "")
    stack = data.get("stack", "")
    src = data.get("source", "")
    line = data.get("lineno", "")
    log_error(f"[JS {err_type}] {msg} | file={src}:{line} | stack={stack}")
    return json.dumps({"ok": True})


def find_browser_exe():
    """Find Edge, Brave, or Chrome for frameless desktop app mode."""
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
        r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\BraveSoftware\Brave-Browser\Application\brave.exe"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


def main():
    log_debug("Starting Bottle HTTP server on 127.0.0.1:47632...")
    server_adapter = ThreadedServer(host="127.0.0.1", port=PORT)
    t = threading.Thread(target=lambda: run(server=server_adapter, quiet=True), daemon=True)
    t.start()
    time.sleep(0.4)

    browser = find_browser_exe()
    url = f"http://127.0.0.1:{PORT}"
    profile_dir = os.path.join(BASE_DIR, "app_profile")

    if browser:
        log_debug(f"Launching standalone window using: {browser}")
        args = [
            browser,
            f"--app={url}",
            f"--user-data-dir={profile_dir}",
            "--window-size=1260,840",
            "--window-position=100,50",
            "--enable-logging=stderr",
            "--v=1"
        ]
        p = subprocess.Popen(args)
        p.wait()
    else:
        log_debug("Browser executable not found, falling back to webbrowser.open")
        import webbrowser
        webbrowser.open(url)
        while True:
            time.sleep(1)


if __name__ == "__main__":
    main()
