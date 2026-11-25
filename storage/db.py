"""
SQLite database engine for Vega 2.0 with WAL mode, FTS5 search, and automatic JSON migration.
Provides thread-safe connections with busy timeout context managers.
"""

import os
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE, "vega.db")
_LOCAL = threading.local()


def get_db_path():
    return DB_PATH


@contextmanager
def get_db(db_path=None):
    """Context manager for thread-safe SQLite connection with WAL mode and 30s timeout."""
    path = db_path or DB_PATH
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path=None):
    """Initializes tables, indexes, triggers, and FTS5 search."""
    with get_db(db_path) as conn:
        # Settings
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Projects
        conn.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                color TEXT DEFAULT '#4f8cff',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Lab Drills
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lab_drills (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                drill_id TEXT UNIQUE,
                topic TEXT NOT NULL,
                title TEXT NOT NULL,
                broken_content TEXT NOT NULL,
                hints_json TEXT,
                expected_bugs_json TEXT,
                user_solution TEXT,
                score INTEGER DEFAULT 0,
                feedback TEXT,
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Study Flashcards
        conn.execute("""
            CREATE TABLE IF NOT EXISTS study_flashcards (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT DEFAULT 'General',
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                explanation TEXT,
                difficulty TEXT DEFAULT 'medium',
                mastered INTEGER DEFAULT 0,
                review_count INTEGER DEFAULT 0,
                last_reviewed TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Mock Interviews
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mock_interviews (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT UNIQUE,
                job_id INTEGER,
                role TEXT,
                company TEXT,
                track TEXT,
                questions_json TEXT,
                current_question_idx INTEGER DEFAULT 0,
                answers_json TEXT,
                scorecard_json TEXT,
                status TEXT DEFAULT 'in_progress',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Chats
        conn.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                project_id TEXT,
                mode TEXT DEFAULT 'chat',
                created_at TEXT,
                updated_at TEXT,
                FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
            )
        """)

        # Messages
        conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                who TEXT NOT NULL,
                text TEXT NOT NULL,
                mode TEXT DEFAULT 'chat',
                timestamp TEXT,
                raw_json TEXT,
                FOREIGN KEY (chat_id) REFERENCES chats(id) ON DELETE CASCADE
            )
        """)

        # Schema migrations for existing databases
        cursor = conn.execute("PRAGMA table_info(chats);")
        chat_cols = [row["name"] for row in cursor.fetchall()]
        if "mode" not in chat_cols:
            conn.execute("ALTER TABLE chats ADD COLUMN mode TEXT DEFAULT 'chat';")

        cursor = conn.execute("PRAGMA table_info(messages);")
        msg_cols = [row["name"] for row in cursor.fetchall()]
        if "mode" not in msg_cols:
            conn.execute("ALTER TABLE messages ADD COLUMN mode TEXT DEFAULT 'chat';")

        # FTS5 Virtual Table for Message Full-Text Search
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                chat_id UNINDEXED,
                who UNINDEXED,
                text,
                content='messages',
                content_rowid='id'
            )
        """)

        # Triggers to keep FTS5 synchronized with messages
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, chat_id, who, text)
                VALUES (new.id, new.chat_id, new.who, new.text);
            END;
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, chat_id, who, text)
                VALUES('delete', old.id, old.chat_id, old.who, old.text);
            END;
        """)
        conn.execute("""
            CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, chat_id, who, text)
                VALUES('delete', old.id, old.chat_id, old.who, old.text);
                INSERT INTO messages_fts(rowid, chat_id, who, text)
                VALUES (new.id, new.chat_id, new.who, new.text);
            END;
        """)

        # Reminders
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                due_time TEXT,
                due_timestamp REAL,
                recurring TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Lab Log Progress
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lablog_progress (
                track TEXT NOT NULL,
                day INTEGER NOT NULL,
                step INTEGER NOT NULL,
                done INTEGER DEFAULT 0,
                note TEXT DEFAULT '',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (track, day, step)
            )
        """)

        # Contacts for fuzzy lookup & WhatsApp
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                normalized_name TEXT NOT NULL,
                phone TEXT NOT NULL,
                email TEXT DEFAULT '',
                notes TEXT DEFAULT ''
            )
        """)

        # Indexes
        conn.execute("CREATE INDEX IF NOT EXISTS idx_messages_chat_id ON messages(chat_id);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_chats_updated ON chats(updated_at);")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_contacts_name ON contacts(normalized_name);")


def search_messages(query: str, limit: int = 20):
    """Full-text search across all messages using FTS5."""
    with get_db() as conn:
        cursor = conn.execute("""
            SELECT m.id, m.chat_id, m.who, m.text, m.timestamp, c.title as chat_title
            FROM messages_fts f
            JOIN messages m ON f.rowid = m.id
            JOIN chats c ON m.chat_id = c.id
            WHERE messages_fts MATCH ?
            ORDER BY rank
            LIMIT ?
        """, (query, limit))
        return [dict(row) for row in cursor.fetchall()]


def save_chat(chat_id: str, title: str = "New chat", project_id: str = None, mode: str = "chat", created_at: str = None, updated_at: str = None):
    """Upsert chat record in SQLite."""
    now = datetime.now().isoformat()
    c_at = created_at or now
    u_at = updated_at or now
    with get_db() as conn:
        conn.execute("""
            INSERT INTO chats (id, title, project_id, mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                title = excluded.title,
                project_id = coalesce(excluded.project_id, chats.project_id),
                mode = excluded.mode,
                updated_at = excluded.updated_at
        """, (chat_id, title, project_id, mode, c_at, u_at))


def save_message(chat_id: str, who: str, text: str, mode: str = "chat", raw_json: str = None, timestamp: str = None):
    """Persist message in SQLite with assigned mode and updated chat timestamp."""
    ts = timestamp or datetime.now().isoformat()
    with get_db() as conn:
        # Ensure chat exists
        conn.execute("""
            INSERT OR IGNORE INTO chats (id, title, mode, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
        """, (chat_id, "New chat", mode, ts, ts))
        conn.execute("UPDATE chats SET updated_at = ?, mode = ? WHERE id = ?", (ts, mode, chat_id))
        conn.execute("""
            INSERT INTO messages (chat_id, who, text, mode, timestamp, raw_json)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (chat_id, who, text, mode, ts, raw_json))


def get_messages(chat_id: str, limit: int = 200, mode: str = None):
    """Retrieve messages for a given chat."""
    with get_db() as conn:
        if mode:
            cursor = conn.execute("""
                SELECT id, chat_id, who, text, mode, timestamp, raw_json
                FROM messages
                WHERE chat_id = ? AND mode = ?
                ORDER BY id ASC
                LIMIT ?
            """, (chat_id, mode, limit))
        else:
            cursor = conn.execute("""
                SELECT id, chat_id, who, text, mode, timestamp, raw_json
                FROM messages
                WHERE chat_id = ?
                ORDER BY id ASC
                LIMIT ?
            """, (chat_id, limit))
        return [dict(row) for row in cursor.fetchall()]


def migrate_from_json():
    """One-time safe migration from legacy JSON files into SQLite."""
    chats_dir = os.path.join(BASE, "chats")
    migrated_chats = 0
    migrated_reminders = 0

    init_db()

    with get_db() as conn:
        # 1. Migrate chats
        if os.path.isdir(chats_dir):
            for fname in os.listdir(chats_dir):
                if not fname.endswith(".json"):
                    continue
                fpath = os.path.join(chats_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    cid = data.get("id") or fname[:-5]
                    # Check if already migrated
                    exists = conn.execute("SELECT 1 FROM chats WHERE id = ?", (cid,)).fetchone()
                    if exists:
                        continue

                    title = data.get("title") or "Untitled Conversation"
                    project = data.get("project") or None
                    chat_mode = data.get("mode") or "chat"
                    created = data.get("created") or datetime.now().isoformat()
                    updated = data.get("updated") or created

                    conn.execute("""
                        INSERT INTO chats (id, title, project_id, mode, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (cid, title, project, chat_mode, created, updated))

                    for msg in data.get("messages", []):
                        who = msg.get("who") or msg.get("role") or "unknown"
                        text = msg.get("text") or msg.get("content") or ""
                        ts = msg.get("t") or msg.get("timestamp") or created
                        msg_mode = msg.get("mode") or chat_mode
                        conn.execute("""
                            INSERT INTO messages (chat_id, who, text, mode, timestamp, raw_json)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (cid, who, text, msg_mode, ts, json.dumps(msg, ensure_ascii=False)))

                    migrated_chats += 1
                except Exception:
                    pass

        # 2. Migrate reminders
        reminders_file = os.path.join(BASE, "reminders.json")
        if os.path.isfile(reminders_file):
            try:
                with open(reminders_file, "r", encoding="utf-8") as f:
                    rems = json.load(f)
                if isinstance(rems, list):
                    for r in rems:
                        rid = r.get("id") or str(hash(json.dumps(r)))
                        exists = conn.execute("SELECT 1 FROM reminders WHERE id = ?", (rid,)).fetchone()
                        if not exists:
                            conn.execute("""
                                INSERT INTO reminders (id, text, due_time, due_timestamp, recurring, status)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (
                                rid,
                                r.get("text") or r.get("message") or "",
                                r.get("time") or "",
                                r.get("due_timestamp", 0.0),
                                r.get("recurring", ""),
                                r.get("status", "pending")
                            ))
                            migrated_reminders += 1
            except Exception:
                pass

    return {"migrated_chats": migrated_chats, "migrated_reminders": migrated_reminders}

