"""
Vega's daily Lab Log: today's study task with its steps, your lab notes, and Vega's review of your work.

Saved on this laptop in lablog.json:
  {"days": {"<day index>": {"ticked": [step numbers], "notes": "...", "review": "...", "score": 7, "reviewed": "<iso time>"}}}

Say:  "lab log: installed Rocky and ran df -h"     (adds a note to today)
      "step 2 done" / "untick step 2"
      "assess my lab log" / "review yesterday's lab log"
      "open lab log" (opens the Lab Log window) / "read my lab log yesterday"
Every evening (study_nudge_time) Vega reviews the day by herself, and the morning briefing
tells you yesterday's score and tip, today's steps, the news and fresh internship news.
"""

import sys
if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys, "stderr") and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import json
import re
from datetime import date, datetime

INTERN_QUERY = 'internship (devops OR cloud OR linux OR "software engineer" OR "IT") India'


def clean_step(s):
    return re.sub(r"`([^`]*)`", r"\1", s)


class LabLogMixin:
    # ---------------- storage
    def lablog_data(self):
        from vega_core import read_json
        d = read_json("lablog.json", None)
        if not isinstance(d, dict) or not isinstance(d.get("days"), dict):
            d = {"days": {}}
        return d

    def lab_entry(self, idx):
        e = self.lablog_data()["days"].get(str(idx)) or {}
        e.setdefault("ticked", [])
        e.setdefault("notes", "")
        return e

    def save_lab_entry(self, idx, entry):
        from vega_core import write_json
        d = self.lablog_data()
        d["days"][str(idx)] = entry
        write_json("lablog.json", d)

    def add_lab_note(self, idx, text):
        e = self.lab_entry(idx)
        stamp = datetime.now().strftime("%I:%M %p").lstrip("0")
        e["notes"] = (e["notes"].rstrip() + "\n" if e["notes"].strip() else "") + f"[{stamp}] {text.strip()}"
        e.pop("review", None)  # the old review no longer matches the notes
        e.pop("score", None)
        self.save_lab_entry(idx, e)

    def tick_step(self, idx, n, on=True):
        """Tick step n (1-based). Returns True if every step is now ticked."""
        plan = self.load_plan()
        d = self.plan_day(plan, idx) if plan else None
        if not d:
            return False
        e = self.lab_entry(idx)
        ticked = set(int(x) for x in e["ticked"])
        (ticked.add if on else ticked.discard)(n)
        e["ticked"] = sorted(x for x in ticked if 1 <= x <= len(d["steps"]))
        self.save_lab_entry(idx, e)
        return len(e["ticked"]) == len(d["steps"])

    # ---------------- Vega's review of the day
    def review_model(self):
        chosen = (self.cfg.get("chat_model") or "").strip()
        if chosen and chosen.lower() != "auto" and self.has_model(chosen):
            return chosen
        return self.smart_model()

    def assess_day(self, idx):
        """Ask the AI to review this day's lab log. Returns (text, score or None)."""
        import ollama
        plan = self.load_plan()
        d = self.plan_day(plan, idx) if plan else None
        if not d:
            return "That day isn't in your plan.", None
        e = self.lab_entry(idx)
        if not e["notes"].strip() and not e["ticked"]:
            return (f"Your lab log for {d['label']} is empty, so there's nothing to review yet.\n"
                    "Tell me what you did, like:  lab log: installed Rocky Linux and ran df -h\n"
                    "or tick the steps you finished in the Lab Log window."), None
        steps = "\n".join(f"{n}. [{'x' if n in e['ticked'] else ' '}] {clean_step(s)}" for n, s in enumerate(d["steps"], 1))
        prompt = (
            f"Hemanth is a final-year student learning Linux, OpenShift and cloud with a 24-week plan.\n"
            f"Today's task: {d['label']} ({d['topic']}): {d['title']}\n"
            f"Steps ([x] = he ticked it as done):\n{steps}\n\n"
            f"His lab notes:\n{e['notes'].strip() or '(no notes written)'}\n\n"
            "Review his work like a kind but honest lab teacher. Use simple English. Reply in exactly this format:\n"
            "Score: <0-10>/10\n"
            "Done well: <one or two short points>\n"
            "Missing: <steps or checks he skipped, or 'Nothing, well done'>\n"
            "Check yourself: 1) <short question about today's topic> 2) <another short question>\n"
            "Tip for tomorrow: <one practical tip>\n"
            "If his notes show a mistake or an error message, explain the fix in one line under Missing."
        )
        r = ollama.chat(model=self.review_model(), keep_alive="15m", options={"temperature": 0.3, "num_ctx": 4096},
                        messages=[{"role": "system", "content": self.personality()}, {"role": "user", "content": prompt}])
        text = (r["message"]["content"] if isinstance(r, dict) else r.message.content) or ""
        from vega_core import strip_thinking
        text = strip_thinking(text).strip()
        m = re.search(r"score\s*:\s*(\d{1,2})\s*/\s*10", text, re.I)
        score = max(0, min(10, int(m.group(1)))) if m else None
        e = self.lab_entry(idx)
        e["review"], e["score"], e["reviewed"] = text, score, datetime.now().isoformat(timespec="seconds")
        self.save_lab_entry(idx, e)
        return text, score

    # ---------------- learning sources per day (learn.json)
    def learn_for(self, idx):
        from vega_core import read_json
        data = read_json("learn.json", {}) or {}
        return data.get(str(idx))

    def learn_text(self, idx):
        from vega_core import reply
        plan = self.load_plan()
        d = self.plan_day(plan, idx) if plan else None
        day = self.learn_for(idx)
        if not d:
            return reply("That day isn't in your plan.")
        if not day:
            return reply(f"I don't have sources for {d['label']} yet. Ask me to explain the topic: “{d['title']}”.")
        lines = [f"How to learn {d['label']}: {d['title']}"]
        if day.get("course"):
            lines.append(f"Course: {day['course'][0]} — {day['course'][1]}")
        for n, tp in enumerate(day.get("topics", []), 1):
            lines += ["", f"{n}. {tp['name']}"]
            for key, title in (("watch", "Watch"), ("read", "Read"), ("practice", "Practice")):
                for label, url in tp.get(key) or []:
                    lines.append(f"   {title}: {label}" + (f" — {url}" if url else ""))
            lines.append(f"   AI in the job: {tp.get('ai_job', '')}")
            for pr in tp.get("prompts", [])[:1]:
                lines.append(f"   Try asking: {pr}")
        return reply("\n".join(lines), f"Here's how to learn {d['title']}. The sources are on screen.")

    # ---------------- commands
    def lablog_command(self, t, raw):
        from vega_core import reply
        plan = self.load_plan()
        if not plan:
            return None
        idx = self.today_index(plan)
        # add a note: "lab log: ...", "log: ...", "add to my lab log ..."
        m = re.match(r"^\s*(?:lab ?log|lablog|lab note|lab notes|log)\s*[:\-,]\s*(.+)$", raw, re.I | re.S) or \
            re.match(r"^\s*(?:add|write|note)\s+(?:this\s+)?(?:to|in)\s+(?:my\s+|the\s+)?lab ?log\s*[:\-,]?\s*(.+)$", raw, re.I | re.S)
        if m:
            self.add_lab_note(idx, m.group(1))
            d = self.plan_day(plan, idx)
            return reply(f"Added to your lab log for {d['label'] if d else 'today'}. "
                         "Say “assess my lab log” when you're done for a review.", "Added to your lab log.")
        m = re.match(r"^(?:tick|mark|check|done|finished|completed|i did|i finished|i completed)?\s*step\s*(\d{1,2})\s*(done|finished|completed|complete)?$", words(t))
        if m and (m.group(2) or re.match(r"^(tick|mark|check|done|finished|completed|i did|i finished|i completed)", words(t))):
            n = int(m.group(1))
            d = self.plan_day(plan, idx)
            if not d or not 1 <= n <= len(d["steps"]):
                return reply("That step isn't in today's task.")
            all_done = self.tick_step(idx, n, True)
            text = f"Ticked step {n}: {clean_step(d['steps'][n - 1])}"
            if all_done and not self.is_done(idx):
                self.set_done(idx, True)
                text += "\nAll steps are done, so I marked today's task as finished. Say “assess my lab log” for a review."
            return reply(text, f"Ticked step {n}.")
        m = re.match(r"^(?:untick|uncheck|unmark)\s+step\s*(\d{1,2})$", words(t))
        if m:
            self.tick_step(idx, int(m.group(1)), False)
            return reply(f"Unticked step {m.group(1)}.")
        if re.search(r"^(?:assess|review|check|grade|evaluate|rate|mark)\s+(?:my\s+)?(?:lab ?log|lab ?work|lab|today'?s (?:lab|work|log)|work today|yesterday'?s (?:lab ?log|work))"
                     r"(?:\s+(?:for\s+)?(?:today|yesterday))?$|^how did i do(?: today| yesterday)?(?: in (?:the )?lab)?$", t):
            i = idx - 1 if "yesterday" in t else idx
            text, score = self.assess_day(i)
            d = self.plan_day(plan, i)
            say = f"Your score is {score} out of 10. The full review is on screen." if score is not None else "It's on screen."
            return reply((f"Lab Log review, {d['label']}:\n" if d and score is not None else "") + text, say, open_lablog=False)
        m = re.match(r"^(?:how (?:do|can|should) i (?:learn|study)|where (?:can|do|should) i (?:learn|study)|(?:show |give me )?(?:study |learning )?"
                     r"(?:sources|resources|materials?|links))(?:.*?)(today|tomorrow|yesterday)?(?:'?s)?(?: topic| task)?$", t)
        if m and re.search(r"learn|study|sources|resources|material|links", t) and not re.search(r"internship|news", t):
            which = m.group(1) or ("today" if re.search(r"today|this|it\b", t) else "today")
            return self.learn_text(idx + (1 if which == "tomorrow" else -1 if which == "yesterday" else 0))
        m = re.match(r"^(?:read|what'?s in|what is in)\s+(?:me\s+)?(?:my\s+|the\s+|our\s+|today'?s\s+)?(?:lab ?log|lablog)(?:\s+(?:for\s+)?(today|yesterday))?$", t)
        if m:
            return self.lablog_text(idx - 1 if m.group(1) == "yesterday" else idx)

        # Open lab log window in Vega (unless asking for the website/Claude artifact explicitly)
        aliases = r"(?:lab ?log|lablog|black lock|applock|lag log|lap log|lab lock)"
        if not re.search(r"\b(claude|website|site|artifact|in browser|in chrome|in brave)\b", t):
            if re.search(r"\b" + aliases + r"\b", t) or re.search(r"\b(?:our\s+clock|today'?s\s+clock|clock\s+in\s+vega)\b", t):
                if re.match(r"^(?:open|show|view|check|go to|see|display)\b", t) or \
                   re.match(r"^(?:(?:our|today'?s|my|the)\s+)?" + aliases + r"(?:\s+(?:in|on)\s+(?:our\s+)?vega)?(?:\s+now)?$", t) or \
                   t in ("lab log", "lablog", "lab lock", "lag log", "black lock", "lap log"):
                    if getattr(self, "_source", "laptop") == "laptop":
                        return reply("Opening your Lab Log.", "", open_lablog=True)
                    return self.lablog_text(idx)
        return None

    def lablog_text(self, idx):
        from vega_core import reply
        plan = self.load_plan()
        d = self.plan_day(plan, idx) if plan else None
        if not d:
            return reply("That day isn't in your plan.")
        e = self.lab_entry(idx)
        lines = [f"Lab Log, {d['label']} ({d['date'].strftime('%a %d %b')}): {d['title']}" + (" ✓ done" if self.is_done(idx) else "")]
        lines += [f"  [{'x' if n in e['ticked'] else ' '}] {n}. {clean_step(s)}" for n, s in enumerate(d["steps"], 1)]
        lines += ["", "Notes:", e["notes"].strip() or "  (none yet — say “lab log: what you did”)"]
        if e.get("review"):
            lines += ["", "Vega's review:", e["review"]]
        return reply("\n".join(lines), f"Here's your lab log for {d['label']}. It's on screen.")

    # ---------------- evening check and morning extras
    def evening_check(self):
        """At study_nudge_time: review today's lab log by itself, or ask him to write it. Returns a reply or None."""
        from vega_core import reply
        plan = self.load_plan()
        if not plan:
            return None
        idx = self.today_index(plan)
        d = self.plan_day(plan, idx)
        if not d:
            return None
        e = self.lab_entry(idx)
        left = [tk["text"] for tk, done, _ in self.tasks_for(date.today()) if not done]
        tasks = f"\nTasks still open today: {', '.join(left[:5])}." if left else ""
        if e["notes"].strip() or e["ticked"]:
            if not e.get("review"):
                try:
                    text, score = self.assess_day(idx)
                except Exception:
                    return reply(f"Evening check: you wrote your lab log today. Say “assess my lab log” for a review.{tasks}")
                return reply(f"Evening check. I reviewed today's lab log ({d['title']}):\n{text}{tasks}",
                             f"Evening check. I reviewed your lab log. Your score is {score} out of 10. It's on screen."
                             if score is not None else "Evening check. I reviewed your lab log. It's on screen.")
            return reply(f"Evening check: your lab log is reviewed. Nice work today.{tasks}") if tasks else None
        if not self.is_done(idx):
            return reply(f"Evening check: you haven't written today's lab log yet ({d['title']}).\n"
                         "Even 30 minutes counts. When you're done, tell me what you did, like:\n"
                         "lab log: ran find and grep on /etc, learned which and whereis" + tasks,
                         f"Evening check. You haven't finished today's task yet: {d['title']}. Even 30 minutes counts.")
        return reply(f"Evening check: today's task is done. Tell me what you did with “lab log: …” so I can review it.{tasks}",
                     "Evening check. Today's task is done. Tell me what you did so I can review it.")

    def fresh_internship_news(self, n=3):
        """Internship news from the last day that he hasn't seen before (changes every day)."""
        from vega_core import read_json, write_json
        try:
            items = self.fetch_news(INTERN_QUERY, 12)
        except Exception:
            return []
        state = read_json("briefing_state.json", {}) or {}
        seen = list(state.get("seen_internships") or [])
        fresh = [(h, u) for h, u in items if h not in seen][:n]
        state["seen_internships"] = (seen + [h for h, _ in fresh])[-300:]
        write_json("briefing_state.json", state)
        return fresh

    def lab_briefing_lines(self):
        """Extra lines for the morning briefing: yesterday's review and today's steps."""
        plan = self.load_plan()
        if not plan:
            return []
        idx = self.today_index(plan)
        lines = []
        if idx - 1 >= 0:
            y = self.lab_entry(idx - 1)
            if y.get("review"):
                tip = re.search(r"tip for tomorrow\s*:\s*(.+)", y["review"], re.I)
                lines.append(f"Yesterday's lab log: {y.get('score', '?')}/10." + (f" Tip: {tip.group(1).strip()}" if tip else ""))
            elif not y["notes"].strip() and not self.is_done(idx - 1):
                lines.append("Yesterday's lab log is empty. You can still add it: lab log: …")
        d = self.plan_day(plan, idx)
        if d:
            lines.append("Today's steps: " + " | ".join(f"{n}. {clean_step(s)}" for n, s in enumerate(d["steps"], 1)))
        return lines


def words(t):
    from vega_core import words_to_digits
    return words_to_digits(t)
