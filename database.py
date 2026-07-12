"""
database.py
SQLite data layer for the Credit Link-Exchange Bot.

Tables:
    users  -> user_id, username, credit_balance, total_clicks, created_at
    links  -> id, owner_id, url, status ('active' | 'done'), created_at
    clicks -> id, link_id, clicker_id, created_at   (who claimed credit for which link)
"""

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "bot_data.db")


def init_db():
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id         INTEGER PRIMARY KEY,
                username        TEXT,
                credit_balance  INTEGER NOT NULL DEFAULT 0,
                total_clicks    INTEGER NOT NULL DEFAULT 0,
                referred_by     INTEGER,
                total_referrals INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS links (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                owner_id       INTEGER NOT NULL,
                url            TEXT NOT NULL,
                needed_clicks  INTEGER NOT NULL DEFAULT 1,
                current_clicks INTEGER NOT NULL DEFAULT 0,
                status         TEXT NOT NULL DEFAULT 'active',
                created_at     TEXT NOT NULL,
                FOREIGN KEY (owner_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS clicks (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                link_id    INTEGER NOT NULL,
                clicker_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(link_id, clicker_id),
                FOREIGN KEY (link_id) REFERENCES links(id),
                FOREIGN KEY (clicker_id) REFERENCES users(user_id)
            );
            """
        )
    _migrate_add_referral_columns()


def _migrate_add_referral_columns():
    """Safe migration for databases created before the referral feature existed."""
    with get_conn() as conn:
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "referred_by" not in cols:
            conn.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")
        if "total_referrals" not in cols:
            conn.execute(
                "ALTER TABLE users ADD COLUMN total_referrals INTEGER NOT NULL DEFAULT 0"
            )


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def user_exists(user_id: int) -> bool:
    with get_conn() as conn:
        row = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return row is not None


def set_referral(new_user_id: int, referrer_id: int, bonus: int = 5):
    """
    Link new_user_id as having been referred by referrer_id, and reward
    the referrer with `bonus` credits. Only call this once, right when the
    new user is created for the very first time.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET referred_by = ? WHERE user_id = ?",
            (referrer_id, new_user_id),
        )
        conn.execute(
            """
            UPDATE users
            SET credit_balance = credit_balance + ?,
                total_referrals = total_referrals + 1
            WHERE user_id = ?
            """,
            (bonus, referrer_id),
        )


# ---------------------------------------------------------------- users ----

def ensure_user(user_id: int, username: str):
    """Create the user row if it doesn't exist yet (starting credit = 0)."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username, credit_balance, total_clicks, created_at)
            VALUES (?, ?, 0, 0, ?)
            ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
            """,
            (user_id, username or "N/A", datetime.utcnow().isoformat()),
        )


def get_user(user_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None


def add_credit(user_id: int, amount: int = 1):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET credit_balance = credit_balance + ? WHERE user_id = ?",
            (amount, user_id),
        )


def deduct_credit(user_id: int, amount: int = 1):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET credit_balance = credit_balance - ? WHERE user_id = ?",
            (amount, user_id),
        )


def increment_total_clicks(user_id: int):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET total_clicks = total_clicks + 1 WHERE user_id = ?",
            (user_id,),
        )


# ---------------------------------------------------------------- links ----

def create_link(owner_id: int, url: str, needed_clicks: int = 1) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO links (owner_id, url, needed_clicks, current_clicks, status, created_at)
            VALUES (?, ?, ?, 0, 'active', ?)
            """,
            (owner_id, url, needed_clicks, datetime.utcnow().isoformat()),
        )
        return cur.lastrowid


def increment_link_click_and_maybe_complete(link_id: int):
    """
    Bump current_clicks by 1. If current_clicks reaches needed_clicks,
    mark the link 'done' so it disappears from everyone's feed
    (each unique link is only ever shown to needed_clicks different people).
    Returns the updated link row as a dict.
    """
    with get_conn() as conn:
        conn.execute(
            "UPDATE links SET current_clicks = current_clicks + 1 WHERE id = ?", (link_id,)
        )
        row = conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone()
        if row["current_clicks"] >= row["needed_clicks"]:
            conn.execute("UPDATE links SET status = 'done' WHERE id = ?", (link_id,))
        return dict(row)


def get_next_link_for_user(user_id: int):
    """
    Return one random active link that:
      - does not belong to this user
      - this user has not already clicked/claimed
    """
    with get_conn() as conn:
        row = conn.execute(
            """
            SELECT l.* FROM links l
            WHERE l.status = 'active'
              AND l.owner_id != ?
              AND l.id NOT IN (
                    SELECT link_id FROM clicks WHERE clicker_id = ?
              )
            ORDER BY RANDOM()
            LIMIT 1
            """,
            (user_id, user_id),
        ).fetchone()
        return dict(row) if row else None


def get_link(link_id: int):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM links WHERE id = ?", (link_id,)).fetchone()
        return dict(row) if row else None


def mark_link_done(link_id: int):
    with get_conn() as conn:
        conn.execute("UPDATE links SET status = 'done' WHERE id = ?", (link_id,))


# --------------------------------------------------------------- clicks ----

def record_click(link_id: int, clicker_id: int) -> bool:
    """
    Insert a click record. Returns False if this user already claimed this
    link before (UNIQUE constraint), True on success.
    """
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO clicks (link_id, clicker_id, created_at) VALUES (?, ?, ?)",
                (link_id, clicker_id, datetime.utcnow().isoformat()),
            )
            return True
        except sqlite3.IntegrityError:
            return False
