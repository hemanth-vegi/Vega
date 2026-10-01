"""Pages shown in Vega's main area from the sidebar: News, Tech news, Internships (fresh every day)."""

import sys
if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys, "stderr") and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import threading
import tkinter as tk
from datetime import datetime

from vega_lablog import INTERN_QUERY


class ThinBar(tk.Canvas):
    """A slim dark scrollbar (like ChatGPT / Claude). It only shows when there is something to scroll.
    Works like tk.Scrollbar: ThinBar(parent, widget.yview) and widget.configure(yscrollcommand=bar.set)."""

    THUMB, THUMB_HI = "#323B47", "#4B5767"

    def __init__(self, parent, command, bg, width=10):
        super().__init__(parent, width=width, bg=bg, highlightthickness=0, bd=0)
        self.command = command
        self.lo, self.hi = 0.0, 1.0
        self.hover = False
        self.drag = None
        self.bind("<Configure>", lambda e: self.redraw())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<B1-Motion>", self._motion)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "drag", None))
        self.bind("<MouseWheel>", lambda e: self.command("scroll", int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units"))

    def _hover(self, on):
        self.hover = on
        self.redraw()

    def set(self, lo, hi):
        self.lo, self.hi = float(lo), float(hi)
        self.redraw()

    def _thumb(self):
        h = max(self.winfo_height(), 1)
        y0 = self.lo * h
        y1 = max(self.hi * h, y0 + 28)
        if y1 > h:
            y0, y1 = max(0, h - (y1 - y0)), h
        return y0, y1, h

    def redraw(self):
        self.delete("all")
        if self.hi - self.lo >= 0.999:
            return  # everything fits: no scrollbar, like a modern app
        y0, y1, h = self._thumb()
        w = self.winfo_width()
        pad = 2
        r = (w - 2 * pad) / 2
        color = self.THUMB_HI if (self.hover or self.drag) else self.THUMB
        x0, x1 = pad, w - pad
        self.create_oval(x0, y0 + 2, x1, y0 + 2 + 2 * r, fill=color, outline=color)
        self.create_oval(x0, y1 - 2 - 2 * r, x1, y1 - 2, fill=color, outline=color)
        self.create_rectangle(x0, y0 + 2 + r, x1, y1 - 2 - r, fill=color, outline=color)

    def _press(self, e):
        y0, y1, h = self._thumb()
        if y0 <= e.y <= y1:
            self.drag = (e.y, self.lo)
        else:  # click on the empty track: jump there
            self.command("moveto", max(0.0, e.y / h - (self.hi - self.lo) / 2))
        self.redraw()

    def _motion(self, e):
        if self.drag:
            y, lo = self.drag
            self.command("moveto", lo + (e.y - y) / max(self.winfo_height(), 1))


def center_pad(width, column=880, least=18):
    """Side padding that keeps content in a comfortable column on wide windows."""
    return max(least, (width - column) // 2)


class ScrollFrame(tk.Frame):
    """A frame that scrolls with the mouse wheel."""

    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = ThinBar(self, self.canvas.yview, bg)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.win, width=e.width))
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        # scroll with the mouse wheel anywhere over the page, even over cards, text and buttons inside it
        self.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.bind("<Leave>", self._maybe_leave)

    def _inside(self, x, y):
        w = self.winfo_containing(x, y)
        while w is not None:
            if w is self:
                return True
            w = w.master
        return False

    def _maybe_leave(self, e):
        try:
            if not self._inside(e.x_root, e.y_root):
                self.canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass

    def _wheel(self, e):
        try:
            if not self.winfo_exists() or not self._inside(e.x_root, e.y_root):
                return
            if isinstance(e.widget, tk.Text) and e.widget.yview() != (0.0, 1.0):
                return  # a text box that scrolls by itself (your notes) keeps its own wheel
            self.canvas.yview_scroll(int(-e.delta / 120) or (-1 if e.delta > 0 else 1), "units")
        except Exception:
            pass


class FeedPage:
    """A list of headlines with Open buttons, loaded in the background, with a Refresh button."""

    def __init__(self, app, parent, kind):
        self.app, self.vega, self.kind = app, app.vega, kind
        C, F = app.C, app.F
        self.C, self.F = C, F
        self.frame = tk.Frame(parent, bg=C["bg"])
        head = tk.Frame(self.frame, bg=C["bg"])
        head.pack(fill="x", padx=28, pady=(22, 4))
        titles = {"news": ("Top news", "India and the world, fresh from Google News"),
                  "tech": ("Tech news", "Technology, AI, cloud and gadgets from the last day"),
                  "internships": ("Internships", "New internship news from the last day, and live job searches")}
        t, sub = titles[kind]
        tk.Label(head, text=t, font=F["title"], bg=C["bg"], fg=C["ink"]).pack(side="left")
        app.button(head, "Refresh", self.load, font=F["small"]).pack(side="right")
        self.sum_btn = app.button(head, "Summarize", self.summarize, font=F["small"], bg=C["accent"], fg=C["bg"])
        self.sum_btn.pack(side="right", padx=(0, 6))
        self.items = []
        self.summary_busy = False
        self.sub = tk.Label(self.frame, text=sub, font=F["small"], bg=C["bg"], fg=C["muted"], anchor="w")
        self.sub.pack(fill="x", padx=28, pady=(0, 10))
        # the summary card (shows above the list when he presses Summarize)
        self.sum_card = tk.Frame(self.frame, bg=C["panel"], highlightbackground=C["line"], highlightthickness=1)
        top = tk.Frame(self.sum_card, bg=C["panel"])
        top.pack(fill="x", padx=12, pady=(10, 0))
        tk.Label(top, text="Summary", font=F["bold"], bg=C["panel"], fg=C["ink"]).pack(side="left")
        self.sum_note = tk.Label(top, text="", font=F["small"], bg=C["panel"], fg=C["muted"])
        self.sum_note.pack(side="left", padx=8)
        close = tk.Label(top, text="✕", font=F["small"], bg=C["panel"], fg=C["muted"], cursor="hand2")
        close.pack(side="right")
        close.bind("<Button-1>", lambda e: self.sum_card.pack_forget())
        self.sum_body = tk.Label(self.sum_card, text="", font=F["normal"], bg=C["panel"], fg=C["ink"], anchor="w", justify="left",
                                 wraplength=640)
        self.sum_body.pack(fill="x", padx=12, pady=(6, 12))
        self.sum_card.bind("<Configure>", lambda e: self.sum_body.configure(wraplength=max(300, e.width - 40)))
        self.scroll = ScrollFrame(self.frame, C["bg"])
        self.scroll.pack(fill="both", expand=True, padx=(20, 8), pady=(0, 12))
        self._head = head

        def centre(e):
            side = center_pad(e.width, 900, 20)
            head.pack_configure(padx=side + 8)
            self.sub.pack_configure(padx=side + 8)
            self.scroll.pack_configure(padx=(side, max(8, side - 12)))
            self.sum_card.pack_configure(padx=(side + 8, max(8, side - 4))) if self.sum_card.winfo_manager() else None
        self.frame.bind("<Configure>", centre)
        self.load()

    def clear(self):
        for w in self.scroll.inner.winfo_children():
            w.destroy()

    def note(self, text):
        tk.Label(self.scroll.inner, text=text, font=self.F["normal"], bg=self.C["bg"], fg=self.C["muted"],
                 anchor="w", justify="left").pack(fill="x", padx=8, pady=8)

    def load(self):
        self.clear()
        self.note("Loading…")

        def work():
            items, links, err = [], [], None
            try:
                if self.kind == "news":
                    items = self.vega.fetch_news(None, 15)
                elif self.kind == "tech":
                    items = self.vega.fetch_news("technology OR AI OR cloud computing OR smartphones", 15)
                else:
                    seen = set((self.vega_state().get("seen_internships") or []))
                    items = [(h, u, h not in seen) for h, u in self.vega.fetch_news(INTERN_QUERY, 15)]
                    links = self.vega.internship_links()
                    self.vega.fresh_internship_news(15)  # remember what he has now seen
            except Exception as e:
                err = str(e)
            self.app.ui.put(lambda: self.show(items, links, err))

        threading.Thread(target=work, daemon=True).start()

    def vega_state(self):
        from vega_core import read_json
        return read_json("briefing_state.json", {}) or {}

    def summarize(self):
        """Ask the AI (free, on this laptop) for a short summary of the headlines on this page."""
        if self.summary_busy:
            return
        if not self.items:
            self.show_summary("Nothing to summarize yet. Press Refresh first.", "")
            return
        from vega_summaries import SUMMARY_RULES, news_prompt
        label = {"news": "top news (India and the world)", "tech": "tech", "internships": "internship news"}[self.kind]
        heads = [it[0] for it in self.items[:15]]
        prompt = news_prompt(heads, label)
        self.summary_busy = True
        self.sum_btn.configure(state="disabled", text="Summarizing…")
        self.show_summary("Reading the headlines… (the first time can take about 10 seconds)", "")

        def work():
            text, err = "", None
            try:
                import ollama
                last = 0
                for part in ollama.chat(model=self.vega.smart_model(), stream=True, keep_alive="15m",
                                        options={"num_ctx": 4096, "temperature": 0.3},
                                        messages=[{"role": "system", "content": self.vega.personality() + "\n\n" + SUMMARY_RULES},
                                                  {"role": "user", "content": prompt}]):
                    msg = part["message"] if isinstance(part, dict) else part.message
                    text += (msg["content"] if isinstance(msg, dict) else msg.content) or ""
                    if len(text) - last > 20:
                        last = len(text)
                        self.app.ui.put(lambda t=text: self.show_summary(t, "writing…"))
            except Exception as ex:
                err = str(ex)

            def done():
                self.summary_busy = False
                if not self.frame.winfo_exists():
                    return
                self.sum_btn.configure(state="normal", text="Summarize")
                if err:
                    self.show_summary(f"I couldn't summarize right now ({err}). Is the Ollama app running?", "")
                else:
                    self.show_summary(text, f"from {len(heads)} headlines · {datetime.now().strftime('%I:%M %p').lstrip('0')}")
            self.app.ui.put(done)

        threading.Thread(target=work, daemon=True).start()

    def show_summary(self, text, note):
        import re
        if not self.frame.winfo_exists():
            return
        try:
            from vega_core import strip_thinking
            text = strip_thinking(text)
        except Exception:
            pass
        text = re.sub(r"\*\*(.+?)\*\*", r"\1", text).replace("**", "")
        text = re.sub(r"(?m)^\s*#{1,6}\s*", "", text)
        text = re.sub(r"(?m)^\s*[-*•]\s+", "•  ", text).strip()
        self.sum_body.configure(text=text or "…")
        self.sum_note.configure(text=note)
        if not self.sum_card.winfo_manager():
            side = center_pad(self.frame.winfo_width(), 900, 20)
            self.sum_card.pack(fill="x", padx=(side + 8, max(8, side - 4)), pady=(0, 10), before=self.scroll)

    def show(self, items, links, err):
        if not self.frame.winfo_exists():
            return
        self.items = list(items or [])
        self.clear()
        C, F = self.C, self.F
        if err:
            self.note(f"I couldn't load this ({err}).\nCheck your internet and press Refresh.")
            return
        if links:
            box = tk.Frame(self.scroll.inner, bg=C["bg"])
            box.pack(fill="x", padx=8, pady=(0, 10))
            tk.Label(box, text="Live searches (new posts every day):", font=F["bold"], bg=C["bg"], fg=C["ink"],
                     anchor="w").pack(fill="x", pady=(0, 6))
            row = tk.Frame(box, bg=C["bg"])
            row.pack(fill="x")
            for name, url in links:
                short = name.replace(" internships", "").split(" roles")[0].replace(": ", " · ")
                short = short if len(short) <= 30 else short[:28] + "…"
                self.app.button(row, short, lambda u=url: self.open(u), font=F["small"]).pack(side="left", padx=(0, 6), pady=2)
        if not items:
            self.note("Nothing new right now. Try Refresh later.")
            return
        for it in items:
            head, url = it[0], it[1]
            new = it[2] if len(it) > 2 else False
            source = ""
            if head.endswith(")") and " (" in head:
                head, source = head[:-1].rsplit(" (", 1)
            card = tk.Frame(self.scroll.inner, bg=C["panel"], highlightbackground=C["line"], highlightthickness=1, cursor="hand2")
            card.pack(fill="x", padx=8, pady=4)
            tk.Label(card, text=("NEW  ·  " if new else "") + (source or "News"), font=F["small"], bg=C["panel"],
                     fg=C["ok"] if new else C["muted"], anchor="w").pack(fill="x", padx=12, pady=(8, 0))
            lbl = tk.Label(card, text=head, font=F["normal"], bg=C["panel"], fg=C["ink"], anchor="w", justify="left",
                           wraplength=640, cursor="hand2")
            lbl.pack(fill="x", padx=12, pady=(2, 10))
            for w in (card, lbl):
                w.bind("<Button-1>", lambda e, u=url: self.open(u))
        tk.Label(self.scroll.inner, text=f"Updated {datetime.now().strftime('%I:%M %p').lstrip('0')}  ·  click a story to open it",
                 font=F["small"], bg=C["bg"], fg=C["muted"], anchor="w").pack(fill="x", padx=8, pady=10)

    def open(self, url):
        try:
            self.vega.open_in_browser(self.vega.browser_key(""), url)
        except Exception:
            import webbrowser
            webbrowser.open(url)
