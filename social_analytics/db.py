"""
SQLite persistence layer for historical social media data.

Tables:
  snapshots   - Daily account-level stats (followers, reach, impressions)
  posts       - Individual post records with engagement metrics
"""

import os
import sqlite3
import logging
from contextlib import contextmanager
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("DB_PATH", "social_analytics.db")


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create tables if they don't exist."""
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                platform    TEXT NOT NULL,          -- 'instagram' | 'tiktok'
                captured_at TEXT NOT NULL,          -- ISO-8601 UTC
                followers   INTEGER,
                following   INTEGER,
                post_count  INTEGER,
                impressions INTEGER,
                reach       INTEGER,
                profile_views INTEGER,
                likes_total INTEGER,                -- cumulative (TikTok)
                UNIQUE(platform, captured_at)
            );

            CREATE TABLE IF NOT EXISTS posts (
                id              TEXT NOT NULL,      -- platform post/video ID
                platform        TEXT NOT NULL,
                captured_at     TEXT NOT NULL,      -- when we fetched this row
                posted_at       TEXT,               -- when the content was published
                media_type      TEXT,               -- IMAGE / VIDEO / CAROUSEL / REELS
                caption         TEXT,
                permalink       TEXT,
                likes           INTEGER DEFAULT 0,
                comments        INTEGER DEFAULT 0,
                shares          INTEGER DEFAULT 0,
                saves           INTEGER DEFAULT 0,
                plays           INTEGER DEFAULT 0,  -- video plays / views
                impressions     INTEGER DEFAULT 0,
                reach           INTEGER DEFAULT 0,
                total_interactions INTEGER DEFAULT 0,
                avg_watch_time  REAL DEFAULT 0,
                PRIMARY KEY (id, platform, captured_at)
            );

            CREATE INDEX IF NOT EXISTS idx_posts_platform_posted
                ON posts(platform, posted_at);

            CREATE INDEX IF NOT EXISTS idx_snapshots_platform_captured
                ON snapshots(platform, captured_at);
            """
        )
    logger.info("Database initialised at %s", DB_PATH)


# ------------------------------------------------------------------
# Snapshot helpers
# ------------------------------------------------------------------

def upsert_snapshot(platform: str, data: dict):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO snapshots
                (platform, captured_at, followers, following, post_count,
                 impressions, reach, profile_views, likes_total)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, captured_at) DO UPDATE SET
                followers    = excluded.followers,
                following    = excluded.following,
                post_count   = excluded.post_count,
                impressions  = excluded.impressions,
                reach        = excluded.reach,
                profile_views= excluded.profile_views,
                likes_total  = excluded.likes_total
            """,
            (
                platform,
                now,
                data.get("followers"),
                data.get("following"),
                data.get("post_count"),
                data.get("impressions"),
                data.get("reach"),
                data.get("profile_views"),
                data.get("likes_total"),
            ),
        )


def get_snapshots(platform: str, limit: int = 30) -> list[dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM snapshots WHERE platform=? ORDER BY captured_at DESC LIMIT ?",
            (platform, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ------------------------------------------------------------------
# Post helpers
# ------------------------------------------------------------------

def upsert_posts(platform: str, posts: list[dict]):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        for p in posts:
            ins = p.get("insights", {})
            conn.execute(
                """
                INSERT INTO posts
                    (id, platform, captured_at, posted_at, media_type, caption,
                     permalink, likes, comments, shares, saves, plays,
                     impressions, reach, total_interactions, avg_watch_time)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id, platform, captured_at) DO UPDATE SET
                    likes             = excluded.likes,
                    comments          = excluded.comments,
                    shares            = excluded.shares,
                    saves             = excluded.saves,
                    plays             = excluded.plays,
                    impressions       = excluded.impressions,
                    reach             = excluded.reach,
                    total_interactions= excluded.total_interactions,
                    avg_watch_time    = excluded.avg_watch_time
                """,
                (
                    p.get("id"),
                    platform,
                    now,
                    p.get("posted_at", "").isoformat() if p.get("posted_at") else None,
                    p.get("media_type") or p.get("type"),
                    (p.get("caption") or p.get("title") or "")[:1000],
                    p.get("permalink") or p.get("share_url"),
                    ins.get("likes") or p.get("like_count", 0),
                    ins.get("comments") or p.get("comment_count", 0),
                    ins.get("shares") or p.get("share_count", 0),
                    ins.get("saved", 0),
                    ins.get("plays") or p.get("play_count") or p.get("view_count", 0),
                    ins.get("impressions", 0),
                    ins.get("reach", 0),
                    ins.get("total_interactions") or p.get("total_interactions", 0),
                    ins.get("average_watch_time", 0),
                ),
            )


def get_posts(platform: str, days_back: int = 30) -> list[dict]:
    """Return the most recent snapshot of each post within the window."""
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT p.*
            FROM posts p
            INNER JOIN (
                SELECT id, MAX(captured_at) AS latest
                FROM posts
                WHERE platform = ?
                  AND posted_at >= datetime('now', ? || ' days')
                GROUP BY id
            ) latest ON p.id = latest.id AND p.captured_at = latest.latest
            ORDER BY p.posted_at DESC
            """,
            (platform, f"-{days_back}"),
        ).fetchall()
    return [dict(r) for r in rows]


def purge_old_data(days: int = 365):
    """Delete data older than `days` days to manage DB size."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM snapshots WHERE captured_at < datetime('now', ? || ' days')",
            (f"-{days}",),
        )
        conn.execute(
            "DELETE FROM posts WHERE captured_at < datetime('now', ? || ' days')",
            (f"-{days}",),
        )
    logger.info("Purged data older than %d days", days)
