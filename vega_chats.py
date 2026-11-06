"""
Vega's chats and projects, like ChatGPT / Claude: every conversation is saved as its own chat,
and chats can live inside projects (with the project's own notes for Vega).

chats/<id>.json   {"id", "title", "project", "created", "updated", "messages": [{"who", "text", "t"}]}
projects.json     [{"id", "name", "notes"}]
All on this laptop only.
"""

import sys
if hasattr(sys, "stdout") and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys, "stderr") and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import json
import os
import re
import secrets
from datetime import datetime

from vega_core import here, read_json, write_json, write_json_file


def _now():
    return datetime.now().isoformat(timespec="seconds")


class ChatStore:
    def __init__(self):
        self.folder = here("chats")
        os.makedirs(self.folder, exist_ok=True)
        self._migrate_old_log()

    # ---------------- chats
    def path(self, cid):
        return os.path.join(self.folder, re.sub(r"[^a-z0-9]", "", cid) + ".json")

    def new(self, project=None):
        return {"id": datetime.now().strftime("%Y%m%d%H%M%S") + secrets.token_hex(2), "title": "New chat",
                "project": project, "created": _now(), "updated": _now(), "messages": []}

    def save(self, chat, force=False):
        if not chat.get("messages") and not force and chat.get("title") == "New chat" and not chat.get("pinned"):
            return  # untouched empty chats aren't kept
        chat["updated"] = _now()
        write_json_file(self.path(chat["id"]), chat)

    def load(self, cid):
        try:
            with open(self.path(cid), encoding="utf-8") as f:
                c = json.load(f)
            c.setdefault("messages", [])
            return c
        except (OSError, ValueError):
            return None

    def delete(self, cid):
        try:
            os.remove(self.path(cid))
        except OSError:
            pass

    def all(self, project="*"):
        """Newest first. project='*' = every chat, None = chats outside projects, or a project id."""
        out = []
        for f in os.listdir(self.folder):
            if not f.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.folder, f), encoding="utf-8") as fh:
                    c = json.load(fh)
            except (OSError, ValueError):
                continue
            if project != "*" and c.get("project") != project:
                continue
            out.append({"id": c.get("id"), "title": c.get("title") or "Chat", "project": c.get("project"),
                        "pinned": bool(c.get("pinned")),
                        "updated": c.get("updated") or ""})
        return sorted(out, key=lambda c: c["updated"], reverse=True)

    def search(self, q):
        """Chats whose title or messages contain the words."""
        words = [w for w in q.lower().split() if w]
        out = []
        for f in os.listdir(self.folder):
            if not f.endswith(".json"):
                continue
            try:
                with open(os.path.join(self.folder, f), encoding="utf-8") as fh:
                    c = json.load(fh)
            except (OSError, ValueError):
                continue
            blob = (c.get("title", "") + " " + " ".join(m.get("text", "") for m in c.get("messages", []))).lower()
            if all(w in blob for w in words):
                out.append({"id": c.get("id"), "title": c.get("title") or "Chat", "updated": c.get("updated") or ""})
        return sorted(out, key=lambda c: c["updated"], reverse=True)

    @staticmethod
    def title_from(text):
        t = re.sub(r"\s+", " ", re.sub(r"^(hey|hi|ok|okay)?[\s,]*(vega)?[\s,.!]*", "", text.strip(), flags=re.I)).strip()
        if len(t.split()) < 2:  # "hi there" -> keep the whole thing
            t = re.sub(r"\s+", " ", text.strip())
        t = t[:1].upper() + t[1:] if t else "Chat"
        return t if len(t) <= 38 else t[:36].rsplit(" ", 1)[0] + "…"

    def add(self, chat, who, text):
        chat["messages"].append({"who": who, "text": text, "t": _now()})
        if chat["title"] == "New chat" and who in ("you", "phone"):
            chat["title"] = self.title_from(text)
        elif chat["title"] == "New chat" and who == "vega" and text.startswith("Good morning"):
            chat["title"] = "Morning briefing, " + datetime.now().strftime("%d %b")
        self.save(chat)

    # ---------------- projects
    def projects(self):
        p = read_json("projects.json", [])
        return p if isinstance(p, list) else []

    def project(self, pid):
        return next((p for p in self.projects() if p.get("id") == pid), None)

    def add_project(self, name, notes=""):
        ps = self.projects()
        p = {"id": "p" + secrets.token_hex(3), "name": name.strip()[:40], "notes": notes.strip()[:2000]}
        ps.append(p)
        write_json("projects.json", ps)
        return p

    def update_project(self, pid, **fields):
        ps = self.projects()
        for p in ps:
            if p.get("id") == pid:
                p.update(fields)
        write_json("projects.json", ps)

    def pinned_projects_first(self):
        ps = self.projects()
        return [p for p in ps if p.get("pinned")] + [p for p in ps if not p.get("pinned")]

    def set_pinned(self, cid, on):
        c = self.load(cid)
        if c:
            c["pinned"] = bool(on)
            c["updated"] = c.get("updated") or _now()
            write_json_file(self.path(cid), c)  # pinning doesn't move it to the top of "recent"

    # ---------------- project files (like Claude's project knowledge)
    def project_folder(self, pid):
        f = here("projects", re.sub(r"[^a-z0-9]", "", pid))
        os.makedirs(f, exist_ok=True)
        return f

    def project_files(self, pid):
        f = self.project_folder(pid)
        return sorted(x for x in os.listdir(f) if os.path.isfile(os.path.join(f, x)))

    def add_project_file(self, pid, path):
        import shutil
        dest = os.path.join(self.project_folder(pid), os.path.basename(path))
        shutil.copy2(path, dest)
        return os.path.basename(dest)

    def remove_project_file(self, pid, name):
        try:
            os.remove(os.path.join(self.project_folder(pid), os.path.basename(name)))
        except OSError:
            pass

    def delete_project(self, pid):
        write_json("projects.json", [p for p in self.projects() if p.get("id") != pid])
        for c in self.all(pid):  # its chats move out of the project, they aren't deleted
            full = self.load(c["id"])
            if full:
                full["project"] = None
                self.save(full)

    # ---------------- first run: bring in the old single chat log
    def _migrate_old_log(self):
        if os.listdir(self.folder):
            return
        path = here("chat_log.jsonl")
        if not os.path.exists(path):
            return
        msgs = []
        try:
            with open(path, encoding="utf-8") as f:
                for line in f.readlines()[-400:]:
                    try:
                        m = json.loads(line)
                        if m.get("text"):
                            msgs.append({"who": m.get("who", "vega"), "text": m["text"], "t": m.get("t", "")})
                    except ValueError:
                        pass
        except OSError:
            return
        if msgs:
            c = self.new()
            c["title"] = "Earlier chats"
            c["messages"] = msgs
            self.save(c)
