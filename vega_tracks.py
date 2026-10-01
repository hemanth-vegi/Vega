"""
Skills in the Lab Log: Linux (the 24-week plan), Python, C, and any skill you add yourself.

Each skill has its own lessons ("days"), start date, study days of the week and minutes per day,
and its own ticks, notes and reviews. Everything can be changed in the Lab Log window.

Projects are ideas for later (no dates). Pressing Start on one turns its plan into a skill with dates.

Files on this laptop:
  skills.json   - the ready-made Python and C lessons (comes with Vega updates)
  lab_projects.json - ready-made project ideas (comes with Vega updates)
  tracks.json   - your changes: settings per skill, edited lessons, skills you added, hidden skills, your projects
  lablog.json   - "tracks": {skill: {lesson number: {ticked, notes, done, review, score}}}  (Linux stays in "days")
"""

import sys
if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys, "stderr") and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import re
from datetime import date, datetime, timedelta

WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
BUILTIN = ("linux", "python", "c")
DEFAULTS = {  # light by default: about 8 hours a week in total, Sundays free
    "linux": {"days": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat"], "minutes": 50},
    "python": {"days": ["Mon", "Wed", "Fri"], "minutes": 40},
    "c": {"days": ["Tue", "Thu"], "minutes": 30},
}


def day_numbers(days):
    out = sorted({WEEKDAYS.index(d[:3].title()) for d in days or [] if isinstance(d, str) and d[:3].title() in WEEKDAYS})
    return out or list(range(7))


def parse_lessons(text):
    """One lesson per line:  Title: step one; step two; step three"""
    out = []
    for line in (text or "").splitlines():
        line = line.strip().lstrip("-*•0123456789.) ").strip()
        if not line:
            continue
        title, _, rest = line.partition(":")
        steps = [s.strip() for s in rest.split(";") if s.strip()] if rest else []
        out.append({"title": title.strip()[:120] or "Lesson", "steps": steps[:12]})
    return out


def lessons_to_text(lessons):
    return "\n".join(l["title"] + (": " + "; ".join(l.get("steps") or []) if l.get("steps") else "") for l in lessons)


class TracksMixin:
    # ---------------- storage
    def track_store(self):
        from vega_core import read_json
        s = read_json("tracks.json", None)
        if not isinstance(s, dict):
            s = {}
        for k in ("settings", "custom", "lessons", "linux_edits", "projects"):
            if not isinstance(s.get(k), dict):
                s[k] = {}
        if not isinstance(s.get("hidden"), list):
            s["hidden"] = []
        return s

    def save_track_store(self, s):
        from vega_core import write_json
        write_json("tracks.json", s)
        self._sched_cache = {}

    def skills_data(self):
        from vega_core import read_json
        d = read_json("skills.json", {}) or {}
        return d if isinstance(d, dict) else {}

    # ---------------- which skills exist
    def track_ids(self, include_hidden=False):
        s = self.track_store()
        ids = [t for t in BUILTIN if t == "linux" or t in self.skills_data()]
        ids += [t for t in s["custom"] if t not in ids]
        return [t for t in ids if include_hidden or t == "linux" or t not in s["hidden"]]

    def track_name(self, tid):
        return self.track_info(tid)["name"]

    def track_info(self, tid):
        s = self.track_store()
        base = self.skills_data().get(tid) or s["custom"].get(tid) or {}
        st = dict(DEFAULTS.get(tid, {"days": ["Mon", "Wed", "Fri"], "minutes": 30}))
        st.update({k: v for k, v in base.items() if k in ("days", "minutes", "start")})
        st.update(s["settings"].get(tid) or {})
        if tid == "linux":
            plan = self.load_plan() or {"start": date.today().isoformat()}
            start = date.fromisoformat(plan["start"]) + timedelta(days=self.plan_shift())
            name, about = "Linux", "Your 24-week Linux, OpenShift and cloud plan."
        else:
            try:
                start = date.fromisoformat(str(st.get("start") or base.get("start")))
            except ValueError:
                start = date.today()
            name = st.get("name") or base.get("name") or tid
            about = base.get("about", "")
        try:
            minutes = max(5, min(600, int(st.get("minutes") or 30)))
        except (TypeError, ValueError):
            minutes = 30
        return {"id": tid, "name": name, "start": start, "days": day_numbers(st.get("days")), "minutes": minutes,
                "about": about, "builtin": tid in BUILTIN}

    # ---------------- lessons
    def track_lessons(self, tid):
        s = self.track_store()
        if tid == "linux":
            plan = self.load_plan()
            if not plan:
                return []
            out = []
            for w, week in enumerate(plan["weeks"]):
                for n, day in enumerate(week["days"]):
                    i = w * 7 + n
                    ed = s["linux_edits"].get(str(i)) or {}
                    out.append({"title": ed.get("title") or day["title"], "steps": ed.get("steps") or day["steps"],
                                "topic": week["topic"], "week": w, "label": f"Week {w + 1}, Day {n + 1}", "short": f"W{w + 1} D{n + 1}"})
            return out
        if tid in s["lessons"]:  # he edited a ready-made skill
            raw = s["lessons"][tid]
        elif tid in s["custom"]:
            raw = s["custom"][tid].get("lessons") or []
        else:
            raw = []
            for w, week in enumerate(self.skills_data().get(tid, {}).get("weeks", [])):
                for l in week.get("lessons", []):
                    raw.append({"title": l["title"], "steps": l.get("steps", []), "topic": week.get("topic", ""), "week": w})
        per = max(1, len(self.track_info(tid)["days"]))
        out, count = [], {}
        for i, l in enumerate(raw):
            w = l.get("week") if isinstance(l.get("week"), int) else i // per
            count[w] = count.get(w, 0) + 1
            out.append({"title": l.get("title") or "Lesson", "steps": list(l.get("steps") or []), "topic": l.get("topic", ""),
                        "week": w, "label": f"Week {w + 1}, Lesson {count[w]}", "short": f"W{w + 1} L{count[w]}"})
        return out

    def track_learn(self, tid, week):
        weeks = self.skills_data().get(tid, {}).get("weeks", [])
        return weeks[week] if 0 <= week < len(weeks) else None

    # ---------------- dates
    def track_dates(self, tid, n=None):
        info = self.track_info(tid)
        n = len(self.track_lessons(tid)) if n is None else n
        key = (tid, info["start"], tuple(info["days"]), n)
        cache = getattr(self, "_sched_cache", None)
        if cache is None:
            cache = self._sched_cache = {}
        if key not in cache:
            out, d, days = [], info["start"], set(info["days"])
            while len(out) < n:
                if d.weekday() in days:
                    out.append(d)
                d += timedelta(days=1)
            cache[key] = out
        return cache[key]

    def track_day(self, tid, i):
        lessons = self.track_lessons(tid)
        if not 0 <= i < len(lessons):
            return None
        d = dict(lessons[i], index=i, date=self.track_dates(tid, len(lessons))[i], track=tid)
        if tid == "linux":
            plan = self.load_plan()
            d["week"] = plan["weeks"][i // 7]
            d["week_no"] = i // 7
        else:
            d["week_no"] = lessons[i]["week"]
        return d

    def track_today(self, tid, when=None):
        """Index of today's lesson (or the next one on a rest day). -1 before the skill starts."""
        when = when or date.today()
        dates = self.track_dates(tid)
        if not dates or when < dates[0]:
            return -1 if dates else 0
        return sum(1 for d in dates if d < when)

    def track_lesson_on(self, tid, when):
        """The lesson planned exactly on this date, or None (rest day)."""
        dates = self.track_dates(tid)
        return dates.index(when) if when in dates else None

    # ---------------- progress
    def track_entry(self, tid, i):
        if tid == "linux":
            return self.lab_entry(i)
        e = ((self.lablog_data().get("tracks") or {}).get(tid) or {}).get(str(i)) or {}
        e.setdefault("ticked", [])
        e.setdefault("notes", "")
        return e

    def save_track_entry(self, tid, i, entry):
        if tid == "linux":
            return self.save_lab_entry(i, entry)
        from vega_core import write_json
        d = self.lablog_data()
        d.setdefault("tracks", {}).setdefault(tid, {})[str(i)] = entry
        write_json("lablog.json", d)

    def track_is_done(self, tid, i):
        if tid == "linux":
            return self.is_done(i)
        return bool(self.track_entry(tid, i).get("done"))

    def track_set_done(self, tid, i, on=True):
        if tid == "linux":
            return self.set_done(i, on)
        e = self.track_entry(tid, i)
        if on:
            e["done"] = datetime.now().isoformat(timespec="seconds")
        else:
            e.pop("done", None)
        self.save_track_entry(tid, i, e)

    def track_tick(self, tid, i, n, on=True):
        """Tick step n (1-based). True if every step is now ticked."""
        if tid == "linux":
            return self.tick_step(i, n, on)
        d = self.track_day(tid, i)
        if not d:
            return False
        e = self.track_entry(tid, i)
        ticked = set(int(x) for x in e["ticked"])
        (ticked.add if on else ticked.discard)(n)
        e["ticked"] = sorted(x for x in ticked if 1 <= x <= len(d["steps"]))
        self.save_track_entry(tid, i, e)
        return bool(d["steps"]) and len(e["ticked"]) == len(d["steps"])

    def track_stats(self, tid):
        total = len(self.track_lessons(tid))
        today = self.track_today(tid)
        done = [self.track_is_done(tid, i) for i in range(total)]
        missed = sum(1 for i in range(0, min(max(today, 0), total)) if not done[i])
        cur, i = 0, min(max(today, 0), total) - 1
        if 0 <= today < total and done[today]:
            i = today
        while i >= 0 and done[i]:
            cur, i = cur + 1, i - 1
        best = run = 0
        for x in done[:max(today + 1, 0)]:
            run = run + 1 if x else 0
            best = max(best, run)
        return {"total": total, "done": sum(done), "missed": missed, "streak": cur, "best": best, "today": today}

    def _rekey(self, tid, at, delta):
        """Lessons were inserted or removed: move saved ticks/notes so they stay on the same lesson."""
        from vega_core import write_json
        d = self.lablog_data()
        old = (d.get("tracks") or {}).get(tid) or {}
        new = {}
        for k, v in old.items():
            if not str(k).isdigit():
                continue
            k = int(k)
            if delta < 0 and k == at:
                continue
            new[str(k + delta if k >= at + (1 if delta < 0 else 0) else k)] = v
        d.setdefault("tracks", {})[tid] = new
        write_json("lablog.json", d)

    # ---------------- changing things
    def save_track_settings(self, tid, name=None, start=None, days=None, minutes=None):
        s = self.track_store()
        st = s["settings"].setdefault(tid, {})
        if days is not None:
            st["days"] = [WEEKDAYS[n] for n in sorted(set(days))] or WEEKDAYS[:]
        if minutes is not None:
            st["minutes"] = int(minutes)
        if name and tid != "linux":
            st["name"] = name.strip()[:40]
        if start is not None:
            if tid == "linux":  # Linux keeps using the plan's shift, so "move my plan back 2 days" still works
                from vega_core import read_json, write_json
                plan = self.load_plan()
                ps = read_json("plan_settings.json", {}) or {}
                ps["shift"] = (start - date.fromisoformat(plan["start"])).days
                write_json("plan_settings.json", ps)
            else:
                st["start"] = start.isoformat()
        self.save_track_store(s)

    def _editable(self, tid, s):
        if tid in s["custom"]:
            return s["custom"][tid].setdefault("lessons", [])
        if tid not in s["lessons"]:
            s["lessons"][tid] = [{"title": l["title"], "steps": l["steps"], "topic": l["topic"], "week": l["week"]}
                                 for l in self.track_lessons(tid)]
        return s["lessons"][tid]

    def edit_lesson(self, tid, i, title, steps):
        s = self.track_store()
        if tid == "linux":
            s["linux_edits"][str(i)] = {"title": title.strip(), "steps": steps}
        else:
            lessons = self._editable(tid, s)
            if 0 <= i < len(lessons):
                lessons[i].update(title=title.strip(), steps=steps)
        self.save_track_store(s)

    def reset_lesson(self, tid, i):
        s = self.track_store()
        s["linux_edits"].pop(str(i), None)
        self.save_track_store(s)

    def insert_lesson(self, tid, at, title, steps):
        if tid == "linux":
            return False
        s = self.track_store()
        lessons = self._editable(tid, s)
        at = max(0, min(at, len(lessons)))
        ref = lessons[at - 1] if at > 0 and lessons else {}
        lessons.insert(at, {"title": title.strip() or "New lesson", "steps": steps, "topic": ref.get("topic", ""),
                            "week": ref.get("week") if isinstance(ref.get("week"), int) else None})
        self.save_track_store(s)
        self._rekey(tid, at, +1)
        return True

    def delete_lesson(self, tid, i):
        if tid == "linux":
            return False
        s = self.track_store()
        lessons = self._editable(tid, s)
        if not 0 <= i < len(lessons) or len(lessons) <= 1:
            return False
        lessons.pop(i)
        self.save_track_store(s)
        self._rekey(tid, i, -1)
        return True

    def add_track(self, name, start, days, minutes, lessons):
        s = self.track_store()
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "skill"
        tid, n = base, 2
        while tid in BUILTIN or tid in s["custom"]:
            tid, n = f"{base}-{n}", n + 1
        s["custom"][tid] = {"name": name.strip()[:40], "start": start.isoformat(), "lessons": lessons or [{"title": "First lesson", "steps": []}]}
        s["settings"][tid] = {"days": [WEEKDAYS[d] for d in sorted(set(days))] or ["Mon", "Wed", "Fri"], "minutes": int(minutes)}
        self.save_track_store(s)
        return tid

    def remove_track(self, tid):
        """Custom skills are deleted; Python and C are only hidden (they can come back)."""
        if tid == "linux":
            return False
        s = self.track_store()
        if tid in s["custom"]:
            s["custom"].pop(tid)
            s["settings"].pop(tid, None)
        elif tid not in s["hidden"]:
            s["hidden"].append(tid)
        self.save_track_store(s)
        return True

    def show_track(self, tid):
        s = self.track_store()
        s["hidden"] = [t for t in s["hidden"] if t != tid]
        self.save_track_store(s)

    def reset_track_lessons(self, tid):
        s = self.track_store()
        s["lessons"].pop(tid, None)
        self.save_track_store(s)

    # ---------------- projects: ideas for later; Start turns one into a skill with dates
    def projects_builtin(self):
        from vega_core import read_json
        d = read_json("lab_projects.json", []) or []  # not projects.json: that name belongs to the chat projects
        return [p for p in d if isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"]] if isinstance(d, list) else []

    @staticmethod
    def _project(base, d, builtin):
        def text(x, n):
            return x[:n] if isinstance(x, str) else ""

        def lines(x):
            return [str(s).strip()[:300] for s in x if str(s).strip()][:30] if isinstance(x, list) else None

        def plan(x):
            if not isinstance(x, list):
                return None
            return [{"title": str(l.get("title") or l.get("t") or "Session")[:120],
                     "steps": [str(s)[:300] for s in (l.get("steps") or l.get("items") or []) if str(s).strip()][:12]}
                    for l in x if isinstance(l, dict)][:100]

        status = d.get("status") if d.get("status") in ("later", "active", "done") else "later"
        return {"id": base.get("id") or d.get("id"), "builtin": builtin,
                "name": text(d.get("name"), 60) or base.get("name") or "Project",
                "about": d["about"][:600] if isinstance(d.get("about"), str) else base.get("about", ""),
                "decided": lines(d.get("decided")) if lines(d.get("decided")) is not None else list(base.get("decided") or []),
                "steps": plan(d.get("steps")) if plan(d.get("steps")) is not None else plan(base.get("steps")) or [],
                "added": text(d.get("added"), 10) or base.get("added") or date.today().isoformat(),
                "status": status, "notes": text(d.get("notes"), 20000), "track": text(d.get("track"), 40) or None,
                "done_at": text(d.get("done_at"), 40) or None, "hidden": bool(d.get("hidden")),
                "edited": builtin and any(k in d for k in ("name", "about", "decided", "steps"))}

    def project_list(self, include_hidden=False):
        """Active first, then later, then done; newest first inside each group."""
        state = self.track_store()["projects"]
        builtins = self.projects_builtin()
        ids = {b["id"] for b in builtins}
        out = [self._project(b, state.get(b["id"]) if isinstance(state.get(b["id"]), dict) else {}, True) for b in builtins]
        out += [self._project({"id": pid}, d, False) for pid, d in state.items()
                if pid not in ids and isinstance(d, dict) and d.get("custom")]
        out = [p for p in out if include_hidden or not p["hidden"]]
        out.sort(key=lambda p: p["added"], reverse=True)
        out.sort(key=lambda p: {"active": 0, "later": 1, "done": 2}[p["status"]])
        return out

    def project(self, pid):
        return next((p for p in self.project_list(include_hidden=True) if p["id"] == pid), None)

    def save_project(self, pid, **changes):
        """Change a project. A value of None removes that key (for example to undo edits of a ready-made one)."""
        s = self.track_store()
        d = s["projects"].setdefault(pid, {})
        for k, v in changes.items():
            if v is None:
                d.pop(k, None)
            else:
                d[k] = v
        self.save_track_store(s)

    def add_project(self, name, about="", decided=None, steps=None):
        s = self.track_store()
        base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "project"
        taken = {b["id"] for b in self.projects_builtin()} | set(s["projects"])
        pid, n = base, 2
        while pid in taken:
            pid, n = f"{base}-{n}", n + 1
        s["projects"][pid] = {"custom": True, "name": name.strip()[:60], "about": (about or "").strip()[:600],
                              "decided": [x.strip()[:300] for x in (decided or []) if x.strip()][:30],
                              "steps": steps or [], "status": "later", "added": date.today().isoformat(), "notes": ""}
        self.save_track_store(s)
        return pid

    def delete_project(self, pid):
        """His own projects are deleted; ready-made ones are only hidden (they can come back)."""
        p = self.project(pid)
        if not p:
            return False
        if p["builtin"]:
            self.save_project(pid, hidden=True)
        else:
            s = self.track_store()
            s["projects"].pop(pid, None)
            self.save_track_store(s)
        return True

    def set_project_status(self, pid, status):
        p = self.project(pid)
        if not p or status not in ("later", "active", "done"):
            return
        if status == "done":
            self.save_project(pid, status="done", done_at=datetime.now().isoformat(timespec="seconds"))
        else:
            self.save_project(pid, status=status, done_at=None)

    def start_project(self, pid, start, days, minutes):
        """Turn the project's plan into a skill with dates. Returns the new skill id (or None)."""
        p = self.project(pid)
        if not p or not p["steps"]:
            return None
        lessons = [{"title": st["title"], "steps": list(st["steps"])} for st in p["steps"]]
        tid = self.add_track(p["name"], start, days, minutes, lessons)
        self.save_project(pid, status="active", track=tid, started=datetime.now().isoformat(timespec="seconds"), done_at=None)
        return tid

    # ---------------- for chat, the briefing and "what's on my plan today"
    def other_study_today(self, when=None):
        """[(name, lesson dict)] for Python, C and his own skills planned on this date."""
        when = when or date.today()
        out = []
        for tid in self.track_ids():
            if tid == "linux":
                continue
            try:
                i = self.track_lesson_on(tid, when)
            except Exception:
                continue
            if i is not None:
                d = self.track_day(tid, i)
                if d:
                    d["done"] = self.track_is_done(tid, i)
                    out.append((self.track_name(tid), d))
        return out

    def assess_lesson(self, tid, idx):
        """Review a Python / C / own-skill lesson. Returns (text, score or None)."""
        if tid == "linux":
            return self.assess_day(idx)
        import ollama
        from vega_lablog import clean_step
        d = self.track_day(tid, idx)
        if not d:
            return "That lesson isn't in this skill.", None
        e = self.track_entry(tid, idx)
        if not e["notes"].strip() and not e["ticked"]:
            return (f"Your log for {d['label']} is empty, so there's nothing to review yet.\n"
                    "Write what you did in the notes box, or tick the steps you finished."), None
        steps = "\n".join(f"{n}. [{'x' if n in e['ticked'] else ' '}] {clean_step(s)}" for n, s in enumerate(d["steps"], 1))
        prompt = (
            f"Hemanth is a beginner learning {self.track_name(tid)}.\n"
            f"Today's lesson: {d['label']} ({d['topic']}): {d['title']}\n"
            f"Steps ([x] = he ticked it as done):\n{steps}\n\n"
            f"His notes:\n{e['notes'].strip() or '(no notes written)'}\n\n"
            "Review his work like a kind but honest teacher. Use simple English. Reply in exactly this format:\n"
            "Score: <0-10>/10\n"
            "Done well: <one or two short points>\n"
            "Missing: <steps or checks he skipped, or 'Nothing, well done'>\n"
            "Check yourself: 1) <short question about this lesson> 2) <another short question>\n"
            "Tip for next time: <one practical tip>\n"
            "If his notes show a mistake or an error message, explain the fix in one line under Missing."
        )
        r = ollama.chat(model=self.review_model(), keep_alive="15m", options={"temperature": 0.3, "num_ctx": 4096},
                        messages=[{"role": "system", "content": self.personality()}, {"role": "user", "content": prompt}])
        text = (r["message"]["content"] if isinstance(r, dict) else r.message.content) or ""
        from vega_core import strip_thinking
        text = strip_thinking(text).strip()
        m = re.search(r"score\s*:\s*(\d{1,2})\s*/\s*10", text, re.I)
        score = max(0, min(10, int(m.group(1)))) if m else None
        e = self.track_entry(tid, idx)
        e["review"], e["score"], e["reviewed"] = text, score, datetime.now().isoformat(timespec="seconds")
        self.save_track_entry(tid, idx, e)
        return text, score

    def draft_lessons(self, name, count, minutes):
        """Ask the AI for a simple lesson list for a new skill (he checks and edits it before saving)."""
        import ollama
        prompt = (f"Make a simple beginner study plan for: {name}.\n"
                  f"Exactly {count} lessons, each about {minutes} minutes, from zero to basic projects.\n"
                  "Write ONE lesson per line in this exact format, nothing else:\n"
                  "Title: small step; small step; small step\n"
                  "Keep steps short and practical. No numbering, no headings, no extra text.")
        r = ollama.chat(model=self.smart_model(), keep_alive="15m", options={"temperature": 0.4, "num_ctx": 4096},
                        messages=[{"role": "user", "content": prompt}])
        text = (r["message"]["content"] if isinstance(r, dict) else r.message.content) or ""
        from vega_core import strip_thinking
        lines = [l for l in strip_thinking(text).splitlines() if ":" in l and len(l) > 6]
        return "\n".join(re.sub(r"\*\*|`|^#+\s*", "", l).strip() for l in lines[:count])
