"""
Vega - Hemanth's personal assistant (app window).

Start it by double-clicking the Vega shortcut on your desktop,
or in PowerShell:   cd C:\\CloudLab\\mybot   then   python vega_app.py
"""

import os
import queue
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import json
import webbrowser
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vega_core import IS_WINDOWS, NO_WINDOW, ReminderClock, Vega, here, save_config_value  # noqa: E402
from vega_voice import Ears, Mouth, WakeListener, beep, shorten_for_speech  # noqa: E402
import vega_voice  # noqa: E402
import vega_security as sec  # noqa: E402
from vega_chats import ChatStore  # noqa: E402
from vega_pages import FeedPage, ScrollFrame, ThinBar, center_pad  # noqa: E402

# calm dark palette (softer than before, easier on the eyes at night)
C = dict(side="#0D0E12", bg="#131419", panel="#1A1C23", line="#262832", ink="#F0F3F9", muted="#949AA6", accent="#7BA4F8",
         you="#A8C7FA", ok="#4ADE80", warn="#FBBF24", bad="#F87171", entry="#1A1C23", btn="#21232B", btn_hi="#2C2F3A",
         side_ink="#C8D0DC", user_bg="#22252E", chip="#1F212A", chip_hi="#2A2D39")
UI_FONT = "Segoe UI" if IS_WINDOWS else "DejaVu Sans"
F = (UI_FONT, 11)
F_SMALL = (UI_FONT, 9)
F_BOLD = (UI_FONT, 11, "bold")
F_TITLE = (UI_FONT, 18, "bold")
PORT = 47631  # used to make sure only one Vega runs at a time
URL_RE = re.compile(r"https?://[^\s)]+")
MD_LINK_RE = re.compile(r"\[([^\]\n]+)\]\((https?://[^\s)]+)\)")
LOG_FILE = "chat_log.jsonl"   # everything you and Vega said, shown again when you reopen her
STAMP_RE = re.compile(r"^\[\d{1,2} \w{3} \d{4}, \d{1,2}:\d{2} [AP]M\]\s*")


def shade(color, amount=0.10):
    """A slightly lighter version of a #RRGGBB colour (for hover)."""
    try:
        c = color.lstrip("#")
        if len(c) != 6:
            return color
        r, g, b = (int(c[i:i + 2], 16) for i in (0, 2, 4))
        return "#%02X%02X%02X" % tuple(int(v + (255 - v) * amount) for v in (r, g, b))
    except (ValueError, AttributeError):
        return color


def add_hover(b):
    """Buttons light up a little under the mouse, like a modern app."""
    def on(_):
        try:
            if str(b.cget("state")) == "disabled":
                return
            cur = b.cget("bg")
            b._hv = (cur, shade(cur))
            b.configure(bg=b._hv[1])
        except tk.TclError:
            pass

    def off(_):
        try:
            hv = getattr(b, "_hv", None)
            if hv and b.cget("bg") == hv[1]:
                b.configure(bg=hv[0])
            b._hv = None
        except tk.TclError:
            pass
    b.bind("<Enter>", on, add="+")
    b.bind("<Leave>", off, add="+")


def dark_titlebar(win, settle=True):
    """Windows 10/11: make the window's title bar dark to match Vega (it's white by default)."""
    if not IS_WINDOWS:
        return
    try:
        import ctypes
        if settle:
            win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        on = ctypes.c_int(1)
        dwm = ctypes.windll.dwmapi
        if dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on)) != 0:
            dwm.DwmSetWindowAttribute(hwnd, 19, ctypes.byref(on), ctypes.sizeof(on))  # older Windows 10
        side = C["side"].lstrip("#")
        colorref = ctypes.c_int(int(side[4:6] + side[2:4] + side[0:2], 16))  # Windows 11: title bar in Vega's colour
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(colorref), ctypes.sizeof(colorref))
    except Exception:
        pass


# ---------------------------------------------------------------- self-repair

NEEDED = {"requests": "requests", "psutil": "psutil", "pypdf": "pypdf", "docx": "python-docx",
          "ollama": "ollama", "faster_whisper": "faster-whisper", "sounddevice": "sounddevice", "numpy": "numpy",
          "speech_recognition": "SpeechRecognition"}


def missing_packages():
    import importlib.util
    out = []
    for mod, pip in NEEDED.items():
        try:
            if importlib.util.find_spec(mod) is None:
                out.append(pip)
        except (ImportError, ValueError):
            pass
    return out


def pip_python():
    """The Python that is running Vega right now (python.exe, not pythonw.exe, for pip)."""
    exe = sys.executable
    if exe.lower().endswith("pythonw.exe"):
        alt = exe[:-len("pythonw.exe")] + "python.exe"
        if os.path.exists(alt):
            return alt
    return exe


# ---------------------------------------------------------------- chat history

def log_message(who, text):
    try:
        with open(here(LOG_FILE), "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": datetime.now().isoformat(timespec="seconds"), "who": who, "text": text[:6000]},
                               ensure_ascii=False) + "\n")
    except OSError:
        pass


def read_log(last=40):
    """The last messages from chat_log.jsonl. The first time, bring in the old chats from memory.json."""
    path = here(LOG_FILE)
    if not os.path.exists(path):
        try:
            with open(here("memory.json"), "r", encoding="utf-8") as f:
                old = json.load(f)
            with open(path, "w", encoding="utf-8") as f:
                for m in old if isinstance(old, list) else []:
                    if isinstance(m, dict) and m.get("content") and m.get("role") in ("user", "assistant"):
                        f.write(json.dumps({"t": "", "who": "you" if m["role"] == "user" else "vega",
                                            "text": STAMP_RE.sub("", m["content"])}, ensure_ascii=False) + "\n")
        except (OSError, ValueError):
            return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return []
    if len(lines) > 3000:  # keep the file small
        lines = lines[-1500:]
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(lines)
    out = []
    for line in lines[-last:]:
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    return out


TENGLISH = re.compile(r"\b(cheyyi|cheyu|chey|cheppu|chepu|enti|emiti|naaku|naku|nenu|nuvvu|meeru|ledu|undi|undhi|avunu|kaadu|"
                      r"ela|entha|ekkada|eppudu|enduku|matladu|pettu|teruvu|chesi|chestha|chesa|vachindi|kavali|baagundi|"
                      r"bagundi|ivala|ivvala|repu|ninna|gurinchi|cheyali|cheyyali|chudu|chupinchu|telusa|teliyadu|vadu|"
                      r"adi|idi|emaindi|ippudu|konchem|chala|baga|anna|akka|amma|nanna|ra|raa|andi)\b", re.I)


def fix_emojis(text):
    """Tk gives emojis from the Windows emoji keyboard as two 'half' characters (surrogates).
    Join them back into one real emoji, so sending and saving never fails."""
    try:
        return text.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeError:
        return "".join(ch for ch in text if not 0xD800 <= ord(ch) <= 0xDFFF)


def detect_language(text):
    """'te' if the typed message is Telugu script or Tenglish, else 'en'."""
    if re.search(r"[\u0C00-\u0C7F]", text or ""):
        return "te"
    words = re.findall(r"[a-z]+", (text or "").lower())
    hits = [w for w in words if TENGLISH.fullmatch(w)]
    strong = [w for w in hits if w not in ("amma", "akka", "anna", "nanna", "ra", "raa", "andi", "adi", "idi")]
    return "te" if strong or len(hits) >= 2 else "en"


def nice_time(iso):
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        return ""
    t = dt.strftime("%I:%M %p").lstrip("0")
    if dt.date() == datetime.now().date():
        return t
    return dt.strftime("%d %b, ") + t


# ---------------------------------------------------------------- shortcuts

def pythonw():
    p = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return p if os.path.exists(p) else sys.executable


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
    env = dict(os.environ, VEGA_LNK=lnk, VEGA_PYW=wscript, VEGA_SCRIPT=here("Start Vega.vbs"), VEGA_DIR=here())
    ps = ("$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:VEGA_LNK);"
          "$s.TargetPath=$env:VEGA_PYW;$s.Arguments='\"'+$env:VEGA_SCRIPT+'\"';"
          "$s.WorkingDirectory=$env:VEGA_DIR;$s.Description='Vega - personal assistant';$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", ps],
                   env=env, creationflags=NO_WINDOW, timeout=30, check=True)


def open_path(path):
    if IS_WINDOWS:
        os.startfile(path)
    else:
        webbrowser.open("file://" + path)


# ---------------------------------------------------------------- floating quick bar

class QuickBar:
    def __init__(self, app):
        self.app = app
        self.win = None
        self.entry = None
        self.result_lbl = None
        self.result_frame = None
        self.dot = None
        self.dot_id = None
        self.status_lbl = None
        self.is_visible = False

    def toggle(self):
        if self.win and self.win.winfo_exists() and self.is_visible:
            self.hide()
        else:
            self.show()

    def show(self):
        if self.win is None or not self.win.winfo_exists():
            self.build()
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", True)
        self.entry.focus_set()
        self.entry.selection_range(0, "end")
        self.is_visible = True

    def hide(self):
        if self.win and self.win.winfo_exists():
            self.win.withdraw()
        self.is_visible = False

    def build(self):
        self.win = tk.Toplevel(self.app.root)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.configure(bg=C["line"])

        sw = self.win.winfo_screenwidth()
        w, h = 580, 105
        x = (sw - w) // 2
        y = 120
        self.win.geometry(f"{w}x{h}+{x}+{y}")

        self._drag = {"x": 0, "y": 0}
        def start_drag(e):
            self._drag["x"] = e.x
            self._drag["y"] = e.y
        def do_drag(e):
            dx = e.x - self._drag["x"]
            dy = e.y - self._drag["y"]
            nx = self.win.winfo_x() + dx
            ny = self.win.winfo_y() + dy
            self.win.geometry(f"+{nx}+{ny}")

        outer = tk.Frame(self.win, bg=C["line"], padx=1, pady=1)
        outer.pack(fill="both", expand=True)

        card = tk.Frame(outer, bg=C["bg"], padx=14, pady=10)
        card.pack(fill="both", expand=True)
        card.bind("<Button-1>", start_drag)
        card.bind("<B1-Motion>", do_drag)

        # Header
        header = tk.Frame(card, bg=C["bg"])
        header.pack(fill="x", pady=(0, 6))
        header.bind("<Button-1>", start_drag)
        header.bind("<B1-Motion>", do_drag)

        title_frame = tk.Frame(header, bg=C["bg"])
        title_frame.pack(side="left")
        title_frame.bind("<Button-1>", start_drag)
        title_frame.bind("<B1-Motion>", do_drag)

        tk.Label(title_frame, text="⚡ VEGA QUICK BAR", font=F_BOLD, bg=C["bg"], fg=C["accent"]).pack(side="left")

        self.dot = tk.Canvas(title_frame, width=10, height=10, bg=C["bg"], highlightthickness=0)
        self.dot.pack(side="left", padx=(10, 4))
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=C["ok"], outline="")
        self.status_lbl = tk.Label(title_frame, text="Ready", font=F_SMALL, bg=C["bg"], fg=C["muted"])
        self.status_lbl.pack(side="left")

        close_btn = tk.Label(header, text="✕", font=F_BOLD, bg=C["bg"], fg=C["muted"], cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Button-1>", lambda e: self.hide())
        close_btn.bind("<Enter>", lambda e: close_btn.configure(fg=C["ink"]))
        close_btn.bind("<Leave>", lambda e: close_btn.configure(fg=C["muted"]))

        # Input Row
        input_frame = tk.Frame(card, bg=C["entry"], padx=8, pady=4, highlightthickness=1, highlightbackground=C["line"])
        input_frame.pack(fill="x")

        self.entry = tk.Entry(input_frame, font=(UI_FONT, 11), bg=C["entry"], fg=C["ink"], relief="flat", bd=0,
                              insertbackground=C["ink"], highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True, ipady=4, padx=4)
        self.entry.bind("<Return>", lambda e: self.submit())
        self.entry.bind("<Escape>", lambda e: self.hide())

        self.mute_btn = tk.Button(input_frame, text="👂", command=self.app.toggle_mute_listening, font=F_SMALL, bg=C["entry"], fg=C["ok"],
                                  activebackground=C["btn_hi"], activeforeground=C["ink"], relief="flat", bd=0, padx=6, cursor="hand2")
        self.mute_btn.pack(side="right")
        mic_btn = tk.Button(input_frame, text="🎤", command=self.on_mic, font=F_SMALL, bg=C["entry"], fg=C["accent"],
                            activebackground=C["btn_hi"], activeforeground=C["ink"], relief="flat", bd=0, padx=6, cursor="hand2")
        mic_btn.pack(side="right")

        send_btn = tk.Button(input_frame, text="➤", command=self.submit, font=F_SMALL, bg=C["entry"], fg=C["ink"],
                             activebackground=C["btn_hi"], activeforeground=C["accent"], relief="flat", bd=0, padx=6, cursor="hand2")
        send_btn.pack(side="right")

        # Result Dropdown card
        self.result_frame = tk.Frame(card, bg=C["panel"], padx=10, pady=8, highlightthickness=1, highlightbackground=C["line"])
        self.result_lbl = tk.Label(self.result_frame, text="", font=F, bg=C["panel"], fg=C["ink"], justify="left",
                                   anchor="w", wraplength=540)
        self.result_lbl.pack(fill="both", expand=True)

    def set_status(self, text, color="ok"):
        if self.status_lbl and self.status_lbl.winfo_exists():
            self.status_lbl.configure(text=text)
            self.dot.itemconfigure(self.dot_id, fill=C.get(color, C["ok"]))

    def on_mic(self):
        self.app.on_mic()

    def submit(self):
        query = self.entry.get().strip()
        if not query:
            return
        self.entry.delete(0, "end")
        self.set_status("Thinking...", "warn")

        self.result_lbl.configure(text="Processing command...")
        if not self.result_frame.winfo_ismapped():
            self.result_frame.pack(fill="x", pady=(8, 0))
            sw = self.win.winfo_screenwidth()
            w = 580
            x = (sw - w) // 2
            y = self.win.winfo_y()
            self.win.geometry(f"{w}x190+{x}+{y}")

        def worker():
            out = self.app.vega.handle(query, source="quickbar")
            res_text = out.get("text", "")
            def update():
                self.set_status("Ready", "ok")
                short = res_text[:350] + ("..." if len(res_text) > 350 else "")
                self.result_lbl.configure(text=short)
                if self.app.voice_var.get() and out.get("say"):
                    self.app.mouth.say(out.get("say"), voice_lang=out.get("voice_lang", "auto"))
            self.app.ui.put(update)

        threading.Thread(target=worker, daemon=True).start()


# ---------------------------------------------------------------- the window

class App:
    def __init__(self, root, server):
        self.root = root
        self.vega = Vega()
        cfg = self.vega.cfg
        self.mouth = Mouth(rate=cfg.get("speech_rate", 1), voice=cfg.get("voice", "female"))
        self.ears = Ears(cfg.get("whisper_model", "small.en"), cfg.get("wake_model", "base.en"))
        vega_voice.PLAIN_PHRASES.update(str(p).lower() for p in cfg.get("wake_plain", []) if 1 < len(str(p).split()) <= 4)
        vega_voice.BLOCKED.update(str(w).lower() for w in cfg.get("not_wake_words", []) if str(w).lower() != "vega")
        vega_voice.LEARNED.update(str(w).lower() for w in cfg.get("wake_variants", []) if vega_voice.good_wake_variant(str(w)))
        self.ears.telugu = bool(cfg.get("understand_telugu", False))
        self.ears.cloud = bool(cfg.get("cloud_speech", True))
        self.mouth.telugu = bool(cfg.get("speak_telugu", False))
        self.mouth.telugu_voice = cfg.get("telugu_voice", "te-IN-ShrutiNeural")
        self.mouth.on_error = lambda m: self.ui.put(lambda: self.system(m, "warn"))
        self.ears.telugu_model_name = cfg.get("telugu_model", "small")
        self.ears.hint = self.vega.speech_hint()
        self.ears.hotwords = self.vega.speech_hotwords()
        threading.Thread(target=self.vega.refresh_app_index, daemon=True).start()  # learn every installed app
        self.ui = queue.Queue()
        self.busy = False
        self.listening = False
        self.listen_stop = None
        self.streaming = False
        self.phone = None
        self.told_about_close = False
        self.locked = False
        self.lock_frame = None
        self.pending_briefing = False
        self.pin_fails = 0
        self.recent_scores = []  # last voice matches, shown in Voice strictness
        self.C, self.F = C, {"normal": F, "small": F_SMALL, "bold": F_BOLD, "title_small": (UI_FONT, 15, "bold")}
        self.lablog_win = None
        self.welcome_shown = False
        self.last_verified = 0.0  # when your voice (or PIN) last proved it's you
        self.pin_wait_until = 0.0
        self.last_activity = time.time()
        self.voice = sec.VoiceID()
        self.quickbar = QuickBar(self)

        self.build()
        threading.Thread(target=self.accept_other_instances, args=(server,), daemon=True).start()

        ReminderClock(self.vega, lambda text: self.ui.put(lambda: self.on_reminder(text)),
                      on_briefing=lambda: self.ui.put(self.auto_briefing),
                      on_note=lambda note: self.ui.put(lambda: self.on_note(note))).start()
        self.wake = WakeListener(
            self.ears,
            on_wake=lambda audio=None: self.ui.put(lambda: self.on_wake(audio)),
            on_command=lambda text, audio=None: self.ui.put(lambda: self.voice_gate(text, audio)),
            is_busy=self.wake_busy,
            on_error=lambda m: self.ui.put(lambda: self.system(m, "warn")))
        self.wake.strictness = str(cfg.get("wake_strictness", "easy"))
        self.wake.voice_score = self.wake_voice_score
        self.front = False
        self.last_key = 0.0
        self.wake.direct = lambda: self.front
        self.wake.on_near_miss = lambda a, b: self.ui.put(lambda: self.wake_near_miss(a, b))
        self.wake.on_status = lambda t: self.ui.put(lambda: self.set_status(t or "Ready", "accent" if t else "ok"))
        threading.Thread(target=self.warm_up_ears, daemon=True).start()
        self.wake.start()
        self.wake.set_enabled(bool(self.wake_var.get()))
        self.start_hotkey()
        self.start_phone()

        self.show_history()
        self.greet()
        self.repair_if_needed()
        for ev in ("<KeyPress>", "<ButtonPress>"):
            self.root.bind_all(ev, lambda e: setattr(self, "last_activity", time.time()), add="+")
        self.root.bind_all("<KeyPress>", lambda e: setattr(self, "last_key", time.time()), add="+")
        self.watch_front()
        if sec.has_pin():
            self.lock()
        self.root.after(30000, self.check_idle)
        threading.Thread(target=self.check_ollama, daemon=True).start()
        self.root.after(50, self.poll)

    # ---------------- layout
    def button(self, parent, text, cmd, **kw):
        b = tk.Button(parent, text=text, command=cmd, font=kw.pop("font", F), bg=kw.pop("bg", C["btn"]), fg=kw.pop("fg", C["ink"]),
                      activebackground=C["btn_hi"], activeforeground=C["ink"], relief="flat", bd=0,
                      padx=kw.pop("padx", 12), pady=kw.pop("pady", 6), cursor="hand2", highlightthickness=0, **kw)
        add_hover(b)
        return b

    def center_chat(self, e):
        """Keep the chat and the typing box in one readable column (about 860 px), centred, like ChatGPT / Claude."""
        if e.widget is not self.chat_view:
            return
        side = center_pad(e.width, 860, 24)
        self.composer.pack_configure(padx=side)
        self.hint_lbl.pack_configure(padx=side)
        self.text.configure(padx=max(16, side - 24))

    def build(self):
        r = self.root
        r.title("Vega")
        r.geometry("1180x780")
        r.minsize(820, 540)
        r.configure(bg=C["bg"])
        r.protocol("WM_DELETE_WINDOW", self.on_close)
        dark_titlebar(r)
        r.after(300, lambda: dark_titlebar(r))
        r.bind_class("Toplevel", "<Map>", lambda e: dark_titlebar(e.widget, settle=False) if isinstance(e.widget, tk.Toplevel)
                     and not e.widget.overrideredirect() else None, add="+")
        self.F["title"] = F_TITLE
        self.store = ChatStore()
        all_chats = self.store.all()
        recent_chat = None
        if all_chats:
            recent_chat = self.store.load(all_chats[0]["id"])
        self.chat = recent_chat if (recent_chat and recent_chat.get("messages")) else self.store.new()
        if self.chat.get("messages"):
            hist = []
            for m in self.chat["messages"][-14:]:
                if m.get("who") in ("you", "phone"):
                    hist.append({"role": "user", "content": m.get("text", "")})
                elif m.get("who") == "vega":
                    hist.append({"role": "assistant", "content": m.get("text", "")})
            self.vega.history = hist
        self.page = None

        # ---------- sidebar (hidden on startup: opens only after pressing toggle bar)
        side = self.side = tk.Frame(r, bg=C["side"], width=264)
        side.pack_propagate(False)
        # Hidden by default so chat canvas is clean; opens when user clicks the toggle bar
        brand = tk.Frame(side, bg=C["side"])
        brand.pack(fill="x", padx=16, pady=(16, 10))
        tk.Label(brand, text="Vega", font=F_TITLE, bg=C["side"], fg=C["ink"]).pack(side="left")
        tk.Label(brand, text="your personal AI", font=F_SMALL, bg=C["side"], fg=C["muted"]).pack(side="left", padx=(8, 0), pady=(8, 0))
        self.side_button(side, "＋   New chat", self.new_chat, bold=True, bg=C["btn"]).pack(fill="x", padx=12, pady=(0, 10))

        self.side_label(side, "DAILY")
        for label, fn in (("Lab Log", lambda: self.show_page("lablog")), ("News", lambda: self.show_page("news")),
                          ("Tech news", lambda: self.show_page("tech")), ("Internships", lambda: self.show_page("internships")),
                          ("Weather", lambda: self.quick("weather")), ("My tasks", lambda: self.quick("my tasks")),
                          ("Reminders", lambda: self.quick("my reminders")), ("Diary", lambda: self.quick("open my diary"))):
            self.side_button(side, label, fn).pack(fill="x", padx=12)
        self.summ_btn = self.side_button(side, "Summaries  ▸", lambda: self.summaries_menu())
        self.summ_btn.pack(fill="x", padx=12)

        prow = tk.Frame(side, bg=C["side"])
        prow.pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(prow, text="PROJECTS", font=F_SMALL, bg=C["side"], fg=C["muted"]).pack(side="left")
        tk.Button(prow, text="＋", command=self.new_project, font=F_SMALL, bg=C["side"], fg=C["muted"], relief="flat", bd=0,
                  activebackground=C["btn_hi"], activeforeground=C["ink"], cursor="hand2", highlightthickness=0).pack(side="right")
        self.proj_box = tk.Frame(side, bg=C["side"])
        self.proj_box.pack(fill="x")
        self.side_button(side, "＋   New project", self.new_project, fg=C["muted"]).pack(fill="x", padx=12)

        self.side_label(side, "CHATS")
        self.search_var = tk.StringVar()
        search = tk.Entry(side, textvariable=self.search_var, font=F_SMALL, bg=C["entry"], fg=C["ink"], relief="flat",
                          insertbackground=C["ink"], highlightthickness=0)
        search.pack(fill="x", padx=16, pady=(2, 6), ipady=5)
        search.insert(0, "Search chats…")
        search.configure(fg=C["muted"])
        search.bind("<FocusIn>", lambda e: (search.delete(0, "end"), search.configure(fg=C["ink"])) if search.get() == "Search chats…" else None)
        self.search_var.trace_add("write", lambda *a: self.refresh_sidebar())
        bottom_side = tk.Frame(side, bg=C["side"])
        bottom_side.pack(side="bottom", fill="x", padx=12, pady=12)
        self.chat_scroll = ScrollFrame(side, C["side"])
        self.chat_scroll.pack(fill="both", expand=True, padx=(12, 0))
        self.chat_box = self.chat_scroll.inner

        # ---------- main area
        main = self.main_frame = tk.Frame(r, bg=C["bg"])
        main.pack(side="left", fill="both", expand=True)
        top = tk.Frame(main, bg=C["bg"])
        top.pack(fill="x", padx=20, pady=(12, 6))
        toggles = tk.Frame(top, bg=C["bg"])
        toggles.pack(side="right")  # packed first so it always keeps its room
        self.sidebar_toggle_btn = tk.Button(top, text="☰", command=self.toggle_sidebar,
                                            font=(UI_FONT, 12), bg=C["bg"], fg=C["muted"],
                                            activebackground=C["btn_hi"], activeforeground=C["ink"],
                                            relief="flat", bd=0, padx=6, pady=0, cursor="hand2", highlightthickness=0)
        self.sidebar_toggle_btn.pack(side="left", padx=(0, 10))
        self.title_lbl = tk.Label(top, text="New chat", font=(UI_FONT, 12, "bold"), bg=C["bg"], fg=C["ink"])
        self.title_lbl.pack(side="left")
        self.pin_btn = tk.Button(top, text="☆ Pin", command=lambda: self.toggle_pin_chat(), font=F_SMALL, bg=C["bg"],
                                 fg=C["muted"], activebackground=C["btn_hi"], activeforeground=C["ink"], relief="flat", bd=0,
                                 padx=8, cursor="hand2", highlightthickness=0)
        self.pin_btn.pack(side="left", padx=(8, 0))
        self.dot = tk.Canvas(top, width=12, height=12, bg=C["bg"], highlightthickness=0)
        self.dot.pack(side="left", padx=(14, 6), pady=(2, 0))
        self.dot_id = self.dot.create_oval(2, 2, 11, 11, fill=C["ok"], outline="")
        self.status_lbl = tk.Label(top, text="Ready", font=F_SMALL, bg=C["bg"], fg=C["muted"])
        self.status_lbl.pack(side="left")
        self.quickbar_btn = tk.Button(top, text="⚡ Quick Bar", command=self.toggle_quickbar, font=F_SMALL, bg=C["btn"],
                                      fg=C["accent"], activebackground=C["btn_hi"], activeforeground=C["ink"], relief="flat", bd=0,
                                      padx=8, pady=1, cursor="hand2", highlightthickness=0)
        self.quickbar_btn.pack(side="left", padx=(12, 0))
        self.top_mute_btn = tk.Button(top, text="🎤 Listening: ON", command=self.toggle_mute_listening, font=F_SMALL,
                                      bg=C["btn"], fg=C["ok"], activebackground=C["btn_hi"], activeforeground=C["ink"],
                                      relief="flat", bd=0, padx=8, pady=1, cursor="hand2", highlightthickness=0)
        self.top_mute_btn.pack(side="left", padx=(8, 0))

        menu_btn = tk.Menubutton(bottom_side, text="⚙   Settings and more", font=F, bg=C["side"], fg=C["ink"],
                                 activebackground=C["btn_hi"], activeforeground=C["ink"], relief="flat", padx=10, pady=8,
                                 cursor="hand2", anchor="w", highlightthickness=0)
        menu = tk.Menu(menu_btn, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"], activeforeground=C["ink"])
        menu.add_command(label="⚡ Floating Quick Bar (Ctrl + Shift + V)", command=self.toggle_quickbar)
        menu.add_command(label="Personalize Vega…", command=self.personalize)
        menu.add_command(label="Import contacts (.vcf)…", command=self.import_contacts)
        self.plan_var = tk.BooleanVar(value=bool(self.vega.cfg.get("study_plan_reminders", False)))
        menu.add_checkbutton(label="Remind me about my 24-week study plan", variable=self.plan_var,
                             command=lambda: (self.vega.cfg.__setitem__("study_plan_reminders", bool(self.plan_var.get())),
                                              save_config_value("study_plan_reminders", bool(self.plan_var.get())),
                                              self.system("Study plan reminders on." if self.plan_var.get() else
                                                          "Study plan reminders off. I'll only remind you about tasks and reminders you set.")))
        menu.add_command(label="What Vega remembers about me", command=lambda: self.quick("what do you remember about me"))
        menu.add_command(label="Help: everything I can do", command=lambda: self.quick("help"))
        menu.add_separator()
        self.startup_var = tk.BooleanVar(value=os.path.exists(startup_shortcut_path()))
        menu.add_checkbutton(label="Start with Windows", variable=self.startup_var, command=self.toggle_startup)
        menu.add_command(label="Create desktop shortcut", command=self.desktop_shortcut)
        menu.add_command(label="Connect phone (Telegram)…", command=self.connect_phone)
        menu.add_separator()
        menu.add_command(label="Open documents folder", command=lambda: open_path(self.vega.doc_folder()))
        menu.add_command(label="Open diary folder", command=lambda: open_path(here("diary")))
        menu.add_command(label="Edit settings (config.json)", command=self.edit_settings)
        menu.add_command(label="Delete this chat", command=self.clear_chat)
        menu.add_separator()
        menu.add_command(label="Set or change PIN…", command=self.set_pin)
        menu.add_command(label="Remove PIN", command=self.remove_pin)
        menu.add_command(label="Lock Vega now", command=self.lock)
        lock_menu = tk.Menu(menu, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"], activeforeground=C["ink"])
        self.lock_min_var = tk.IntVar(value=sec.lock_minutes())
        for label, n in (("After 5 minutes", 5), ("After 15 minutes", 15), ("After 30 minutes", 30),
                         ("After 1 hour", 60), ("After 3 hours", 180), ("Never (only when I start)", 0)):
            lock_menu.add_radiobutton(label=label, value=n, variable=self.lock_min_var,
                                      command=lambda n=n: self.set_autolock(n))
        menu.add_cascade(label="Auto-lock", menu=lock_menu)
        self.brain_menu = tk.Menu(menu, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"],
                                  activeforeground=C["ink"], postcommand=self.fill_brain_menu)
        menu.add_cascade(label="Brain (AI model)", menu=self.brain_menu)
        menu.add_separator()
        acc_menu = tk.Menu(menu, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"], activeforeground=C["ink"])
        self.acc_var = tk.StringVar(value=str(self.vega.cfg.get("whisper_model", "small.en")))
        for label, val in (("Fast (small.en)", "small.en"), ("Better for Indian accent (medium.en, 1.5 GB)", "medium.en"),
                           ("Best English (distil-large-v3, 1.5 GB)", "distil-large-v3")):
            acc_menu.add_radiobutton(label=label, value=val, variable=self.acc_var, command=lambda v=val: self.set_speech_model(v))
        acc_menu.add_separator()
        self.acc_te_var = tk.StringVar(value=str(self.vega.cfg.get("telugu_model", "small")))
        for label, val in (("Telugu listening: fast (small)", "small"), ("Telugu listening: better (medium, 1.5 GB)", "medium")):
            acc_menu.add_radiobutton(label=label, value=val, variable=self.acc_te_var, command=lambda v=val: self.set_telugu_model(v))
        self.cloud_var = tk.BooleanVar(value=bool(self.vega.cfg.get("cloud_speech", True)))
        acc_menu.add_separator()
        acc_menu.add_checkbutton(label="Use Google speech when online (best for Indian accent)", variable=self.cloud_var,
                                 command=self.toggle_cloud_speech)
        menu.add_cascade(label="Speech accuracy", menu=acc_menu)
        menu.add_command(label="Voice check-up (find and fix voice problems)…", command=self.voice_checkup)
        menu.add_command(label="Set up my voice…", command=self.setup_voice)
        menu.add_command(label="Voice strictness…", command=self.voice_strictness)
        menu.add_command(label="Teach me your “Hey Vega”…", command=self.teach_wake)
        wake_menu = tk.Menu(menu, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"], activeforeground=C["ink"])
        self.wake_strict_var = tk.StringVar(value=str(self.vega.cfg.get("wake_strictness", "easy")))
        for label, val in (("Strict (fewest mistakes)", "strict"), ("Normal", "normal"),
                           ("Easy", "easy"), ("Very easy (wakes up most easily)", "very_easy")):
            wake_menu.add_radiobutton(label=label, value=val, variable=self.wake_strict_var,
                                      command=lambda v=val: self.set_wake_strictness(v))
        menu.add_cascade(label="Hey Vega sensitivity", menu=wake_menu)
        self.learn_var = tk.BooleanVar(value=bool(self.vega.cfg.get("auto_learn", True)))
        menu.add_checkbutton(label="Learn from every chat by myself (saves the important points)", variable=self.learn_var,
                             command=lambda: (self.vega.cfg.__setitem__("auto_learn", bool(self.learn_var.get())),
                                              save_config_value("auto_learn", bool(self.learn_var.get()))))
        self.recall_var = tk.BooleanVar(value=bool(self.vega.cfg.get("recall_chats", True)))
        menu.add_checkbutton(label="Use my old chats when answering", variable=self.recall_var,
                             command=lambda: (self.vega.cfg.__setitem__("recall_chats", bool(self.recall_var.get())),
                                              save_config_value("recall_chats", bool(self.recall_var.get()))))
        self.bubbles_var = tk.BooleanVar(value=bool(self.vega.cfg.get("bubbles", True)))
        menu.add_checkbutton(label="Show pop-up messages when I'm minimized", variable=self.bubbles_var,
                             command=lambda: (self.vega.cfg.__setitem__("bubbles", bool(self.bubbles_var.get())),
                                              save_config_value("bubbles", bool(self.bubbles_var.get())),
                                              None if self.bubbles_var.get() else self.hide_bubble()))
        self.direct_var = tk.BooleanVar(value=bool(self.vega.cfg.get("talk_without_wake", True)))
        menu.add_checkbutton(label="No “Hey Vega” needed while my window is open", variable=self.direct_var,
                             command=self.toggle_direct)
        self.wake_me_var = tk.BooleanVar(value=bool(self.vega.cfg.get("wake_only_me", True)))
        menu.add_checkbutton(label="Wake up only for my voice", variable=self.wake_me_var, command=self.toggle_wake_only_me)
        self.telugu_var = tk.BooleanVar(value=bool(self.vega.cfg.get("understand_telugu", False)))
        menu.add_checkbutton(label="Understand Telugu speech", variable=self.telugu_var, command=self.toggle_telugu)
        self.speak_te_var = tk.BooleanVar(value=bool(self.vega.cfg.get("speak_telugu", False)))
        lang_menu = tk.Menu(menu, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"], activeforeground=C["ink"])
        self.lang_var = tk.StringVar(value=self.voice_mode())
        for label, val in (("Same as I speak (Telugu or English)", "auto"), ("Always English", "english"),
                           ("Always Telugu", "telugu")):
            lang_menu.add_radiobutton(label=label, value=val, variable=self.lang_var, command=lambda v=val: self.set_voice_language(v))
        menu.add_cascade(label="Voice language", menu=lang_menu)
        self.voice_only_var = tk.BooleanVar(value=sec.voice_only())
        menu.add_checkbutton(label="Only obey my voice", variable=self.voice_only_var, command=self.toggle_voice_only)
        menu.add_separator()
        close_menu = tk.Menu(menu, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"], activeforeground=C["ink"])
        self.close_var = tk.StringVar(value=str(self.vega.cfg.get("close_action", "ask")).lower())
        for label, val in (("Ask me every time", "ask"), ("Quit Vega", "quit"), ("Keep running in background", "hide")):
            close_menu.add_radiobutton(label=label, value=val, variable=self.close_var,
                                       command=lambda v=val: self.set_close_action(v))
        menu.add_cascade(label="Close button (X)", menu=close_menu)
        menu.add_command(label="Quit Vega", command=self.quit)
        menu_btn.configure(menu=menu)
        menu_btn.pack(fill="x")

        self.voice_var = tk.IntVar(value=1 if self.vega.cfg.get("voice_on", True) else 0)
        self.wake_var = tk.IntVar(value=1 if self.vega.cfg.get("wake_word_on", False) else 0)
        for text, var, cmd in (("Speak answers", self.voice_var, self.toggle_voice),
                               ("Listen for “Hey Vega”", self.wake_var, self.toggle_wake)):
            tk.Checkbutton(toggles, text=text, variable=var, command=cmd, font=F_SMALL, bg=C["bg"], fg=C["muted"],
                           selectcolor=C["panel"], activebackground=C["bg"], activeforeground=C["ink"],
                           highlightthickness=0, bd=0, cursor="hand2").pack(side="left", padx=(14, 0))

        self.body = tk.Frame(main, bg=C["bg"])
        self.body.pack(fill="both", expand=True)
        self.chat_view = tk.Frame(self.body, bg=C["bg"])
        self.chat_view.pack(fill="both", expand=True)
        box = tk.Frame(self.chat_view, bg=C["bg"])
        box.pack(fill="both", expand=True, padx=(24, 12))
        self.text = tk.Text(box, wrap="word", font=F, height=10, bg=C["bg"], fg=C["ink"], relief="flat", bd=0, padx=40, pady=12,
                            insertbackground=C["ink"], selectbackground=C["btn_hi"], spacing1=2, spacing3=2, cursor="arrow",
                            highlightthickness=0)
        sb = ThinBar(box, self.text.yview, C["bg"])
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("who_you", foreground=C["you"], font=(UI_FONT, 10, "bold"), spacing1=14)
        self.text.tag_configure("who_vega", foreground=C["accent"], font=(UI_FONT, 10, "bold"), spacing1=14)
        self.text.tag_configure("body", foreground=C["ink"], lmargin1=4, lmargin2=4, spacing3=4)
        self.text.tag_configure("bold", font=F_BOLD)
        self.text.tag_configure("heading", font=(UI_FONT, 13, "bold"), spacing1=10, spacing3=3)
        self.text.tag_configure("bullet", lmargin1=14, lmargin2=34)
        self.text.tag_configure("codeblock", font=("Consolas" if IS_WINDOWS else "DejaVu Sans Mono", 10), foreground="#6EE7B7",
                                background="#16171E", lmargin1=14, lmargin2=14)
        self.text.tag_configure("actions", foreground=C["muted"], font=F_SMALL, spacing1=3, spacing3=6)
        self.text.tag_configure("you_body", background=C["user_bg"], lmargin1=140, lmargin2=140, rmargin=12, spacing1=6, spacing3=8)
        self.text.tag_configure("who_you_r", foreground=C["you"], font=(UI_FONT, 10, "bold"), spacing1=14, lmargin1=140)
        self.text.tag_configure("welcome", foreground=C["accent"], font=(UI_FONT, 25, "bold"), justify="center", spacing1=60, spacing3=6)
        self.text.tag_configure("welcome_sub", foreground=C["muted"], font=(UI_FONT, 12), justify="center", spacing3=24)
        self.text.tag_configure("suggest_title", foreground=C["ink"], background=C["chip"], font=(UI_FONT, 10, "bold"), justify="center")
        self.text.tag_configure("suggest_desc", foreground=C["muted"], font=(UI_FONT, 10), justify="center", spacing3=10)
        self.text.tag_configure("muted", foreground=C["muted"], font=F_SMALL, spacing1=8)
        self.text.tag_configure("stamp", foreground=C["muted"], font=F_SMALL)
        self.text.tag_configure("divider", foreground=C["muted"], font=F_SMALL, justify="center", spacing1=14, spacing3=4)
        self.text.tag_configure("warn", foreground=C["warn"], font=F_SMALL, spacing1=8)
        self.text.tag_configure("link", foreground=C["accent"], underline=True)
        self.text.tag_configure("code", font=("Consolas" if IS_WINDOWS else "DejaVu Sans Mono", 10), foreground=C["ok"])
        self.text.tag_bind("link", "<Button-1>", self.click_link)
        self.text.tag_bind("link", "<Enter>", lambda e: self.text.configure(cursor="hand2"))
        self.text.tag_bind("link", "<Leave>", lambda e: self.text.configure(cursor="arrow"))
        self.text.bind("<Key>", lambda e: None if (e.state & 4 and e.keysym.lower() in ("c", "a")) else "break")

        composer = self.composer = tk.Frame(self.chat_view, bg=C["entry"], highlightthickness=1, highlightbackground=C["line"])
        composer.pack(fill="x", padx=64, pady=(10, 4))
        # like ChatGPT / Claude: messages and the typing box stay in one readable column, even on a wide screen
        self._chat_box = box
        self.chat_view.bind("<Configure>", self.center_chat, add="+")
        bottom = tk.Frame(composer, bg=C["entry"])
        bottom.pack(fill="x", padx=8, pady=8)
        self.entry_var = tk.StringVar()
        self.entry = tk.Entry(bottom, textvariable=self.entry_var, font=F, bg=C["entry"], fg=C["ink"], insertbackground=C["ink"],
                              relief="flat", highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(6, 0))
        # "Message Vega…" hint inside the empty box (it's only a label on top, never part of what you send)
        self.ph = tk.Label(bottom, text="Message Vega…", font=F, bg=C["entry"], fg=C["muted"], cursor="xterm")
        self.ph.bind("<Button-1>", lambda e: self.entry.focus_set())

        def placeholder(*_):
            try:
                if self.entry_var.get():
                    self.ph.place_forget()
                else:
                    self.ph.place(in_=self.entry, x=2, rely=0.5, anchor="w")
            except tk.TclError:
                pass
        self.entry_var.trace_add("write", placeholder)
        self.root.after(150, placeholder)
        self.entry.bind("<Return>", lambda e: self.on_send())
        # Windows' own emoji keyboard (Windows + .) can't type into this kind of box, so Ctrl + . opens Vega's emoji picker
        for key in ("<Control-period>", "<Control-e>", "<Control-E>"):
            self.entry.bind(key, lambda e: (self.show_emojis(), "break")[1])
        for key in ("<Control-m>", "<Control-M>"):
            self.root.bind(key, lambda e: (self.toggle_mute_listening(), "break")[1])
        for key in ("<Control-b>", "<Control-B>"):
            self.root.bind(key, lambda e: (self.toggle_sidebar(), "break")[1])
        self.button(bottom, "📎", self.on_attach, font=(UI_FONT, 10), bg=C["entry"], fg=C["muted"]).pack(side="left", padx=(2, 4))
        self.prompts_btn = self.button(bottom, "✨ Prompts", self.show_prompts, font=(UI_FONT, 9), bg=C["btn"], fg=C["muted"])
        self.prompts_btn.pack(side="left", padx=3)
        self.mic_btn = self.button(bottom, "🎤", self.on_mic, font=(UI_FONT, 11), bg=C["btn"], fg=C["accent"])
        self.mic_btn.pack(side="left", padx=3)
        self.mute_btn = self.button(bottom, "👂", self.toggle_mute_listening, font=(UI_FONT, 11), bg=C["btn"], fg=C["ok"])
        self.mute_btn.pack(side="left", padx=3)
        self.send_btn = tk.Button(bottom, text="➤", command=self.on_send, font=(UI_FONT, 11, "bold"), bg=C["accent"], fg="#0D0E12",
                                  activebackground="#A8C7FA", activeforeground="#0D0E12", relief="flat", bd=0, padx=10, pady=4, cursor="hand2")
        self.send_btn.pack(side="left", padx=(6, 2))
        self.hint_lbl = tk.Label(self.chat_view, text="Enter to send  ·  Ctrl + . for emojis  ·  Ctrl + Alt + V to talk from any app  ·  Say “help”",
                                 font=F_SMALL, bg=C["bg"], fg=C["muted"])
        self.hint_lbl.pack(pady=(0, 10))
        self.refresh_sidebar()
        self.entry.focus_set()

    # ---------------- chat display
    def _insert(self, text, tags):
        try:
            self.text.insert("end", text, tags)
        except tk.TclError:  # a character this Tk can't show (some emojis): show the rest anyway
            self.text.insert("end", "".join(ch if ord(ch) < 0x10000 else "□" for ch in text), tags)

    def _insert_inline(self, line, base=("body",)):
        """**bold**, `code` and links inside one line."""
        for n, chunk in enumerate(re.split(r"`", line)):
            if n % 2:
                self._insert(chunk, base + ("code",))
                continue
            for k, part in enumerate(re.split(r"\*\*(.+?)\*\*", chunk)):
                tags = base + (("bold",) if k % 2 else ())
                # [title](https://...) -> just the title, clickable (no long ugly links on screen)
                for j, piece in enumerate(MD_LINK_RE.split(part)):
                    if j % 3 == 1:
                        continue
                    if j % 3 == 2:
                        self._link_n = getattr(self, "_link_n", 0) + 1
                        name = f"url{self._link_n}"
                        self.link_urls = getattr(self, "link_urls", {})
                        self.link_urls[name] = piece
                        self._insert(MD_LINK_RE.split(part)[j - 1], tags + ("link", name))
                        continue
                    pos = 0
                    for m in URL_RE.finditer(piece):
                        self._insert(piece[pos:m.start()], tags)
                        self._insert(m.group(0), tags + ("link",))
                        pos = m.end()
                    self._insert(piece[pos:], tags)

    def _insert_body(self, text):
        """Show an answer nicely, like ChatGPT: headings, bullet points, bold, code blocks and links."""
        in_code = False
        for line in text.split("\n"):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                self._insert(line + "\n", ("body", "codeblock"))
                continue
            m = re.match(r"^\s*#{1,4}\s+(.*)$", line)
            if m:
                self._insert_inline(m.group(1).replace("**", ""), ("body", "heading"))
                self._insert("\n", ("body",))
                continue
            m = re.match(r"^(\s*)[-*•]\s+(.*)$", line)
            if m:
                self._insert("    " * (len(m.group(1)) // 2) + "•  ", ("body", "bullet"))
                self._insert_inline(m.group(2), ("body", "bullet"))
                self._insert("\n", ("body",))
                continue
            self._insert_inline(line)
            self._insert("\n", ("body",))

    def _message_actions(self, text, retry=False):
        """Small 'Copy' and 'Try again' links under Vega's answers."""
        self._n_actions = getattr(self, "_n_actions", 0) + 1
        n = self._n_actions
        self._insert("Copy", ("actions", f"copy{n}"))
        self.text.tag_bind(f"copy{n}", "<Button-1>", lambda e, t=text: self.copy_text(t))
        if retry:
            self._insert("     Try again", ("actions", f"retry{n}"))
            self.text.tag_bind(f"retry{n}", "<Button-1>", lambda e: self.try_again())
        self._insert("\n", ("actions",))
        for tag in (f"copy{n}", f"retry{n}"):
            self.text.tag_bind(tag, "<Enter>", lambda e: self.text.configure(cursor="hand2"))
            self.text.tag_bind(tag, "<Leave>", lambda e: self.text.configure(cursor="arrow"))

    def copy_text(self, text):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status("Copied", "ok")

    def try_again(self):
        """Ask the last question again for a new answer (like ChatGPT's regenerate)."""
        if self.busy:
            return
        last = next((m["text"] for m in reversed(self.chat["messages"]) if m.get("who") in ("you", "phone")), None)
        if not last:
            return
        h = self.vega.history
        if len(h) >= 2 and h[-1]["role"] == "assistant":
            del h[-2:]  # forget the old answer so the new one is fresh
        self.system("Trying again…")
        self.run_job(lambda on_token: self.vega.handle(last, on_token=on_token))

    def _name(self, who, when=None):
        tag = "who_you_r" if who in ("you", "phone") else "who_vega"
        label = {"you": "You", "phone": "You (phone)"}.get(who, "Vega")
        self.text.insert("end", label, (tag,))
        stamp = nice_time(when) if when is not None else nice_time(datetime.now().isoformat())
        if stamp:
            self.text.insert("end", "   " + stamp, (tag, "stamp"))
        self.text.insert("end", "\n", (tag,))

    def add_message(self, who, text, when=None, save=True):
        if self.page is not None:
            self.show_chat_view()
        if self.welcome_shown:
            self.text.delete("1.0", "end")
            self.welcome_shown = False
        self._name(who, when)
        if who in ("you", "phone"):
            self._insert(text.strip() + "\n", ("body", "you_body"))
        else:
            self._insert_body(text.strip())
            self._message_actions(text.strip())
        self.text.see("end")
        if save:
            self.save_message(who, text)

    def save_message(self, who, text):
        new_chat = not self.chat["messages"]
        old_title = self.chat["title"]
        self.store.add(self.chat, who, text)
        if new_chat or self.chat["title"] != old_title:
            self.refresh_sidebar()
            self.update_title()

    def show_history(self):
        """Show the current chat's messages (or the welcome screen for a new chat)."""
        self.text.delete("1.0", "end")
        self.welcome_shown = False
        if not self.chat["messages"]:
            self.show_welcome()
            return
        for m in self.chat["messages"][-200:]:
            self.add_message(m.get("who", "vega"), m.get("text", ""), when=m.get("t", ""), save=False)

    def show_welcome(self):
        h = datetime.now().hour
        part = "morning" if h < 12 else ("afternoon" if h < 17 else "evening")
        proj = self.store.project(self.chat.get("project")) if self.chat.get("project") else None
        self.text.insert("end", "\n")
        self.text.insert("end", f"Good {part}, Hemanth\n", ("welcome",))
        self.text.insert("end", (f"Project: {proj['name']}  ·  " if proj else "") + "How can I help you today?\n\n", ("welcome_sub",))
        cards = [
            ("⚡ Morning Briefing", "Catch up on Chennai weather, tasks & news", "good morning"),
            ("💻 DevOps & Cloud Lab", "Practice commands or check VirtualBox server", "open lab log"),
            ("📝 Tasks & Study Plan", "Review your 24-week schedule & reminders", "my tasks"),
            ("🔍 Tech News & Internships", "Explore latest DevOps & Linux opportunities", "internships"),
        ]
        for n, (title, desc, cmd) in enumerate(cards):
            tag = f"sg{n}"
            self.text.insert("end", f"   {title}   ", ("suggest_title", tag))
            self.text.insert("end", f"  {desc}\n\n", ("suggest_desc", tag))
            self.text.tag_bind(tag, "<Button-1>", lambda e, c=cmd: self.send(c))
            self.text.tag_bind(tag, "<Enter>", lambda e: self.text.configure(cursor="hand2"))
            self.text.tag_bind(tag, "<Leave>", lambda e: self.text.configure(cursor="arrow"))
        self.welcome_shown = True

    DEFAULT_PROMPTS = [
        ("Explain simply", "Explain this in simple English with one real-life example and one command I can try: "),
        ("Interview practice", "Act as an interviewer for a cloud/DevOps intern role. Ask me one question at a time about: "),
        ("Fix my error", "Here is an error I got. Tell me what it means, the likely cause, and the exact fix step by step:\n"),
        ("Make notes", "Make short exam notes with headings and bullet points on: "),
        ("Quiz me", "Give me 5 quick questions (one at a time) to test me on: "),
    ]

    def prompt_library(self):
        from vega_core import read_json
        saved = read_json("prompts.json", None)
        if not isinstance(saved, list):
            saved = [{"name": n, "text": t} for n, t in self.DEFAULT_PROMPTS]
        return saved

    EMOJIS = ("😀 😂 🤣 😊 😍 😘 😎 🤔 😅 😢 😭 😡 😴 🥳 🤩 😇 🙏 👍 👎 👌 👏 🙌 💪 🤝 "
              "❤️ 💔 🔥 ✨ 🎉 ✅ ❌ ⭐ 💯 🚀 💻 📚 📝 ☕ 🍕 🎵").split()

    def show_emojis(self):
        """A small emoji picker (Tk's text box can't always take emojis from the Windows emoji keyboard)."""
        win = getattr(self, "emoji_win", None)
        if win is not None and win.winfo_exists():
            win.destroy()
            return
        win = self.emoji_win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.configure(bg=C["panel"], highlightthickness=1, highlightbackground=C["line"])
        for i, e in enumerate(self.EMOJIS):
            b = tk.Label(win, text=e, font=("Segoe UI Emoji" if IS_WINDOWS else UI_FONT, 16), bg=C["panel"], fg=C["ink"],
                         cursor="hand2", padx=4, pady=2)
            b.grid(row=i // 10, column=i % 10)
            b.bind("<Button-1>", lambda ev, e=e: self.insert_emoji(e))
            b.bind("<Enter>", lambda ev, w=b: w.configure(bg=C["btn_hi"]))
            b.bind("<Leave>", lambda ev, w=b: w.configure(bg=C["panel"]))
        win.update_idletasks()
        x = self.entry.winfo_rootx() + 10
        y = self.entry.winfo_rooty() - win.winfo_height() - 12
        win.geometry(f"+{max(0, x)}+{max(0, y)}")
        win.bind("<Escape>", lambda e: win.destroy())
        win.bind("<FocusOut>", lambda e: win.after(150, lambda: win.winfo_exists() and win.destroy()))
        win.focus_set()

    def insert_emoji(self, e):
        try:
            self.entry.insert("insert", e)
        except tk.TclError:
            self.entry.insert("insert", "(" + e.encode("unicode_escape").decode() + ")")
        self.entry.focus_set()

    def show_prompts(self):
        """Saved prompts: pick one to put it in the message box (then add your topic and press Enter)."""
        m = tk.Menu(self.root, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"], activeforeground=C["ink"])
        for p in self.prompt_library():
            m.add_command(label=p["name"], command=lambda p=p: self.use_prompt(p["text"]))
        m.add_separator()
        m.add_command(label="＋ Save a new prompt…", command=self.add_prompt)
        m.add_command(label="Delete a prompt…", command=self.delete_prompt)
        x, y = self.prompts_btn.winfo_rootx(), self.prompts_btn.winfo_rooty()
        m.tk_popup(x, y - 10 - 26 * (len(self.prompt_library()) + 3))

    def use_prompt(self, text):
        self.show_chat_view()
        self.entry.delete(0, "end")
        self.entry.insert(0, text.replace("\n", " "))
        self.entry.focus_set()
        self.entry.icursor("end")

    def add_prompt(self):
        from vega_core import write_json
        name = simpledialog.askstring("Save a prompt", "Short name (e.g. 'Lab report'):", parent=self.root)
        if not name or not name.strip():
            return
        text = simpledialog.askstring("Save a prompt", "The prompt text (you add your topic after it when you use it):",
                                      parent=self.root)
        if not text or not text.strip():
            return
        lib = self.prompt_library()
        lib.append({"name": name.strip()[:40], "text": text.strip()[:2000] + " "})
        write_json("prompts.json", lib)
        self.system(f"Saved the prompt “{name.strip()}”. Find it under the Prompts button.")

    def delete_prompt(self):
        from vega_core import write_json
        lib = self.prompt_library()
        name = simpledialog.askstring("Delete a prompt", "Name of the prompt to delete:\n" + ", ".join(p["name"] for p in lib),
                                      parent=self.root)
        if name:
            new = [p for p in lib if p["name"].lower() != name.strip().lower()]
            write_json("prompts.json", new)
            self.system("Deleted." if len(new) < len(lib) else "I couldn't find that prompt.")

    def personalize(self):
        """Like ChatGPT's custom instructions: what Vega should know about you, and how she should answer."""
        from vega_core import read_json, write_json
        data = read_json("personal.json", {}) or {}
        win = tk.Toplevel(self.root)
        win.title("Personalize Vega")
        win.configure(bg=C["bg"], padx=20, pady=16)
        win.transient(self.root)
        boxes = {}
        for key, title, hint in (("about", "What should Vega know about you?",
                                  "e.g. I'm Hemanth, final-year student at SRM. I'm learning Linux, OpenShift and cloud. "
                                  "I want a cloud/DevOps internship. I speak Telugu and English."),
                                 ("style", "How should Vega answer you?",
                                  "e.g. Use simple English. Keep answers short. Give examples. Explain like a friend. "
                                  "For commands, show them step by step."),
                                 ("prompt", "My own prompt for Vega (advanced, like a system prompt)",
                                  "Any instructions you want her to follow in every chat, e.g. 'Act as my senior DevOps mentor. "
                                  "Before answering, think step by step. Always end with one practice task. Point out my mistakes honestly.'")):
            tk.Label(win, text=title, font=F_BOLD, bg=C["bg"], fg=C["ink"], anchor="w").pack(fill="x", pady=(6, 2))
            tk.Label(win, text=hint, font=F_SMALL, bg=C["bg"], fg=C["muted"], anchor="w", justify="left",
                     wraplength=560).pack(fill="x")
            box = tk.Text(win, height=7 if key == "prompt" else 4, width=70, wrap="word", font=F, bg=C["entry"], fg=C["ink"], relief="flat",
                          insertbackground=C["ink"], padx=8, pady=6, highlightthickness=1, highlightbackground=C["line"])
            box.insert("1.0", data.get(key, ""))
            box.pack(fill="x", pady=(4, 8))
            boxes[key] = box
        tk.Label(win, text="Saved only on this laptop (personal.json). Don't put passwords or bank details here.",
                 font=F_SMALL, bg=C["bg"], fg=C["muted"], anchor="w").pack(fill="x")

        def save():
            write_json("personal.json", {k: b.get("1.0", "end").strip()[:4000] for k, b in boxes.items()})
            win.destroy()
            self.system("Saved. I'll use this in every chat.")

        row = tk.Frame(win, bg=C["bg"])
        row.pack(fill="x", pady=(10, 0))
        self.button(row, "Save", save, bg=C["accent"], fg=C["bg"], font=F_BOLD).pack(side="right")
        self.button(row, "Cancel", win.destroy).pack(side="right", padx=8)
        win.grab_set()

    # ---------------- sidebar: chats, projects, daily pages
    def side_button(self, parent, text, cmd, bold=False, bg=None, fg=None):
        bg = bg or C["side"]
        b = tk.Button(parent, text=text, command=cmd, font=F_BOLD if bold else F, bg=bg, fg=fg or C["ink"], anchor="w",
                      activebackground=C["btn_hi"], activeforeground=C["ink"], relief="flat", bd=0, padx=10, pady=5, cursor="hand2",
                      highlightthickness=0)
        b.bind("<Enter>", lambda e: b.configure(bg=C["btn_hi"]))
        b.bind("<Leave>", lambda e: b.configure(bg=b._vega_bg))
        b._vega_bg = bg
        return b

    SUMMARIES = (("My whole day", "summarize my day"), ("News", "summarize the news"), ("Tech news", "summarize tech news"),
                 ("Weather", "summarize the weather"), ("My tasks", "summarize my tasks"),
                 ("My diary: this week", "summarize my diary"), ("My diary: today", "summarize my diary today"))

    def summaries_menu(self):
        """Sidebar → Summaries: a short summary (free, on this laptop) of the day, news, weather, tasks or diary."""
        m = tk.Menu(self.root, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"], activeforeground=C["ink"])
        m.add_command(label="Summarize…", state="disabled")
        for label, cmd in self.SUMMARIES:
            m.add_command(label="   " + label, command=lambda c=cmd: self.quick(c))
        w = self.summ_btn
        try:
            m.tk_popup(w.winfo_rootx() + w.winfo_width() - 12, w.winfo_rooty())
        finally:
            m.grab_release()

    def side_label(self, parent, text):
        tk.Label(parent, text=text, font=F_SMALL, bg=C["side"], fg=C["muted"], anchor="w").pack(fill="x", padx=16, pady=(14, 2))

    def refresh_sidebar(self):
        for w in self.proj_box.winfo_children():
            w.destroy()
        for p in self.store.pinned_projects_first():
            current = self.page == ("project", p["id"])
            b = self.side_button(self.proj_box, ("★  " if p.get("pinned") else "▸  ") + p["name"],
                                 lambda p=p: self.show_project(p["id"]), bg=C["btn"] if current else None)
            b.pack(fill="x", padx=12)
            b.bind("<Button-3>", lambda e, p=p: self.project_menu(e, p["id"]))
        for w in self.chat_box.winfo_children():
            w.destroy()
        q = self.search_var.get().strip() if hasattr(self, "search_var") else ""
        if q and q != "Search chats…":
            hits = self.store.search(q)
            tk.Label(self.chat_box, text=f"{len(hits)} chat{'s' if len(hits) != 1 else ''} found", font=F_SMALL, bg=C["side"],
                     fg=C["muted"], anchor="w").pack(fill="x", padx=4, pady=(0, 2))
            for c in hits[:60]:
                b = self.side_button(self.chat_box, c["title"], lambda c=c: self.open_chat(c["id"]), fg=C["side_ink"])
                b.pack(fill="x", padx=(0, 8))
            return
        every = self.store.all()
        pinned = [c for c in every if c["pinned"]]
        recent = [c for c in every if not c["pinned"] and not c["project"]][:80]
        if self.chat["messages"] and self.chat.get("pinned") and not any(c["id"] == self.chat["id"] for c in pinned):
            pinned.insert(0, {"id": self.chat["id"], "title": self.chat["title"], "pinned": True, "project": self.chat.get("project")})

        def row(c):
            current = c["id"] == self.chat["id"] and self.page is None
            b = self.side_button(self.chat_box, c["title"], lambda c=c: self.open_chat(c["id"]),
                                 bg=C["btn"] if current else None, fg=C["ink"] if current else C["side_ink"])
            b.pack(fill="x", padx=(0, 8))
            b.bind("<Button-3>", lambda e, c=c: self.chat_menu(e, c["id"]))

        if pinned:
            tk.Label(self.chat_box, text="★ PINNED", font=F_SMALL, bg=C["side"], fg=C["muted"], anchor="w").pack(fill="x", padx=4, pady=(0, 2))
            for c in pinned:
                row(c)
            tk.Label(self.chat_box, text="RECENT", font=F_SMALL, bg=C["side"], fg=C["muted"], anchor="w").pack(fill="x", padx=4, pady=(10, 2))
        if not recent and not pinned:
            tk.Label(self.chat_box, text="Your chats will show here.\nRight-click a chat to pin, rename or delete it.",
                     font=F_SMALL, bg=C["side"], fg=C["muted"], anchor="w", justify="left", wraplength=210).pack(fill="x", padx=10, pady=4)
        for c in recent:
            row(c)

    def is_pinned(self, cid):
        c = self.chat if cid == self.chat["id"] else self.store.load(cid)
        return bool(c and c.get("pinned"))

    def toggle_sidebar(self):
        """Smoothly collapse or expand the sidebar like ChatGPT / Gemini."""
        if self.side.winfo_manager() == "pack":
            self.side.pack_forget()
            if hasattr(self, "sidebar_toggle_btn"):
                self.sidebar_toggle_btn.configure(text="☰", fg=C["muted"])
        else:
            self.side.pack(side="left", fill="y", before=self.main_frame)
            if hasattr(self, "sidebar_toggle_btn"):
                self.sidebar_toggle_btn.configure(text="◫", fg=C["accent"])

    def toggle_pin_chat(self, cid=None):
        cid = cid or self.chat["id"]
        if cid == self.chat["id"] and not self.chat["messages"]:
            self.system("Say something in this chat first, then you can pin it.")
            return
        on = not self.is_pinned(cid)
        if cid == self.chat["id"]:
            self.chat["pinned"] = on
        self.store.set_pinned(cid, on)
        self.refresh_sidebar()
        self.update_title()

    def project_menu(self, event, pid):
        p = self.store.project(pid)
        if not p:
            return
        m = tk.Menu(self.root, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"], activeforeground=C["ink"])
        m.add_command(label="Unpin" if p.get("pinned") else "Pin to top", command=lambda: self.toggle_pin_project(pid))
        m.add_command(label="Rename", command=lambda: self.rename_project(pid))
        m.add_command(label="New chat in this project", command=lambda: self.new_chat(project=pid))
        m.add_separator()
        m.add_command(label="Delete project", command=lambda: self.remove_project(pid))
        m.tk_popup(event.x_root, event.y_root)

    def toggle_pin_project(self, pid):
        p = self.store.project(pid)
        if p:
            self.store.update_project(pid, pinned=not p.get("pinned"))
            if self.page == ("project", pid):
                self.show_project(pid)
            else:
                self.refresh_sidebar()

    def rename_project(self, pid):
        p = self.store.project(pid)
        name = p and simpledialog.askstring("Rename project", "New name:", initialvalue=p["name"], parent=self.root)
        if name and name.strip():
            self.store.update_project(pid, name=name.strip()[:40])
            if self.page == ("project", pid):
                self.show_project(pid)
            else:
                self.refresh_sidebar()
                self.update_title()

    def chat_menu(self, event, cid):
        m = tk.Menu(self.root, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"], activeforeground=C["ink"])
        m.add_command(label="Unpin" if self.is_pinned(cid) else "Pin to top", command=lambda: self.toggle_pin_chat(cid))
        m.add_command(label="Rename", command=lambda: self.rename_chat(cid))
        pm = tk.Menu(m, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"], activeforeground=C["ink"])
        for p in self.store.projects():
            pm.add_command(label=p["name"], command=lambda p=p: self.move_chat(cid, p["id"]))
        pm.add_command(label="(no project)", command=lambda: self.move_chat(cid, None))
        m.add_cascade(label="Move to project", menu=pm)
        m.add_separator()
        m.add_command(label="Delete", command=lambda: self.delete_chat(cid))
        m.tk_popup(event.x_root, event.y_root)

    def rename_chat(self, cid):
        c = self.chat if cid == self.chat["id"] else self.store.load(cid)
        if not c:
            return
        name = simpledialog.askstring("Rename chat", "New name:", initialvalue=c["title"], parent=self.root)
        if name and name.strip():
            c["title"] = name.strip()[:60]
            self.store.save(c)
            self.refresh_sidebar()
            self.update_title()

    def move_chat(self, cid, pid):
        c = self.chat if cid == self.chat["id"] else self.store.load(cid)
        if c:
            c["project"] = pid
            self.store.save(c)
            self.refresh_sidebar()
            self.update_title()

    def delete_chat(self, cid):
        c = self.store.load(cid)
        if not c or not messagebox.askyesno("Delete chat", f"Delete “{c['title']}”? This can't be undone.", parent=self.root):
            return
        self.store.delete(cid)
        if cid == self.chat["id"]:
            self.new_chat()
        else:
            self.refresh_sidebar()

    def auto_learn(self, chat):
        """When he leaves a chat (new chat / another chat), the brain saves the important new points from it
        to memory by itself. This is how Vega gets to know him better after every conversation."""
        if not chat or not self.vega.cfg.get("auto_learn", True):
            return
        msgs = [m for m in chat.get("messages", []) if m.get("who") in ("you", "phone", "vega")]
        done = int(chat.get("learned_upto", 0) or 0)
        if len(msgs) - done < 4:  # nothing much new since last time
            return
        hist = [{"role": "user" if m["who"] in ("you", "phone") else "assistant", "content": m.get("text", "")}
                for m in msgs[done:][-24:]]
        chat["learned_upto"] = len(msgs)
        try:
            self.store.save(chat)
        except Exception:
            pass

        def work():
            try:
                new = self.vega.remember_conversation(history=hist, quiet=True)
            except Exception:
                new = []
            if new:
                vega_voice.log_problem(f"auto-learned {len(new)} things from a chat")
                self.ui.put(lambda: self.system("I learned from our last chat: " + "; ".join(new) +
                                                "  (Say “what do you remember” to see or remove them.)"))

        threading.Thread(target=work, daemon=True).start()

    def use_chat(self, chat):
        """Switch to a chat: show it, and give Vega its recent messages as memory."""
        if self.busy:
            self.system("Wait until I finish answering, or press Stop.", "warn")
            return False
        if getattr(self, "chat", None) is not None and self.chat is not chat and self.chat.get("id") != chat.get("id"):
            self.auto_learn(self.chat)
        self.chat = chat
        hist = []
        for m in chat["messages"][-14:]:
            if m.get("who") in ("you", "phone"):
                hist.append({"role": "user", "content": m["text"]})
            elif m.get("who") == "vega":
                hist.append({"role": "assistant", "content": m["text"]})
        self.vega.history = hist
        proj = self.store.project(chat.get("project")) if chat.get("project") else None
        self.vega.project_note = self.project_context(proj) if proj else ""
        self.vega.project_has_files = bool(proj and self.store.project_files(proj["id"]))
        self.show_chat_view()
        self.show_history()
        self.refresh_sidebar()
        self.update_title()
        self.entry.focus_set()
        return True

    def new_chat(self, project=None):
        self.use_chat(self.store.new(project=project))

    def open_chat(self, cid):
        c = self.store.load(cid)
        if c:
            self.use_chat(c)

    def update_title(self):
        if self.page is not None:
            self.pin_btn.pack_forget()
            return
        self.pin_btn.pack(side="left", padx=(8, 0), after=self.title_lbl)
        pinned = bool(self.chat.get("pinned"))
        self.pin_btn.configure(text="★ Pinned" if pinned else "☆ Pin", fg=C["you"] if pinned else C["muted"])
        proj = self.store.project(self.chat.get("project")) if self.chat.get("project") else None
        self.title_lbl.configure(text=(proj["name"] + "  /  " if proj else "") + self.chat["title"])

    def show_chat_view(self):
        if self.page is not None:
            if self.lablog_win:
                self.lablog_win.save_notes(quiet=True)
                self.lablog_win = None
            for w in self.body.winfo_children():
                if w is not self.chat_view:
                    w.destroy()
            self.page = None
            self.chat_view.pack(fill="both", expand=True)
            self.update_title()
            self.refresh_sidebar()

    def show_page(self, kind):
        """Open a sidebar page (News, Tech news, Internships, Lab Log) in the main area."""
        if self.page is not None:
            self.show_chat_view()
        self.chat_view.pack_forget()
        self.page = kind
        self.pin_btn.pack_forget()
        titles = {"news": "News", "tech": "Tech news", "internships": "Internships", "lablog": "Lab Log"}
        self.title_lbl.configure(text=titles.get(kind, kind))
        if kind == "lablog":
            from vega_lablog_window import LabLogWindow
            self.lablog_win = LabLogWindow(self, container=self.body)
            self.lablog_win.frame.pack(fill="both", expand=True, padx=(16, 0))
        else:
            self.feed_page = FeedPage(self, self.body, kind)
            self.feed_page.frame.pack(fill="both", expand=True)
        self.refresh_sidebar()

    def quick(self, command):
        """A sidebar shortcut: ask Vega in the current chat."""
        self.show_chat_view()
        self.send(command)

    def new_project(self):
        name = simpledialog.askstring("New project", "Project name (e.g. Assignments, Resume, Semester 8):", parent=self.root)
        if not name or not name.strip():
            return
        notes = simpledialog.askstring("New project", "Anything Vega should always know in this project? (optional)\n"
                                                      "e.g. 'These are my SRM cloud computing assignments.'", parent=self.root) or ""
        p = self.store.add_project(name, notes)
        self.show_project(p["id"])

    def show_project(self, pid):
        p = self.store.project(pid)
        if not p:
            return
        if self.page is not None:
            self.show_chat_view()
        self.chat_view.pack_forget()
        self.page = ("project", pid)
        self.pin_btn.pack_forget()
        self.title_lbl.configure(text="Project")
        page = ScrollFrame(self.body, C["bg"])
        page.pack(fill="both", expand=True, padx=(28, 8), pady=(8, 8))
        f = page.inner
        tk.Label(f, text=p["name"], font=F_TITLE, bg=C["bg"], fg=C["ink"], anchor="w").pack(fill="x", pady=(10, 2))
        tk.Label(f, text="Chats in this project share these notes. Vega reads them in every chat here.",
                 font=F_SMALL, bg=C["bg"], fg=C["muted"], anchor="w").pack(fill="x")
        notes = tk.Text(f, height=4, wrap="word", font=F, bg=C["entry"], fg=C["ink"], relief="flat", insertbackground=C["ink"],
                        padx=10, pady=8, highlightthickness=1, highlightbackground=C["line"])
        notes.insert("1.0", p.get("notes", ""))
        notes.pack(fill="x", pady=(8, 6))
        row = tk.Frame(f, bg=C["bg"])
        row.pack(fill="x", pady=(0, 14))
        self.button(row, "Save notes", lambda: (self.store.update_project(pid, notes=notes.get("1.0", "end").strip()[:2000]),
                                                self.system("Project notes saved.")), font=F_SMALL).pack(side="left")
        self.button(row, "＋  New chat in this project", lambda: self.new_chat(project=pid), font=F_SMALL,
                    bg=C["accent"], fg=C["bg"]).pack(side="left", padx=8)
        self.button(row, "Delete project", lambda: self.remove_project(pid), font=F_SMALL).pack(side="right")
        self.button(row, "★ Unpin project" if p.get("pinned") else "☆ Pin project", lambda: self.toggle_pin_project(pid),
                    font=F_SMALL).pack(side="right", padx=8)
        files = self.store.project_files(pid)
        frow = tk.Frame(f, bg=C["bg"])
        frow.pack(fill="x", pady=(4, 4))
        tk.Label(frow, text=f"Project files ({len(files)})", font=F_BOLD, bg=C["bg"], fg=C["ink"]).pack(side="left")
        self.button(frow, "＋  Add files", lambda: self.add_project_files(pid), font=F_SMALL).pack(side="right")
        tk.Label(f, text="PDF, Word, .txt or .md. Vega reads these in every chat of this project (like Claude's project knowledge).",
                 font=F_SMALL, bg=C["bg"], fg=C["muted"], anchor="w").pack(fill="x")
        for name in files:
            r2 = tk.Frame(f, bg=C["panel"])
            r2.pack(fill="x", pady=2)
            tk.Label(r2, text="•  " + name, font=F, bg=C["panel"], fg=C["ink"], anchor="w").pack(side="left", padx=10, pady=6)
            tk.Button(r2, text="Remove", font=F_SMALL, bg=C["panel"], fg=C["muted"], relief="flat", bd=0, highlightthickness=0,
                      cursor="hand2", command=lambda n=name: (self.store.remove_project_file(pid, n), self.show_project(pid))
                      ).pack(side="right", padx=8)
        tk.Frame(f, bg=C["bg"], height=10).pack(fill="x")
        chats = self.store.all(project=pid)
        tk.Label(f, text=f"Chats ({len(chats)})", font=F_BOLD, bg=C["bg"], fg=C["ink"], anchor="w").pack(fill="x", pady=(4, 6))
        if not chats:
            tk.Label(f, text="No chats yet. Start one with “New chat in this project”.", font=F_SMALL, bg=C["bg"],
                     fg=C["muted"], anchor="w").pack(fill="x")
        for c in chats:
            card = self.button(f, c["title"] + "     " + nice_time(c["updated"]), lambda c=c: self.open_chat(c["id"]),
                               font=F, bg=C["panel"])
            card.configure(anchor="w")
            card.pack(fill="x", pady=3)
            card.bind("<Button-3>", lambda e, c=c: self.chat_menu(e, c["id"]))
        self.refresh_sidebar()

    def project_context(self, proj):
        """Project notes + the text of the project's files, for Vega to read in every chat of the project."""
        note = f"We're chatting inside my project “{proj['name']}”."
        if proj.get("notes"):
            note += f" Project notes: {proj['notes']}"
        files = self.store.project_files(proj["id"])
        budget = int(self.vega.cfg.get("doc_chars", 24000) or 24000)
        for name in files:
            if budget <= 500:
                note += f"\n(Project file '{name}' not read: too much text.)"
                continue
            try:
                text, cut = self.vega.read_document(os.path.join(self.store.project_folder(proj["id"]), name), limit=budget)
            except Exception:
                continue
            if text:
                note += f"\n\nProject file '{name}'{' (first part)' if cut else ''}:\n\"\"\"\n{text}\n\"\"\""
                budget -= len(text)
        return note

    def add_project_files(self, pid):
        paths = filedialog.askopenfilenames(title="Add files to this project", parent=self.root,
                                            filetypes=[("Documents", "*.pdf *.docx *.txt *.md"), ("All files", "*.*")])
        for p in paths:
            try:
                self.store.add_project_file(pid, p)
            except OSError as e:
                self.system(f"Couldn't add {os.path.basename(p)}: {e}", "warn")
        if paths:
            self.show_project(pid)
            if self.chat.get("project") == pid:
                self.vega.project_note = self.project_context(self.store.project(pid))
                self.vega.project_has_files = True

    def remove_project(self, pid):
        p = self.store.project(pid)
        if p and messagebox.askyesno("Delete project", f"Delete the project “{p['name']}”?\nIts chats are kept (moved to Chats).",
                                     parent=self.root):
            self.store.delete_project(pid)
            self.new_chat()

    def system(self, text, level="muted"):
        if getattr(self, "streaming", False):  # an answer is being written: add this after it, or it gets erased
            self._held_system = getattr(self, "_held_system", []) + [(text, level)]
            return
        self._insert(text.strip() + "\n", (level,))
        self.text.see("end")

    def stream_update(self, text):
        if self.welcome_shown:
            self.text.delete("1.0", "end")
            self.welcome_shown = False
        if not self.streaming:
            self.streaming = True
            self._name("vega")
            self.text.mark_set("stream", "end-1c")
            self.text.mark_gravity("stream", "left")
        self.text.delete("stream", "end-1c")
        self._insert(text, ("body",))
        self.text.see("end")

    def click_link(self, event):
        url = next((self.link_urls.get(t) for t in self.text.tag_names("current")
                    if t.startswith("url") and t in getattr(self, "link_urls", {})), None)
        if not url:
            rng = self.text.tag_prevrange("link", "current+1c")
            url = self.text.get(*rng) if rng else None
        if url:
            try:
                self.vega.open_in_browser(self.vega.browser_key(""), url)  # your usual browser (Brave)
            except Exception:
                webbrowser.open(url)

    def toggle_quickbar(self):
        self.quickbar.toggle()

    def set_status(self, text, color="ok"):
        if text == "Ready" and getattr(self, "front", False):
            text = "Ready · just talk"
        self.status_lbl.configure(text=text)
        self.dot.itemconfigure(self.dot_id, fill=C.get(color, C["ok"]))
        if hasattr(self, "quickbar") and self.quickbar:
            self.quickbar.set_status(text, color)

    # ---------------- sending
    def on_send(self):
        if self.busy:
            self.vega.stop()
            self.mouth.stop()
            return
        text = fix_emojis(self.entry.get()).strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.send(text)

    def send(self, text, spoken=False):
        text = fix_emojis(text or "")
        if self.locked:
            return
        self.last_activity = time.time()
        self.last_was_spoken = spoken
        self.reply_lang = self.ears.last_lang if spoken else detect_language(text)
        if self.NOT_FOR_YOU.search(text.strip()):
            if self.busy:
                self.vega.stop()
            self.not_for_you(text)
            return
        if self.busy:
            self.system("I'm still answering. Press Stop first.", "warn")
            return
        if re.sub(r"[^a-z ]", "", text.lower()).strip() in ("allow phone", "connect phone", "connect that phone", "connect my phone", "allow my phone"):
            self.add_message("you", text)
            if getattr(self, "pending_pair", None):
                self.allow_phone()
            else:
                self.connect_phone()
            return
        self.mouth.stop()
        self.add_message("you", text)
        if spoken:
            self.bubble("You said", f"“{text}”\nThinking…", 15)
        self.ears.hint = self.vega.speech_hint()
        self.ears.hotwords = self.vega.speech_hotwords()
        self.run_job(lambda on_token: self.vega.handle(text, on_token=on_token, spoken=spoken))

    def run_job(self, fn):
        self.job_id = getattr(self, "job_id", 0) + 1
        my_id = self.job_id
        self.busy = True
        self.streaming = False
        self.send_btn.configure(text="Stop")
        self.set_status("Thinking…", "accent")

        def on_token(t):
            self.ui.put(lambda t=t: self.stream_update(t))

        def work():
            try:
                result = fn(on_token)
            except Exception as e:
                result = {"text": f"Something went wrong: {e}", "say": "Something went wrong."}
            self.ui.put(lambda: self.finish(result, my_id))

        threading.Thread(target=work, daemon=True).start()

    def finish(self, result, job=None):
        text = result.get("text", "")
        if job is not None and job != getattr(self, "job_id", job):
            return  # an old answer that was stopped: a newer question is running, so don't touch it
        if self.streaming:
            self.text.delete("stream", "end-1c")
            self._insert_body(text.strip())
            self._message_actions(text.strip(), retry=result.get("kind") == "chat")
            self.text.see("end")
            self.save_message("vega", text)
        else:
            self.add_message("vega", text)
        self.streaming = False
        try:
            if len(self.chat.get("messages", [])) - int(self.chat.get("learned_upto", 0) or 0) >= 16:
                self.auto_learn(self.chat)  # a long chat: learn from it now too
        except Exception:
            pass
        for held_text, held_level in getattr(self, "_held_system", []):
            self.system(held_text, held_level)
        self._held_system = []
        self.busy = False
        self.send_btn.configure(text="Send")
        used = result.get("model")
        if used and not self.vega._same(used, self.vega.cfg.get("model") or "vega") and not self.vega.diary_mode:
            self.set_status(f"Ready (that answer came from {used})", "ok")
        else:
            self.set_status("Diary open" if self.vega.diary_mode else "Ready", "you" if self.vega.diary_mode else "ok")
        if result.get("wake") in (True, False):
            self.wake_var.set(1 if result["wake"] else 0)
            self.toggle_wake()
        mode = result.get("voice_lang")
        if mode in ("auto", "english", "telugu"):
            self.set_voice_language(mode, quiet=True)
            if not self.voice_var.get():  # he asked her to speak, so turn speaking on
                self.voice_var.set(1)
                self.toggle_voice()
        if self.voice_var.get():  # speak first, so nothing below can stop her voice
            try:
                lang = self.answer_language()
            except Exception:
                lang = None
            self.mouth.say(shorten_for_speech(result.get("say") or text), lang=lang)
        if getattr(self, "last_was_spoken", False):
            self.bubble("Vega", text, 12)
        if result.get("open_lablog"):
            self.open_lablog()
            if result.get("open_projects") and self.lablog_win:
                self.lablog_win.show_projects()
        elif self.lablog_win and self.page == "lablog" and re.search(r"lab log|step \d", text.lower()):
            self.lablog_win.show(self.lablog_win.idx, save=False)  # keep the window up to date
        if result.get("confirm") and getattr(self, "last_was_spoken", False):
            self.listen_after_speaking(time.time() + 25)  # "Are you sure?" -> listen for yes / no by itself

    # ---------------- microphone
    def on_mic(self):
        if self.locked:
            return
        if self.listening:
            if self.listen_stop:
                self.listen_stop.set()
            return
        if self.busy:
            self.system("Wait until I finish answering, or press Stop.", "warn")
            return
        self.mouth.stop()
        self.listening = True
        self.listen_stop = threading.Event()
        self.wake.paused.set()
        self.mic_btn.configure(text="Done", bg=C["you"], fg=C["bg"])
        self.set_status("Listening… speak now (I stop when you stop)", "you")
        first_time = self.ears.main_model_name() not in self.ears._models
        if first_time:
            self.system("Loading speech recognition. The first time, it downloads about 500 MB, so wait a minute or two.")

        def work():
            text, err, audio = "", None, None
            try:
                self.ears.model(self.ears.main_model_name())
                audio = self.ears.listen_once(self.listen_stop)
                if audio is not None:
                    self.ui.put(lambda: self.set_status("Understanding…", "accent"))
                    waiting = getattr(self.vega, "pending_action", None)
                    text = self.ears.transcribe(audio, prompt="Yes. No. Yes, go ahead. No, cancel." if waiting else None)
            except Exception as e:
                err = str(e)
            self.ui.put(lambda: self.mic_done(text, err, audio))

        threading.Thread(target=work, daemon=True).start()

    def start_hotkey(self):
        """Global shortcuts from ANY app:
        - Ctrl + Alt + V: Mic / Dictation
        - Ctrl + Shift + V: Toggle floating Quick Bar
        """
        if os.name != "nt":
            return

        def run():
            try:
                import ctypes
                from ctypes import wintypes
                user32 = ctypes.windll.user32
                MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_NOREPEAT, VK_V, WM_HOTKEY = 0x1, 0x2, 0x4, 0x4000, 0x56, 0x0312
                if not user32.RegisterHotKey(None, 1, MOD_ALT | MOD_CONTROL | MOD_NOREPEAT, VK_V):
                    vega_voice.log_problem("shortcut: Ctrl+Alt+V is already used by another app")
                user32.RegisterHotKey(None, 2, MOD_SHIFT | MOD_CONTROL | MOD_NOREPEAT, VK_V)
                vega_voice.log_problem("shortcuts: Ctrl+Alt+V (Mic) and Ctrl+Shift+V (Quick Bar) registered")
                msg = wintypes.MSG()
                while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                    if msg.message == WM_HOTKEY:
                        if msg.wParam == 1:
                            self.ui.put(self.hotkey_mic)
                        elif msg.wParam == 2:
                            self.ui.put(self.toggle_quickbar)
            except Exception as e:
                vega_voice.log_problem(f"shortcut error: {e}")

        threading.Thread(target=run, daemon=True).start()

    def hotkey_mic(self):
        if self.locked:
            self.bubble("Vega is locked", "Open Vega and type your PIN.", 6, force=True)
            return
        self.last_activity = time.time()
        if self.listening:  # pressed again = "I'm done talking"
            self.on_mic()
            return
        if self.busy:
            self.vega.stop()
            self.busy = False
            self.send_btn.configure(text="Send")
        try:
            vega_voice.beep("wake")
        except Exception:
            pass
        self.bubble("Vega", "Listening… speak now.\n(I stop when you stop, or press Ctrl + Alt + V again.)", 10)
        self.on_mic()

    def listen_after_speaking(self, until):
        if time.time() > until or self.locked or self.listening:
            return
        if self.busy or self.mouth.busy():
            self.root.after(250, lambda: self.listen_after_speaking(until))
            return
        self.on_mic()

    def mic_done(self, text, err, audio=None):
        self.listening = False
        self.wake.paused.clear()
        self.mic_btn.configure(text="Mic", bg=C["btn"], fg=C["ink"])
        self.set_status("Ready")
        if err:
            self.system(f"Microphone problem: {err}", "warn")
            self.bubble("Vega", f"Microphone problem: {err}", 6)
        elif not text:
            pending = getattr(self.vega, "pending_action", None)
            if pending and time.time() < pending[1] and not getattr(self, "_yes_retry", False):
                # she asked "Are you sure?" and didn't catch the answer: listen once more by herself
                self._yes_retry = True
                self.system("I didn't catch that. Say “yes” or “no” now (or type it).")
                if self.voice_var.get():
                    self.mouth.say("Sorry, say yes or no.")
                self.listen_after_speaking(time.time() + 15)
                return
            self._yes_retry = False
            self.system("I didn't hear anything. Press Mic and try again, a bit closer.")
            self.bubble("Vega", "I didn't hear anything. Press Ctrl + Alt + V and try again.", 5)
        else:
            self._yes_retry = False
            self.voice_gate(text, audio)

    def voice_gate(self, text, audio):
        """If 'Only obey my voice' is on, check it's Hemanth speaking before doing anything."""
        vega_voice.log_problem(f"command received: “{text[:80]}” (locked={self.locked}, voice_only={sec.voice_only()}, busy={self.busy})")
        if self.busy:  # a stuck old answer must not block new voice commands
            self.vega.stop()
            self.busy = False
            self.send_btn.configure(text="Send")
        if self.locked:
            if audio is not None and sec.voice_unlock_ready():
                self.bubble("Vega is locked", "Checking your voice to unlock…", 6)
                self.voice_unlock(audio, then_text=text)
            else:
                self.bubble("Vega is locked", "Open Vega and type your PIN.", 8)
            return
        if not (sec.voice_only() and audio is not None):
            self.send(text, spoken=True)
            return
        self.set_status("Checking it's you…", "accent")

        def work():
            try:
                score, err = self.voice.score(audio), None
            except Exception as e:
                score, err = None, str(e)
            self.ui.put(lambda: after(score, err))

        def after(score, err):
            self.set_status("Ready")
            if score is not None:
                self.note_score(score)
            if err:
                self.system(f"I couldn't check your voice ({err}), so I'm doing it anyway.", "warn")
                self.send(text, spoken=True)
            elif score >= min(0.24, sec.voice_threshold()) or (time.time() - self.last_verified < 300 and score >= 0.12):
                # verified recently or score is positive: accept command smoothly
                self.last_verified = time.time()
                self.send(text, spoken=True)
            else:
                self.bubble("Vega", "That didn't sound like you, so I ignored it. Say it again.", 6)
                self.system(f"Heard “{text}”, but that didn't sound like Hemanth (voice match {score:.2f}, "
                            f"needs {sec.voice_threshold():.2f}). I ignored it.\n"
                            "Tip: say “Hey Vega” and the command together in one go, a little slower. "
                            "Or lower the level in Menu → Voice strictness.", "warn")

        threading.Thread(target=work, daemon=True).start()

    def wake_near_miss(self, first, second):
        """It almost heard "Hey Vega" but wasn't sure. Say so (at most every 30 seconds) so he knows to repeat."""
        # Only a quiet line in the chat, and only when Vega's window is in front.
        # No pop-up over other apps: most "almost Hey Vega" sounds are just noise, videos or other people.
        if time.time() - getattr(self, "_last_near_miss", 0) < 120 or self.locked or self.in_background():
            return
        self._last_near_miss = time.time()
        self.system(f"I think I heard “Hey Vega”, but I wasn't sure (heard: “{(second or first or '').strip()[:60]}”). "
                    "Say it again a bit clearer. If this happens a lot: Settings and more → Hey Vega sensitivity → Easy.", "muted")

    def toggle_cloud_speech(self):
        on = bool(self.cloud_var.get())
        self.ears.cloud = on
        self.vega.cfg["cloud_speech"] = on
        save_config_value("cloud_speech", on)
        self.system("I'll use Google's speech engine (Indian English) for your commands when you're online, and my "
                    "laptop model when you're offline. Only what you say after “Hey Vega” or the Mic goes to Google."
                    if on else "I'll understand you with the laptop model only. Nothing goes to Google.")

    def watch_front(self):
        """Every half second (in the window's own thread): is Vega open in front, so he can just talk?"""
        try:
            from vega_control import is_media_playing
            media_playing = is_media_playing()
            on_call = self.on_a_call()
            on = (bool(self.vega.cfg.get("talk_without_wake", False)) and bool(self.wake_var.get()) and not self.locked
                  and not getattr(self, "listening_paused", False)
                  and not media_playing and not on_call
                  and not self.in_background() and time.time() - self.last_key > 4)  # not while he's typing
            if on != self.front:
                self.front = on
                vega_voice.log_problem("just talk: " + ("ON (window open)" if on else "off (say Hey Vega)"))
                if self.status_lbl.cget("text") in ("Ready", "Ready · just talk"):
                    self.set_status("Ready")
        except Exception:
            pass
        self.root.after(500, self.watch_front)

    def toggle_direct(self):
        on = bool(self.direct_var.get())
        self.vega.cfg["talk_without_wake"] = on
        save_config_value("talk_without_wake", on)
        self.system("While my window is open in front, just talk: no need to say “Hey Vega”. "
                    "When I'm minimized, say “Hey Vega” first." if on else
                    "You'll always need to say “Hey Vega” (or press Mic / Ctrl + Alt + V).")

    def wake_busy(self):
        """Why \"Hey Vega\" isn't being listened for right now (written to voice_log.txt when it changes)."""
        if getattr(self, "listening_paused", False):
            return True
        if self.mouth.busy():
            self._last_spoke = time.time()
        try:
            from vega_control import is_media_playing
            media_playing = is_media_playing()
        except Exception:
            media_playing = False
        reason = ("answering" if self.busy else "mic in use by Vega" if self.listening else "speaking" if self.mouth.busy()
                  else "speaking" if time.time() - getattr(self, "_last_spoke", 0) < 1.2
                  else "PAUSED: media is playing (YouTube, videos, music)" if media_playing
                  else "PAUSED: another app is using the mic (a call, WhatsApp, Meet, Zoom...)" if self.on_a_call()
                  else "LOCKED (voice unlock is off: open Vega and type your PIN)" if (self.locked and not sec.voice_unlock_ready())
                  else "")
        if reason != getattr(self, "_busy_reason", ""):
            self._busy_reason = reason
            if reason not in ("answering", "speaking", "mic in use by Vega"):
                vega_voice.log_problem("listening: " + (reason or "back on, waiting for Hey Vega")
                                       + ("" if getattr(self, "front", False) else " [window not in front]"))
        return bool(reason)

    def wake_voice_score(self, audio):
        """For the \"Hey Vega\" listener: how much does this sound like Hemanth?"""
        if not self.voice.has_voiceprint() or sec.voice_packages_missing():
            return None
        score = self.voice.score(audio)
        strong = max(0.24, min(0.28, sec.voice_threshold()))   # responsive command match for Hemanth
        low = 0.20                                            # strict floor against TV, video or ambient voices
        if score >= strong:
            self.last_verified = time.time()
        return score, strong, low, bool(self.vega.cfg.get("wake_only_me", True))

    def toggle_wake_only_me(self):
        on = bool(self.wake_me_var.get())
        if on and not self.voice.has_voiceprint():
            self.system("First set up your voice: Settings and more → Set up my voice. Then tick this again.", "warn")
            self.wake_me_var.set(False)
            on = False
        self.vega.cfg["wake_only_me"] = on
        save_config_value("wake_only_me", on)
        self.system("I'll wake up only when it's your voice saying “Hey Vega” (not videos, calls or other people)."
                    if on else "I'll wake up for anyone who says “Hey Vega”.")

    NOT_FOR_YOU = re.compile(r"^(?:hey\s+)?(?:vega\W*)?(?:no\W*)?(?:that|this|it)?\s*(?:was\s*n[o']?t|wasnt|is\s*n[o']?t|isnt|was not|is not)\s+"
                             r"(?:for|to)\s+you\b|^(?:not|wasn'?t)\s+(?:talking\s+)?(?:for|to)\s+you\b|^i\s+(?:was\s*n[o']?t|wasnt|was not|didn'?t|did not)\s+"
                             r"(?:talking to|call|calling|talk to)\s+you\b|^false alarm\b|^wrong wake\b", re.I)

    def not_for_you(self, text):
        """ "That wasn't for you": she woke by mistake. Stop, and learn the word that fooled her."""
        self.mouth.stop()
        self.add_message("you", text)
        heard, when = getattr(self.wake, "last_wake", ("", 0.0))
        if "for you" in heard.lower() or "talking" in heard.lower():  # that wake WAS this "not for you" - use the one before
            heard, when = getattr(self.wake, "prev_wake", ("", 0.0))
        word = vega_voice.wake_word_in(heard) if heard and time.time() - when < 600 else ""
        cfg = self.vega.cfg
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
            if heard:
                msg += f" (I heard “{heard.strip()[:50]}”.)"
        today = time.strftime("%Y-%m-%d")
        mistakes = [t for t in cfg.get("wake_mistakes", []) if t.startswith(today)] + [time.strftime("%Y-%m-%d %H:%M")]
        cfg["wake_mistakes"] = mistakes[-20:]
        save_config_value("wake_mistakes", cfg["wake_mistakes"])
        level = str(cfg.get("wake_strictness", "easy"))
        if len(mistakes) >= 3 and level in ("very_easy", "easy"):
            msg += (" That's a few mistakes today. If it keeps happening: Settings and more → Hey Vega sensitivity → "
                    + ("Easy." if level == "very_easy" else "Normal."))
        self.system(msg)
        self.bubble("Vega", msg, 6)
        self.set_status("Ready")

    def set_wake_strictness(self, level):
        self.vega.cfg["wake_strictness"] = level
        save_config_value("wake_strictness", level)
        self.wake.strictness = level
        self.system({"strict": "Hey Vega: strict. Fewest mistakes, but you may need to say it clearly.",
                     "normal": "Hey Vega: normal. A good balance.",
                     "easy": "Hey Vega: easy. She wakes up easily, but may sometimes wake by mistake.",
                     "very_easy": "Hey Vega: very easy. She wakes up most easily. If she wakes by mistake too often, choose Easy."}[level])

    def on_a_call(self):
        """While another app uses the mic (a call, ChatGPT voice...), don't listen for Hey Vega."""
        if not self.vega.cfg.get("pause_on_calls", True):
            return False
        from vega_control import other_app_using_mic
        busy = other_app_using_mic()
        if busy != getattr(self, "_was_on_call", False):
            self._was_on_call = busy
            self.ui.put(lambda: self.set_status("Paused: another app is using the mic" if busy else "Ready",
                                                "warn" if busy else "ok"))
        return busy

    def on_wake(self, audio=None):
        if self.locked:  # just "Hey Vega" unlocks her, if it's your voice
            if audio is not None and sec.voice_unlock_ready():
                self.wake_unlock(audio)
            else:
                self.bubble("Vega is locked", "Open Vega and type your PIN.", 6)
            return
        self.mouth.stop()
        self.set_status("Yes? I'm listening…", "you")
        self.bubble("Vega", "Yes? I'm listening…", 9)
        self._wake_time = time.time()
        self.root.after(11000, self.wake_timeout)

    def wake_timeout(self):
        """If no command came after "Hey Vega", don't stay stuck on "listening"."""
        if self.status_lbl.cget("text").startswith("Yes? I'm listening") and not self.busy:
            self.set_status("Ready")
            self.bubble("Vega", "I didn't hear a command. Say “Hey Vega” and then your question.", 5)

    def warm_up_ears(self):
        """Load (and the first time, download) the speech models now, not in the middle of a command."""
        # Keep memory low: if wake model and main model are both base.en, load only one
        names = [self.ears.wake_model_name]
        if self.ears.main_model_name() != self.ears.wake_model_name:
            names.append(self.ears.main_model_name())
        slow = [n for n in dict.fromkeys(names) if n not in self.ears._models]
        if not slow:
            return
        start = time.time()
        shown = False
        errors = []

        def load(n):
            try:
                self.ears.model(n)
                vega_voice.log_problem(f"speech model '{n}' ready")
            except Exception as e:
                errors.append(f"{n}: {e}")
                vega_voice.log_problem(f"speech model '{n}' failed to load: {e}")

        for n in slow:
            t = threading.Thread(target=load, args=(n,), daemon=True)
            t.start()
            while t.is_alive():
                if not shown and time.time() - start > 4:
                    shown = True
                    self.ui.put(lambda: (self.set_status("Getting my ears ready…", "accent"),
                                         self.system("Getting my ears ready (the first time I download the speech model, "
                                                     "up to 1.5 GB, so it can take a few minutes). “Hey Vega” works when this is done.")))
                t.join(1)
        if errors:
            self.ui.put(lambda: self.system("I couldn't load a speech model: " + "; ".join(errors) +
                                            "\nCheck your internet, or choose Settings → Speech accuracy → Fast (small.en).", "warn"))
        elif shown:
            self.ui.put(lambda: (self.set_status("Ready"), self.system("My ears are ready. Say “Hey Vega”.")))

    # ---------------- Siri-style bubble (when Vega's window isn't in front)
    def in_background(self):
        try:
            return self.root.state() == "iconic" or self.root.focus_displayof() is None
        except Exception:  # Tk sometimes fails here (e.g. while a menu is open)
            return True

    def bubble(self, title, text, seconds=10, force=False):
        """A small box at the bottom-right of the screen, on top of other apps, like Siri."""
        if not force and not self.in_background():
            return
        if not force and not self.vega.cfg.get("bubbles", True):
            return  # he turned pop-ups off (Settings)
        try:
            b = getattr(self, "_bubble", None)
            if b is None or not b.winfo_exists():
                b = self._bubble = tk.Toplevel(self.root)
                b.overrideredirect(True)
                b.attributes("-topmost", True)
                b.configure(bg=C["line"])
                inner = tk.Frame(b, bg=C["panel"], padx=16, pady=12)
                inner.pack(fill="both", expand=True, padx=1, pady=1)
                head = tk.Frame(inner, bg=C["panel"])
                head.pack(fill="x")
                self._bubble_title = tk.Label(head, font=F_BOLD, bg=C["panel"], fg=C["accent"], anchor="w")
                self._bubble_title.pack(side="left", fill="x", expand=True)
                close = tk.Label(head, text="✕", font=F_BOLD, bg=C["panel"], fg=C["muted"], cursor="hand2", padx=4)
                close.pack(side="right")
                close.bind("<Button-1>", lambda e: (self.hide_bubble(), "break")[1])
                close.bind("<Enter>", lambda e: close.configure(fg=C["ink"]))
                close.bind("<Leave>", lambda e: close.configure(fg=C["muted"]))
                self._bubble_text = tk.Label(inner, font=F, bg=C["panel"], fg=C["ink"], anchor="w", justify="left", wraplength=360)
                self._bubble_text.pack(fill="x", pady=(4, 0))
                for w in (b, inner, self._bubble_title, self._bubble_text):
                    w.bind("<Button-1>", lambda e: (self.hide_bubble(), self.show()))  # click the message: open Vega
                    w.bind("<Button-3>", lambda e: self.hide_bubble())                # right-click: just hide it
            self._bubble_title.configure(text=title)
            short = re.sub(r"\s+", " ", text).strip()
            self._bubble_text.configure(text=short if len(short) <= 260 else short[:257].rsplit(" ", 1)[0] + "…")
            b.update_idletasks()
            w, h = 400, b.winfo_reqheight()
            x = b.winfo_screenwidth() - w - 24
            y = b.winfo_screenheight() - h - 72
            b.geometry(f"{w}x{h}+{x}+{y}")
            b.deiconify()
            b.lift()
            if getattr(self, "_bubble_job", None):
                self.root.after_cancel(self._bubble_job)
            self._bubble_job = self.root.after(int(seconds * 1000), self.hide_bubble)
        except Exception:
            pass  # the popup must never stop Vega from answering or speaking

    def hide_bubble(self):
        b = getattr(self, "_bubble", None)
        if b is not None and b.winfo_exists():
            b.withdraw()

    # ---------------- documents
    def import_contacts(self, path=None):
        path = path or filedialog.askopenfilename(title="Choose your contacts file (.vcf)",
                                                  filetypes=[("Contacts", "*.vcf"), ("All files", "*.*")])
        if not path:
            return
        try:
            out = self.vega.import_vcf(path)
        except Exception as e:
            self.system(f"I couldn't read that contacts file: {e}", "warn")
            return
        self.add_message("vega", out["text"])
        self.ears.hotwords = self.vega.speech_hotwords()

    def on_attach(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title="Choose a document for Vega",
                                          filetypes=[("Documents", "*.pdf *.docx *.txt *.md *.csv *.py *.sh *.yaml *.yml *.json *.log"),
                                                     ("Contacts (.vcf)", "*.vcf"), ("All files", "*.*")])
        if not path:
            return
        if path.lower().endswith(".vcf"):
            self.import_contacts(path)
            return
        folder = self.vega.doc_folder()
        name = os.path.basename(path)
        target = os.path.join(folder, name)
        try:
            if os.path.abspath(os.path.dirname(path)) != os.path.abspath(folder) and not os.path.exists(target):
                shutil.copy2(path, target)
                path = target
        except OSError:
            pass
        self.add_message("you", f"(attached {name})")

        def job(on_token):
            out = self.vega.load_document(path)
            if out.get("ask"):
                return self.vega.chat(out["ask"], on_token=on_token, shown=f"(attached {name})", instruction=out.get("instruction"))
            return out

        self.run_job(job)

    # ---------------- reminders
    def on_note(self, note):
        """Vega speaking up by herself (the evening lab log check)."""
        self.add_message("vega", note["text"])
        if not self.locked and self.voice_var.get():
            self.mouth.say(shorten_for_speech(note.get("say") or note["text"]))
        if self.phone:
            self.phone.send(note["text"])

    def open_lablog(self):
        self.show_page("lablog")

    def on_reminder(self, text):
        self.system(f"Reminder: {text}", "warn")
        try:
            self.root.deiconify()
            self.root.lift()
            self.root.attributes("-topmost", True)
            self.root.after(1500, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass
        beep("reminder")
        self.mouth.say("Hemanth, you have a reminder. Unlock me to see it." if self.locked else f"Hemanth, reminder: {text}")
        if self.phone:
            self.phone.send(f"Reminder: {text}")

    def auto_briefing(self):
        """The daily morning briefing: on screen, spoken, and sent to the phone."""
        def job(on_token):
            out = self.vega.morning_briefing()
            if self.phone:
                self.phone.send(out["text"])
            return out
        if self.locked:
            self.pending_briefing = True
            return
        if self.busy:
            self.root.after(30000, self.auto_briefing)
            return
        self.run_job(job)

    # ---------------- PIN lock
    def lock(self):
        if not sec.has_pin():
            self.system("Set a PIN first: Menu → Set or change PIN.")
            return
        if self.locked:
            return
        self.locked = True
        self.mouth.stop()
        if self.listen_stop:
            self.listen_stop.set()
        f = tk.Frame(self.root, bg=C["bg"])
        f.place(relx=0, rely=0, relwidth=1, relheight=1)
        inner = tk.Frame(f, bg=C["bg"])
        inner.place(relx=0.5, rely=0.42, anchor="center")
        tk.Label(inner, text="Vega", font=F_TITLE, bg=C["bg"], fg=C["ink"]).pack(pady=(0, 6))
        tk.Label(inner, text="Locked. Enter your PIN.", font=F, bg=C["bg"], fg=C["muted"]).pack(pady=(0, 12))
        self.pin_entry = tk.Entry(inner, show="•", font=(UI_FONT, 16), justify="center", width=12, bg=C["entry"], fg=C["ink"],
                                  insertbackground=C["ink"], relief="flat", highlightthickness=1,
                                  highlightbackground=C["line"], highlightcolor=C["accent"])
        self.pin_entry.pack(ipady=6)
        self.pin_entry.bind("<Return>", lambda e: self.try_unlock())
        self.button(inner, "Unlock", self.try_unlock, bg=C["accent"], fg=C["bg"], font=F_BOLD).pack(pady=12, fill="x")
        if sec.voice_unlock_ready():
            self.button(inner, "Unlock with my voice", lambda: self.voice_unlock()).pack(pady=(0, 10), fill="x")
            tk.Label(inner, text="Or just say “Hey Vega”.", font=F_SMALL, bg=C["bg"], fg=C["muted"]).pack()
        self.pin_msg = tk.Label(inner, text="", font=F_SMALL, bg=C["bg"], fg=C["warn"], wraplength=320, justify="center")
        self.pin_msg.pack(pady=(6, 0))
        tk.Label(inner, text="Reminders and your phone link keep working while I'm locked.", font=F_SMALL,
                 bg=C["bg"], fg=C["muted"]).pack(pady=(18, 0))
        self.lock_frame = f
        f.lift()
        self.set_status("Locked", "you")
        self.root.after(100, self.pin_entry.focus_set)

    def try_unlock(self):
        now = time.time()
        if now < self.pin_wait_until:
            self.pin_msg.configure(text=f"Too many wrong tries. Wait {int(self.pin_wait_until - now) + 1} seconds.")
            return
        pin = self.pin_entry.get()
        self.pin_entry.delete(0, "end")
        if sec.check_pin(pin):
            fv = getattr(self, "failed_voice", None)
            self.failed_voice = None
            self.unlock()
            if fv and time.time() - fv[0] < 120:
                threading.Thread(target=lambda: self.voice.add_sample(fv[1]), daemon=True).start()
                self.system("Learned from your last voice try, so voice unlock should work better next time.")
            return
        self.pin_fails += 1
        if self.pin_fails >= 5:
            self.pin_fails = 0
            self.pin_wait_until = now + 30
            self.pin_msg.configure(text="Wrong PIN 5 times. Wait 30 seconds.")
        else:
            self.pin_msg.configure(text="Wrong PIN. Try again.", fg=C["warn"])

    def set_autolock(self, n):
        sec.set_lock_minutes(n)
        self.system("I'll only lock when I start." if n == 0 else
                    f"I'll lock myself after {n if n < 60 else n // 60} {'minutes' if n < 60 else ('hour' if n == 60 else 'hours')} without use.")

    def wake_unlock(self, audio):
        """Locked + "Hey Vega" in your voice = unlocked. A short "Hey Vega" is little voice to check,
        so if it's close but not sure, one more "Hey Vega" is joined to it and checked again."""
        import numpy as np

        def work():
            clip = audio
            prev = getattr(self, "_unlock_clip", None)
            if prev is not None and time.time() - prev[1] < 20:
                clip = np.concatenate([prev[0], audio])
            try:
                score = self.voice.score(clip)
            except Exception as e:
                msg = str(e)
                self.ui.put(lambda: self.bubble("Vega is locked", f"Voice check didn't work ({msg}). Type your PIN.", 6))
                return
            self.ui.put(lambda: done(score, clip))

        def done(score, clip):
            if not self.locked:
                return
            vega_voice.log_problem(f"unlock by Hey Vega: voice {score:.2f} (needs {sec.unlock_threshold():.2f})")
            if self.note_score(score) >= sec.unlock_threshold():
                self._unlock_clip = None
                self.unlock()
                self.system(f"Unlocked by your voice (match {score:.2f}).")
                self.mouth.say("Welcome back, Hemanth.")
                self.bubble("Vega", "Unlocked. Welcome back!", 4)
            elif score >= sec.voice_threshold() - 0.05:
                self._unlock_clip = (clip, time.time())
                self.bubble("Vega is locked", "Almost sure it's you. Say “Hey Vega” once more.", 6)
            else:
                self._unlock_clip = None
                self.bubble("Vega is locked", "That didn't sound like you. Type your PIN.", 6)

        threading.Thread(target=work, daemon=True).start()

    def voice_unlock(self, audio=None, then_text=None):
        """Unlock by voice: record (or use the given audio) and compare with the saved voiceprint."""
        if not self.locked or not sec.voice_unlock_ready():
            return
        if audio is None:
            self.pin_msg.configure(text="Listening… say: Hey Vega.", fg=C["muted"])
            self.listening = True
            self.wake.paused.set()

        def work():
            nonlocal audio
            try:
                if audio is None:
                    audio = self.ears.listen_once(wait_seconds=6, silence=1.2)
                score = self.voice.score(audio) if audio is not None else None
                err = None
            except Exception as e:
                score, err = None, str(e)
            self.ui.put(lambda: done(score, err))

        def done(score, err):
            self.listening = False
            self.wake.paused.clear()
            if not self.locked:
                return
            if err:
                self.pin_msg.configure(text=f"Voice check didn't work ({err}). Use your PIN.", fg=C["warn"])
            elif score is None:
                self.pin_msg.configure(text="I didn't hear anything. Try again, or use your PIN.", fg=C["warn"])
            elif self.note_score(score) >= sec.unlock_threshold():
                self.unlock()
                self.system(f"Unlocked by your voice (match {score:.2f}).")
                if then_text and not re.search(r"\b(unlock|it'?s me|it is me|it'?s hemanth)\b", then_text.lower()):
                    self.send(then_text, spoken=True)
                else:
                    self.mouth.say("Welcome back, Hemanth.")
            else:
                self.failed_voice = (time.time(), audio)
                self.pin_msg.configure(text=f"That didn't match your voice closely enough ({score:.2f}, needs "
                                            f"{sec.unlock_threshold():.2f}). Use your PIN, and I'll learn from this try.",
                                       fg=C["warn"])

        threading.Thread(target=work, daemon=True).start()

    def unlock(self):
        self.last_verified = time.time()
        self.pin_fails = 0
        self.locked = False
        if self.lock_frame:
            self.lock_frame.destroy()
        self.lock_frame = None
        self.last_activity = time.time()
        self.set_status("Ready")
        self.entry.focus_set()
        if self.pending_briefing:
            self.pending_briefing = False
            self.auto_briefing()

    def check_idle(self):
        try:
            if sec.has_pin() and sec.lock_minutes() > 0 and not self.locked \
                    and time.time() - self.last_activity > sec.lock_minutes() * 60 and not self.busy:
                self.lock()
        finally:
            self.root.after(30000, self.check_idle)

    def set_pin(self):
        if sec.has_pin():
            old = simpledialog.askstring("Change PIN", "Enter your current PIN:", show="•", parent=self.root)
            if old is None:
                return
            if not sec.check_pin(old):
                messagebox.showwarning("Wrong PIN", "That's not your current PIN.", parent=self.root)
                return
        pin = simpledialog.askstring("Set PIN", "Choose a PIN (4 to 12 digits or letters):", show="•", parent=self.root)
        if not pin:
            return
        if not re.fullmatch(r"[A-Za-z0-9]{4,12}", pin):
            messagebox.showwarning("PIN not saved", "Use 4 to 12 letters or digits, no spaces.", parent=self.root)
            return
        again = simpledialog.askstring("Set PIN", "Type the same PIN again:", show="•", parent=self.root)
        if again != pin:
            messagebox.showwarning("PIN not saved", "The two PINs were different. Try again.", parent=self.root)
            return
        sec.set_pin(pin)
        mins = sec.lock_minutes()
        self.system("PIN saved. I'll ask for it when I start" +
                    (f", and lock myself after {mins} minutes without use (change it in Menu → Auto-lock)." if mins else ".") +
                    (" You can also unlock me with your voice." if sec.voice_unlock_ready() else
                     " Set up your voice (Menu → Set up my voice) to unlock me by voice too.") +
                    "\nMenu → Lock Vega now locks me straight away.\n"
                    "If you ever forget it: quit me and delete security.json in the mybot folder.")

    def remove_pin(self):
        if not sec.has_pin():
            self.system("There's no PIN set.")
            return
        old = simpledialog.askstring("Remove PIN", "Enter your current PIN:", show="•", parent=self.root)
        if old is None:
            return
        if not sec.check_pin(old):
            messagebox.showwarning("Wrong PIN", "That's not your current PIN.", parent=self.root)
            return
        sec.remove_pin()
        self.system("PIN removed. Anyone using this laptop can use me now.")

    # ---------------- voice ID
    def install_packages(self, packages, message, then):
        self.system(message, "warn")

        def work():
            try:
                r = subprocess.run([pip_python(), "-m", "pip", "install", *packages], capture_output=True, text=True,
                                   timeout=1800, creationflags=NO_WINDOW)
                ok, detail = r.returncode == 0, (r.stdout + r.stderr).strip().splitlines()[-3:]
            except Exception as e:
                ok, detail = False, [str(e)]

            def done():
                if ok:
                    import importlib
                    importlib.invalidate_caches()
                    then()
                else:
                    self.system("Install failed:\n" + "\n".join(detail) +
                                f"\nIn PowerShell run:  \"{pip_python()}\" -m pip install " + " ".join(packages), "warn")
            self.ui.put(done)

        threading.Thread(target=work, daemon=True).start()

    def setup_voice(self):
        if self.busy or self.listening or self.locked:
            return
        missing = sec.voice_packages_missing()
        if missing:
            if messagebox.askyesno("Set up my voice",
                                   "Voice recognition needs extra parts (about 400 MB, one time). Install them now?",
                                   parent=self.root):
                self.install_packages(missing, "Installing voice recognition parts. This takes a few minutes. Keep me open.",
                                      self.setup_voice)
            return
        if not messagebox.askokcancel("Set up my voice",
                                      "I'll record you 5 times. Each time, wait for the beep, read the sentence I show, "
                                      "then stop talking.\n\nUse a quiet room and your normal voice.", parent=self.root):
            return
        sentences = ["Hey Vega, it's Hemanth.",
                     "Hey Vega, what's my task for today, and do I have any reminders?",
                     "Hey Vega, it's me, Hemanth. Unlock.",
                     "Remind me at seven in the evening to call my family and study Linux.",
                     "Open YouTube, play some music, and tell me the weather in Chennai."]
        self.listening = True
        self.wake.paused.set()
        self.mouth.stop()

        def work():
            clips = []
            try:
                self.ears.model(self.ears.model_name)
                for i, line in enumerate(sentences, 1):
                    self.ui.put(lambda i=i, line=line: (self.system(f"Recording {i} of {len(sentences)}. After the beep, say:\n“{line}”"),
                                                        self.set_status(f"Recording {i} of {len(sentences)}…", "you")))
                    time.sleep(1.2)
                    beep("wake")
                    clips.append(self.ears.listen_once(wait_seconds=8, silence=1.2))
                self.ui.put(lambda: (self.system("Learning your voice. The first time, I download a small model (about 80 MB)."),
                                     self.set_status("Learning your voice…", "accent")))
                consistency = self.voice.enroll(clips)
                sec.set_voice_only(True)
                ok, msg = True, consistency
            except Exception as e:
                ok, msg = False, str(e)

            def done():
                self.listening = False
                self.wake.paused.clear()
                self.set_status("Ready")
                if ok:
                    self.voice_only_var.set(True)
                    self.system("Your voice is saved. From now on I only obey spoken commands in your voice. "
                                "Typing always works.\nIf I ever wrongly ignore you, run Set up my voice again, "
                                f"I set my checks to fit your voice: commands need {sec.voice_threshold():.2f}, "
                                f"unlocking needs {sec.unlock_threshold():.2f}. "
                                "You can change these yourself in Menu → Voice strictness.")
                else:
                    self.system(f"Voice setup didn't work: {msg}\nTry again in a quieter place.", "warn")
            self.ui.put(done)

        threading.Thread(target=work, daemon=True).start()

    def voice_checkup(self):
        """Tests her voice, the microphone and "Hey Vega" 3 times, fixes what it can, and writes vega_report.txt."""
        if self.busy or self.listening or self.locked:
            return
        if not messagebox.askokcancel(
                "Voice check-up",
                "1. I'll say a test sentence. Check that you hear me.\n"
                "2. Then, after each beep, say:  “Hey Vega, what time is it”  (3 times), the way you normally talk.\n\n"
                "I'll fix what I can and write a report (vega_report.txt).", parent=self.root):
            return
        self.listening = True
        self.wake.paused.set()
        self.mouth.stop()
        self.set_status("Voice check-up: testing my voice…", "accent")
        import numpy as np
        report = [f"VEGA VOICE REPORT  {datetime.now():%d %b %Y %I:%M %p}", ""]
        cfg = self.vega.cfg
        for k in ("whisper_model", "wake_model", "wake_strictness", "understand_telugu", "telugu_model", "voice_language",
                  "speak_telugu", "voice_on", "wake_word_on", "pause_on_calls"):
            report.append(f"{k}: {cfg.get(k)}")
        report.append(f"speak answers ticked: {bool(self.voice_var.get())}   listen for hey vega ticked: {bool(self.wake_var.get())}")
        report.append(f"only obey my voice: {sec.voice_only()}  command level: {sec.voice_threshold():.2f}")
        report.append(f"learned wake words: {', '.join(sorted(vega_voice.LEARNED)) or '-'}")
        state = {}

        def step1():
            state["voice"] = self.mouth.test_voice()
            self.ui.put(step2)

        def step2():
            report.append("")
            report.append(f"Windows voice test: {state['voice']}")
            heard_ok = messagebox.askyesno("Voice check-up", "Did you hear me say “Hello Hemanth. This is Vega”?", parent=self.root)
            report.append(f"Hemanth heard it: {heard_ok}")
            state["heard_ok"] = heard_ok
            threading.Thread(target=step3, daemon=True).start()

        def step3():
            clips = []
            try:
                self.ears.model(self.ears.wake_model_name)
                for i in range(1, 4):
                    self.ui.put(lambda i=i: self.set_status(f"Say “Hey Vega, what time is it” ({i} of 3)…", "you"))
                    time.sleep(1.0)
                    beep("wake")
                    audio = self.ears.listen_once(wait_seconds=7, silence=1.2, max_seconds=7)
                    if audio is None:
                        clips.append(None)
                        continue
                    level = float(np.sqrt(np.mean(np.asarray(audio) ** 2)))
                    hinted = self.ears.transcribe(audio, fast=True)
                    plain = self.ears.transcribe(audio, fast=True, prompt="Hello.")
                    full = self.ears.transcribe(audio)
                    score = None
                    try:
                        if self.voice.has_voiceprint():
                            score = self.voice.score(audio)
                    except Exception:
                        pass
                    clips.append({"level": level, "hinted": hinted, "plain": plain, "full": full, "score": score,
                                  "lang": self.ears.last_lang})
            except Exception as e:
                clips.append({"error": str(e)})
            self.ui.put(lambda: finish(clips))

        def finish(clips):
            self.listening = False
            self.wake.paused.clear()
            report.append("")
            tips = []
            if not state.get("heard_ok"):
                tips.append("You didn't hear my voice. Check the laptop volume and that 'Speak answers' is ticked. "
                            + ("Windows voice error: " + state["voice"] if state["voice"] != "ok" else
                               "The Windows voice ran fine, so check the speaker / headphones / volume."))
            got = [c for c in clips if c and "error" not in c]
            for n, c in enumerate(clips, 1):
                if not c:
                    report.append(f"Try {n}: heard nothing (mic too quiet, or wrong microphone)")
                elif "error" in c:
                    report.append(f"Try {n}: error {c['error']}")
                else:
                    match = "-" if c["score"] is None else f"{c['score']:.2f}"
                    report.append(f"Try {n}: mic level {c['level']:.3f}  lang {c['lang']}  voice match {match}")
                    report.append(f"   with hint: {c['hinted']}")
                    report.append(f"   no hint:   {c['plain']}")
                    report.append(f"   full:      {c['full']}")
            if len(got) < len(clips) or any(c["level"] < 0.01 for c in got):
                tips.append("Your microphone sounds quiet. Speak a little closer, or raise the mic volume in Windows "
                            "Settings → System → Sound → Input.")
            # learn how she hears his "Vega", then pick the strictest level that still catches him
            new = vega_voice.learn_wake_words([c["hinted"] for c in got] + [c["plain"] for c in got])
            if new:
                vega_voice.LEARNED.update(new)
                saved = sorted(set(cfg.get("wake_variants", [])) | vega_voice.LEARNED)[:30]
                cfg["wake_variants"] = saved
                save_config_value("wake_variants", saved)
            chosen = None
            if got:
                for level in ("normal", "easy", "very_easy"):
                    ok = 0
                    for c in got:
                        loose = level == "very_easy"
                        if vega_voice.find_wake(c["hinted"], loose=loose) is None:
                            continue
                        if level == "normal":
                            w = vega_voice.WakeListener.__new__(vega_voice.WakeListener)
                            w.strictness = "normal"
                            if vega_voice.find_wake(c["plain"]) is None and not w.second_check_ok(c["plain"]):
                                continue
                        ok += 1
                    report.append(f"'Hey Vega' caught at {level}: {ok} of {len(got)}")
                    if chosen is None and ok >= max(1, len(got) - 1):
                        chosen = level
                if chosen:
                    self.set_wake_strictness(chosen)
                    self.wake_strict_var.set(chosen)
                    tips.append(f"I set Hey Vega sensitivity to “{chosen.replace('_', ' ')}”, the strictest level that still catches you.")
                else:
                    tips.append("I couldn't catch your “Hey Vega” reliably. Try Settings → Speech accuracy → Better (medium.en), "
                                "then run this check-up again.")
            if sec.voice_only() and got and any(c["score"] is not None and c["score"] < sec.voice_threshold() for c in got):
                low = min(c["score"] for c in got if c["score"] is not None)
                new_level = max(0.20, round(low - 0.05, 2))
                sec.set_thresholds(new_level, max(new_level, sec.unlock_threshold()))
                tips.append(f"'Only obey my voice' was rejecting you, so I lowered the command level to {new_level:.2f}.")
            if not self.voice_var.get():
                self.voice_var.set(1)
                self.toggle_voice()
                tips.append("“Speak answers” was off, so I turned it on.")
            if not self.wake_var.get():
                self.wake_var.set(1)
                self.toggle_wake()
                tips.append("“Listen for Hey Vega” was off, so I turned it on.")
            report.append("")
            report.append("FIXES / TIPS:")
            report.extend(["- " + t for t in tips] or ["- Everything looks fine."])
            for name in ("voice_log.txt", "vega_error.log"):
                try:
                    with open(here(name), encoding="utf-8", errors="replace") as f:
                        tail = f.readlines()[-25:]
                    report.extend(["", f"--- last lines of {name} ---"] + [x.rstrip() for x in tail])
                except OSError:
                    pass
            try:
                with open(here("vega_report.txt"), "w", encoding="utf-8") as f:
                    f.write("\n".join(report))
            except OSError:
                pass
            self.set_status("Ready")
            self.system("Voice check-up done.\n" + "\n".join("• " + t for t in tips or ["Everything looks fine."]) +
                        "\nThe full report is in vega_report.txt (in your mybot folder). If problems stay, send a screenshot of it.")
            try:
                open_path(here("vega_report.txt"))
            except Exception:
                pass

        threading.Thread(target=step1, daemon=True).start()

    def teach_wake(self):
        """Record 'Hey Vega' 5 times and remember how Whisper hears it in Hemanth's accent."""
        if self.busy or self.listening or self.locked:
            return
        if not messagebox.askokcancel("Teach me your “Hey Vega”",
                                      "I'll listen 5 times. After each beep, say just “Hey Vega” the way you normally do, "
                                      "then stop.", parent=self.root):
            return
        self.listening = True
        self.wake.paused.set()
        self.mouth.stop()

        plains = []

        def work():
            heard, err = [], None
            try:
                self.ears.model(self.ears.wake_model_name)
                for i in range(1, 6):
                    self.ui.put(lambda i=i: self.set_status(f"Say “Hey Vega” ({i} of 5)…", "you"))
                    time.sleep(0.9)
                    beep("wake")
                    audio = self.ears.listen_once(wait_seconds=6, silence=0.9, max_seconds=5)
                    if audio is None:
                        continue
                    h = self.ears.transcribe(audio, fast=True)
                    heard.append(h)
                    plain = self.ears.transcribe(audio, fast=True, prompt="Hello.")  # how she hears it without a hint
                    if plain and plain != h:
                        heard.append(plain)
                    p_norm = vega_voice._norm(plain)
                    if 1 < len(p_norm.split()) <= 4 and p_norm.split()[0] in vega_voice.GREETINGS:
                        plains.append(p_norm)
                    self.ui.put(lambda h=h, i=i: self.system(f"{i}: I heard “{h or '…'}”"))
            except Exception as e:
                err = str(e)

            def done():
                self.listening = False
                self.wake.paused.clear()
                self.set_status("Ready")
                if err:
                    self.system(f"That didn't work: {err}", "warn")
                    return
                new = vega_voice.learn_wake_words(heard)
                vega_voice.LEARNED.update(new)
                saved = sorted(set(self.vega.cfg.get("wake_variants", [])) | vega_voice.LEARNED)[:30]
                self.vega.cfg["wake_variants"] = saved
                save_config_value("wake_variants", saved)
                if plains:
                    vega_voice.PLAIN_PHRASES.update(plains)
                    kept = sorted(set(self.vega.cfg.get("wake_plain", [])) | set(plains))[:20]
                    self.vega.cfg["wake_plain"] = kept
                    save_config_value("wake_plain", kept)
                    vega_voice.log_problem("teach: learned how you sound without a hint: " + ", ".join(sorted(set(plains))))
                caught = sum(1 for h in heard if vega_voice.find_wake(h) is not None)
                self.system(f"Learned how I hear you: {', '.join(sorted(new)) or 'nothing new'}. "
                            f"Now I catch your “Hey Vega” in {caught} of {len(heard)} tries."
                            + ("" if self.wake_var.get() else " Tick “Listen for Hey Vega” to use it."))
            self.ui.put(done)

        threading.Thread(target=work, daemon=True).start()

    def toggle_telugu(self):
        on = bool(self.telugu_var.get())
        self.ears.telugu = on
        self.vega.cfg["understand_telugu"] = on
        save_config_value("understand_telugu", on)
        if on:
            self.system("Telugu on. Speak in Telugu, English, or a mix. I'll understand and show your words in English, "
                        "then answer in English. Keep saying “Hey Vega” in English to wake me.\n"
                        "The first time you press Mic, I download the Telugu-capable model (about 500 MB).\n"
                        "Tip: for better Telugu (but slower), set \"telugu_model\": \"medium\" in Menu → Edit settings.")
        else:
            self.system("Telugu off. Back to English-only listening, which is a bit more accurate for English.")

    @staticmethod
    def telugu_speech_ready():
        import importlib.util
        return all(importlib.util.find_spec(m) is not None for m in ("edge_tts", "av"))

    def voice_mode(self):
        mode = str(self.vega.cfg.get("voice_language") or "auto").lower()
        return mode if mode in ("auto", "english", "telugu") else "auto"

    def answer_language(self):
        """Which voice to answer with: the language he just used (auto), or the fixed one."""
        mode = self.voice_mode()
        lang = "te" if mode == "telugu" else "en" if mode == "english" else getattr(self, "reply_lang", "en")
        if lang == "te" and not self.telugu_speech_ready():
            if not getattr(self, "told_telugu_missing", False):
                self.told_telugu_missing = True
                self.system("You spoke Telugu, but my Telugu voice isn't installed yet, so I answered in English. "
                            "Menu → Voice language → Always Telugu installs it (1 minute).", "warn")
            return "en"
        return lang

    def set_voice_language(self, mode, quiet=False):
        if mode in ("telugu", "auto") and not self.telugu_speech_ready():
            import importlib.util
            missing = [p for p, m in (("edge-tts", "edge_tts"), ("av", "av"), ("deep-translator", "deep_translator"))
                       if importlib.util.find_spec(m) is None]
            if missing and mode == "telugu":
                self.install_packages(missing, "Installing the Telugu voice parts (small, about 1 minute)…",
                                      lambda: self.set_voice_language(mode))
                return
        self.vega.cfg["voice_language"] = mode
        save_config_value("voice_language", mode)
        self.vega.cfg["speak_telugu"] = mode == "telugu"
        save_config_value("speak_telugu", mode == "telugu")
        self.mouth.telugu = mode == "telugu"
        self.speak_te_var.set(mode == "telugu")
        if hasattr(self, "lang_var"):
            self.lang_var.set(mode)
        if quiet:
            return
        if mode == "auto":
            self.system("Voice language: same as you. Speak Telugu or Tenglish and I answer in Telugu; speak English and I answer "
                        "in English. (To hear your Telugu, also tick Menu → Understand Telugu speech.)")
        elif mode == "telugu":
            self.system("Voice language: always Telugu. The text stays in English. The Telugu voice needs internet.")
            self.mouth.say("Hello Hemanth. From now on, I will speak to you in Telugu.", lang="te")
        else:
            self.system("Voice language: always English.")

    def toggle_speak_telugu(self):  # old menu item, kept for older settings
        self.set_voice_language("telugu" if self.speak_te_var.get() else "english")

    # ---------------- brain (AI model)
    def fill_brain_menu(self):
        m = self.brain_menu
        m.delete(0, "end")
        main = self.vega.cfg.get("model") or "vega"
        chosen = (self.vega.cfg.get("chat_model") or "").strip() or main
        self.brain_var = tk.StringVar(value="auto" if chosen.lower() == "auto" else chosen)
        names = self.vega.installed_models(fresh=True)
        m.add_radiobutton(label=f"Auto: pick the best brain for each question (uses {self.vega.smart_model()} to think)",
                          value="auto", variable=self.brain_var, command=lambda: self.set_brain("auto"))
        m.add_separator()
        m.add_command(label="Or always use one brain for chatting:", state="disabled")
        if not names:
            m.add_command(label="  (Ollama isn't running)", state="disabled")
        for name in names:
            if name.lower().startswith(("nomic", "mxbai", "all-minilm")) or "embed" in name.lower():
                continue  # not chat models
            label = name + ("   (Vega, fast)" if self.vega._same(name, main) else "")
            m.add_radiobutton(label=label, value=name if not self.vega._same(name, main) else main,
                              variable=self.brain_var, command=lambda: self.set_brain(self.brain_var.get()))
        m.add_separator()
        self.auto_code_var = tk.BooleanVar(value=bool(self.vega.cfg.get("auto_code_model", True)))
        code = self.vega.cfg.get("code_model") or "qwen2.5:7b"
        m.add_checkbutton(label=f"Use {code} for code questions", variable=self.auto_code_var,
                          command=self.toggle_auto_code)
        m.add_command(label="How to get a smarter brain…", command=self.brain_help)

    def set_brain(self, name):
        main = self.vega.cfg.get("model") or "vega"
        value = "" if self.vega._same(name, main) else name
        self.vega.cfg["chat_model"] = value
        save_config_value("chat_model", value)
        if value == "auto":
            smart = self.vega.smart_model()
            code = self.vega.cfg.get("code_model") or "qwen2.5:7b"
            self.system("Auto brain on. For each question I pick the right brain:\n"
                        f"• Small talk and commands → my fast brain ({main})\n"
                        f"• Study, explain, assignments, long questions → {smart}\n"
                        f"• Code, errors, Linux commands → {code}\n"
                        "The status line shows which brain answered." +
                        ("" if not self.vega._same(smart, main) else
                         "\nTip: download a smarter brain with  ollama pull qwen2.5:7b  and I'll use it by myself."))
        elif value:
            self.system(f"Okay. {name} now answers when we chat. Commands still use my fast brain.\n"
                        "The first answer may take a little longer while it loads.")
            threading.Thread(target=self._warm, args=(name,), daemon=True).start()
        else:
            self.system("Okay. I'm back to my normal fast brain for chatting.")

    @staticmethod
    def _warm(name):
        try:
            import ollama
            ollama.generate(model=name, prompt="", keep_alive="15m")
        except Exception:
            pass

    def toggle_auto_code(self):
        on = bool(self.auto_code_var.get())
        self.vega.cfg["auto_code_model"] = on
        save_config_value("auto_code_model", on)
        code = self.vega.cfg.get("code_model") or "qwen2.5:7b"
        self.system(f"Code questions will go to {code}." if on else "Code questions will use my normal brain.")

    def brain_help(self):
        self.system("To give me a smarter brain for chatting:\n"
                    "1. Open PowerShell and run:  ollama pull qwen2.5:7b\n"
                    "   (about 4.7 GB; wait until it says success)\n"
                    "2. Menu → Brain (AI model) → Auto. I'll use it for study questions by myself.\n"
                    "Commands like 'open YouTube' always use my fast brain.")

    def note_score(self, score):
        self.recent_scores = (self.recent_scores + [round(float(score), 2)])[-8:]
        return score

    def voice_strictness(self):
        """Two sliders (0.20 to 0.45): how closely a voice must match Hemanth's."""
        if not self.voice.has_voiceprint():
            messagebox.showinfo("Voice strictness", "First set up your voice: Menu → Set up my voice.", parent=self.root)
            return
        if sec.has_pin():  # so nobody else can make the voice check easier
            pin = simpledialog.askstring("Voice strictness", "Enter your PIN:", show="•", parent=self.root)
            if pin is None:
                return
            if not sec.check_pin(pin):
                messagebox.showwarning("Wrong PIN", "That's not your PIN.", parent=self.root)
                return
        win = tk.Toplevel(self.root)
        win.title("Voice strictness")
        win.configure(bg=C["bg"], padx=18, pady=14)
        win.resizable(False, False)
        win.transient(self.root)
        tk.Label(win, text="How closely must a voice match yours?", bg=C["bg"], fg=C["ink"], font=F_BOLD).pack(anchor="w")
        tk.Label(win, text="Lower number = easier (fewer wrong “ignored” messages, but other people may get through).\n"
                           "Higher number = stricter (safer, but may ignore you in a noisy room).",
                 bg=C["bg"], fg=C["muted"], font=F_SMALL, justify="left").pack(anchor="w", pady=(2, 10))
        cmd_var = tk.DoubleVar(value=sec.voice_threshold())
        unl_var = tk.DoubleVar(value=sec.unlock_threshold())
        for label, var in (("Voice commands need:", cmd_var), ("Voice unlock needs:", unl_var)):
            tk.Label(win, text=label, bg=C["bg"], fg=C["ink"], font=F).pack(anchor="w")
            tk.Scale(win, from_=sec.MIN_LEVEL, to=sec.MAX_LEVEL, resolution=0.01, orient="horizontal", length=360,
                     variable=var, bg=C["bg"], fg=C["ink"], troughcolor=C["entry"], highlightthickness=0,
                     activebackground=C["accent"], font=F).pack(anchor="w", pady=(0, 8))
        seen = ", ".join(f"{x:.2f}" for x in self.recent_scores) or "none yet (speak to me a few times first)"
        tk.Label(win, text=f"Your last voice matches: {seen}\nTip: set commands a little below your usual match.",
                 bg=C["bg"], fg=C["muted"], font=F_SMALL, justify="left").pack(anchor="w", pady=(0, 10))
        row = tk.Frame(win, bg=C["bg"])
        row.pack(anchor="e")

        def save():
            c, u = cmd_var.get(), unl_var.get()
            if u < c:
                u = c  # unlocking should never be easier than a normal command
            sec.set_thresholds(c, u)
            win.destroy()
            self.system(f"Saved. Voice commands need {sec.voice_threshold():.2f}, "
                        f"voice unlock needs {sec.unlock_threshold():.2f}.")

        tk.Button(row, text="Cancel", command=win.destroy, bg=C["btn"], fg=C["ink"], relief="flat",
                  activebackground=C["btn_hi"], padx=12).pack(side="left", padx=(0, 8))
        tk.Button(row, text="Save", command=save, bg=C["accent"], fg=C["bg"], relief="flat",
                  activebackground=C["btn_hi"], padx=16).pack(side="left")
        win.grab_set()

    def toggle_voice_only(self):
        on = bool(self.voice_only_var.get())
        if on and not self.voice.has_voiceprint():
            self.voice_only_var.set(False)
            self.setup_voice()
            return
        sec.set_voice_only(on)
        self.system("I'll only obey spoken commands in your voice." if on else "I'll obey any voice again.")

    # ---------------- phone
    def start_phone(self):
        if self.phone:
            self.phone.running = False  # the old link stops after its current check
            self.phone = None
        if not str(self.vega.cfg.get("telegram_token") or "").strip():
            return
        from vega_telegram import TelegramBridge
        self.phone = TelegramBridge(self.vega, self.ears, lambda *ev: self.ui.put(lambda ev=ev: self.phone_event(*ev)))
        self.phone.start()

    def phone_event(self, kind, *data):
        if kind == "status":
            self.system(data[0])
        elif kind == "phone":
            text_in, text_out = data
            self.add_message("phone", text_in)
            self.add_message("vega", text_out)
        elif kind == "pair_request":
            chat_id, name = data
            self.pending_pair = (chat_id, name)
            self.system(f"{name} messaged your Vega bot on Telegram. If that's you, type  allow phone  and press Enter.", "warn")
            self.show()
            ok = messagebox.askyesno("Connect this phone?",
                                     f"{name} just messaged your Vega bot on Telegram.\n\n"
                                     f"Allow this chat to control Vega?\nOnly say Yes if it's YOU.", parent=self.root)
            if ok:
                self.allow_phone()

    def allow_phone(self):
        pending = getattr(self, "pending_pair", None)
        if not pending or not self.phone:
            self.system("No phone is waiting to connect. Send 'hi' to your bot from your phone first.")
            return
        chat_id, _ = pending
        self.pending_pair = None
        save_config_value("telegram_chat_id", str(chat_id))
        self.vega.cfg["telegram_chat_id"] = str(chat_id)
        self.phone.pair(chat_id)
        self.system("Phone connected. You can now message Vega on Telegram while this laptop is on.")

    def connect_phone(self):
        token = simpledialog.askstring(
            "Connect phone (Telegram)",
            "1. In Telegram, open @BotFather and send /newbot\n"
            "2. Give it a name and a username ending in 'bot'\n"
            "3. Copy the token it gives you and paste it here:",
            parent=self.root)
        if not token:
            return
        token = token.strip()
        if not re.match(r"^\d+:[A-Za-z0-9_-]{20,}$", token):
            messagebox.showwarning("That doesn't look right", "A bot token looks like 123456789:AAH... Copy it again from BotFather.")
            return
        save_config_value("telegram_token", token)
        save_config_value("telegram_chat_id", "")
        self.vega.cfg["telegram_token"] = token
        self.vega.cfg["telegram_chat_id"] = ""
        self.start_phone()
        self.system("Now open your new bot in Telegram and send it 'hi'. A box will pop up here: click Yes.")

    # ---------------- settings
    def toggle_voice(self):
        on = bool(self.voice_var.get())
        save_config_value("voice_on", on)
        if not on:
            self.mouth.stop()

    def toggle_wake(self):
        on = bool(self.wake_var.get())
        save_config_value("wake_word_on", on)
        self.wake.set_enabled(on)
        if on:
            if getattr(self, "listening_paused", False):
                self.listening_paused = False
                self.wake.paused.clear()
            self.system("Listening for “Hey Vega”. Say it, wait for the beep, then speak.")
            if hasattr(self, "mute_btn"):
                self.mute_btn.configure(text="👂", fg=C["ok"], bg=C["btn"])
            if hasattr(self, "top_mute_btn"):
                self.top_mute_btn.configure(text="🎤 Listening: ON", fg=C["ok"], bg=C["btn"])
            if hasattr(self, "quickbar") and hasattr(self.quickbar, "mute_btn"):
                self.quickbar.mute_btn.configure(text="👂", fg=C["ok"])
        else:
            self.system("Stopped listening for “Hey Vega”.")
            if hasattr(self, "mute_btn"):
                self.mute_btn.configure(text="🔇", fg=C["bad"], bg="#2A1B1F")
            if hasattr(self, "top_mute_btn"):
                self.top_mute_btn.configure(text="🔇 Muted", fg=C["bad"], bg="#2A1B1F")
            if hasattr(self, "quickbar") and hasattr(self.quickbar, "mute_btn"):
                self.quickbar.mute_btn.configure(text="🔇", fg=C["bad"])

    def toggle_mute_listening(self):
        """Instant pause/resume button for microphone listening (stops both wake word and room speech)."""
        self.listening_paused = not getattr(self, "listening_paused", False)
        if self.listening_paused:
            self.wake.paused.set()
            self.front = False
            self.mouth.stop()
            self.set_status("Paused (Not listening)", "bad")
            self.system("Microphone listening paused. Vega will not listen for 'Hey Vega' or room speech until you click Resume.")
            if hasattr(self, "mute_btn"):
                self.mute_btn.configure(text="🔇", fg=C["bad"], bg="#2A1B1F")
            if hasattr(self, "top_mute_btn"):
                self.top_mute_btn.configure(text="🔇 Muted", fg=C["bad"], bg="#2A1B1F")
            if hasattr(self, "quickbar") and hasattr(self.quickbar, "mute_btn"):
                self.quickbar.mute_btn.configure(text="🔇", fg=C["bad"])
            vega_voice.log_problem("listening: PAUSED by user (Stop Listening button)")
        else:
            self.wake.paused.clear()
            if bool(self.wake_var.get()):
                self.wake.set_enabled(True)
            self.set_status("Ready", "ok")
            self.system("Microphone listening resumed.")
            if hasattr(self, "mute_btn"):
                self.mute_btn.configure(text="👂", fg=C["ok"], bg=C["btn"])
            if hasattr(self, "top_mute_btn"):
                self.top_mute_btn.configure(text="🎤 Listening: ON", fg=C["ok"], bg=C["btn"])
            if hasattr(self, "quickbar") and hasattr(self.quickbar, "mute_btn"):
                self.quickbar.mute_btn.configure(text="👂", fg=C["ok"])
            vega_voice.log_problem("listening: RESUMED by user")

    def toggle_startup(self):
        lnk = startup_shortcut_path()
        try:
            if self.startup_var.get():
                make_shortcut(lnk)
                self.system("Done. Vega will start by herself when you turn on your laptop.")
            else:
                if os.path.exists(lnk):
                    os.remove(lnk)
                self.system("Vega won't start with Windows any more.")
        except Exception as e:
            self.startup_var.set(os.path.exists(lnk))
            self.system(f"Couldn't change that: {e}", "warn")

    def desktop_shortcut(self):
        try:
            make_shortcut(os.path.join(desktop_folder(), "Vega.lnk"))
            self.system("Added a Vega shortcut to your desktop. Double-click it to open me.")
        except Exception as e:
            self.system(f"Couldn't make the shortcut: {e}", "warn")

    def edit_settings(self):
        path = here("config.json")
        if IS_WINDOWS:
            subprocess.Popen(["notepad.exe", path])
        self.system("Change settings in Notepad, save, then quit and reopen Vega.")

    def clear_chat(self):
        if self.chat["messages"]:
            self.delete_chat(self.chat["id"])
        else:
            self.new_chat()

    # ---------------- start, close
    def set_speech_model(self, name):
        self.vega.cfg["whisper_model"] = name
        save_config_value("whisper_model", name)
        self.ears.model_name = name
        self.ears._models.pop(name, None)
        self.system(f"Speech accuracy set to {name}. The first time you press Mic it downloads the model (about 1.5 GB for the "
                    "bigger ones), so wait a few minutes. Bigger = understands your accent better, but answers 1–2 seconds slower.")

    def set_telugu_model(self, name):
        self.vega.cfg["telugu_model"] = name
        save_config_value("telugu_model", name)
        self.ears.telugu_model_name = name
        self.system(f"Telugu listening set to {name}. The first use downloads it. Medium understands Telugu much better but is slower.")

    def greet(self):
        h = datetime.now().hour
        part = "morning" if h < 12 else ("afternoon" if h < 17 else "evening")
        msg = f"Good {part}, Hemanth. Vega here."
        plan = self.vega.load_plan() if self.vega.cfg.get("study_plan_reminders") else None
        rows = [r for r in self.vega.tasks_for(datetime.now().date()) if not r[1]]
        if rows:
            msg += f" You have {len(rows)} task{'s' if len(rows) != 1 else ''} today: " + ", ".join(r[0]["text"] for r in rows[:3]) + "."
        if plan:
            idx = self.vega.today_index(plan)
            d = self.vega.plan_day(plan, idx)
            if d:
                done = self.vega.is_done(idx)
                streak, _ = self.vega.streaks(idx)
                msg += f" Today's study task is {d['title']}" + (", and it's already done. Nice." if done else ".")
                if streak >= 2:
                    msg += f" You're on a {streak}-day streak."
        self.status_lbl.configure(text=msg.replace("Vega here. ", ""))
        # Say the full greeting (with today's task) only the first time she opens each day
        from vega_core import read_json, write_json
        state = read_json("briefing_state.json", {}) or {}
        today = datetime.now().date().isoformat()
        if state.get("greeted") == today:
            return
        state["greeted"] = today
        write_json("briefing_state.json", state)
        if self.voice_var.get():
            self.mouth.say(msg)

    def repair_if_needed(self):
        missing = missing_packages()
        if not missing:
            return
        self.system("Some parts I need aren't installed for the Python I'm running on: " + ", ".join(missing) +
                    ". Installing them now. This takes 1 to 3 minutes. Keep me open.", "warn")

        def work():
            try:
                r = subprocess.run([pip_python(), "-m", "pip", "install", *missing], capture_output=True, text=True,
                                   timeout=900, creationflags=NO_WINDOW)
                ok = r.returncode == 0
                detail = (r.stdout + r.stderr).strip().splitlines()[-3:]
            except Exception as e:
                ok, detail = False, [str(e)]

            def done():
                if ok:
                    import importlib
                    importlib.invalidate_caches()
                    self.system("Done. Everything is installed now.")
                    self.start_phone()
                else:
                    self.system("I couldn't install them myself:\n" + "\n".join(detail) +
                                f"\nIn PowerShell run:  \"{pip_python()}\" -m pip install " + " ".join(missing), "warn")
            self.ui.put(done)

        threading.Thread(target=work, daemon=True).start()

    def check_ollama(self):
        try:
            import ollama
            names = [m.model for m in ollama.list().models]
            model = self.vega.cfg["model"]
            if not any(n == model or n.startswith(model + ":") for n in names):
                self.ui.put(lambda: self.system(f"I can't find the model '{model}'. In PowerShell run: "
                                                f"ollama create {model} -f Modelfile-full", "warn"))
                return
            brain = self.vega.smart_model() if self.vega.cfg.get("one_brain", True) else model
            ollama.generate(model=brain, prompt="", keep_alive="15m")  # load the brain into the GPU now, so the first answer is fast
        except Exception:
            self.ui.put(lambda: self.system("I can't reach Ollama. Open the Ollama app from the Start menu. "
                                            "Commands still work; chatting needs Ollama.", "warn"))

    def accept_other_instances(self, server):
        while True:
            try:
                conn, _ = server.accept()
                conn.close()
                self.ui.put(self.show)
            except OSError:
                return

    def show(self):
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def on_close(self):
        """The window's X button: ask, quit, or keep running in the taskbar (Menu → Close button)."""
        action = str(self.vega.cfg.get("close_action", "ask")).lower()
        if action == "quit":
            self.quit()
        elif action == "hide":
            self.hide_to_taskbar()
        else:
            self.ask_close()

    def hide_to_taskbar(self):
        self.root.iconify()
        if not self.told_about_close:
            self.told_about_close = True
            self.system("I'm still running in the taskbar so your reminders work.")

    def set_close_action(self, action):
        self.vega.cfg["close_action"] = action
        save_config_value("close_action", action)
        if hasattr(self, "close_var"):
            self.close_var.set(action)

    def ask_close(self):
        if getattr(self, "close_win", None) and self.close_win.winfo_exists():
            self.close_win.lift()
            return
        win = self.close_win = tk.Toplevel(self.root)
        win.title("Close Vega")
        win.configure(bg=C["bg"], padx=20, pady=16)
        win.resizable(False, False)
        win.transient(self.root)
        tk.Label(win, text="Do you want to quit Vega?", bg=C["bg"], fg=C["ink"], font=F_BOLD).pack(anchor="w")
        tk.Label(win, text="If you quit, reminders and “Hey Vega” stop until you open me again.\n"
                           "If I keep running, I wait in the taskbar and your reminders still work.",
                 bg=C["bg"], fg=C["muted"], font=F_SMALL, justify="left").pack(anchor="w", pady=(4, 10))
        remember = tk.BooleanVar(value=False)
        tk.Checkbutton(win, text="Remember my choice (change it later in Menu → Close button)", variable=remember,
                       bg=C["bg"], fg=C["ink"], selectcolor=C["entry"], activebackground=C["bg"],
                       activeforeground=C["ink"], font=F_SMALL).pack(anchor="w", pady=(0, 12))
        row = tk.Frame(win, bg=C["bg"])
        row.pack(fill="x")

        def choose(action):
            if remember.get():
                self.set_close_action(action)
            win.destroy()
            if action == "quit":
                self.quit()
            else:
                self.hide_to_taskbar()

        tk.Button(row, text="Keep running in background", command=lambda: choose("hide"), bg=C["btn"], fg=C["ink"],
                  relief="flat", activebackground=C["btn_hi"], padx=12, pady=4).pack(side="left")
        tk.Button(row, text="Quit Vega", command=lambda: choose("quit"), bg=C["bad"], fg=C["bg"],
                  relief="flat", activebackground=C["btn_hi"], padx=16, pady=4).pack(side="right")
        win.bind("<Escape>", lambda e: win.destroy())
        win.grab_set()

    def quit(self):
        self.mouth.stop()
        self.root.destroy()
        os._exit(0)

    def poll(self):
        try:
            while True:
                fn = self.ui.get_nowait()
                try:
                    fn()
                except Exception as e:
                    print("UI error:", e)
        except queue.Empty:
            pass
        self.root.after(50, self.poll)


def main():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        server.bind(("127.0.0.1", PORT))
        server.listen(2)
    except OSError:
        try:  # Vega is already running: bring that window to the front instead
            socket.create_connection(("127.0.0.1", PORT), timeout=2).close()
        except OSError:
            pass
        return
    if IS_WINDOWS:
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on laptop screens
        except Exception:
            pass
    root = tk.Tk()

    def report(*exc):
        import traceback
        with open(here("vega_error.log"), "a", encoding="utf-8") as f:
            f.write(f"\n--- {datetime.now()} ---\n" + "".join(traceback.format_exception(*exc)))

    root.report_callback_exception = report
    try:
        App(root, server)
    except Exception:
        report(*sys.exc_info())
        messagebox.showerror("Vega couldn't start",
                             "Something went wrong while starting. The details are in vega_error.log "
                             "in your mybot folder. Send a screenshot of that file.")
        return
    root.mainloop()


if __name__ == "__main__":
    main()
