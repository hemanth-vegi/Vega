"""The Lab Log window: one tab per skill (Linux, Python, C, your own), today's lesson, ticks, notes,
Vega's review, Ask Vega, sources and a progress map. Skills, dates, study days and hours are all editable.
The Projects tab keeps ideas for later; Start turns a project's plan into a skill with dates."""

import sys
if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys, "stderr") and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import os
import re
import subprocess
import threading
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import filedialog, messagebox

from vega_lablog import clean_step
from vega_tracks import WEEKDAYS, lessons_to_text, parse_lessons


class LabLogWindow:
    def __init__(self, app, container=None):
        self.app, self.vega = app, app.vega
        self.C, self.F = app.C, app.F
        C = self.C
        self.plan = self.vega.load_plan()
        self.tid = "linux"
        self.load_track("linux")
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
        self.draw_tracks()
        self.show(self.idx)

    # ---------------- layout
    def label(self, parent, text="", font=None, fg=None, **kw):
        return tk.Label(parent, text=text, bg=kw.pop("bg", self.C["bg"]), fg=fg or self.C["ink"], font=font or self.F["normal"],
                        justify="left", anchor="w", **kw)

    def build(self):
        C, F, w = self.C, self.F, self.win
        outer = tk.Frame(w, bg=C["bg"])
        outer.pack(fill="both", expand=True, padx=18, pady=14)
        if self.embedded:  # keep the page in a comfortable column on wide windows
            from vega_pages import center_pad
            self.frame.canvas.bind("<Configure>", lambda e: (self.frame.canvas.itemconfigure(self.frame.win, width=e.width),
                                                            outer.pack_configure(padx=center_pad(e.width, 940, 18))), add="+")

        # one tab per skill + add / edit buttons
        self.track_bar = tk.Frame(outer, bg=C["bg"])
        self.track_bar.pack(fill="x", pady=(0, 10))

        # the lesson page (everything below the tabs) and the Projects page take turns under the tabs
        page = self.lesson_page = tk.Frame(outer, bg=C["bg"])
        page.pack(fill="x")
        self.proj_page = tk.Frame(outer, bg=C["bg"])
        self.view = "lesson"
        self.open_plans = set()

        nav = tk.Frame(page, bg=C["bg"])
        nav.pack(fill="x")
        self.app.button(nav, "◀", lambda: self.show(self.idx - 1), font=F["small"], padx=10).pack(side="left")
        self.app.button(nav, "Today", lambda: self.show(self.today), font=F["small"], padx=10).pack(side="left", padx=6)
        self.app.button(nav, "▶", lambda: self.show(self.idx + 1), font=F["small"], padx=10).pack(side="left")
        self.status = self.label(nav, "", F["small"], C["muted"])
        self.status.pack(side="right")

        self.head = self.label(page, "", F["small"], C["muted"])
        self.head.pack(fill="x", pady=(12, 0))
        self.title = self.label(page, "", F["title_small"], wraplength=700)
        self.title.pack(fill="x")
        self.res = self.label(page, "", F["small"], C["muted"], wraplength=700)
        self.res.pack(fill="x", pady=(2, 8))

        self.steps_box = tk.Frame(page, bg=C["panel"], highlightbackground=C["line"], highlightthickness=1)
        self.steps_box.pack(fill="x")

        row = tk.Frame(page, bg=C["bg"])
        row.pack(fill="x", pady=(12, 4))
        self.label(row, "My lab notes (what I did, what I learned, errors I hit)", F["bold"]).pack(side="left")
        self.notes = tk.Text(page, height=7, wrap="word", font=F["normal"], bg=C["entry"], fg=C["ink"], relief="flat",
                             insertbackground=C["ink"], padx=10, pady=8, highlightthickness=1, highlightbackground=C["line"])
        self.notes.pack(fill="x")

        btns = tk.Frame(page, bg=C["bg"])
        btns.pack(fill="x", pady=8)
        self.app.button(btns, "Save notes", self.save_notes, font=F["small"]).pack(side="left")
        self.done_btn = self.app.button(btns, "Mark done", self.toggle_done, font=F["small"])
        self.done_btn.pack(side="left", padx=6)
        self.assess_btn = self.app.button(btns, "Assess my log", self.assess, font=F["small"], bg=C["accent"], fg=C["bg"])
        self.assess_btn.pack(side="right")

        self.label(page, "Vega's review", F["bold"]).pack(fill="x", pady=(6, 2))
        self.review = tk.Text(page, height=7, wrap="word", font=F["small"], bg=C["panel"], fg=C["ink"], relief="flat",
                              padx=10, pady=8, highlightthickness=1, highlightbackground=C["line"], state="disabled")
        self.review.pack(fill="x")

        # Ask Vega about THIS day, right here (answers stay in the Lab Log, not in the main chat)
        # ---------- Ask Vega about this day: a small, clean chat (like ChatGPT) that only talks about this day
        self.helper_title = self.label(page, "Ask Vega about this day", F["bold"])
        self.helper_title.pack(fill="x", pady=(18, 0))
        self.label(page, "She knows this day's task, steps and your notes. To send a photo: Photo button, or Windows + Shift + S "
                          "and then Ctrl + V in the box.", F["small"], C["muted"], wraplength=760).pack(fill="x", pady=(0, 6))
        chat = tk.Frame(page, bg=C["bg"], highlightthickness=1, highlightbackground=C["line"])
        chat.pack(fill="x")
        self.helper = tk.Text(chat, height=18, wrap="word", font=F["normal"], bg=C["bg"], fg=C["ink"], relief="flat", bd=0,
                              padx=22, pady=14, highlightthickness=0, cursor="arrow", spacing1=2, spacing3=2, state="disabled")
        from vega_pages import ThinBar
        hsb = ThinBar(chat, self.helper.yview, C["bg"])
        self.helper.configure(yscrollcommand=hsb.set)
        hsb.pack(side="right", fill="y")
        self.helper.pack(side="left", fill="both", expand=True)
        mono = ("Consolas" if os.name == "nt" else "DejaVu Sans Mono", 11)
        tc = self.helper.tag_configure
        tc("who_you", foreground=C["you"], font=F["bold"], lmargin1=160, spacing1=16)
        tc("you", background=C["panel"], lmargin1=160, lmargin2=160, rmargin=6, spacing1=4, spacing3=6)
        tc("who_vega", foreground=C["accent"], font=F["bold"], spacing1=16, spacing3=2)
        tc("a", foreground=C["ink"], spacing3=3)
        tc("b", font=F["bold"])
        tc("h", font=F["bold"], spacing1=10, spacing3=2)
        tc("bullet", lmargin1=12, lmargin2=30)
        tc("code", foreground=C["ok"], font=mono)
        tc("codeblock", foreground=C["ok"], font=mono, background=C["panel"], lmargin1=14, lmargin2=14, rmargin=6)
        tc("muted", foreground=C["muted"], font=F["small"])
        tc("act", foreground=C["muted"], font=F["small"], spacing1=6, spacing3=4)
        tc("hint", foreground=C["muted"], justify="center", spacing1=60)
        tc("hint2", foreground=C["muted"], font=F["small"], justify="center", spacing1=6)
        try:  # keep the space left of his bubble plain, so it looks like a chat bubble
            tc("you", lmargincolor=C["bg"])
        except tk.TclError:
            pass

        chips = tk.Frame(page, bg=C["bg"])
        chips.pack(fill="x", pady=(10, 0))
        for text, q in (("Commands for today", "QUICK_COMMANDS"),
                        ("How to use AI today", "QUICK_AI"),
                        ("Explain today simply", "QUICK_EXPLAIN"),
                        ("Quiz me", "QUICK_QUIZ")):
            self.app.button(chips, text, lambda q=q: self.ask_here(q), font=F["small"]).pack(side="left", padx=(0, 6))
        self.app.button(chips, "Clear chat", self.clear_helper, font=F["small"]).pack(side="right")

        self.photo = None  # a screenshot waiting to be sent with the next question
        self.photo_row = tk.Frame(page, bg=C["bg"])
        self.photo_label = self.label(self.photo_row, "", F["small"], C["accent"])
        self.photo_label.pack(side="left")
        self.app.button(self.photo_row, "Remove photo", lambda: self.set_photo(None), font=F["small"]).pack(side="left", padx=8)

        self.ask_row = tk.Frame(page, bg=C["entry"], highlightthickness=1, highlightbackground=C["line"])
        self.ask_row.pack(fill="x", pady=(8, 4))
        self.ask_entry = tk.Entry(self.ask_row, font=F["normal"], bg=C["entry"], fg=C["ink"], insertbackground=C["ink"],
                                  relief="flat", highlightthickness=0)
        self.ask_entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(12, 0), pady=6)
        self.ask_entry.bind("<Return>", lambda e: self.ask_here())
        for key in ("<Control-v>", "<Control-V>"):
            self.ask_entry.bind(key, self.on_paste)
        self.app.button(self.ask_row, "Photo", self.pick_photo, font=F["small"]).pack(side="left", padx=(6, 0), pady=6)
        self.ask_btn = self.app.button(self.ask_row, "Ask", self.ask_here, font=F["small"], bg=C["accent"], fg=C["bg"])
        self.ask_btn.pack(side="left", padx=6, pady=6)
        self.helper_hist = {}  # day -> [(question, answer)] so follow-up questions make sense
        self.helper_busy = False
        self._act_n = 0

        # "How to learn this": sources, how AI is used in the job, prompts and interview questions for each topic
        self.learn_head = self.label(page, "How to learn this", F["bold"])
        self.learn_head.pack(fill="x", pady=(14, 2))
        self.learn_box = tk.Frame(page, bg=C["bg"])
        self.learn_box.pack(fill="x")

        self.stats = self.label(page, "", F["small"], C["muted"])
        self.stats.pack(fill="x", pady=(12, 4))
        self.cell, self.gap = 14, 3
        self.canvas = tk.Canvas(page, bg=C["bg"], highlightthickness=0,
                                height=7 * (self.cell + self.gap), width=24 * (self.cell + self.gap))
        self.canvas.pack(anchor="w")
        self.canvas.bind("<Button-1>", self.click_map)
        self.label(page, "Green = done · red = missed · blue = the day you're viewing · click a square to open that day",
                   F["small"], C["muted"]).pack(fill="x", pady=(4, 0))

    # ---------------- skills (tracks)
    def load_track(self, tid):
        self.tid = tid
        self.total = len(self.vega.track_lessons(tid))
        t = self.vega.track_today(tid)
        self.today = t
        self.idx = min(max(t, 0), max(self.total - 1, 0))

    def switch_track(self, tid):
        self.save_notes(quiet=True)
        self.loaded = None
        if self.view == "projects":  # back from the Projects page to a skill
            self.proj_page.pack_forget()
            self.lesson_page.pack(fill="x")
            self.view = "lesson"
        self.load_track(tid)
        self.draw_tracks()
        if self.total:
            self.show(self.idx, save=False)

    def draw_tracks(self):
        C, F = self.C, self.F
        for w in self.track_bar.winfo_children():
            w.destroy()
        on_projects = self.view == "projects"
        for tid in self.vega.track_ids():
            on = tid == self.tid and not on_projects
            self.app.button(self.track_bar, self.vega.track_name(tid), lambda t=tid: self.switch_track(t), font=F["bold"] if on else F["small"],
                            bg=C["accent"] if on else C["btn"], fg=C["bg"] if on else C["ink"], padx=14).pack(side="left", padx=(0, 6))
        todo = sum(1 for p in self.vega.project_list() if p["status"] != "done")
        self.app.button(self.track_bar, "Projects" + (f"  {todo}" if todo else ""), self.show_projects,
                        font=F["bold"] if on_projects else F["small"], bg=C["accent"] if on_projects else C["btn"],
                        fg=C["bg"] if on_projects else C["ink"], padx=14).pack(side="left", padx=(10, 6))
        self.app.button(self.track_bar, "+ Add skill", self.add_skill_dialog, font=F["small"]).pack(side="left", padx=(6, 0))
        if on_projects:
            self.app.button(self.track_bar, "+ Add project", self.project_dialog, font=F["small"]).pack(side="right")
            return
        self.app.button(self.track_bar, "Skill settings", self.settings_dialog, font=F["small"]).pack(side="right")
        self.app.button(self.track_bar, "Edit this day", self.edit_day_dialog, font=F["small"]).pack(side="right", padx=6)

    # ---------------- projects: ideas for later; Start turns one into a skill with dates
    def show_projects(self):
        self.save_notes(quiet=True)
        if self.view != "projects":
            self.lesson_page.pack_forget()
            self.proj_page.pack(fill="x")
            self.view = "projects"
        self.draw_tracks()
        self.draw_projects()
        if self.embedded:
            self.frame.canvas.yview_moveto(0)

    def draw_projects(self):
        C, F = self.C, self.F
        page = self.proj_page
        for w in page.winfo_children():
            w.destroy()
        projects = self.vega.project_list()
        todo = sum(1 for p in projects if p["status"] != "done")
        self.label(page, "Projects", F["title_small"]).pack(fill="x", pady=(4, 0))
        self.label(page, (f"{todo} to do  ·  " if todo else "") + "Ideas you'll build later. Press Start when you're ready, "
                   "and the plan becomes lessons with dates, like Python or C.", F["small"], C["muted"], wraplength=700
                   ).pack(fill="x", pady=(2, 12))
        if not projects:
            self.label(page, "No projects yet. Press + Add project to save an idea for later.", F["small"], C["muted"]).pack(fill="x")
        for p in projects:
            self.project_card(page, p)
        self.draw_project_helper(page)

    PROJECT_PROMPTS = {
        "PROJECT_TEACHER": (
            "Give me the teacher script",
            "Generate the complete Python teacher script for Session 3 of Train Vega. "
            "It should read questions from questions.txt, use Ollama to get conversational answers from Gemma (gemma3:4b) "
            "and coding/Linux answers from Qwen (qwen2.5-coder:3b), and save the dataset to a JSONL file ready for Unsloth training on Colab. "
            "Include exact instructions on how to run it."
        ),
        "PROJECT_COLAB": (
            "Help me set up Google Colab",
            "Give me a clear, step-by-step guide to set up Google Colab for Session 5 of Train Vega. "
            "Explain: 1) How to switch to the free T4 GPU runtime, 2) The pip install command for Unsloth, "
            "3) How to upload my dataset file, 4) What cells to run, and 5) How to download the fine-tuned GGUF file."
        ),
        "PROJECT_CARD": (
            "Draft vega_card.txt",
            "Help me write 'vega_card.txt' for Session 1 of Train Vega. "
            "Draft a complete file with: 1) Who Vega is (caring, smart assistant for Hemanth, SRM student), "
            "2) 10 rules for her tone and style (simple English, short answers, friendly, Telugu/Tenglish understanding), "
            "3) Safe facts about studies and goals without any private details."
        ),
        "PROJECT_EXPLAIN": (
            "Explain this session",
            "Explain the Train Vega project roadmap. Break down the sessions simply from planning to Ollama import, "
            "and tell me how to get started on Session 1 right now."
        ),
    }

    def draw_project_helper(self, parent):
        C, F = self.C, self.F
        box = tk.Frame(parent, bg=C["bg"])
        box.pack(fill="x", pady=(14, 20))

        self.label(box, "Ask Vega about this project", F["bold"]).pack(fill="x", pady=(10, 2))
        self.label(box, "Vega is your Project Mentor. She knows the roadmap, teacher scripts, Colab setup, and all sessions.",
                   F["small"], C["muted"], wraplength=760).pack(fill="x", pady=(0, 6))

        chat_frame = tk.Frame(box, bg=C["bg"], highlightthickness=1, highlightbackground=C["line"])
        chat_frame.pack(fill="x")

        self.proj_helper_text = tk.Text(chat_frame, height=13, wrap="word", font=F["normal"], bg=C["bg"], fg=C["ink"],
                                        relief="flat", bd=0, padx=20, pady=12, highlightthickness=0, cursor="arrow",
                                        spacing1=2, spacing3=2, state="disabled")
        from vega_pages import ThinBar
        hsb = ThinBar(chat_frame, self.proj_helper_text.yview, C["bg"])
        self.proj_helper_text.configure(yscrollcommand=hsb.set)
        hsb.pack(side="right", fill="y")
        self.proj_helper_text.pack(side="left", fill="both", expand=True)

        mono = ("Consolas" if os.name == "nt" else "DejaVu Sans Mono", 11)
        tc = self.proj_helper_text.tag_configure
        tc("who_you", foreground=C["you"], font=F["bold"], lmargin1=160, spacing1=14)
        tc("you", background=C["panel"], lmargin1=160, lmargin2=160, rmargin=6, spacing1=4, spacing3=6)
        tc("who_vega", foreground=C["accent"], font=F["bold"], spacing1=14, spacing3=2)
        tc("a", foreground=C["ink"], spacing3=3)
        tc("b", font=F["bold"])
        tc("h", font=F["bold"], spacing1=10, spacing3=2)
        tc("bullet", lmargin1=12, lmargin2=30)
        tc("code", foreground=C["ok"], font=mono)
        tc("codeblock", foreground=C["ok"], font=mono, background=C["panel"], lmargin1=14, lmargin2=14, rmargin=6)
        tc("muted", foreground=C["muted"], font=F["small"])
        tc("act", foreground=C["muted"], font=F["small"], spacing1=6, spacing3=4)
        tc("hint", foreground=C["muted"], justify="center", spacing1=50)
        tc("hint2", foreground=C["muted"], font=F["small"], justify="center", spacing1=6)

        chips = tk.Frame(box, bg=C["bg"])
        chips.pack(fill="x", pady=(10, 0))
        for key in ("PROJECT_TEACHER", "PROJECT_COLAB", "PROJECT_CARD", "PROJECT_EXPLAIN"):
            lbl, prompt = self.PROJECT_PROMPTS[key]
            self.app.button(chips, lbl, lambda p=prompt: self.ask_project(p), font=F["small"]).pack(side="left", padx=(0, 6))
        self.app.button(chips, "Clear chat", self.clear_proj_helper, font=F["small"]).pack(side="right")

        ask_row = tk.Frame(box, bg=C["entry"], highlightthickness=1, highlightbackground=C["line"])
        ask_row.pack(fill="x", pady=(10, 0))
        self.proj_entry = tk.Entry(ask_row, font=F["normal"], bg=C["entry"], fg=C["ink"], relief="flat",
                                   insertbackground=C["ink"], highlightthickness=0)
        self.proj_entry.pack(side="left", fill="x", expand=True, padx=12, ipady=8)
        self.proj_entry.bind("<Return>", lambda e: self.submit_proj_entry())
        self.app.button(ask_row, "Ask", self.submit_proj_entry, font=F["bold"], bg=C["accent"], fg=C["bg"], padx=18, pady=6).pack(side="right")

        self.show_project_helper()

    def submit_proj_entry(self):
        txt = getattr(self, "proj_entry", None)
        if txt:
            q = txt.get().strip()
            if q:
                txt.delete(0, "end")
                self.ask_project(q)

    def clear_proj_helper(self):
        self.proj_helper_hist = []
        self.show_project_helper()

    def show_project_helper(self):
        h = getattr(self, "proj_helper_text", None)
        if not h or not h.winfo_exists():
            return
        h.configure(state="normal")
        h.delete("1.0", "end")
        hist = getattr(self, "proj_helper_hist", [])
        if not hist:
            try:
                h.insert("end", "Ask Vega anything about your projects.\n", ("hint",))
                h.insert("end", "Use the quick buttons above to get the Teacher script, Colab guide, or vega_card template.", ("hint2",))
            except Exception:
                pass
        for shown, a in hist:
            try:
                h.insert("end", "You\n", ("who_you",))
                h.insert("end", shown.strip() + "\n", ("you",))
                h.insert("end", "Vega\n", ("who_vega",))
                self.render_project_answer(a)
            except Exception:
                pass
        h.see("end")
        h.configure(state="disabled")

    def render_project_answer(self, text):
        h = self.proj_helper_text
        in_code = False
        for line in text.split("\n"):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                h.insert("end", line + "\n", ("codeblock",))
                continue
            if line.startswith("#"):
                h.insert("end", line + "\n", ("h",))
            elif line.strip().startswith(("-", "*", "•")):
                h.insert("end", "•  " + line.strip().lstrip("-*• ") + "\n", ("bullet",))
            else:
                h.insert("end", line + "\n", ("a",))

    def ask_project(self, prompt):
        if not prompt.strip():
            return
        if not hasattr(self, "proj_helper_hist"):
            self.proj_helper_hist = []
        self.proj_helper_hist.append((prompt, "Thinking..."))
        self.show_project_helper()

        projs = self.vega.project_list()
        ctx = ["Hemanth's Lab Log Projects:"]
        for p in projs:
            ctx.append(f"\nProject: {p['name']} (Status: {p['status']})")
            ctx.append(f"About: {p['about']}")
            if p.get("decided"):
                ctx.append("Decided rules & facts:\n" + "\n".join(f"- {d}" for d in p["decided"]))
            if p.get("steps"):
                ctx.append(f"Plan Sessions ({len(p['steps'])} total):")
                for n, s in enumerate(p["steps"], 1):
                    ctx.append(f"  Session {n}: {s.get('title')}: {'; '.join(s.get('steps', []))}")

        ctx_text = "\n".join(ctx)
        instruction = (
            "You are Vega, Hemanth's dedicated Project Mentor and AI Coach. "
            f"Here is his project roadmap:\n{ctx_text}\n\n"
            "Answer clearly and step by step. When providing scripts or code, give complete, working code blocks "
            "with zero placeholders. Be helpful, direct, and encouraging."
        )

        def worker():
            out = self.vega.chat(prompt, source="project_helper", instruction=instruction, save=False)
            answer = out.get("text", "")
            if self.proj_helper_hist and self.proj_helper_hist[-1][0] == prompt:
                self.proj_helper_hist[-1] = (prompt, answer)
            self.app.ui.put(self.show_project_helper)

        threading.Thread(target=worker, daemon=True).start()

    def project_card(self, parent, p):
        C, F = self.C, self.F
        bg = C["panel"]
        card = tk.Frame(parent, bg=bg, highlightbackground=C["line"], highlightthickness=1)
        card.pack(fill="x", pady=(0, 14))
        box = tk.Frame(card, bg=bg)
        box.pack(fill="x", padx=16, pady=12)

        def lab(text, font=None, fg=None, **kw):
            return self.label(box, text, font or F["small"], fg, bg=bg, **kw)

        track_ok = bool(p["track"]) and p["track"] in self.vega.track_ids(include_hidden=True)
        top = tk.Frame(box, bg=bg)
        top.pack(fill="x")
        try:
            added = date.fromisoformat(p["added"]).strftime("%a %d %b")
        except ValueError:
            added = p["added"]
        where = f"  ·  lessons in “{self.vega.track_name(p['track'])}”" if track_ok and p["status"] != "later" else ""
        self.label(top, f"Project  ·  added {added}{where}", F["small"], C["muted"], bg=bg).pack(side="left")
        pill = {"later": ("○ later", C["muted"]), "active": ("● started", C["accent"]), "done": ("● done", C["ok"])}[p["status"]]
        self.label(top, pill[0], F["small"], pill[1], bg=bg).pack(side="right")
        lab(p["name"], F["title_small"], C["muted"] if p["status"] == "done" else None).pack(fill="x", pady=(2, 0))
        if p["about"]:
            lab(p["about"], F["small"], C["muted"], wraplength=660).pack(fill="x", pady=(2, 4))
        if p["decided"]:
            lab("What we decided", F["bold"]).pack(fill="x", pady=(8, 2))
            for x in p["decided"]:
                lab("•  " + clean_step(x), F["small"], wraplength=650).pack(fill="x", padx=(4, 0))
        if p["steps"]:
            is_open = p["id"] in self.open_plans

            def toggle(pid=p["id"]):
                (self.open_plans.discard if pid in self.open_plans else self.open_plans.add)(pid)
                self.draw_projects()
            row = tk.Frame(box, bg=bg)
            row.pack(fill="x", pady=(10, 2))
            self.label(row, f"Plan  ·  {len(p['steps'])} session{'s' if len(p['steps']) != 1 else ''}", F["bold"], bg=bg).pack(side="left")
            self.app.button(row, "Hide plan" if is_open else "Show plan", toggle, font=F["small"], padx=10, pady=2).pack(side="left", padx=10)
            if p["status"] == "later":
                self.label(row, "becomes lessons when you start", F["small"], C["muted"], bg=bg).pack(side="right")
            if is_open:
                for n, st in enumerate(p["steps"], 1):
                    lab(f"{n}.  {st['title']}", F["bold"], wraplength=650).pack(fill="x", pady=(6, 0), padx=(4, 0))
                    for x in st["steps"]:
                        lab("     –  " + clean_step(x), F["small"], C["muted"], wraplength=620).pack(fill="x", padx=(4, 0))
        else:
            lab("No plan yet. Press Edit to add sessions.", F["small"], C["muted"]).pack(fill="x", pady=(8, 0))

        lab("My notes", F["bold"]).pack(fill="x", pady=(12, 2))
        notes = tk.Text(box, height=3, wrap="word", font=F["normal"], bg=C["entry"], fg=C["ink"], relief="flat",
                        insertbackground=C["ink"], padx=8, pady=6, highlightthickness=1, highlightbackground=C["line"])
        notes.insert("1.0", p["notes"])
        notes.pack(fill="x")
        saved = lab("", F["small"], C["muted"])

        def save_notes(pid=p["id"]):
            self.vega.save_project(pid, notes=notes.get("1.0", "end").strip()[:20000])
            saved.configure(text="Saved ✓")

        acts = tk.Frame(box, bg=bg)
        acts.pack(fill="x", pady=(8, 0))
        btn = lambda text, cmd, **kw: self.app.button(acts, text, cmd, font=F["small"], **kw).pack(side="left", padx=(0, 6))
        btn("Save notes", save_notes)
        pid = p["id"]
        if p["status"] == "later":
            btn("Start this project", lambda: self.start_project_dialog(pid), bg=C["accent"], fg=C["bg"])
            btn("Mark done", lambda: self.project_status(pid, "done"))
        elif p["status"] == "active":
            if track_ok:
                btn("Open its lessons", lambda: self.switch_track(p["track"]), bg=C["accent"], fg=C["bg"])
            btn("Mark done", lambda: self.project_status(pid, "done"))
            btn("Move back to later", lambda: self.project_status(pid, "later"))
        else:
            btn("Not done yet", lambda: self.project_status(pid, "active" if track_ok else "later"))
        btn("Edit", lambda: self.project_dialog(pid))
        if p["status"] != "active":
            btn("Hide" if p["builtin"] else "Delete", lambda: self.remove_project(pid))
        saved.pack(in_=acts, side="left", padx=6)
        if p["status"] == "active" and not track_ok:
            lab("Its lessons were removed from your skills. Press Move back to later, then Start it again.",
                F["small"], C["muted"], wraplength=640).pack(fill="x", pady=(6, 0))

    def project_status(self, pid, status):
        self.vega.set_project_status(pid, status)
        self.draw_tracks()
        self.draw_projects()

    def remove_project(self, pid):
        p = self.vega.project(pid)
        if not p:
            return
        if p["builtin"]:
            ok = messagebox.askyesno("Hide project", f"Hide “{p['name']}”?\n(You can bring it back with + Add project.)", parent=self.app.root)
        else:
            ok = messagebox.askyesno("Delete project", f"Delete “{p['name']}” and its notes?", parent=self.app.root)
        if ok:
            self.vega.delete_project(pid)
            self.draw_tracks()
            self.draw_projects()

    def start_project_dialog(self, pid):
        p = self.vega.project(pid)
        if not p:
            return
        top, body = self.dialog(f"Start {p['name']}")
        C, F = self.C, self.F
        d = date.today() + timedelta(days=1)
        while d.weekday() != 5:  # next Saturday
            d += timedelta(days=1)
        self.label(body, f"{len(p['steps'])} sessions. They become a skill tab with dates, ticks and notes. "
                         "You can change the days and hours later in its settings.", F["small"], C["muted"], wraplength=520).pack(fill="x")
        start = self.field(body, "First day (YYYY-MM-DD)", d.isoformat())
        days = self.days_picker(body, [5])
        mins = self.field(body, "Minutes per session", "45", width=8)
        msg = self.label(body, "", F["small"], C["bad"])
        msg.pack(fill="x")

        def go():
            first = self.read_date(start.get())
            chosen = [n for n, v in enumerate(days) if v.get()]
            try:
                m = int(mins.get().strip())
            except ValueError:
                m = 0
            if not first:
                return msg.configure(text="Write the date like 2026-10-03.")
            if not chosen:
                return msg.configure(text="Pick at least one day.")
            if not 5 <= m <= 600:
                return msg.configure(text="Minutes should be between 5 and 600.")
            if not p["steps"]:
                return msg.configure(text="This project has no sessions yet. Press Edit and add a few first.")
            tid = self.vega.start_project(pid, first, chosen, m)
            top.destroy()
            if tid:
                self.switch_track(tid)
                when = self.vega.track_dates(tid, 1)
                self.app.system(f"Started {p['name']}: {len(p['steps'])} lessons in your Lab Log"
                                + (f", the first on {when[0].strftime('%A %d %B')}." if when else "."))

        row = tk.Frame(body, bg=C["bg"])
        row.pack(fill="x", pady=(12, 0))
        self.app.button(row, "Start", go, bg=C["accent"], fg=C["bg"], font=F["small"]).pack(side="left")
        self.app.button(row, "Cancel", top.destroy, font=F["small"]).pack(side="left", padx=6)

    def project_dialog(self, pid=None):
        p = self.vega.project(pid) if pid else None
        top, body = self.dialog(f"Edit {p['name']}" if p else "Add a project", 600)
        C, F = self.C, self.F
        if not p:
            hidden = [x for x in self.vega.project_list(include_hidden=True) if x["hidden"]]
            if hidden:
                row = tk.Frame(body, bg=C["bg"])
                row.pack(fill="x", pady=(0, 6))
                self.label(row, "Bring back:", F["small"], C["muted"]).pack(side="left")
                for x in hidden:
                    def back(x=x):
                        self.vega.save_project(x["id"], hidden=False)
                        top.destroy()
                        self.show_projects()
                    self.app.button(row, x["name"], back, font=F["small"]).pack(side="left", padx=4)
        name = self.field(body, "Project name (for example: Portfolio website, Snake game, Vega on my phone)", p["name"] if p else "")
        about = self.text_box(body, "What is it?", p["about"] if p else "", height=3)
        decided = self.text_box(body, "What we decided (optional, one per line)", "\n".join(p["decided"]) if p else "", height=4)
        plan = self.text_box(body, "Plan, one session per line, like:   Build the first screen: make the window; add one button",
                             lessons_to_text(p["steps"]) if p else "", height=7)
        msg = self.label(body, "", F["small"], C["bad"], wraplength=540)
        msg.pack(fill="x")

        def save():
            nm = name.get().strip()
            if not nm:
                return msg.configure(text="Give the project a name.")
            fields = dict(about=about.get("1.0", "end").strip()[:600],
                          decided=[x.strip() for x in decided.get("1.0", "end").splitlines() if x.strip()][:30],
                          steps=parse_lessons(plan.get("1.0", "end")))
            if p:
                self.vega.save_project(p["id"], name=nm[:60], **fields)
            else:
                self.vega.add_project(nm, **fields)
            top.destroy()
            self.show_projects()

        row = tk.Frame(body, bg=C["bg"])
        row.pack(fill="x", pady=(12, 0))
        self.app.button(row, "Save" if p else "Add project", save, bg=C["accent"], fg=C["bg"], font=F["small"]).pack(side="left")
        self.app.button(row, "Cancel", top.destroy, font=F["small"]).pack(side="left", padx=6)
        if p and p["edited"]:
            def original():
                self.vega.save_project(p["id"], name=None, about=None, decided=None, steps=None)
                top.destroy()
                self.show_projects()
            self.app.button(row, "Back to original", original, font=F["small"]).pack(side="right")

    def days_text(self, info):
        days = info["days"]
        if len(days) == 7:
            return "every day"
        if days == [0, 1, 2, 3, 4]:
            return "Mon–Fri"
        if days == [0, 1, 2, 3, 4, 5]:
            return "Mon–Sat"
        return ", ".join(WEEKDAYS[d] for d in days)

    # ---------------- show a day
    def show(self, i, save=True):
        if not self.total:
            return
        if save:
            self.save_notes(quiet=True)
        self.idx = min(max(i, 0), self.total - 1)
        d = self.vega.track_day(self.tid, self.idx)
        e = self.vega.track_entry(self.tid, self.idx)
        info = self.vega.track_info(self.tid)
        C = self.C
        today = date.today()
        when = "Today" if d["date"] == today else ("Upcoming" if d["date"] > today else "Past day")
        self.head.configure(text=f"{info['name']}  ·  {d['label']}  ·  {d['date'].strftime('%A %d %B')}  ·  {when}"
                                 + (f"  ·  {d['topic']}" if d.get("topic") else ""))
        self.title.configure(text=d["title"])
        if hasattr(self, "helper"):
            self.show_helper()
        time_line = f"About {info['minutes']} min  ·  {self.days_text(info)}"
        if self.tid == "linux":
            wk = d["week"]
            self.res.configure(text=f"{time_line}   |   Coursera: {wk.get('coursera', '')}   |   YouTube: {wk.get('youtube', '')}")
        else:
            self.res.configure(text=time_line + (f"   |   {info['about']}" if info.get("about") else ""))
        for child in self.steps_box.winfo_children():
            child.destroy()
        self.step_vars = []
        for n, s in enumerate(d["steps"], 1):
            v = tk.BooleanVar(value=n in e["ticked"])
            self.step_vars.append(v)
            tk.Checkbutton(self.steps_box, text=f"{n}. {clean_step(s)}", variable=v, anchor="w", justify="left",
                           wraplength=660, bg=C["panel"], fg=C["ink"], selectcolor=C["entry"], activebackground=C["panel"],
                           activeforeground=C["ink"], font=self.F["normal"], command=lambda n=n, v=v: self.tick(n, v.get()),
                           highlightthickness=0, bd=0, relief="flat", cursor="hand2", padx=4, pady=2
                           ).pack(fill="x", padx=10, pady=3)
        if not d["steps"]:
            self.label(self.steps_box, "No steps yet. Press “Edit this day” to add some.", self.F["small"], C["muted"],
                       bg=C["panel"]).pack(fill="x", padx=10, pady=6)
        self.show_learn(self.idx)
        self.notes.delete("1.0", "end")
        self.notes.insert("1.0", e["notes"])
        self.loaded = (self.tid, self.idx)
        self.set_review(e.get("review") or "No review yet. Write your notes or tick steps, then press “Assess my log”.\n"
                                          + ("I also review today's log by myself every evening." if self.tid == "linux" else ""))
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

    def ask_ai(self, prompt):
        """The "Ask Vega" buttons in "How to learn this": ask right here. Prompts with a [paste] part go into the box first."""
        if "[" in prompt:
            self.ask_entry.delete(0, "end")
            self.ask_entry.insert(0, prompt)
            self.ask_entry.focus_set()
            self.ask_entry.icursor(prompt.index("["))
            self.scroll_to_helper()
            return
        self.ask_here(prompt)
        self.scroll_to_helper()

    def scroll_to_helper(self):
        """Jump up to the "Ask Vega about this day" box so the answer is in view."""
        try:
            if self.embedded:
                self.win.update_idletasks()
                total = max(self.win.winfo_height(), 1)
                y = self.helper_title.winfo_rooty() - self.win.winfo_rooty()  # its place on the whole page
                self.frame.canvas.yview_moveto(max(y - 10, 0) / total)
        except Exception:
            pass

    def clear_helper(self):
        self.helper_hist.pop((self.tid, self.idx), None)
        self.show_helper()

    def show_helper(self):
        """Show this day's questions and answers (each day keeps its own)."""
        h = self.helper
        h.configure(state="normal")
        h.delete("1.0", "end")
        hist = self.helper_hist.get((self.tid, self.idx), [])
        if not hist:
            self._put("Ask me anything about this day.\n", ("hint",))
            self._put("Try \"Commands for today\" below, paste an error, or send a photo of your screen.", ("hint2",))
        for shown, a in hist:
            self._add_q(shown)
            self._put("Vega\n", ("who_vega",))
            self.render(a)
            self._add_actions(a)
        h.see("end")
        h.configure(state="disabled")

    def _put(self, text, tags):
        try:
            self.helper.insert("end", text, tags)
        except tk.TclError:  # a character this Tk can't show (some emojis)
            self.helper.insert("end", "".join(ch if ord(ch) < 0x10000 else "?" for ch in text), tags)

    def _add_q(self, shown):
        self._put("You\n", ("who_you",))
        self._put(shown.strip() + "\n", ("you",))

    def _add_actions(self, answer):
        self._act_n += 1
        tag = f"copy{self._act_n}"
        self._put("Copy answer", ("act", tag))
        self._put("\n", ("act",))
        self.helper.tag_bind(tag, "<Button-1>", lambda e, t=answer: self.app.copy_text(t))
        self.helper.tag_bind(tag, "<Enter>", lambda e: self.helper.configure(cursor="hand2"))
        self.helper.tag_bind(tag, "<Leave>", lambda e: self.helper.configure(cursor="arrow"))

    def render(self, text):
        """Show an answer cleanly: headings, bullets, bold, commands in green - no raw ** or ` symbols."""
        text = re.sub(r"\*\*\s*`([^`\n]+)`\s*\*\*", r"`\1`", text)  # **`ls -la`**  ->  `ls -la`
        text = re.sub(r"\n{3,}", "\n\n", text.replace("^^", "").strip())
        in_code = False
        for line in text.split("\n"):
            if line.strip().startswith("```"):
                in_code = not in_code
                continue
            if in_code:
                self._put(line + "\n", ("codeblock",))
                continue
            head = re.match(r"^\s*#{1,6}\s+(.*)$", line)
            if head:
                self._put(head.group(1).replace("**", "") + "\n", ("a", "h"))
                continue
            extra = ()
            bullet = re.match(r"^\s*[*\-\u2022]\s+(.*)$", line)
            if bullet:
                self._put("\u2022  ", ("a", "bullet"))
                line, extra = bullet.group(1), ("bullet",)
            elif re.match(r"^\s*\d+[.)]\s+", line):
                line, extra = line.strip(), ("bullet",)
            for k, chunk in enumerate(line.split("`")):
                if k % 2:
                    self._put(chunk, ("a", "code") + extra)
                    continue
                for j, part in enumerate(re.split(r"\*\*(.+?)\*\*", chunk)):
                    part = re.sub(r"(?<![\w*])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![\w*])", r"\1", part).replace("**", "")
                    self._put(part, ("a", "b") + extra if j % 2 else ("a",) + extra)
            self._put("\n", ("a",) + extra)

    # ---------- photos of his screen
    PASTE_SCRIPT = (
        "Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing; "
        "$img = [System.Windows.Forms.Clipboard]::GetImage(); "
        "if ($img -ne $null) { $img.Save($env:VEGA_SHOT, [System.Drawing.Imaging.ImageFormat]::Png); Write-Output 'SAVED' } "
        "else { $f = [System.Windows.Forms.Clipboard]::GetFileDropList(); if ($f.Count -gt 0) { Write-Output ('FILE:' + $f[0]) } }")
    PHOTO_TYPES = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")

    def set_photo(self, path):
        self.photo = path
        if path:
            self.photo_label.configure(text=f"Photo ready: {os.path.basename(path)}  (type a question or just press Ask)")
            self.photo_row.pack(fill="x", pady=(6, 0), before=self.ask_row)
            self.ask_entry.focus_set()
        else:
            self.photo_row.pack_forget()

    def pick_photo(self):
        start = os.path.join(os.path.expanduser("~"), "Pictures", "Screenshots")
        path = filedialog.askopenfilename(title="Choose a photo of your screen",
                                          initialdir=start if os.path.isdir(start) else os.path.expanduser("~"),
                                          filetypes=[("Pictures", "*.png *.jpg *.jpeg *.bmp *.webp"), ("All files", "*.*")])
        if path:
            self.set_photo(path)

    def on_paste(self, event=None):
        """Ctrl + V: text pastes normally; a screenshot (Windows + Shift + S) becomes a photo for Vega."""
        try:
            if self.win.clipboard_get():
                return None
        except tk.TclError:
            pass
        if os.name != "nt":
            return "break"
        folder = os.path.join(os.path.expanduser("~"), "Pictures", "Vega Screenshots")

        def work():
            path = None
            try:
                os.makedirs(folder, exist_ok=True)
                target = os.path.join(folder, datetime.now().strftime("Lab photo %Y-%m-%d %H-%M-%S.png"))
                out = subprocess.run(["powershell", "-NoProfile", "-STA", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                      "-Command", self.PASTE_SCRIPT], capture_output=True, text=True, timeout=20,
                                     env=dict(os.environ, VEGA_SHOT=target),
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
                if "SAVED" in out and os.path.exists(target):
                    path = target
                elif "FILE:" in out:
                    f = out.split("FILE:", 1)[1].strip()
                    if f.lower().endswith(self.PHOTO_TYPES) and os.path.exists(f):
                        path = f
            except Exception:
                path = None
            self.app.ui.put(lambda: self.set_photo(path) if path else self.app.system(
                "There's no picture to paste. Take one with Windows + Shift + S first, or use the Photo button."))

        threading.Thread(target=work, daemon=True).start()
        return "break"

    def photo_model(self):
        """A brain that can see pictures (gemma3:4b can)."""
        m = self.vega.smart_model()
        low = m.lower()
        if any(v in low for v in ("gemma3", "llava", "vision", "vl", "minicpm-v", "moondream")) and "gemma3:1b" not in low:
            return m
        for name in ("gemma3:4b", "gemma3", "qwen2.5vl:3b", "llava", "moondream"):
            if self.vega.has_model(name):
                return name
        return None

    def day_context(self, i):
        d = self.vega.track_day(self.tid, i)
        e = self.vega.track_entry(self.tid, i)
        name = self.vega.track_name(self.tid)
        steps = "\n".join(f"{n}. {clean_step(x)}" for n, x in enumerate(d["steps"], 1)) or "(no steps written)"
        ticked = ", ".join(str(n) for n in sorted(e.get("ticked", []))) or "none yet"
        if self.tid == "linux":
            learn = self.vega.learn_for(i) or {}
            topics = "; ".join(t.get("name", "") for t in learn.get("topics", []))
            where = "He practises on a Rocky Linux 9 VM in VirtualBox (hostname server1, user hemanth). "
        else:
            topics = d.get("topic", "")
            where = {"python": "He practises Python 3.11 on Windows (IDLE and PowerShell) and python3 on his Rocky Linux VM. ",
                     "c": "He writes C with vi and compiles with gcc on his Rocky Linux 9 VM (hostname server1, user hemanth). "
                     }.get(self.tid, "")
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
                  "a command. If he asks about stars or symbols he sees around a command, tell him they are only text "
                  "formatting and not part of the command. If he sends a photo of his screen, read the text in it "
                  "carefully (prompt, command, output, error) and explain it. Keep answers short and practical.")

    def ask_here(self, question=None):
        q = (question or self.ask_entry.get()).strip()
        photo = self.photo
        if (not q and not photo) or self.helper_busy:
            return
        model = self.vega.smart_model()
        image = None
        if photo:
            model = self.photo_model()
            if not model:
                self.set_photo(None)
                self.app.system("To read photos Vega needs a brain that can see. In PowerShell run:  ollama pull gemma3:4b")
                return
            try:
                with open(photo, "rb") as f:
                    image = f.read()
            except OSError:
                self.set_photo(None)
                self.app.system("I couldn't open that photo. Try taking it again.")
                return
            if not q:
                q = ("Here is a photo of my screen. Read the text in it carefully and tell me what it shows. "
                     "If there is an error, give the cause in one line and the exact fix.")
                shown = f"[photo: {os.path.basename(photo)}]"
            else:
                shown = f"{q}\n[photo: {os.path.basename(photo)}]"
            self.set_photo(None)
        else:
            shown = q
        if q in self.QUICK:
            shown, q = self.QUICK[q]
        elif question is None or photo:
            self.ask_entry.delete(0, "end")
        i = self.idx
        k = (self.tid, i)
        msgs = [{"role": "system", "content": self.day_context(i)}]
        for hq, ha in self.helper_hist.get(k, [])[-3:]:
            msgs += [{"role": "user", "content": hq}, {"role": "assistant", "content": ha[:1500]}]
        msgs.append({"role": "user", "content": q, **({"images": [image]} if image else {})})
        self.helper.configure(state="normal")
        if not self.helper_hist.get(k):
            self.helper.delete("1.0", "end")
        self._add_q(shown)
        self._put("Vega\n", ("who_vega",))
        self.helper.mark_set("ans", "end-1c")
        self.helper.mark_gravity("ans", "left")
        self._put("thinking… (the first answer can take about 10 seconds)", ("muted",))
        self.helper.see("end")
        self.helper.configure(state="disabled")
        self.helper_busy = True
        self.ask_btn.configure(state="disabled", text="…")

        def show(a, final):
            if not self.win.winfo_exists():
                return
            from vega_core import strip_thinking
            text = strip_thinking(a).strip()
            if (self.tid, self.idx) == k:
                self.helper.configure(state="normal")
                self.helper.delete("ans", "end-1c")
                self.render(text or "…")
                if final:
                    self._add_actions(text)
                self.helper.see("end")
                self.helper.configure(state="disabled")
            if final:
                self.helper_hist.setdefault(k, []).append((shown, text))
                self.helper_hist[k] = self.helper_hist[k][-8:]
                self.helper_busy = False
                self.ask_btn.configure(state="normal", text="Ask")
                if (self.tid, self.idx) != k:
                    self.show_helper()

        def work():
            answer = ""
            if image is None:  # a text question: Gemini first when online (better answers), the laptop model if not
                try:
                    res = self.vega.cloud_chat(msgs[-1]["content"], msgs[1:-1], system_instruction=msgs[0]["content"],
                                                is_hard_or_code=True)
                except Exception:
                    res = None
                if res and res[0]:
                    self.app.ui.put(lambda a=res[0]: show(a, True))
                    return
            try:
                import ollama
                last = 0
                for part in ollama.chat(model=model, messages=msgs, stream=True, keep_alive="15m",
                                        options={"num_ctx": 6144}):
                    msg = part["message"] if isinstance(part, dict) else part.message
                    piece = msg["content"] if isinstance(msg, dict) else msg.content
                    answer += piece or ""
                    if len(answer) - last > 12:
                        last = len(answer)
                        self.app.ui.put(lambda a=answer: show(a, False))
            except Exception as ex:
                answer = f"I couldn't answer ({ex}). Is the Ollama app running?"
            self.app.ui.put(lambda a=answer: show(a, True))

        threading.Thread(target=work, daemon=True).start()

    def copy(self, prompt):
        self.app.copy_text(prompt)

    def show_learn(self, i):
        C, F = self.C, self.F
        for w in self.learn_box.winfo_children():
            w.destroy()
        if self.tid != "linux":
            self.learn_head.configure(text="How to learn this week")
            d = self.vega.track_day(self.tid, i)
            week = self.vega.track_learn(self.tid, d["week_no"]) if d else None
            if not week or not week.get("learn"):
                self.label(self.learn_box, "No sources for this skill yet. Ask Vega above: “how do I learn this?”",
                           F["small"], C["muted"]).pack(fill="x")
                return
            card = tk.Frame(self.learn_box, bg=C["panel"], highlightbackground=C["line"], highlightthickness=1)
            card.pack(fill="x", pady=4)
            tk.Label(card, text=f"Week {d['week_no'] + 1}: {week.get('topic', '')}", font=F["bold"], bg=C["panel"], fg=C["ink"],
                     anchor="w").pack(fill="x", padx=10, pady=(8, 2))
            self.label(card, "Free sources (pick one, you don't need all)", F["small"], C["muted"], bg=C["panel"]).pack(fill="x", padx=10)
            for label, url in week["learn"]:
                self.link(card, label, url).pack(fill="x", padx=18)
            self.label(card, "Tip: use “How to use AI today” above to get ready-made AI prompts for this lesson.", F["small"],
                       C["muted"], bg=C["panel"], wraplength=640).pack(fill="x", padx=10, pady=(6, 8))
            return
        self.learn_head.configure(text="How to learn this")
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
        done = self.vega.track_is_done(self.tid, self.idx)
        self.done_btn.configure(text="Mark not done" if done else "Mark done")
        e = self.vega.track_entry(self.tid, self.idx)
        score = f"  ·  Score {e['score']}/10" if e.get("score") is not None else ""
        self.status.configure(text=("✓ Done" if done else "Not done yet") + score,
                              fg=self.C["ok"] if done else self.C["muted"])
        st = self.vega.track_stats(self.tid)
        self.stats.configure(text=f"Streak {st['streak']}  ·  best {st['best']}  ·  {st['done']} of {st['total']} done"
                                  f"  ·  {st['missed']} carried over")
        self.draw_map()

    def map_rows(self):
        return 7 if self.tid == "linux" else max(1, min(7, len(self.vega.track_info(self.tid)["days"])))

    def draw_map(self):
        c, C = self.canvas, self.C
        c.delete("all")
        rows = self.map_rows()
        step = self.cell + self.gap
        cols = (self.total + rows - 1) // rows
        c.configure(height=rows * step, width=max(cols, 1) * step)
        for i in range(self.total):
            x, y = (i // rows) * step, (i % rows) * step
            if i == self.idx:
                fill, outline = C["accent"], C["accent"]
            elif self.vega.track_is_done(self.tid, i):
                fill, outline = C["ok"], C["ok"]
            elif i < self.today:
                fill, outline = C["panel"], C["bad"]
            else:
                fill, outline = C["panel"], C["line"]
            c.create_rectangle(x, y, x + self.cell, y + self.cell, fill=fill, outline=outline)

    def click_map(self, ev):
        step = self.cell + self.gap
        rows = self.map_rows()
        if ev.y // step >= rows:
            return
        i = (ev.x // step) * rows + (ev.y // step)
        if 0 <= i < self.total:
            self.show(i)

    # ---------------- actions
    def tick(self, n, on):
        all_done = self.vega.track_tick(self.tid, self.idx, n, on)
        if all_done and not self.vega.track_is_done(self.tid, self.idx):
            self.vega.track_set_done(self.tid, self.idx, True)
            self.app.system(f"All steps ticked, so {self.vega.track_name(self.tid)} "
                            f"{self.vega.track_day(self.tid, self.idx)['label']} is marked done. Nice work!")
        self.refresh_status()

    def save_notes(self, quiet=False):
        if not hasattr(self, "notes") or self.loaded is None:
            return
        tid, i = self.loaded
        text = self.notes.get("1.0", "end").strip()
        e = self.vega.track_entry(tid, i)
        if text != e["notes"].strip():
            e["notes"] = text
            e.pop("review", None)
            e.pop("score", None)
            self.vega.save_track_entry(tid, i, e)
            if not quiet:
                self.set_review("Notes saved. Press “Assess my log” for a new review.")
                self.refresh_status()

    def toggle_done(self):
        self.vega.track_set_done(self.tid, self.idx, not self.vega.track_is_done(self.tid, self.idx))
        self.refresh_status()

    def assess(self):
        self.save_notes(quiet=True)
        i, tid = self.idx, self.tid
        self.assess_btn.configure(state="disabled", text="Checking…")
        self.set_review("Reading your lab log… (the first time can take about 10 seconds)")

        def work():
            try:
                text, score = self.vega.assess_lesson(tid, i)
            except Exception as ex:
                text, score = f"I couldn't review it: {ex}\nIs the Ollama app running?", None

            def done():
                if not self.win.winfo_exists():
                    return
                self.assess_btn.configure(state="normal", text="Assess my log")
                if (self.tid, self.idx) == (tid, i):
                    self.set_review(text)
                    self.refresh_status()
                if score is not None and self.app.voice_var.get():
                    self.app.mouth.say(f"Your lab log score is {score} out of 10.")
            self.app.ui.put(done)

        threading.Thread(target=work, daemon=True).start()

    # ---------------- editing skills, days, dates and hours
    def dialog(self, title, width=560):
        C = self.C
        top = tk.Toplevel(self.app.root)
        top.title(title)
        top.configure(bg=C["bg"])
        top.transient(self.app.root)
        top.minsize(width, 200)
        body = tk.Frame(top, bg=C["bg"])
        body.pack(fill="both", expand=True, padx=18, pady=14)
        return top, body

    def field(self, parent, label, value="", width=30):
        C, F = self.C, self.F
        self.label(parent, label, F["small"], C["muted"]).pack(fill="x", pady=(8, 2))
        e = tk.Entry(parent, font=F["normal"], bg=C["entry"], fg=C["ink"], insertbackground=C["ink"], relief="flat",
                     highlightthickness=1, highlightbackground=C["line"], width=width)
        e.insert(0, value)
        e.pack(fill="x", ipady=5)
        return e

    def days_picker(self, parent, selected):
        C, F = self.C, self.F
        self.label(parent, "Study days", F["small"], C["muted"]).pack(fill="x", pady=(8, 2))
        row = tk.Frame(parent, bg=C["bg"])
        row.pack(fill="x")
        out = []
        for n, name in enumerate(WEEKDAYS):
            v = tk.BooleanVar(value=n in selected)
            out.append(v)
            tk.Checkbutton(row, text=name, variable=v, bg=C["bg"], fg=C["ink"], selectcolor=C["entry"], activebackground=C["bg"],
                           activeforeground=C["ink"], font=F["small"]).pack(side="left", padx=(0, 6))
        return out

    def text_box(self, parent, label, value="", height=8):
        C, F = self.C, self.F
        self.label(parent, label, F["small"], C["muted"], wraplength=520).pack(fill="x", pady=(8, 2))
        t = tk.Text(parent, height=height, wrap="word", font=F["normal"], bg=C["entry"], fg=C["ink"], insertbackground=C["ink"],
                    relief="flat", padx=8, pady=6, highlightthickness=1, highlightbackground=C["line"], width=60)
        t.insert("1.0", value)
        t.pack(fill="both", expand=True)
        return t

    @staticmethod
    def read_date(text):
        text = text.strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                pass
        return None

    def after_edit(self, keep_idx=True):
        idx = self.idx
        self.load_track(self.tid)
        self.draw_tracks()
        if self.total:
            self.loaded = None
            self.show(min(idx, self.total - 1) if keep_idx else self.idx, save=False)

    def settings_dialog(self):
        info = self.vega.track_info(self.tid)
        top, body = self.dialog(f"{info['name']} settings")
        C, F = self.C, self.F
        name = self.field(body, "Name", info["name"]) if self.tid != "linux" else None
        first = self.vega.track_dates(self.tid, 1)
        start = self.field(body, "Start date (YYYY-MM-DD). Change it to move ALL days of this skill.",
                           (first[0] if first else info["start"]).isoformat())
        days = self.days_picker(body, info["days"])
        mins = self.field(body, "Minutes per study day", str(info["minutes"]), width=8)
        self.label(body, f"{self.total} days in this skill. With these study days it ends on "
                         f"{self.vega.track_dates(self.tid)[-1].strftime('%d %b %Y') if self.total else '-'}.",
                   F["small"], C["muted"], wraplength=520).pack(fill="x", pady=(10, 0))
        msg = self.label(body, "", F["small"], C["bad"])
        msg.pack(fill="x")

        def save():
            d = self.read_date(start.get())
            chosen = [n for n, v in enumerate(days) if v.get()]
            try:
                m = int(mins.get().strip())
            except ValueError:
                m = 0
            if not d:
                return msg.configure(text="Write the date like 2026-10-05.")
            if not chosen:
                return msg.configure(text="Pick at least one study day.")
            if not 5 <= m <= 600:
                return msg.configure(text="Minutes should be between 5 and 600.")
            # the first lesson lands on the first study day on/after the date he typed
            self.vega.save_track_settings(self.tid, name=name.get() if name else None, start=d, days=chosen, minutes=m)
            top.destroy()
            self.after_edit()
            self.app.system(f"{self.vega.track_name(self.tid)} updated. Days, dates and hours are changed in your Lab Log.")

        def remove():
            nm = info["name"]
            if not messagebox.askyesno("Remove skill", f"Remove {nm} from your Lab Log?"
                                          + ("\n(Python and C can be added back with + Add skill.)" if info["builtin"] else
                                             "\nIts lessons and settings will be deleted."), parent=top):
                return
            self.vega.remove_track(self.tid)
            top.destroy()
            self.switch_track("linux")

        row = tk.Frame(body, bg=C["bg"])
        row.pack(fill="x", pady=(14, 0))
        self.app.button(row, "Save", save, bg=C["accent"], fg=C["bg"], font=F["small"]).pack(side="left")
        self.app.button(row, "Cancel", top.destroy, font=F["small"]).pack(side="left", padx=6)
        if self.tid != "linux":
            self.app.button(row, "Remove this skill", remove, font=F["small"]).pack(side="right")
            if self.tid in ("python", "c") and self.tid in self.vega.track_store()["lessons"]:
                def reset():
                    self.vega.reset_track_lessons(self.tid)
                    top.destroy()
                    self.after_edit()
                self.app.button(row, "Undo my lesson edits", reset, font=F["small"]).pack(side="right", padx=6)

    def edit_day_dialog(self):
        if not self.total:
            return
        d = self.vega.track_day(self.tid, self.idx)
        top, body = self.dialog(f"Edit {d['label']}")
        C, F = self.C, self.F
        title = self.field(body, "Title", d["title"])
        steps = self.text_box(body, "Steps, one per line", "\n".join(d["steps"]), height=8)
        msg = self.label(body, "", F["small"], C["muted"], wraplength=520)
        msg.pack(fill="x")

        def get_steps():
            return [x.strip() for x in steps.get("1.0", "end").splitlines() if x.strip()]

        def save():
            if not title.get().strip():
                return msg.configure(text="Give it a title.", fg=C["bad"])
            self.vega.edit_lesson(self.tid, self.idx, title.get(), get_steps())
            top.destroy()
            self.after_edit()

        def add_after():
            self.vega.insert_lesson(self.tid, self.idx + 1, "New lesson", [])
            top.destroy()
            self.after_edit()
            self.show(self.idx + 1, save=False)
            self.edit_day_dialog()

        def delete():
            if messagebox.askyesno("Delete day", f"Delete “{d['title']}”? The days after it move up one.", parent=top):
                if not self.vega.delete_lesson(self.tid, self.idx):
                    return msg.configure(text="A skill needs at least one day.", fg=C["bad"])
                top.destroy()
                self.after_edit()

        row = tk.Frame(body, bg=C["bg"])
        row.pack(fill="x", pady=(12, 0))
        self.app.button(row, "Save", save, bg=C["accent"], fg=C["bg"], font=F["small"]).pack(side="left")
        self.app.button(row, "Cancel", top.destroy, font=F["small"]).pack(side="left", padx=6)
        if self.tid == "linux":
            msg.configure(text="Linux days can be edited but not added or deleted (they follow the 24-week plan).")
            if str(self.idx) in self.vega.track_store()["linux_edits"]:
                def reset():
                    self.vega.reset_lesson("linux", self.idx)
                    top.destroy()
                    self.after_edit()
                self.app.button(row, "Back to original", reset, font=F["small"]).pack(side="right")
        else:
            self.app.button(row, "Delete this day", delete, font=F["small"]).pack(side="right")
            self.app.button(row, "Add a day after this", add_after, font=F["small"]).pack(side="right", padx=6)

    def add_skill_dialog(self):
        top, body = self.dialog("Add a skill", 600)
        C, F = self.C, self.F
        hidden = [t for t in self.vega.track_ids(include_hidden=True) if t not in self.vega.track_ids()]
        if hidden:
            row = tk.Frame(body, bg=C["bg"])
            row.pack(fill="x", pady=(0, 6))
            self.label(row, "Bring back:", F["small"], C["muted"]).pack(side="left")
            for t in hidden:
                def back(t=t):
                    self.vega.show_track(t)
                    top.destroy()
                    self.switch_track(t)
                self.app.button(row, self.vega.track_name(t), back, font=F["small"]).pack(side="left", padx=4)
        name = self.field(body, "Skill name (for example: Java, DSA, Communication, Gym, Docker)")
        start = self.field(body, "Start date (YYYY-MM-DD)", (date.today() + timedelta(days=1)).isoformat())
        days = self.days_picker(body, [5])
        mins = self.field(body, "Minutes per study day", "30", width=8)
        lessons = self.text_box(body, "Lessons, one per line, like:   Variables: watch the video; write 3 examples; save notes\n"
                                      "(or press “Let Vega write them” and then edit)", height=9)
        msg = self.label(body, "", F["small"], C["muted"], wraplength=540)
        msg.pack(fill="x")

        def draft():
            nm = name.get().strip()
            if not nm:
                return msg.configure(text="Write the skill name first.", fg=C["bad"])
            msg.configure(text="Vega is writing lessons… (about 20 seconds)", fg=C["muted"])
            try:
                m = int(mins.get())
            except ValueError:
                m = 30

            def work():
                try:
                    text = self.vega.draft_lessons(nm, 12, m)
                except Exception as ex:
                    text, err = "", str(ex)
                else:
                    err = ""

                def put():
                    if not top.winfo_exists():
                        return
                    if text:
                        lessons.delete("1.0", "end")
                        lessons.insert("1.0", text)
                        msg.configure(text="Check and change anything you like, then press Add skill.", fg=C["muted"])
                    else:
                        msg.configure(text=f"Vega couldn't write them ({err or 'empty answer'}). Is Ollama running?", fg=C["bad"])
                self.app.ui.put(put)
            threading.Thread(target=work, daemon=True).start()

        def save():
            nm = name.get().strip()
            d = self.read_date(start.get())
            chosen = [n for n, v in enumerate(days) if v.get()]
            try:
                m = int(mins.get().strip())
            except ValueError:
                m = 0
            ls = parse_lessons(lessons.get("1.0", "end"))
            if not nm:
                return msg.configure(text="Give the skill a name.", fg=C["bad"])
            if not d:
                return msg.configure(text="Write the date like 2026-10-05.", fg=C["bad"])
            if not chosen:
                return msg.configure(text="Pick at least one study day.", fg=C["bad"])
            if not 5 <= m <= 600:
                return msg.configure(text="Minutes should be between 5 and 600.", fg=C["bad"])
            if not ls:
                return msg.configure(text="Add at least one lesson (one per line), or press “Let Vega write them”.", fg=C["bad"])
            tid = self.vega.add_track(nm, d, chosen, m, ls)
            top.destroy()
            self.switch_track(tid)
            self.app.system(f"Added {nm} to your Lab Log: {len(ls)} lessons.")

        row = tk.Frame(body, bg=C["bg"])
        row.pack(fill="x", pady=(12, 0))
        self.app.button(row, "Add skill", save, bg=C["accent"], fg=C["bg"], font=F["small"]).pack(side="left")
        self.app.button(row, "Let Vega write them", draft, font=F["small"]).pack(side="left", padx=6)
        self.app.button(row, "Cancel", top.destroy, font=F["small"]).pack(side="left")

    def close(self):
        self.save_notes(quiet=True)
        (self.frame if self.embedded else self.win).destroy()
        self.app.lablog_win = None
