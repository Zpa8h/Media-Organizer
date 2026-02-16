"""Data models and SQLite persistence layer."""

import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = os.environ.get("ORGANIZER_DB", "organizer.db")


class Classification(Enum):
    UNCLASSIFIED = "unclassified"
    KIDS = "kids"
    ADULTS = "adults"
    SKIP = "skip"


@dataclass
class Show:
    jellyfin_id: str
    name: str
    sort_name: str = ""
    year: Optional[int] = None
    official_rating: str = ""
    genres: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    overview: str = ""
    community_rating: Optional[float] = None
    path: str = ""
    image_tag: str = ""
    classification: Classification = Classification.UNCLASSIFIED
    episode_count: int = 0
    season_count: int = 0

    @property
    def folder_name(self) -> str:
        name = sanitize_filename(self.name)
        if self.year:
            return f"{name} ({self.year})"
        return name


@dataclass
class Episode:
    jellyfin_id: str
    show_id: str
    show_name: str
    season_number: int
    episode_number: int
    name: str = ""
    path: str = ""
    container: str = ""
    year: Optional[int] = None

    @property
    def filename(self) -> str:
        show = sanitize_filename(self.show_name)
        year_part = f" ({self.year})" if self.year else ""
        ep_part = f"S{self.season_number:02d}E{self.episode_number:02d}"
        name_part = f" - {sanitize_filename(self.name)}" if self.name else ""
        ext = Path(self.path).suffix if self.path else f".{self.container}"
        return f"{show}{year_part} - {ep_part}{name_part}{ext}"


def sanitize_filename(name: str) -> str:
    """Remove characters that are illegal in filenames."""
    illegal = '<>:"/\\|?*'
    result = name
    for ch in illegal:
        result = result.replace(ch, "")
    # Collapse multiple spaces
    while "  " in result:
        result = result.replace("  ", " ")
    return result.strip()


class Database:
    """SQLite persistence for classifications and cached data."""

    def __init__(self, db_path: str = ""):
        self.db_path = db_path or DB_PATH
        self._init_db()

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS shows (
                    jellyfin_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    sort_name TEXT DEFAULT '',
                    year INTEGER,
                    official_rating TEXT DEFAULT '',
                    genres TEXT DEFAULT '[]',
                    tags TEXT DEFAULT '[]',
                    overview TEXT DEFAULT '',
                    community_rating REAL,
                    path TEXT DEFAULT '',
                    image_tag TEXT DEFAULT '',
                    classification TEXT DEFAULT 'unclassified',
                    episode_count INTEGER DEFAULT 0,
                    season_count INTEGER DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS episodes (
                    jellyfin_id TEXT PRIMARY KEY,
                    show_id TEXT NOT NULL,
                    show_name TEXT NOT NULL,
                    season_number INTEGER NOT NULL,
                    episode_number INTEGER NOT NULL,
                    name TEXT DEFAULT '',
                    path TEXT DEFAULT '',
                    container TEXT DEFAULT '',
                    year INTEGER,
                    FOREIGN KEY (show_id) REFERENCES shows(jellyfin_id)
                );
                CREATE TABLE IF NOT EXISTS move_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT DEFAULT (datetime('now')),
                    source_path TEXT NOT NULL,
                    dest_path TEXT NOT NULL,
                    show_id TEXT,
                    episode_id TEXT,
                    status TEXT DEFAULT 'planned'
                );
                CREATE TABLE IF NOT EXISTS cache_meta (
                    key TEXT PRIMARY KEY,
                    value TEXT,
                    updated_at TEXT DEFAULT (datetime('now'))
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def upsert_show(self, show: Show):
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO shows (jellyfin_id, name, sort_name, year, official_rating,
                    genres, tags, overview, community_rating, path, image_tag,
                    classification, episode_count, season_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jellyfin_id) DO UPDATE SET
                    name=excluded.name, sort_name=excluded.sort_name, year=excluded.year,
                    official_rating=excluded.official_rating, genres=excluded.genres,
                    tags=excluded.tags, overview=excluded.overview,
                    community_rating=excluded.community_rating, path=excluded.path,
                    image_tag=excluded.image_tag,
                    episode_count=excluded.episode_count, season_count=excluded.season_count
            """, (show.jellyfin_id, show.name, show.sort_name, show.year,
                  show.official_rating, json.dumps(show.genres), json.dumps(show.tags),
                  show.overview, show.community_rating, show.path, show.image_tag,
                  show.classification.value, show.episode_count, show.season_count))

    def upsert_episode(self, ep: Episode):
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO episodes (jellyfin_id, show_id, show_name, season_number,
                    episode_number, name, path, container, year)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(jellyfin_id) DO UPDATE SET
                    show_id=excluded.show_id, show_name=excluded.show_name,
                    season_number=excluded.season_number, episode_number=excluded.episode_number,
                    name=excluded.name, path=excluded.path, container=excluded.container,
                    year=excluded.year
            """, (ep.jellyfin_id, ep.show_id, ep.show_name, ep.season_number,
                  ep.episode_number, ep.name, ep.path, ep.container, ep.year))

    def get_show(self, jellyfin_id: str) -> Optional[Show]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM shows WHERE jellyfin_id = ?", (jellyfin_id,)).fetchone()
            if not row:
                return None
            return self._row_to_show(row)

    def get_all_shows(self) -> list[Show]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM shows ORDER BY sort_name, name").fetchall()
            return [self._row_to_show(r) for r in rows]

    def get_episodes_for_show(self, show_id: str) -> list[Episode]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM episodes WHERE show_id = ? ORDER BY season_number, episode_number",
                (show_id,)
            ).fetchall()
            return [self._row_to_episode(r) for r in rows]

    def get_all_episodes(self) -> list[Episode]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM episodes ORDER BY show_name, season_number, episode_number"
            ).fetchall()
            return [self._row_to_episode(r) for r in rows]

    def set_classification(self, jellyfin_id: str, classification: Classification):
        with self._connect() as conn:
            conn.execute("UPDATE shows SET classification = ? WHERE jellyfin_id = ?",
                         (classification.value, jellyfin_id))

    def bulk_set_classification(self, jellyfin_ids: list[str], classification: Classification):
        with self._connect() as conn:
            placeholders = ",".join("?" for _ in jellyfin_ids)
            conn.execute(
                f"UPDATE shows SET classification = ? WHERE jellyfin_id IN ({placeholders})",
                [classification.value] + jellyfin_ids
            )

    def classify_by_rating(self, ratings: list[str], classification: Classification) -> int:
        with self._connect() as conn:
            placeholders = ",".join("?" for _ in ratings)
            cursor = conn.execute(
                f"UPDATE shows SET classification = ? WHERE official_rating IN ({placeholders}) "
                f"AND classification = 'unclassified'",
                [classification.value] + ratings
            )
            return cursor.rowcount

    def classify_by_genre(self, genre: str, classification: Classification) -> int:
        with self._connect() as conn:
            pattern = f'%"{genre}"%'
            cursor = conn.execute(
                "UPDATE shows SET classification = ? WHERE genres LIKE ? "
                "AND classification = 'unclassified'",
                (classification.value, pattern)
            )
            return cursor.rowcount

    def get_classification_stats(self) -> dict:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT classification, COUNT(*) as cnt FROM shows GROUP BY classification"
            ).fetchall()
            stats = {c.value: 0 for c in Classification}
            for row in rows:
                stats[row["classification"]] = row["cnt"]
            stats["total"] = sum(stats.values())
            return stats

    def log_move(self, source: str, dest: str, show_id: str = "", episode_id: str = "",
                 status: str = "planned") -> int:
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO move_log (source_path, dest_path, show_id, episode_id, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (source, dest, show_id, episode_id, status)
            )
            return cursor.lastrowid

    def update_move_status(self, move_id: int, status: str):
        with self._connect() as conn:
            conn.execute("UPDATE move_log SET status = ? WHERE id = ?", (status, move_id))

    def get_move_log(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM move_log ORDER BY id").fetchall()
            return [dict(r) for r in rows]

    def set_cache_meta(self, key: str, value: str):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO cache_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
                (key, value)
            )

    def get_cache_meta(self, key: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM cache_meta WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else None

    @staticmethod
    def _row_to_show(row: sqlite3.Row) -> Show:
        return Show(
            jellyfin_id=row["jellyfin_id"],
            name=row["name"],
            sort_name=row["sort_name"] or "",
            year=row["year"],
            official_rating=row["official_rating"] or "",
            genres=json.loads(row["genres"]) if row["genres"] else [],
            tags=json.loads(row["tags"]) if row["tags"] else [],
            overview=row["overview"] or "",
            community_rating=row["community_rating"],
            path=row["path"] or "",
            image_tag=row["image_tag"] or "",
            classification=Classification(row["classification"]),
            episode_count=row["episode_count"] or 0,
            season_count=row["season_count"] or 0,
        )

    @staticmethod
    def _row_to_episode(row: sqlite3.Row) -> Episode:
        return Episode(
            jellyfin_id=row["jellyfin_id"],
            show_id=row["show_id"],
            show_name=row["show_name"],
            season_number=row["season_number"],
            episode_number=row["episode_number"],
            name=row["name"] or "",
            path=row["path"] or "",
            container=row["container"] or "",
            year=row["year"],
        )
