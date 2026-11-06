"""
Hands-Free Voice Control for Vega 2.0 Lab Log Curriculum Engine.
Allows marking steps, appending lab notes, and summarizing study progress.
Broadcasts SSE lablog_updated events for zero-refresh UI updates.
"""

from datetime import datetime
from tools.registry import tools
from core.events import broadcast
from storage.db import get_db, init_db


@tools.register(
    name="lablog_mark_step",
    description="Marks a step in today's Lab Log study curriculum as done or undone.",
    parameters={
        "type": "object",
        "properties": {
            "step_num": {"type": "integer", "description": "1-based step number to check/uncheck"},
            "done": {"type": "boolean", "description": "True if step is completed, False to unmark", "default": True},
            "track": {"type": "string", "enum": ["linux", "python", "c"], "default": "linux", "description": "Study curriculum track"}
        },
        "required": ["step_num"]
    }
)
def lablog_mark_step(step_num: int, done: bool = True, track: str = "linux") -> str:
    from vega_core import read_json, write_json
    # Read legacy lablog.json
    d = read_json("lablog.json", None)
    if not isinstance(d, dict) or not isinstance(d.get("days"), dict):
        d = {"days": {}}

    # We determine today's day index
    prog = read_json("progress.json", {})
    day_idx = str(prog.get("day", 1))

    entry = d["days"].get(day_idx, {})
    ticked = set(entry.get("ticked", []))
    if done:
        ticked.add(step_num)
    else:
        ticked.discard(step_num)
    entry["ticked"] = sorted(list(ticked))
    d["days"][day_idx] = entry
    write_json("lablog.json", d)

    # Sync with SQLite
    init_db()
    with get_db() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO lablog_progress (track, day, step, done, updated_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (track, int(day_idx), step_num, 1 if done else 0))

    # Real-time SSE notification
    broadcast("lablog_updated", {"track": track, "day": int(day_idx), "step": step_num, "done": done})

    state_str = "completed" if done else "unmarked"
    return f"Step {step_num} of Day {day_idx} ({track}) is now {state_str}."


@tools.register(
    name="lablog_add_note",
    description="Appends a hands-free study observation or command execution note to today's Lab Log.",
    parameters={
        "type": "object",
        "properties": {
            "note_text": {"type": "string", "description": "Observation, command, or explanation to add to the log"},
            "track": {"type": "string", "enum": ["linux", "python", "c"], "default": "linux"}
        },
        "required": ["note_text"]
    }
)
def lablog_add_note(note_text: str, track: str = "linux") -> str:
    from vega_core import read_json, write_json
    d = read_json("lablog.json", None)
    if not isinstance(d, dict) or not isinstance(d.get("days"), dict):
        d = {"days": {}}

    prog = read_json("progress.json", {})
    day_idx = str(prog.get("day", 1))

    entry = d["days"].get(day_idx, {})
    stamp = datetime.now().strftime("%I:%M %p").lstrip("0")
    existing_notes = entry.get("notes", "")
    new_note_entry = f"[{stamp}] {note_text.strip()}"
    entry["notes"] = (existing_notes.rstrip() + "\n" if existing_notes.strip() else "") + new_note_entry
    entry.pop("review", None)
    entry.pop("score", None)
    d["days"][day_idx] = entry
    write_json("lablog.json", d)

    # Sync with SQLite
    init_db()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO lablog_progress (track, day, step, done, note, updated_at)
            VALUES (?, ?, 0, 1, ?, CURRENT_TIMESTAMP)
        """, (track, int(day_idx), new_note_entry))

    broadcast("lablog_updated", {"track": track, "day": int(day_idx), "note": new_note_entry})
    return f"Added note to Day {day_idx} Lab Log: '{note_text}'."


@tools.register(
    name="lablog_get_summary",
    description="Returns today's Lab Log status, completed steps, and recent notes.",
    parameters={
        "type": "object",
        "properties": {
            "track": {"type": "string", "enum": ["linux", "python", "c"], "default": "linux"}
        },
        "required": []
    }
)
def lablog_get_summary(track: str = "linux") -> str:
    from vega_core import read_json
    prog = read_json("progress.json", {})
    day_idx = str(prog.get("day", 1))
    d = read_json("lablog.json", {}).get("days", {}).get(day_idx, {})

    ticked = d.get("ticked", [])
    notes = d.get("notes", "No notes recorded yet today.")
    score = d.get("score")
    score_str = f"Score: {score}/10" if score is not None else "Not yet scored"

    return f"Lab Log Day {day_idx} ({track.capitalize()}):\n- Completed steps: {len(ticked)}\n- Assessment: {score_str}\n- Notes:\n{notes}"

