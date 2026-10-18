"""
src/db/database.py — Async SQLite database layer.

Tables:
  - users         : all tracked usernames + latest bot score + label
  - comments      : every comment observed in real-time (for phrase matching)
  - analysis_log  : full scoring breakdown per analysis run
  - bot_phrases   : persistent phrase list (in addition to data/bot_phrases.py)
"""
import aiosqlite
import asyncio
import json
import os
import time
from pathlib import Path
from typing import Optional

from config.settings import settings


DB_PATH = Path(settings.DB_PATH)


async def init_db() -> None:
    """Create all tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            -- ── Users table ────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS users (
                username        TEXT PRIMARY KEY,
                first_seen      REAL NOT NULL,           -- unix timestamp
                last_analyzed   REAL,
                label           TEXT DEFAULT 'UNKNOWN',  -- HUMAN / SUSPECT / BOT
                bot_score       REAL DEFAULT 0.0,
                score_meta      REAL,
                score_temporal  REAL,
                score_linguistic REAL,
                score_network   REAL,
                account_created REAL,                    -- redditor.created_utc
                comment_karma   INTEGER,
                link_karma      INTEGER,
                is_hidden       INTEGER DEFAULT 0,       -- 1 if history was inaccessible
                analysis_count  INTEGER DEFAULT 0,
                notes           TEXT                     -- JSON blob of extra signals
            );

            -- ── Comments table ──────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS comments (
                id              TEXT PRIMARY KEY,        -- Reddit comment ID
                username        TEXT NOT NULL,
                subreddit       TEXT,
                body            TEXT,
                created_utc     REAL,
                score           INTEGER,
                submission_id   TEXT,
                parent_id       TEXT,
                is_bot_phrase   INTEGER DEFAULT 0,       -- 1 if phrase matched
                matched_phrase  TEXT,
                captured_at     REAL NOT NULL,
                FOREIGN KEY(username) REFERENCES users(username)
            );

            -- ── Analysis log ─────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS analysis_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                username        TEXT NOT NULL,
                analyzed_at     REAL NOT NULL,
                bot_score       REAL,
                label           TEXT,
                score_meta      REAL,
                score_temporal  REAL,
                score_linguistic REAL,
                score_network   REAL,
                detail_json     TEXT,                    -- full breakdown as JSON
                trigger_comment TEXT                     -- comment that triggered this
            );

            -- ── Bot phrases (dynamic, from DB) ───────────────────────────
            CREATE TABLE IF NOT EXISTS bot_phrases (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                phrase          TEXT UNIQUE NOT NULL,
                phrase_type     TEXT NOT NULL,           -- exact / fuzzy / pattern
                added_at        REAL NOT NULL,
                hit_count       INTEGER DEFAULT 0
            );

            -- ── Indexes ───────────────────────────────────────────────────
            CREATE INDEX IF NOT EXISTS idx_users_label ON users(label);
            CREATE INDEX IF NOT EXISTS idx_users_score ON users(bot_score DESC);
            CREATE INDEX IF NOT EXISTS idx_comments_user ON comments(username);
            CREATE INDEX IF NOT EXISTS idx_comments_created ON comments(created_utc);
            CREATE INDEX IF NOT EXISTS idx_analysis_user ON analysis_log(username);
        """)
        await db.commit()


# ── User Operations ────────────────────────────────────────────────────────────

async def upsert_user(username: str, **fields) -> None:
    """Insert or update a user record."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Ensure row exists
        await db.execute(
            "INSERT OR IGNORE INTO users (username, first_seen) VALUES (?, ?)",
            (username, time.time()),
        )
        if fields:
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            values = list(fields.values()) + [username]
            await db.execute(
                f"UPDATE users SET {set_clause} WHERE username = ?", values
            )
        await db.commit()


async def get_user(username: str) -> Optional[dict]:
    """Fetch a user record by username."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def list_bots(limit: int = 50) -> list[dict]:
    """Return the top N users labeled as BOT, ordered by score descending."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT username, bot_score, label, last_analyzed, comment_karma,
                      link_karma, account_created, analysis_count
               FROM users
               WHERE label IN ('BOT', 'SUSPECT')
               ORDER BY bot_score DESC
               LIMIT ?""",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_stats() -> dict:
    """Return aggregate stats about the bot database."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        stats = {}
        for label in ("BOT", "SUSPECT", "HUMAN", "UNKNOWN"):
            async with db.execute(
                "SELECT COUNT(*) as cnt FROM users WHERE label = ?", (label,)
            ) as c:
                row = await c.fetchone()
                stats[label.lower()] = row["cnt"]
        async with db.execute("SELECT COUNT(*) as cnt FROM comments") as c:
            row = await c.fetchone()
            stats["total_comments"] = row["cnt"]
        async with db.execute(
            "SELECT COUNT(*) as cnt FROM comments WHERE is_bot_phrase = 1"
        ) as c:
            row = await c.fetchone()
            stats["phrase_hits"] = row["cnt"]
        return stats


# ── Comment Operations ─────────────────────────────────────────────────────────

async def save_comment(
    comment_id: str,
    username: str,
    subreddit: str,
    body: str,
    created_utc: float,
    score: int,
    submission_id: str = "",
    parent_id: str = "",
    is_bot_phrase: bool = False,
    matched_phrase: Optional[str] = None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR IGNORE INTO comments
               (id, username, subreddit, body, created_utc, score,
                submission_id, parent_id, is_bot_phrase, matched_phrase, captured_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                comment_id, username, subreddit, body, created_utc,
                score, submission_id, parent_id, int(is_bot_phrase), matched_phrase, time.time(),
            ),
        )
        await db.commit()


async def get_user_comments(username: str, limit: int = 200) -> list[dict]:
    """Fetch all stored comments for a user from our DB."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM comments WHERE username = ?
               ORDER BY created_utc DESC LIMIT ?""",
            (username, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


# ── Analysis Log ───────────────────────────────────────────────────────────────

async def log_analysis(
    username: str,
    bot_score: float,
    label: str,
    scores: dict,
    detail: dict,
    trigger_comment: Optional[str] = None,
) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT INTO analysis_log
               (username, analyzed_at, bot_score, label,
                score_meta, score_temporal, score_linguistic, score_network,
                detail_json, trigger_comment)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                username, time.time(), bot_score, label,
                scores.get("meta", 0), scores.get("temporal", 0),
                scores.get("linguistic", 0), scores.get("network", 0),
                json.dumps(detail), trigger_comment,
            ),
        )
        await db.commit()


# ── Phrase Operations ──────────────────────────────────────────────────────────

async def add_phrase(phrase: str, phrase_type: str = "exact") -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO bot_phrases (phrase, phrase_type, added_at) VALUES (?, ?, ?)",
            (phrase.lower().strip(), phrase_type, time.time()),
        )
        await db.commit()


async def get_db_phrases() -> list[dict]:
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM bot_phrases") as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def increment_phrase_hit(phrase_id: int) -> None:
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE bot_phrases SET hit_count = hit_count + 1 WHERE id = ?",
            (phrase_id,),
        )
        await db.commit()

