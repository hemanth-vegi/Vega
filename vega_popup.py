"""
Vega 2.0's small pop-ups, like the old window had:

  - the bubble: a little box at the bottom-right, on top of other apps ("Yes? I'm listening…", what you said,
    Vega's answer, reminders). Click it to open Vega, right-click to hide it.
  - the Quick Bar (Ctrl + Alt + Space): a small floating box to type to Vega without opening her window.

They run in their own thread with their own tkinter loop. Other threads only call bubble(), toggle_quickbar()
and hide(): those just put a job in a queue, so they are safe to call from anywhere.
"""

import queue
import re
import threading

C = dict(bg="#131419", panel="#1A1C23", line="#262832", ink="#F0F3F9", muted="#949AA6", accent="#7BA4F8",
         ok="#4ADE80", warn="#FBBF24", entry="#1A1C23", btn_hi="#2C2F3A")


class Popups(threading.Thread):
    def __init__(self, on_open, on_submit, on_mic):
        super().__init__(daemon=True)
        self.on_open, self.on_submit, self.on_mic = on_open, on_submit, on_mic
        self.jobs = queue.Queue()
        self.ok = False
        self.root = None
        self._bubble = None
        self._bubble_job = None
        self.bar = None
        self.bar_visible = False

    # ---------------- called from any thread
    def bubble(self, title, text, seconds=10):
        self.jobs.put(lambda: self._show_bubble(title, text, seconds))

    def hide(self):
        self.jobs.put(self._hide_bubble)

    def toggle_quickbar(self):
        self.jobs.put(self._toggle_bar)

    def bar_result(self, text, color="ok"):
        self.jobs.put(lambda: self._bar_result(text, color))

    # ---------------- the tkinter thread
    def run(self):
        try:
            import tkinter as tk
            self.tk = tk
            try:
                import ctypes
                ctypes.windll.shcore.SetProcessDpiAwareness(1)  # sharp text on laptop screens
            except Exception:
                pass
            self.root = tk.Tk()
            self.root.withdraw()
            self.font = ("Segoe UI", 11)
            self.small = ("Segoe UI", 9)
            self.bold = ("Segoe UI", 11, "bold")
            self.ok = True
            self.root.after(50, self._poll)
            self.root.mainloop()
        except Exception as e:
            self.ok = False
            print("pop-ups not available:", e)

    def _poll(self):
        try:
            while True:
                job = self.jobs.get_nowait()
                try:
                    job()
                except Exception as e:
                    print("pop-up error:", e)
        except queue.Empty:
            pass
        self.root.after(80, self._poll)

    # ---------------- bubble
    def _show_bubble(self, title, text, seconds):
        tk = self.tk
        b = self._bubble
        if b is None or not b.winfo_exists():
            b = self._bubble = tk.Toplevel(self.root)
            b.overrideredirect(True)
            b.attributes("-topmost", True)
            b.configure(bg=C["line"])
            inner = tk.Frame(b, bg=C["panel"], padx=16, pady=12)
            inner.pack(fill="both", expand=True, padx=1, pady=1)
            head = tk.Frame(inner, bg=C["panel"])
            head.pack(fill="x")
            self._b_title = tk.Label(head, font=self.bold, bg=C["panel"], fg=C["accent"], anchor="w")
            self._b_title.pack(side="left", fill="x", expand=True)
            close = tk.Label(head, text="✕", font=self.bold, bg=C["panel"], fg=C["muted"], cursor="hand2", padx=4)
            close.pack(side="right")
            close.bind("<Button-1>", lambda e: (self._hide_bubble(), "break")[1])
            self._b_text = tk.Label(inner, font=self.font, bg=C["panel"], fg=C["ink"], anchor="w", justify="left",
                                    wraplength=360)
            self._b_text.pack(fill="x", pady=(4, 0))
            self._b_hint = tk.Label(inner, text="Click to open Vega", font=self.small, bg=C["panel"], fg=C["muted"],
                                    anchor="w")
            self._b_hint.pack(fill="x", pady=(6, 0))
            for w in (b, inner, self._b_title, self._b_text, self._b_hint):
                w.bind("<Button-1>", lambda e: (self._hide_bubble(), self.on_open()))   # click: open Vega
                w.bind("<Button-3>", lambda e: self._hide_bubble())                     # right-click: just hide
        self._b_title.configure(text=title)
        short = re.sub(r"[*_`#]+", "", re.sub(r"\s+", " ", text or "")).strip()
        self._b_text.configure(text=short if len(short) <= 260 else short[:257].rsplit(" ", 1)[0] + "…")
        b.update_idletasks()
        w, h = 400, b.winfo_reqheight()
        x = b.winfo_screenwidth() - w - 24
        y = b.winfo_screenheight() - h - 72
        b.geometry(f"{w}x{h}+{x}+{y}")
        b.deiconify()
        b.lift()
        if self._bubble_job:
            self.root.after_cancel(self._bubble_job)
        self._bubble_job = self.root.after(int(seconds * 1000), self._hide_bubble)

    def _hide_bubble(self):
        if self._bubble is not None and self._bubble.winfo_exists():
            self._bubble.withdraw()

    # ---------------- Quick Bar
    def _toggle_bar(self):
        if self.bar is not None and self.bar.winfo_exists() and self.bar_visible:
            self._hide_bar()
        else:
            self._show_bar()

    def _hide_bar(self):
        if self.bar is not None and self.bar.winfo_exists():
            self.bar.withdraw()
        self.bar_visible = False

    def _show_bar(self):
        if self.bar is None or not self.bar.winfo_exists():
            self._build_bar()
        self.bar.deiconify()
        self.bar.lift()
        self.bar.attributes("-topmost", True)
        self.bar.focus_force()
        self.entry.focus_set()
        self.entry.selection_range(0, "end")
        self.bar_visible = True

    def _build_bar(self):
        tk = self.tk
        w = self.bar = tk.Toplevel(self.root)
        w.overrideredirect(True)
        w.attributes("-topmost", True)
        w.configure(bg=C["line"])
        sw = w.winfo_screenwidth()
        w.geometry(f"580x105+{(sw - 580) // 2}+120")
        drag = {"x": 0, "y": 0}

        def start(e):
            drag["x"], drag["y"] = e.x, e.y

        def move(e):
            w.geometry(f"+{w.winfo_x() + e.x - drag['x']}+{w.winfo_y() + e.y - drag['y']}")

        card = tk.Frame(w, bg=C["bg"], padx=14, pady=10)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        head = tk.Frame(card, bg=C["bg"])
        head.pack(fill="x", pady=(0, 6))
        for x in (card, head):
            x.bind("<Button-1>", start)
            x.bind("<B1-Motion>", move)
        tk.Label(head, text="⚡ VEGA QUICK BAR", font=self.bold, bg=C["bg"], fg=C["accent"]).pack(side="left")
        self.bar_status = tk.Label(head, text="Ready", font=self.small, bg=C["bg"], fg=C["muted"])
        self.bar_status.pack(side="left", padx=10)
        x_btn = tk.Label(head, text="✕", font=self.bold, bg=C["bg"], fg=C["muted"], cursor="hand2")
        x_btn.pack(side="right")
        x_btn.bind("<Button-1>", lambda e: self._hide_bar())
        open_btn = tk.Label(head, text="Open Vega", font=self.small, bg=C["bg"], fg=C["accent"], cursor="hand2")
        open_btn.pack(side="right", padx=10)
        open_btn.bind("<Button-1>", lambda e: (self._hide_bar(), self.on_open()))

        row = tk.Frame(card, bg=C["entry"], padx=8, pady=4, highlightthickness=1, highlightbackground=C["line"])
        row.pack(fill="x")
        self.entry = tk.Entry(row, font=self.font, bg=C["entry"], fg=C["ink"], relief="flat", bd=0,
                              insertbackground=C["ink"], highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True, ipady=4, padx=4)
        self.entry.bind("<Return>", lambda e: self._submit())
        self.entry.bind("<Escape>", lambda e: self._hide_bar())
        for text, cmd in (("➤", self._submit), ("🎤", lambda: (self._hide_bar(), self.on_mic()))):
            tk.Button(row, text=text, command=cmd, font=self.small, bg=C["entry"], fg=C["accent"], relief="flat", bd=0,
                      padx=6, cursor="hand2", activebackground=C["btn_hi"]).pack(side="right")
        self.result = tk.Label(card, text="", font=self.font, bg=C["panel"], fg=C["ink"], justify="left", anchor="w",
                               wraplength=540, padx=10, pady=8)

    def _submit(self):
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, "end")
        self._bar_result("Thinking…", "warn")
        threading.Thread(target=self.on_submit, args=(text,), daemon=True).start()

    def _bar_result(self, text, color="ok"):
        if self.bar is None or not self.bar.winfo_exists():
            return
        self.bar_status.configure(text="Thinking…" if color == "warn" else "Ready", fg=C.get(color, C["muted"]))
        short = (text or "").strip()
        self.result.configure(text=short[:400] + ("…" if len(short) > 400 else ""))
        if not self.result.winfo_ismapped():
            self.result.pack(fill="x", pady=(8, 0))
        self.bar.update_idletasks()
        h = self.bar.winfo_reqheight()
        self.bar.geometry(f"580x{h}+{self.bar.winfo_x()}+{self.bar.winfo_y()}")
