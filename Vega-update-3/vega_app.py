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
import json
import webbrowser
from datetime import datetime

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from vega_core import IS_WINDOWS, NO_WINDOW, ReminderClock, Vega, here, save_config_value  # noqa: E402
from vega_voice import Ears, Mouth, WakeListener, beep, shorten_for_speech  # noqa: E402

C = dict(bg="#0D1319", panel="#141C25", line="#223040", ink="#E3EAF1", muted="#8594A3", accent="#8FB4FF",
         you="#F0C674", ok="#6FD19E", warn="#E6B15C", bad="#F08A7E", entry="#1A2430", btn="#1F2B39", btn_hi="#2A3A4D")
UI_FONT = "Segoe UI" if IS_WINDOWS else "DejaVu Sans"
F = (UI_FONT, 11)
F_SMALL = (UI_FONT, 9)
F_BOLD = (UI_FONT, 11, "bold")
F_TITLE = (UI_FONT, 18, "bold")
PORT = 47631  # used to make sure only one Vega runs at a time
URL_RE = re.compile(r"https?://[^\s)]+")
LOG_FILE = "chat_log.jsonl"   # everything you and Vega said, shown again when you reopen her
STAMP_RE = re.compile(r"^\[\d{1,2} \w{3} \d{4}, \d{1,2}:\d{2} [AP]M\]\s*")


# ---------------------------------------------------------------- self-repair

NEEDED = {"requests": "requests", "psutil": "psutil", "pypdf": "pypdf", "docx": "python-docx",
          "ollama": "ollama", "faster_whisper": "faster-whisper", "sounddevice": "sounddevice", "numpy": "numpy"}


def missing_packages():
    import importlib.util
    return [pip for mod, pip in NEEDED.items() if importlib.util.find_spec(mod) is None]


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


# ---------------------------------------------------------------- the window

class App:
    def __init__(self, root, server):
        self.root = root
        self.vega = Vega()
        cfg = self.vega.cfg
        self.mouth = Mouth(rate=cfg.get("speech_rate", 1), voice=cfg.get("voice", "female"))
        self.ears = Ears(cfg.get("whisper_model", "base.en"), cfg.get("wake_model", "tiny.en"))
        self.ui = queue.Queue()
        self.busy = False
        self.listening = False
        self.listen_stop = None
        self.streaming = False
        self.phone = None
        self.told_about_close = False

        self.build()
        threading.Thread(target=self.accept_other_instances, args=(server,), daemon=True).start()

        ReminderClock(self.vega, lambda text: self.ui.put(lambda: self.on_reminder(text))).start()
        self.wake = WakeListener(
            self.ears,
            on_wake=lambda: self.ui.put(self.on_wake),
            on_command=lambda text: self.ui.put(lambda: self.send(text, spoken=True)),
            is_busy=lambda: self.busy or self.listening or self.mouth.busy(),
            on_error=lambda m: self.ui.put(lambda: self.system(m, "warn")))
        self.wake.start()
        self.wake.set_enabled(bool(self.wake_var.get()))
        self.start_phone()

        self.show_history()
        self.greet()
        self.repair_if_needed()
        threading.Thread(target=self.check_ollama, daemon=True).start()
        self.root.after(50, self.poll)

    # ---------------- layout
    def button(self, parent, text, cmd, **kw):
        b = tk.Button(parent, text=text, command=cmd, font=kw.pop("font", F), bg=kw.pop("bg", C["btn"]), fg=kw.pop("fg", C["ink"]),
                      activebackground=C["btn_hi"], activeforeground=C["ink"], relief="flat", bd=0,
                      padx=kw.pop("padx", 12), pady=kw.pop("pady", 6), cursor="hand2", **kw)
        return b

    def build(self):
        r = self.root
        r.title("Vega")
        r.geometry("540x780")
        r.minsize(380, 480)
        r.configure(bg=C["bg"])
        r.protocol("WM_DELETE_WINDOW", self.on_close)

        top = tk.Frame(r, bg=C["bg"])
        top.pack(fill="x", padx=16, pady=(14, 6))
        tk.Label(top, text="Vega", font=F_TITLE, bg=C["bg"], fg=C["ink"]).pack(side="left")
        self.dot = tk.Canvas(top, width=12, height=12, bg=C["bg"], highlightthickness=0)
        self.dot.pack(side="left", padx=(12, 6), pady=(6, 0))
        self.dot_id = self.dot.create_oval(2, 2, 11, 11, fill=C["ok"], outline="")
        self.status_lbl = tk.Label(top, text="Ready", font=F_SMALL, bg=C["bg"], fg=C["muted"])
        self.status_lbl.pack(side="left", pady=(6, 0))

        menu_btn = tk.Menubutton(top, text="Menu ▾", font=F_SMALL, bg=C["btn"], fg=C["ink"], activebackground=C["btn_hi"],
                                 activeforeground=C["ink"], relief="flat", padx=10, pady=4, cursor="hand2")
        menu = tk.Menu(menu_btn, tearoff=0, bg=C["panel"], fg=C["ink"], activebackground=C["btn_hi"], activeforeground=C["ink"])
        self.startup_var = tk.BooleanVar(value=os.path.exists(startup_shortcut_path()))
        menu.add_checkbutton(label="Start with Windows", variable=self.startup_var, command=self.toggle_startup)
        menu.add_command(label="Create desktop shortcut", command=self.desktop_shortcut)
        menu.add_command(label="Connect phone (Telegram)…", command=self.connect_phone)
        menu.add_separator()
        menu.add_command(label="Open documents folder", command=lambda: open_path(self.vega.doc_folder()))
        menu.add_command(label="Open diary folder", command=lambda: open_path(here("diary")))
        menu.add_command(label="Edit settings (config.json)", command=self.edit_settings)
        menu.add_command(label="Clear chat", command=self.clear_chat)
        menu.add_separator()
        menu.add_command(label="Quit Vega", command=self.quit)
        menu_btn.configure(menu=menu)
        menu_btn.pack(side="right")

        toggles = tk.Frame(r, bg=C["bg"])
        toggles.pack(fill="x", padx=16)
        self.voice_var = tk.IntVar(value=1 if self.vega.cfg.get("voice_on", True) else 0)
        self.wake_var = tk.IntVar(value=1 if self.vega.cfg.get("wake_word_on", False) else 0)
        for text, var, cmd in (("Speak answers", self.voice_var, self.toggle_voice),
                               ("Listen for “Hey Vega”", self.wake_var, self.toggle_wake)):
            tk.Checkbutton(toggles, text=text, variable=var, command=cmd, font=F_SMALL, bg=C["bg"], fg=C["muted"],
                           selectcolor=C["panel"], activebackground=C["bg"], activeforeground=C["ink"],
                           highlightthickness=0, bd=0, cursor="hand2").pack(side="left", padx=(0, 14))

        quick = tk.Frame(r, bg=C["bg"])
        quick.pack(fill="x", padx=16, pady=(10, 8))
        items = [("Today's task", "what's my task today"), ("Weather", "weather"), ("News", "news"),
                 ("To-do", "my to-do list"), ("Reminders", "my reminders"), ("Laptop", "laptop status"),
                 ("Diary", "open my diary"), ("Help", "help")]
        for i, (label, cmd) in enumerate(items):
            b = self.button(quick, label, lambda c=cmd: self.send(c), font=F_SMALL, padx=8, pady=5)
            b.grid(row=i // 4, column=i % 4, sticky="ew", padx=3, pady=3)
        for col in range(4):
            quick.columnconfigure(col, weight=1)

        box = tk.Frame(r, bg=C["panel"], highlightbackground=C["line"], highlightthickness=1)
        box.pack(fill="both", expand=True, padx=16)
        self.text = tk.Text(box, wrap="word", font=F, height=10, bg=C["panel"], fg=C["ink"], relief="flat", bd=0, padx=14, pady=12,
                            insertbackground=C["ink"], selectbackground=C["btn_hi"], spacing1=2, spacing3=2, cursor="arrow")
        sb = tk.Scrollbar(box, command=self.text.yview, bg=C["panel"], troughcolor=C["panel"], relief="flat", bd=0)
        self.text.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("who_you", foreground=C["you"], font=F_BOLD, spacing1=12)
        self.text.tag_configure("who_vega", foreground=C["accent"], font=F_BOLD, spacing1=12)
        self.text.tag_configure("body", foreground=C["ink"], lmargin1=2, lmargin2=2)
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

        bottom = tk.Frame(r, bg=C["bg"])
        bottom.pack(fill="x", padx=16, pady=(10, 4))
        self.entry = tk.Entry(bottom, font=F, bg=C["entry"], fg=C["ink"], insertbackground=C["ink"], relief="flat",
                              highlightthickness=1, highlightbackground=C["line"], highlightcolor=C["accent"])
        self.entry.pack(side="left", fill="x", expand=True, ipady=8)
        self.entry.bind("<Return>", lambda e: self.on_send())
        self.mic_btn = self.button(bottom, "Mic", self.on_mic, bg=C["btn"])
        self.mic_btn.pack(side="left", padx=(8, 0))
        self.button(bottom, "Attach", self.on_attach).pack(side="left", padx=(6, 0))
        self.send_btn = self.button(bottom, "Send", self.on_send, bg=C["accent"], fg=C["bg"], font=F_BOLD)
        self.send_btn.pack(side="left", padx=(6, 0))
        tk.Label(r, text="Enter to send  ·  Mic to talk  ·  Say “help” to see everything I can do",
                 font=F_SMALL, bg=C["bg"], fg=C["muted"]).pack(pady=(0, 10))
        self.entry.focus_set()

    # ---------------- chat display
    def _insert(self, text, tags):
        self.text.insert("end", text, tags)

    def _insert_body(self, text):
        text = re.sub(r"```[a-zA-Z]*\n?", "`", text)
        for n, chunk in enumerate(text.split("`")):
            if n % 2:  # inside `backticks`: show as a command
                self._insert(chunk, ("body", "code"))
                continue
            pos = 0
            for m in URL_RE.finditer(chunk):
                self._insert(chunk[pos:m.start()], ("body",))
                self._insert(m.group(0), ("body", "link"))
                pos = m.end()
            self._insert(chunk[pos:], ("body",))

    def _name(self, who, when=None):
        tag = "who_you" if who in ("you", "phone") else "who_vega"
        label = {"you": "You", "phone": "You (phone)"}.get(who, "Vega")
        self.text.insert("end", label, (tag,))
        stamp = nice_time(when) if when is not None else nice_time(datetime.now().isoformat())
        if stamp:
            self.text.insert("end", "   " + stamp, (tag, "stamp"))
        self.text.insert("end", "\n", (tag,))

    def add_message(self, who, text, when=None, save=True):
        self._name(who, when)
        self._insert_body(text.strip() + "\n")
        self.text.see("end")
        if save:
            log_message(who, text)

    def show_history(self):
        old = read_log()
        if not old:
            return
        self._insert("Earlier\n", ("divider",))
        for m in old:
            self.add_message(m.get("who", "vega"), m.get("text", ""), when=m.get("t", ""), save=False)
        self._insert("Now\n", ("divider",))

    def system(self, text, level="muted"):
        self._insert(text.strip() + "\n", (level,))
        self.text.see("end")

    def stream_update(self, text):
        if not self.streaming:
            self.streaming = True
            self._name("vega")
            self.text.mark_set("stream", "end-1c")
            self.text.mark_gravity("stream", "left")
        self.text.delete("stream", "end-1c")
        self._insert(text, ("body",))
        self.text.see("end")

    def click_link(self, event):
        rng = self.text.tag_prevrange("link", "current+1c")
        if rng:
            webbrowser.open(self.text.get(*rng))

    def set_status(self, text, color="ok"):
        self.status_lbl.configure(text=text)
        self.dot.itemconfigure(self.dot_id, fill=C[color])

    # ---------------- sending
    def on_send(self):
        if self.busy:
            self.vega.stop()
            self.mouth.stop()
            return
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self.send(text)

    def send(self, text, spoken=False):
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
        self.run_job(lambda on_token: self.vega.handle(text, on_token=on_token))

    def run_job(self, fn):
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
            self.ui.put(lambda: self.finish(result))

        threading.Thread(target=work, daemon=True).start()

    def finish(self, result):
        text = result.get("text", "")
        if self.streaming:
            self.text.delete("stream", "end-1c")
            self._insert_body(text.strip() + "\n")
            self.text.see("end")
            log_message("vega", text)
        else:
            self.add_message("vega", text)
        self.streaming = False
        self.busy = False
        self.send_btn.configure(text="Send")
        self.set_status("Diary open" if self.vega.diary_mode else "Ready", "you" if self.vega.diary_mode else "ok")
        if self.voice_var.get():
            self.mouth.say(shorten_for_speech(result.get("say") or text))

    # ---------------- microphone
    def on_mic(self):
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
        first_time = self.ears.model_name not in self.ears._models
        if first_time:
            self.system("Loading speech recognition. The first time, it downloads about 150 MB, so wait a moment.")

        def work():
            text, err = "", None
            try:
                self.ears.model(self.ears.model_name)
                audio = self.ears.listen_once(self.listen_stop)
                if audio is not None:
                    self.ui.put(lambda: self.set_status("Understanding…", "accent"))
                    text = self.ears.transcribe(audio)
            except Exception as e:
                err = str(e)
            self.ui.put(lambda: self.mic_done(text, err))

        threading.Thread(target=work, daemon=True).start()

    def mic_done(self, text, err):
        self.listening = False
        self.wake.paused.clear()
        self.mic_btn.configure(text="Mic", bg=C["btn"], fg=C["ink"])
        self.set_status("Ready")
        if err:
            self.system(f"Microphone problem: {err}", "warn")
        elif not text:
            self.system("I didn't hear anything. Press Mic and try again, a bit closer.")
        else:
            self.send(text, spoken=True)

    def on_wake(self):
        self.mouth.stop()
        self.set_status("Yes? I'm listening…", "you")

    # ---------------- documents
    def on_attach(self):
        if self.busy:
            return
        path = filedialog.askopenfilename(title="Choose a document for Vega",
                                          filetypes=[("Documents", "*.pdf *.docx *.txt *.md *.csv *.py *.sh *.yaml *.yml *.json *.log"),
                                                     ("All files", "*.*")])
        if not path:
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
        self.mouth.say(f"Hemanth, reminder: {text}")
        if self.phone:
            self.phone.send(f"Reminder: {text}")

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
            self.system("Listening for “Hey Vega”. Say it, wait for the beep, then speak. "
                        "(The first time, it downloads a small speech model. It uses a little CPU while on.)")
        else:
            self.system("Stopped listening for “Hey Vega”.")

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
        self.text.delete("1.0", "end")
        try:
            open(here(LOG_FILE), "w", encoding="utf-8").close()
        except OSError:
            pass
        self.vega.history = []
        self.vega._save_history()
        self.system("Chat cleared. (Your notebook, reminders, to-dos and diary are kept.)")

    # ---------------- start, close
    def greet(self):
        h = datetime.now().hour
        part = "morning" if h < 12 else ("afternoon" if h < 17 else "evening")
        msg = f"Good {part}, Hemanth. Vega here."
        plan = self.vega.load_plan()
        if plan:
            d = self.vega.plan_day(plan, self.vega.today_index(plan))
            if d:
                msg += f" Today's study task is {d['title']}."
        self.add_message("vega", msg + " What do you need?", save=False)
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
            ollama.generate(model=model, prompt="", keep_alive="15m")  # load me into the GPU now, so the first answer is fast
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
        self.root.iconify()
        if not self.told_about_close:
            self.told_about_close = True
            self.system("I'm still running in the taskbar so your reminders work. To close me fully: Menu → Quit Vega.")

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
