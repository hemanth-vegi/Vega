"""The Lab Log window: today's task, step ticks, your notes, Vega's review and a 24-week progress map."""

import threading
import tkinter as tk
from datetime import date

from vega_lablog import clean_step


class LabLogWindow:
    def __init__(self, app, container=None):
        self.app, self.vega = app, app.vega
        self.C, self.F = app.C, app.F
        C = self.C
        self.plan = self.vega.load_plan()
        self.today = self.vega.today_index(self.plan) if self.plan else 0
        self.total = len(self.plan["weeks"]) * 7 if self.plan else 0
        self.idx = min(max(self.today, 0), max(self.total - 1, 0))
        self.loaded = None  # the day whose notes are in the notes box
        self.embedded = container is not None
        if self.embedded:  # shown inside Vega's main window (from the sidebar)
            from vega_pages import ScrollFrame
            self.frame = ScrollFrame(container, C["bg"])
            w = self.win = self.frame.inner
        else:
            w = self.win = tk.Toplevel(app.root)
            w.title("Lab Log")
            w.configure(bg=C["bg"])
            w.geometry("760x820")
            w.minsize(560, 600)
            w.protocol("WM_DELETE_WINDOW", self.close)
        if not self.plan:
            tk.Label(w, text="I can't find plan.json in your mybot folder.", bg=C["bg"], fg=C["ink"], font=self.F["bold"]).pack(pady=40)
            return
        self.build()
        self.show(self.idx)

    # ---------------- layout
    def label(self, parent, text="", font=None, fg=None, **kw):
        return tk.Label(parent, text=text, bg=kw.pop("bg", self.C["bg"]), fg=fg or self.C["ink"], font=font or self.F["normal"],
                        justify="left", anchor="w", **kw)

    def build(self):
        C, F, w = self.C, self.F, self.win
        outer = tk.Frame(w, bg=C["bg"])
        outer.pack(fill="both", expand=True, padx=18, pady=14)

        nav = tk.Frame(outer, bg=C["bg"])
        nav.pack(fill="x")
        self.app.button(nav, "◀", lambda: self.show(self.idx - 1), font=F["small"], padx=10).pack(side="left")
        self.app.button(nav, "Today", lambda: self.show(self.today), font=F["small"], padx=10).pack(side="left", padx=6)
        self.app.button(nav, "▶", lambda: self.show(self.idx + 1), font=F["small"], padx=10).pack(side="left")
        self.status = self.label(nav, "", F["small"], C["muted"])
        self.status.pack(side="right")

        self.head = self.label(outer, "", F["small"], C["muted"])
        self.head.pack(fill="x", pady=(12, 0))
        self.title = self.label(outer, "", F["title_small"], wraplength=700)
        self.title.pack(fill="x")
        self.res = self.label(outer, "", F["small"], C["muted"], wraplength=700)
        self.res.pack(fill="x", pady=(2, 8))

        self.steps_box = tk.Frame(outer, bg=C["panel"], highlightbackground=C["line"], highlightthickness=1)
        self.steps_box.pack(fill="x")

        # "How to learn this": sources, how AI is used in the job, prompts and interview questions for each topic
        self.learn_head = self.label(outer, "How to learn this", F["bold"])
        self.learn_head.pack(fill="x", pady=(14, 2))
        self.learn_box = tk.Frame(outer, bg=C["bg"])
        self.learn_box.pack(fill="x")

        row = tk.Frame(outer, bg=C["bg"])
        row.pack(fill="x", pady=(12, 4))
        self.label(row, "My lab notes (what I did, what I learned, errors I hit)", F["bold"]).pack(side="left")
        self.notes = tk.Text(outer, height=7, wrap="word", font=F["normal"], bg=C["entry"], fg=C["ink"], relief="flat",
                             insertbackground=C["ink"], padx=10, pady=8, highlightthickness=1, highlightbackground=C["line"])
        self.notes.pack(fill="x")

        btns = tk.Frame(outer, bg=C["bg"])
        btns.pack(fill="x", pady=8)
        self.app.button(btns, "Save notes", self.save_notes, font=F["small"]).pack(side="left")
        self.done_btn = self.app.button(btns, "Mark day done", self.toggle_done, font=F["small"])
        self.done_btn.pack(side="left", padx=6)
        self.assess_btn = self.app.button(btns, "Assess my log", self.assess, font=F["small"], bg=C["accent"], fg=C["bg"])
        self.assess_btn.pack(side="right")

        self.label(outer, "Vega's review", F["bold"]).pack(fill="x", pady=(6, 2))
        self.review = tk.Text(outer, height=7, wrap="word", font=F["small"], bg=C["panel"], fg=C["ink"], relief="flat",
                              padx=10, pady=8, highlightthickness=1, highlightbackground=C["line"], state="disabled")
        self.review.pack(fill="x")

        self.stats = self.label(outer, "", F["small"], C["muted"])
        self.stats.pack(fill="x", pady=(12, 4))
        self.cell, self.gap = 14, 3
        self.canvas = tk.Canvas(outer, bg=C["bg"], highlightthickness=0,
                                height=7 * (self.cell + self.gap), width=24 * (self.cell + self.gap))
        self.canvas.pack(anchor="w")
        self.canvas.bind("<Button-1>", self.click_map)
        self.label(outer, "Green = done · red = missed · blue = the day you're viewing · click a square to open that day",
                   F["small"], C["muted"]).pack(fill="x", pady=(4, 0))

    # ---------------- show a day
    def show(self, i, save=True):
        if not self.plan:
            return
        if save:
            self.save_notes(quiet=True)
        self.idx = min(max(i, 0), self.total - 1)
        d = self.vega.plan_day(self.plan, self.idx)
        e = self.vega.lab_entry(self.idx)
        C = self.C
        when = "Today" if self.idx == self.today else ("Upcoming" if self.idx > self.today else "Past day")
        self.head.configure(text=f"{d['label']}  ·  {d['date'].strftime('%A %d %B')}  ·  {when}  ·  {d['topic']}")
        self.title.configure(text=d["title"])
        wk = d["week"]
        self.res.configure(text=f"This week — Coursera: {wk.get('coursera', '')}   |   YouTube: {wk.get('youtube', '')}")
        for child in self.steps_box.winfo_children():
            child.destroy()
        self.step_vars = []
        for n, s in enumerate(d["steps"], 1):
            v = tk.BooleanVar(value=n in e["ticked"])
            self.step_vars.append(v)
            tk.Checkbutton(self.steps_box, text=f"{n}. {clean_step(s)}", variable=v, anchor="w", justify="left",
                           wraplength=660, bg=C["panel"], fg=C["ink"], selectcolor=C["entry"], activebackground=C["panel"],
                           activeforeground=C["ink"], font=self.F["normal"], command=lambda n=n, v=v: self.tick(n, v.get())
                           ).pack(fill="x", padx=10, pady=3)
        self.show_learn(self.idx)
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", e["notes"])
        self.loaded = self.idx
        self.set_review(e.get("review") or "No review yet. Write your notes or tick steps, then press “Assess my log”.\n"
                                          "I also review today's log by myself every evening.")
        self.refresh_status()

    # ---------------- learning sources
    def link(self, parent, text, url):
        C = self.C
        if not url:
            return self.label(parent, "•  " + text, self.F["small"], C["ink"], bg=C["panel"], wraplength=640)
        lbl = tk.Label(parent, text="•  " + text, font=self.F["small"], bg=C["panel"], fg=C["accent"], anchor="w",
                       justify="left", wraplength=640, cursor="hand2")
        lbl.bind("<Button-1>", lambda e: self.open_url(url))
        return lbl

    def open_url(self, url):
        try:
            self.vega.open_in_browser(self.vega.browser_key(""), url)
        except Exception:
            import webbrowser
            webbrowser.open(url)

    def ask_ai(self, prompt):
        self.app.use_prompt(prompt)  # puts it in the message box; fill in [paste] parts, then press Enter

    def copy(self, prompt):
        self.app.copy_text(prompt)

    def show_learn(self, i):
        C, F = self.C, self.F
        for w in self.learn_box.winfo_children():
            w.destroy()
        day = self.vega.learn_for(i)
        if not day:
            self.label(self.learn_box, "Sources for this day are coming soon. Ask Vega: “how do I learn today's topic?”",
                       F["small"], C["muted"]).pack(fill="x")
            return
        if day.get("course"):
            row = tk.Frame(self.learn_box, bg=C["panel"])
            row.pack(fill="x", pady=(0, 6))
            self.link(row, "Course: " + day["course"][0], day["course"][1]).pack(fill="x", padx=10, pady=6)
        for n, tp in enumerate(day.get("topics", []), 1):
            card = tk.Frame(self.learn_box, bg=C["panel"], highlightbackground=C["line"], highlightthickness=1)
            card.pack(fill="x", pady=4)
            tk.Label(card, text=f"Topic {n}: {tp['name']}", font=F["bold"], bg=C["panel"], fg=C["ink"], anchor="w",
                     justify="left", wraplength=660).pack(fill="x", padx=10, pady=(8, 2))
            for title, key in (("Watch", "watch"), ("Read", "read"), ("Practice (free, hands-on)", "practice")):
                items = tp.get(key) or []
                if not items:
                    continue
                self.label(card, title, F["small"], C["muted"], bg=C["panel"]).pack(fill="x", padx=10, pady=(4, 0))
                for label, url in items:
                    self.link(card, label, url).pack(fill="x", padx=18)
            self.label(card, "How AI is used for this in the job", F["small"], C["muted"], bg=C["panel"]).pack(fill="x", padx=10, pady=(6, 0))
            self.label(card, tp.get("ai_job", ""), F["small"], C["ink"], bg=C["panel"], wraplength=640).pack(fill="x", padx=18)
            self.label(card, "Ask AI (fill in any [paste] part first)", F["small"], C["muted"], bg=C["panel"]).pack(fill="x", padx=10, pady=(6, 0))
            for pr in tp.get("prompts", []):
                prow = tk.Frame(card, bg=C["panel"])
                prow.pack(fill="x", padx=18, pady=1)
                tk.Label(prow, text="“" + pr + "”", font=F["small"], bg=C["panel"], fg=C["ink"], anchor="w", justify="left",
                         wraplength=520).pack(side="left", fill="x", expand=True)
                tk.Button(prow, text="Copy", command=lambda p=pr: self.copy(p), font=F["small"], bg=C["btn"], fg=C["ink"],
                          relief="flat", bd=0, padx=8, highlightthickness=0, cursor="hand2").pack(side="right", padx=(4, 0))
                tk.Button(prow, text="Ask Vega", command=lambda p=pr: self.ask_ai(p), font=F["small"], bg=C["btn"], fg=C["ink"],
                          relief="flat", bd=0, padx=8, highlightthickness=0, cursor="hand2").pack(side="right")
            if tp.get("interview"):
                self.label(card, "Interview questions (practise answering out loud)", F["small"], C["muted"], bg=C["panel"]).pack(
                    fill="x", padx=10, pady=(6, 0))
                for q in tp["interview"]:
                    self.label(card, "•  " + q, F["small"], C["ink"], bg=C["panel"], wraplength=640).pack(fill="x", padx=18)
            tk.Frame(card, bg=C["panel"], height=6).pack(fill="x")

    def set_review(self, text):
        self.review.configure(state="normal")
        self.review.delete("1.0", "end")
        self.review.insert("1.0", text)
        self.review.configure(state="disabled")

    def refresh_status(self):
        done = self.vega.is_done(self.idx)
        self.done_btn.configure(text="Mark not done" if done else "Mark day done")
        e = self.vega.lab_entry(self.idx)
        score = f"  ·  Score {e['score']}/10" if e.get("score") is not None else ""
        self.status.configure(text=("✓ Done" if done else "Not done yet") + score,
                              fg=self.C["ok"] if done else self.C["muted"])
        cur, best = self.vega.streaks(self.today)
        total_done = sum(1 for k in self.vega.progress_data()["done"] if str(k).isdigit() and 0 <= int(k) < self.total)
        missed = len(self.vega.missed(self.plan, self.today))
        self.stats.configure(text=f"Streak {cur} day{'s' if cur != 1 else ''}  ·  best {best}  ·  {total_done} of {self.total} days done"
                                  f"  ·  {missed} carried over")
        self.draw_map()

    def draw_map(self):
        c, C = self.canvas, self.C
        c.delete("all")
        done = {int(k) for k in self.vega.progress_data()["done"] if str(k).isdigit()}
        step = self.cell + self.gap
        for i in range(self.total):
            x, y = (i // 7) * step, (i % 7) * step
            if i == self.idx:
                fill, outline = C["accent"], C["accent"]
            elif i in done:
                fill, outline = C["ok"], C["ok"]
            elif i < self.today:
                fill, outline = C["panel"], C["bad"]
            else:
                fill, outline = C["panel"], C["line"]
            c.create_rectangle(x, y, x + self.cell, y + self.cell, fill=fill, outline=outline)

    def click_map(self, ev):
        step = self.cell + self.gap
        i = (ev.x // step) * 7 + (ev.y // step)
        if 0 <= i < self.total:
            self.show(i)

    # ---------------- actions
    def tick(self, n, on):
        all_done = self.vega.tick_step(self.idx, n, on)
        if all_done and not self.vega.is_done(self.idx):
            self.vega.set_done(self.idx, True)
            self.app.system(f"All steps ticked, so {self.vega.plan_day(self.plan, self.idx)['label']} is marked done. Nice work!")
        self.refresh_status()

    def save_notes(self, quiet=False):
        if not hasattr(self, "notes") or self.loaded is None:
            return
        text = self.notes.get("1.0", "end").strip()
        e = self.vega.lab_entry(self.loaded)
        if text != e["notes"].strip():
            e["notes"] = text
            e.pop("review", None)
            e.pop("score", None)
            self.vega.save_lab_entry(self.loaded, e)
            if not quiet:
                self.set_review("Notes saved. Press “Assess my log” for a new review.")
                self.refresh_status()

    def toggle_done(self):
        self.vega.set_done(self.idx, not self.vega.is_done(self.idx))
        self.refresh_status()

    def assess(self):
        self.save_notes(quiet=True)
        i = self.idx
        self.assess_btn.configure(state="disabled", text="Checking…")
        self.set_review("Reading your lab log… (the first time can take about 10 seconds)")

        def work():
            try:
                text, score = self.vega.assess_day(i)
            except Exception as ex:
                text, score = f"I couldn't review it: {ex}\nIs the Ollama app running?", None

            def done():
                if not self.win.winfo_exists():
                    return
                self.assess_btn.configure(state="normal", text="Assess my log")
                if self.idx == i:
                    self.set_review(text)
                    self.refresh_status()
                if score is not None and self.app.voice_var.get():
                    self.app.mouth.say(f"Your lab log score is {score} out of 10.")
            self.app.ui.put(done)

        threading.Thread(target=work, daemon=True).start()

    def close(self):
        self.save_notes(quiet=True)
        (self.frame if self.embedded else self.win).destroy()
        self.app.lablog_win = None
