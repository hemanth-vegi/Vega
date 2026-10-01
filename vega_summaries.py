"""
Summaries, free and on this laptop (Ollama):
  "summarize the news", "summarize tech news", "summarize news about ISRO",
  "summarize the weather", "summarize my tasks", "summarize my diary" (this week),
  "summarize my diary today / yesterday / this month", "summarize my day" (everything at once).
Also "news summary", "weather summary", "tasks summary", "diary summary".
The sidebar has a "Summaries" button, and the News / Tech news pages have a "Summarize" button.
"""

import sys
if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys, "stderr") and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import os
import re
from datetime import date, datetime, timedelta

TECH_QUERY = "technology OR AI OR cloud computing OR smartphones"

SUMMARY_RULES = ("You are summarizing information Hemanth gave you. Use ONLY that information: never invent details, "
                 "numbers, names or quotes. Simple English, short sentences, bullet points. Keep it short.")


def news_prompt(heads, label):
    """The prompt for a news summary (also used by the News and Tech news pages)."""
    lines = "\n".join(f"- {h}" for h in heads)
    return (f"Here are today's {label} headlines (only the headlines, not the full articles):\n{lines}\n\n"
            "Summarize them for me in simple English:\n"
            "- 3 to 5 short bullet points; join headlines that are about the same story\n"
            "- then one line starting with 'For you:' only if something matters for a student learning Linux, cloud and tech, "
            "for jobs and internships, or for Andhra Pradesh\n"
            "Only use what the headlines say. Don't invent details, numbers or quotes.")


class SummaryMixin:
    START = re.compile(
        r"^(?:(?:please|pls|plz|ok|okay|hey vega|vega|can you|could you|will you|just)\s+)*"
        r"(?:summari[sz]e|sum up|give me (?:a |the )?(?:short |quick |small |brief )?summary (?:of|for|on)|"
        r"(?:a |the )?(?:short |quick |small |brief )?summary (?:of|for|on)|brief me (?:on|about)|tl ?;? ?dr(?: of| for)?)"
        r"\s+(.+)$")
    END = re.compile(
        r"^(?:(?:please|ok|okay|can you|could you)\s+)*(?:give me |show me |show |tell me |read me |read |get )?"
        r"(?:a |the |my )?(?:short |quick |small |brief )?(.{2,40}?)\s+(?:summary|summaries|in short|tl ?;? ?dr)"
        r"(?:\s+(?:please|now|for me))*$")

    def summary_kind(self, obj):
        """What he wants summarized: ('news', topic) / ('tech', None) / ('weather', None) / ('tasks', None) /
        ('diary', period) / ('day', None), or (None, None) for anything else (that goes to normal chat)."""
        o = re.sub(r"\b(?:please|now|for me|quickly|in short|briefly|shortly)\b", " ", obj.lower())
        o = re.sub(r"[?.!,]+", " ", o)
        o = re.sub(r"\s+", " ", o).strip()
        if not o or len(o.split()) > 7:
            return None, None
        if re.fullmatch(r"(?:the |today'?s |todays |latest |my )?(?:tech|technology)(?: news| headlines| updates)?(?: today)?", o) or \
                re.search(r"\b(?:tech|technology|ai|gadget|gadgets)\s+(?:news|headlines|updates)\b", o):
            return "tech", None
        m = re.search(r"\b(?:news|headlines)\b(?:\s+(?:about|on|for|from)\s+(.+))?", o)
        if m:
            topic = (m.group(1) or "").strip() or o[:m.start()].strip()
            while True:  # "the latest top news" -> no topic; "cricket news" -> cricket
                cut = re.sub(r"^(?:the|today'?s|todays|latest|top|all|my|some|any|important|main|big)\b\s*", "", topic)
                if cut == topic:
                    break
                topic = cut
            topic = re.sub(r"\s*\btoday\b\s*", " ", topic).strip()
            return "news", (topic if topic and topic not in ("india", "indian", "world") else None)
        if re.search(r"\b(?:weather|forecast|temperature)\b", o):
            return "weather", None
        if re.search(r"\b(?:tasks?|to-?dos?|to do list|to do|reminders?|schedule|work for today)\b", o):
            return "tasks", None
        if re.search(r"\b(?:diary|journal)\b", o):
            if re.search(r"\byesterday\b", o):
                return "diary", "yesterday"
            if re.search(r"\btoday\b", o):
                return "diary", "today"
            if re.search(r"\bmonth\b", o):
                return "diary", "month"
            return "diary", "week"
        if re.fullmatch(r"(?:my |the |today'?s |todays |this |whole |my whole |the whole )*(?:day|today|everything|all|morning|"
                        r"daily update|day so far)", o):
            return "day", None
        return None, None

    def summarize(self, t, raw):
        m = self.START.match(t) or self.END.match(t)
        if not m:
            return None
        obj = m.group(1).strip()
        kind, arg = self.summary_kind(obj)
        if not kind:
            return None  # "summarize this paragraph…" or "summarize resume" (a document) is not ours
        try:  # a document with the same name wins: "summarize tasks" when he has tasks.pdf
            if any(os.path.splitext(d)[0].lower() == obj.lower() for d in self.list_docs()):
                return None
        except Exception:
            pass
        return self.make_summary(kind, arg)

    # ---------------- gathering what to summarize
    def _today_study(self, d):
        lines = []
        try:
            plan = self.load_plan()
            i = self.track_lesson_on("linux", d) if plan else None
            if i is not None:
                pd = self.plan_day(plan, i)
                lines.append(f"Linux ({self.track_info('linux')['minutes']} min): {pd['title']}" + (" (done)" if self.is_done(i) else ""))
            for name, od in self.other_study_today(d):
                lines.append(f"{name} ({self.track_info(od['track'])['minutes']} min): {od['title']}" + (" (done)" if od.get("done") else ""))
        except Exception:
            pass
        return lines

    def _today_reminders(self, d):
        from vega_core import fmt_time, read_json
        items = read_json("reminders.json", []) or []
        out = []
        for r in items:
            if isinstance(r, dict) and not r.get("done") and str(r.get("when", ""))[:10] == d.isoformat():
                try:
                    out.append((r["when"], f"{fmt_time(datetime.fromisoformat(r['when']))} {r.get('text', '')}"))
                except (ValueError, KeyError):
                    pass
        return [x for _, x in sorted(out)]

    def _task_lines(self, d):
        rows = self.tasks_for(d)
        left = [tk["text"] + (f" ({tag})" if tag else "") for tk, done, tag in rows if not done]
        done = [tk["text"] for tk, done, _ in rows if done]
        up = [f"{td.strftime('%a %d %b')}: {tk['text']}" for td, tk in self.upcoming_tasks(7)[:8]]
        return left, done, up

    def _diary_text(self, period):
        today = date.today()
        if period == "today":
            days, label = [today], "today"
        elif period == "yesterday":
            days, label = [today - timedelta(days=1)], "yesterday"
        elif period == "month":
            days, label = [today - timedelta(days=i) for i in range(30)], "the last 30 days"
        else:
            days, label = [today - timedelta(days=i) for i in range(7)], "the last 7 days"
        parts = []
        for d in sorted(days):
            try:
                with open(self.diary_file(d), "r", encoding="utf-8") as f:
                    content = f.read().strip()
            except OSError:
                content = ""
            if content:
                parts.append(f"{d.strftime('%A %d %B')}:\n{content}")
        text = "\n\n".join(parts)
        if len(text) > 7000:  # keep the most recent part (her memory is limited)
            text = "…" + text[-7000:]
        return text, label, len(parts)

    def make_summary(self, kind, arg=None):
        from vega_core import reply
        today = date.today()
        out = None
        if kind in ("news", "tech"):
            items = self.fetch_news(TECH_QUERY if kind == "tech" else arg, 12)
            if not items:
                return reply("I couldn't find any news right now. Check your internet and try again.")
            label = "tech" if kind == "tech" else (f"news about {arg}" if arg else "top news (India and the world)")
            out = {"ask": news_prompt([h for h, _ in items], label)}
        elif kind == "weather":
            today_w = self.weather("weather", "weather")
            try:
                tmr = "\n" + self.weather("weather tomorrow", "weather tomorrow")["text"]
            except Exception:
                tmr = ""
            out = {"ask": f"My weather:\n{today_w['text']}{tmr}\n\n"
                          "Summarize it in 2 or 3 short lines: how today feels, what tomorrow looks like, and one practical tip "
                          "(umbrella, water, light clothes, best time to go out). Only use these numbers."}
        elif kind == "tasks":
            left, done, up = self._task_lines(today)
            rem = self._today_reminders(today)
            study = self._today_study(today)
            if not (left or done or up or rem or study):
                return reply("Nothing on your list today: no tasks, reminders or study lessons.\n"
                             "Add one like:  add submit lab record for tomorrow", "Nothing on your list today.")
            parts = [f"Today is {today.strftime('%A %d %B')}, it's {datetime.now().strftime('%I:%M %p').lstrip('0')} now."]
            parts.append("Tasks still to do: " + ("; ".join(left) if left else "none"))
            if done:
                parts.append("Already done: " + "; ".join(done))
            if rem:
                parts.append("Reminders today: " + "; ".join(rem))
            if study:
                parts.append("Study today: " + "; ".join(study))
            if up:
                parts.append("Coming up this week: " + "; ".join(up))
            out = {"ask": "Here is my list for today:\n" + "\n".join(parts) + "\n\n"
                          "Summarize my day in simple English: 1) how much is left, 2) the most urgent thing first, "
                          "3) a suggested order with rough times so I finish early and sleep on time, 4) one short encouraging line. "
                          "Don't add tasks I don't have."}
        elif kind == "diary":
            text, label, n = self._diary_text(arg or "week")
            if not text:
                return reply(f"Your diary is empty for {label}. Say “open my diary” to write in it.",
                             f"Your diary is empty for {label}.")
            out = {"ask": f"Here is what I wrote in my diary ({label}, {n} day{'s' if n != 1 else ''}):\n\"\"\"\n{text}\n\"\"\"\n\n"
                          "Summarize it for me kindly, in simple English:\n"
                          "- what I did (3 to 5 bullet points)\n"
                          "- how I felt, only if I wrote about it\n"
                          "- one win to be proud of\n"
                          "- one gentle suggestion for tomorrow\n"
                          "Keep it short. Don't invent anything I didn't write."}
        elif kind == "day":
            parts = [f"Today is {today.strftime('%A %d %B')}, it's {datetime.now().strftime('%I:%M %p').lstrip('0')} now."]
            try:
                parts.append("Weather: " + self.weather("weather", "weather")["text"].replace("\n", " "))
            except Exception:
                pass
            left, done, up = self._task_lines(today)
            parts.append("Tasks still to do: " + ("; ".join(left) if left else "none") + (f" (done: {'; '.join(done)})" if done else ""))
            rem = self._today_reminders(today)
            if rem:
                parts.append("Reminders today: " + "; ".join(rem))
            study = self._today_study(today)
            parts.append("Study today: " + ("; ".join(study) if study else "rest day"))
            for title, topic, n in (("Top news", None, 6), ("Tech news", TECH_QUERY, 4)):
                try:
                    heads = [h for h, _ in self.fetch_news(topic, n)]
                except Exception:
                    heads = []
                if heads:
                    parts.append(f"{title} headlines: " + " | ".join(heads))
            out = {"ask": "Here is everything about my day:\n" + "\n".join(parts) + "\n\n"
                          "Give me a short summary of my day in simple English: the weather in one line, what I must do today "
                          "(most urgent first), my study for today, and the 3 most important news points (headlines only, no invented "
                          "details). End with one short motivating line. Keep it under 150 words."}
        if out:
            out["instruction"] = SUMMARY_RULES
            out["model"] = self.smart_model()  # the smart brain, even if a headline or diary line looks like code
        return out
