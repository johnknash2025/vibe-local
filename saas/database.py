"""
vibe-local SaaS — database layer (SQLite)
Users, billing, request logs, service catalog
"""

import sqlite3
import os
import time
import uuid
import json
from datetime import datetime


class Database:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = os.path.join(
                os.path.expanduser("~"), ".local", "state", "vibe-local", "saas.db"
            )
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self.db_path = db_path
        self._init_schema()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self):
        with self._connect() as conn:
            conn.executescript("""
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    api_key TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    email TEXT,
    plan TEXT DEFAULT 'free',
    credits INTEGER DEFAULT 100,
    created_at TEXT DEFAULT (datetime('now')),
    active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS services (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    cost_per_request INTEGER DEFAULT 1,
    enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    service_slug TEXT NOT NULL,
    input_data TEXT,
    output_data TEXT,
    tokens_used INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    created_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    error TEXT,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS billing (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    type TEXT NOT NULL,
    description TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS faq_knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id TEXT NOT NULL,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    tags TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    schedule TEXT NOT NULL,
    service_slug TEXT NOT NULL,
    params TEXT,
    last_run TEXT,
    next_run TEXT,
    enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS system_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric TEXT NOT NULL,
    value REAL,
    recorded_at TEXT DEFAULT (datetime('now'))
);
""")
            self._seed_services(conn)

    def _seed_services(self, conn):
        services = [
            (
                "content-gen",
                "Content Generator",
                "Generate blog posts, articles, marketing copy",
                2,
            ),
            (
                "data-analysis",
                "Data Analysis",
                "Analyze CSV/data and return insights",
                3,
            ),
            ("translate", "Translation", "Translate documents between languages", 1),
            ("faq-bot", "FAQ Chatbot", "Answer questions from your knowledge base", 1),
            ("summarize", "Summarizer", "Summarize long documents or articles", 1),
            (
                "code-review",
                "Code Review",
                "Review code for bugs and best practices",
                2,
            ),
        ]
        for slug, name, desc, cost in services:
            conn.execute(
                "INSERT OR IGNORE INTO services (slug, name, description, cost_per_request) VALUES (?, ?, ?, ?)",
                (slug, name, desc, cost),
            )

    def create_user(self, name, email=None, plan="free", credits=100):
        api_key = f"vl-{uuid.uuid4().hex[:24]}"
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO users (api_key, name, email, plan, credits) VALUES (?, ?, ?, ?, ?)",
                (api_key, name, email, plan, credits),
            )
            conn.commit()
            return {
                "id": cur.lastrowid,
                "api_key": api_key,
                "name": name,
                "plan": plan,
                "credits": credits,
            }

    def get_user_by_key(self, api_key):
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE api_key = ? AND active = 1", (api_key,)
            ).fetchone()
            return dict(row) if row else None

    def deduct_credits(self, user_id, amount, description=""):
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET credits = credits - ? WHERE id = ? AND credits >= ?",
                (amount, user_id, amount),
            )
            if description:
                conn.execute(
                    "INSERT INTO billing (user_id, amount, type, description) VALUES (?, ?, ?, ?)",
                    (user_id, -amount, "usage", description),
                )
            conn.commit()

    def add_credits(self, user_id, amount, description=""):
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET credits = credits + ? WHERE id = ?", (amount, user_id)
            )
            conn.execute(
                "INSERT INTO billing (user_id, amount, type, description) VALUES (?, ?, ?, ?)",
                (user_id, amount, "topup", description),
            )
            conn.commit()

    def log_request(self, user_id, service_slug, input_data=None, status="pending"):
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO requests (user_id, service_slug, input_data, status) VALUES (?, ?, ?, ?)",
                (
                    user_id,
                    service_slug,
                    json.dumps(input_data) if input_data else None,
                    status,
                ),
            )
            conn.commit()
            return cur.lastrowid

    def complete_request(self, req_id, output_data=None, tokens_used=0, error=None):
        with self._connect() as conn:
            if error:
                conn.execute(
                    "UPDATE requests SET status = 'error', error = ?, completed_at = datetime('now') WHERE id = ?",
                    (error, req_id),
                )
            else:
                conn.execute(
                    "UPDATE requests SET status = 'completed', output_data = ?, tokens_used = ?, completed_at = datetime('now') WHERE id = ?",
                    (
                        json.dumps(output_data) if output_data else None,
                        tokens_used,
                        req_id,
                    ),
                )
            conn.commit()

    def get_pending_requests(self, limit=10):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM requests WHERE status = 'pending' ORDER BY created_at ASC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_services(self):
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM services WHERE enabled = 1").fetchall()
            return [dict(r) for r in rows]

    def get_stats(self):
        with self._connect() as conn:
            total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            total_requests = conn.execute("SELECT COUNT(*) FROM requests").fetchone()[0]
            completed = conn.execute(
                "SELECT COUNT(*) FROM requests WHERE status = 'completed'"
            ).fetchone()[0]
            errors = conn.execute(
                "SELECT COUNT(*) FROM requests WHERE status = 'error'"
            ).fetchone()[0]
            total_credits = (
                conn.execute("SELECT SUM(credits) FROM users").fetchone()[0] or 0
            )
            today_requests = conn.execute(
                "SELECT COUNT(*) FROM requests WHERE date(created_at) = date('now')"
            ).fetchone()[0]
            return {
                "total_users": total_users,
                "total_requests": total_requests,
                "completed_requests": completed,
                "error_requests": errors,
                "total_credits_remaining": total_credits,
                "today_requests": today_requests,
            }

    def get_recent_requests(self, limit=20):
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT r.*, u.name as user_name
                   FROM requests r JOIN users u ON r.user_id = u.id
                   ORDER BY r.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_users(self):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM users ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def record_metric(self, metric, value):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO system_metrics (metric, value) VALUES (?, ?)",
                (metric, value),
            )
            conn.commit()

    def add_faq(self, service_id, question, answer, tags=None):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO faq_knowledge (service_id, question, answer, tags) VALUES (?, ?, ?, ?)",
                (service_id, question, answer, json.dumps(tags) if tags else None),
            )
            conn.commit()

    def search_faq(self, service_id, query):
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM faq_knowledge WHERE service_id = ? AND (question LIKE ? OR answer LIKE ?)",
                (service_id, f"%{query}%", f"%{query}%"),
            ).fetchall()
            return [dict(r) for r in rows]
