"""Low-level DB and row helpers for album SQLite store."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from bunkrr.utils import get_filename, sanitize

from .models import AlbumMediaItem, ManagedAlbum

DEFAULT_DB_NAME = "albums.db"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_SLUG_RE = re.compile(r"/f/([A-Za-z0-9]+)")
_SIGNATURE_FIELDS = (
    "item_key",
    "slug",
    "original_name",
    "suggested_name",
    "media_type",
    "size_bytes",
    "direct_url",
    "fallback_url",
    "referer_url",
    "cdn_origin",
    "cdn_endpoint",
    "thumbnail_url",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_db_path() -> str:
    env_path = os.environ.get("BUNKR_DB_PATH", "").strip()
    if env_path:
        return os.path.abspath(env_path)
    return str((PROJECT_ROOT / DEFAULT_DB_NAME).resolve())


def _resolve_db_path(db_path: str | None) -> str:
    resolved = os.path.abspath(db_path) if db_path else _default_db_path()
    db_dir = os.path.dirname(resolved)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    return resolved


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row["name"]) for row in rows}


def _ensure_columns(
    conn: sqlite3.Connection, table: str, columns: Mapping[str, str]
) -> None:
    existing = _table_columns(conn, table)
    for col, sql_type in columns.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {sql_type}")


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            album_url TEXT NOT NULL UNIQUE,
            album_name TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_synced_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS album_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            album_id INTEGER NOT NULL,
            item_key TEXT NOT NULL,
            slug TEXT,
            original_name TEXT,
            suggested_name TEXT,
            media_type TEXT,
            size_bytes INTEGER,
            direct_url TEXT,
            fallback_url TEXT,
            referer_url TEXT,
            cdn_origin TEXT,
            cdn_endpoint TEXT,
            thumbnail_url TEXT,
            signature TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            removed_at TEXT,
            is_active INTEGER NOT NULL DEFAULT 1,
            is_downloaded INTEGER NOT NULL DEFAULT 0,
            downloaded_path TEXT,
            downloaded_at TEXT,
            local_missing_at TEXT,
            retained_on_remove INTEGER NOT NULL DEFAULT 0,
            local_deleted_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(album_id, item_key),
            FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS sync_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            album_id INTEGER NOT NULL,
            synced_at TEXT NOT NULL,
            total_items INTEGER NOT NULL,
            added_items INTEGER NOT NULL,
            updated_items INTEGER NOT NULL,
            removed_items INTEGER NOT NULL,
            FOREIGN KEY(album_id) REFERENCES albums(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS managed_albums (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            album_url TEXT NOT NULL UNIQUE,
            album_label TEXT NOT NULL,
            target_folder TEXT NOT NULL,
            delete_local_on_remote_remove INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_album_items_album_id
            ON album_items(album_id);
        CREATE INDEX IF NOT EXISTS idx_album_items_active
            ON album_items(album_id, is_active);
        CREATE INDEX IF NOT EXISTS idx_sync_runs_album_id
            ON sync_runs(album_id, synced_at);
        CREATE INDEX IF NOT EXISTS idx_managed_albums_enabled
            ON managed_albums(enabled, id);
        """
    )

    # Backward-compatible migration for older DBs created before these columns existed.
    _ensure_columns(
        conn,
        "album_items",
        {
            "direct_url": "TEXT",
            "fallback_url": "TEXT",
            "referer_url": "TEXT",
            "is_downloaded": "INTEGER NOT NULL DEFAULT 0",
            "downloaded_path": "TEXT",
            "downloaded_at": "TEXT",
            "local_missing_at": "TEXT",
            "retained_on_remove": "INTEGER NOT NULL DEFAULT 0",
            "local_deleted_at": "TEXT",
        },
    )


def _open_db(db_path: str | None = None) -> tuple[sqlite3.Connection, str]:
    resolved = _resolve_db_path(db_path)
    conn = _connect(resolved)
    _ensure_schema(conn)
    return conn, resolved


def _coerce_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _coerce_int(value: Any) -> int | None:
    try:
        if value is None or value == "":
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_slug(url: str) -> str:
    if not url:
        return ""
    match = _SLUG_RE.search(url)
    return match.group(1) if match else ""


def _normalize_item(raw: Mapping[str, Any]) -> dict[str, Any]:
    fallback_url = _coerce_text(raw.get("fallback_url"))
    direct_url = _coerce_text(raw.get("direct_url"))
    slug = (
        _coerce_text(raw.get("slug"))
        or _extract_slug(fallback_url)
        or _extract_slug(direct_url)
    )
    item_key = _coerce_text(raw.get("item_key")) or slug or fallback_url or direct_url
    return {
        "item_key": item_key,
        "slug": slug,
        "original_name": _coerce_text(raw.get("original_name")),
        "suggested_name": _coerce_text(raw.get("suggested_name")),
        "media_type": _coerce_text(raw.get("media_type")),
        "size_bytes": _coerce_int(raw.get("size_bytes")),
        "direct_url": direct_url,
        "fallback_url": fallback_url,
        "referer_url": _coerce_text(raw.get("referer_url")),
        "cdn_origin": _coerce_text(raw.get("cdn_origin")),
        "cdn_endpoint": _coerce_text(raw.get("cdn_endpoint")),
        "thumbnail_url": _coerce_text(raw.get("thumbnail_url")),
    }


def _item_signature(item: Mapping[str, Any]) -> str:
    payload = {key: item.get(key) for key in _SIGNATURE_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _upsert_album(
    conn: sqlite3.Connection, album_url: str, album_name: str | None, now: str
) -> int:
    safe_name = (album_name or "").strip()
    conn.execute(
        """
        INSERT INTO albums (album_url, album_name, created_at, updated_at, last_synced_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(album_url) DO UPDATE SET
            album_name = CASE
                WHEN excluded.album_name <> '' THEN excluded.album_name
                ELSE albums.album_name
            END,
            updated_at = excluded.updated_at,
            last_synced_at = excluded.last_synced_at
        """,
        (album_url, safe_name, now, now, now),
    )
    row = conn.execute(
        "SELECT id FROM albums WHERE album_url = ? LIMIT 1", (album_url,)
    ).fetchone()
    if not row:
        raise RuntimeError(f"Failed to upsert album row for {album_url}")
    return int(row["id"])


def _get_album_id(conn: sqlite3.Connection, album_url: str) -> int | None:
    row = conn.execute(
        "SELECT id FROM albums WHERE album_url = ? LIMIT 1", (album_url,)
    ).fetchone()
    if not row:
        return None
    return int(row["id"])


def _to_managed_album(row: sqlite3.Row) -> ManagedAlbum:
    return ManagedAlbum(
        id=int(row["id"]),
        album_url=str(row["album_url"]),
        album_label=str(row["album_label"]),
        target_folder=str(row["target_folder"]),
        delete_local_on_remote_remove=bool(int(row["delete_local_on_remote_remove"])),
        enabled=bool(int(row["enabled"])),
    )


def _row_value(item_row: sqlite3.Row | Mapping[str, Any], key: str) -> Any:
    if isinstance(item_row, sqlite3.Row):
        return item_row[key] if key in item_row.keys() else None
    return item_row.get(key)


def _guess_expected_filename(item_row: sqlite3.Row | Mapping[str, Any]) -> str:
    suggested = _coerce_text(_row_value(item_row, "suggested_name")) or _coerce_text(
        _row_value(item_row, "original_name")
    )
    direct_url = _coerce_text(_row_value(item_row, "direct_url"))
    fallback_url = _coerce_text(_row_value(item_row, "fallback_url"))
    base_url = direct_url or fallback_url

    if base_url:
        return get_filename(base_url, suggested or None, {})
    if suggested:
        return sanitize(suggested)
    return ""


def _find_existing_file(
    target_folder: str, expected_name: str, folder_entries: Sequence[str]
) -> str | None:
    if not expected_name:
        return None

    exact = os.path.join(target_folder, expected_name)
    if os.path.exists(exact):
        return os.path.abspath(exact)

    root, ext = os.path.splitext(expected_name)
    pattern = re.compile(re.escape(root) + r" \(\d+\)" + re.escape(ext) + r"$")
    for name in sorted(folder_entries):
        if pattern.match(name):
            return os.path.abspath(os.path.join(target_folder, name))
    return None


def _empty_counts() -> dict[str, int]:
    return {"image": 0, "video": 0, "archive": 0, "other": 0}


def _bucket_media_type(media_type: str) -> str:
    media = media_type.lower().strip()
    if media.startswith("image/"):
        return "image"
    if media.startswith("video/"):
        return "video"
    if any(token in media for token in ("zip", "rar", "7z", "tar", "gzip", "xz")):
        return "archive"
    return "other"


def _to_album_media_item(row: sqlite3.Row) -> AlbumMediaItem:
    suggested_name = _coerce_text(row["suggested_name"])
    original_name = _coerce_text(row["original_name"])
    display = suggested_name or original_name or _coerce_text(row["item_key"])
    media_type = _coerce_text(row["media_type"])
    return AlbumMediaItem(
        id=int(row["id"]),
        item_key=_coerce_text(row["item_key"]),
        display_name=display,
        media_type=media_type,
        category=_bucket_media_type(media_type),
        size_bytes=_coerce_int(row["size_bytes"]),
        is_active=bool(int(row["is_active"])),
        is_downloaded=bool(int(row["is_downloaded"])),
        downloaded_path=_coerce_text(row["downloaded_path"]),
        removed_at=(
            _coerce_text(row["removed_at"]) if row["removed_at"] is not None else None
        ),
        direct_url=_coerce_text(_row_value(row, "direct_url")),
        fallback_url=_coerce_text(_row_value(row, "fallback_url")),
        referer_url=_coerce_text(_row_value(row, "referer_url")),
    )
