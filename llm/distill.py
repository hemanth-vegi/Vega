"""
Knowledge Distillation Dataset Generator for Vega 2.0.
Exports synthetic and historical conversation pairs in ChatML / ShareGPT format
into vega_training.jsonl for local QLoRA / Unsloth fine-tuning.
"""

import os
import json
from datetime import datetime
from storage.db import get_db, init_db

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_PATH = os.path.join(BASE, "vega_training.jsonl")

SYSTEM_PROMPT = (
    "You are Vega, a fast, intelligent, and helpful Windows desktop assistant, "
    "DevOps engineering companion, and study mentor. You give concise, precise, "
    "and actionable responses."
)


def export_chatml_dataset(output_file: str = None, min_messages: int = 2) -> int:
    """Exports saved conversations from SQLite into ChatML formatted jsonl."""
    init_db()
    out = output_file or OUTPUT_PATH
    count = 0

    with get_db() as conn:
        chats = conn.execute("SELECT id, title FROM chats ORDER BY updated_at DESC").fetchall()
        with open(out, "w", encoding="utf-8") as f:
            for chat in chats:
                cid = chat["id"]
                msgs = conn.execute(
                    "SELECT who, text FROM messages WHERE chat_id = ? ORDER BY id ASC",
                    (cid,)
                ).fetchall()

                if len(msgs) < min_messages:
                    continue

                conversation = [
                    {"role": "system", "content": SYSTEM_PROMPT}
                ]

                for m in msgs:
                    role = "user" if m["who"] in ("you", "user") else "assistant"
                    text = m["text"].strip()
                    if text:
                        conversation.append({"role": role, "content": text})

                # Write line in ChatML/ShareGPT standard format
                record = {
                    "id": cid,
                    "title": chat["title"],
                    "timestamp": datetime.now().isoformat(),
                    "messages": conversation
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1

    return count


if __name__ == "__main__":
    total = export_chatml_dataset()
    print(f"Distillation dataset generated: {total} conversations exported to {OUTPUT_PATH}")

