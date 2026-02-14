"""High-level album store operations."""

from __future__ import annotations

import os
from typing import Any, Mapping, Sequence

from .db import (
    _bucket_media_type,
    _coerce_text,
    _empty_counts,
    _find_existing_file,
    _get_album_id,
    _guess_expected_filename,
    _item_signature,
    _normalize_item,
    _open_db,
    _to_album_media_item,
    _to_managed_album,
    _upsert_album,
    _utc_now,
)
from .models import (
    AlbumItemCounts,
    AlbumMediaItem,
    DownloadStateSummary,
    ManagedAlbum,
    MediaDeleteResult,
    RemovedPolicySummary,
    SyncSummary,
)


def sync_album_items(
    album_url: str,
    album_name: str | None,
    items: Sequence[Mapping[str, Any]],
    db_path: str | None = None,
) -> SyncSummary:
    """
    Sync current album items into SQLite and return diff counts.

    Args:
        album_url (str): Canonical album URL used as stable album key.
        album_name (str | None): Display name from page metadata.
        items (Sequence[Mapping[str, Any]]): Current item snapshot.
        db_path (str | None): Optional path override for SQLite DB.
    """
    deduped: dict[str, dict[str, Any]] = {}
    for raw in items:
        normalized = _normalize_item(raw)
        key = normalized["item_key"]
        if key:
            deduped[key] = normalized
    normalized_items = list(deduped.values())

    now = _utc_now()
    conn, resolved_db = _open_db(db_path)
    try:
        with conn:
            album_id = _upsert_album(conn, album_url, album_name, now)
            existing_rows = conn.execute(
                "SELECT item_key, signature, is_active FROM album_items WHERE album_id = ?",
                (album_id,),
            ).fetchall()
            existing = {str(row["item_key"]): row for row in existing_rows}

            added = 0
            updated = 0
            seen_keys: set[str] = set()

            for item in normalized_items:
                key = item["item_key"]
                seen_keys.add(key)
                signature = _item_signature(item)
                prev = existing.get(key)

                if prev is None:
                    conn.execute(
                        """
                        INSERT INTO album_items (
                            album_id, item_key, slug, original_name, suggested_name,
                            media_type, size_bytes, direct_url, fallback_url, referer_url,
                            cdn_origin, cdn_endpoint, thumbnail_url, signature,
                            first_seen_at, last_seen_at, removed_at, is_active,
                            created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 1, ?, ?)
                        """,
                        (
                            album_id,
                            key,
                            item["slug"],
                            item["original_name"],
                            item["suggested_name"],
                            item["media_type"],
                            item["size_bytes"],
                            item["direct_url"],
                            item["fallback_url"],
                            item["referer_url"],
                            item["cdn_origin"],
                            item["cdn_endpoint"],
                            item["thumbnail_url"],
                            signature,
                            now,
                            now,
                            now,
                            now,
                        ),
                    )
                    added += 1
                    continue

                was_active = int(prev["is_active"]) == 1
                if prev["signature"] != signature or not was_active:
                    updated += 1

                conn.execute(
                    """
                    UPDATE album_items
                    SET slug = ?, original_name = ?, suggested_name = ?,
                        media_type = ?, size_bytes = ?, direct_url = ?, fallback_url = ?,
                        referer_url = ?, cdn_origin = ?, cdn_endpoint = ?,
                        thumbnail_url = ?, signature = ?, last_seen_at = ?,
                        removed_at = NULL, is_active = 1, retained_on_remove = 0,
                        local_deleted_at = NULL, updated_at = ?
                    WHERE album_id = ? AND item_key = ?
                    """,
                    (
                        item["slug"],
                        item["original_name"],
                        item["suggested_name"],
                        item["media_type"],
                        item["size_bytes"],
                        item["direct_url"],
                        item["fallback_url"],
                        item["referer_url"],
                        item["cdn_origin"],
                        item["cdn_endpoint"],
                        item["thumbnail_url"],
                        signature,
                        now,
                        now,
                        album_id,
                        key,
                    ),
                )

            removed_keys = [
                key
                for key, row in existing.items()
                if key not in seen_keys and int(row["is_active"]) == 1
            ]
            if removed_keys:
                conn.executemany(
                    """
                    UPDATE album_items
                    SET is_active = 0, removed_at = ?, retained_on_remove = 0,
                        local_deleted_at = NULL, updated_at = ?
                    WHERE album_id = ? AND item_key = ?
                    """,
                    [(now, now, album_id, key) for key in removed_keys],
                )

            conn.execute(
                """
                INSERT INTO sync_runs (
                    album_id, synced_at, total_items, added_items, updated_items, removed_items
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    album_id,
                    now,
                    len(normalized_items),
                    added,
                    updated,
                    len(removed_keys),
                ),
            )

        return SyncSummary(
            db_path=resolved_db,
            album_id=album_id,
            total_items=len(normalized_items),
            added_items=added,
            updated_items=updated,
            removed_items=len(removed_keys),
        )
    finally:
        conn.close()


def upsert_managed_album(
    album_url: str,
    album_label: str,
    target_folder: str,
    delete_local_on_remote_remove: bool = False,
    enabled: bool = True,
    db_path: str | None = None,
) -> ManagedAlbum:
    """Create/update managed album config by URL."""
    now = _utc_now()
    safe_label = album_label.strip() or album_url
    safe_target = os.path.abspath(target_folder)
    conn, _ = _open_db(db_path)
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO managed_albums (
                    album_url, album_label, target_folder, delete_local_on_remote_remove,
                    enabled, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(album_url) DO UPDATE SET
                    album_label = CASE
                        WHEN excluded.album_label <> '' THEN excluded.album_label
                        ELSE managed_albums.album_label
                    END,
                    target_folder = excluded.target_folder,
                    delete_local_on_remote_remove = excluded.delete_local_on_remote_remove,
                    enabled = excluded.enabled,
                    updated_at = excluded.updated_at
                """,
                (
                    album_url,
                    safe_label,
                    safe_target,
                    1 if delete_local_on_remote_remove else 0,
                    1 if enabled else 0,
                    now,
                    now,
                ),
            )
            row = conn.execute(
                "SELECT * FROM managed_albums WHERE album_url = ? LIMIT 1", (album_url,)
            ).fetchone()
            if not row:
                raise RuntimeError(f"Failed to upsert managed album for {album_url}")
            return _to_managed_album(row)
    finally:
        conn.close()


def list_managed_albums(
    db_path: str | None = None, enabled_only: bool = True
) -> list[ManagedAlbum]:
    """List managed albums ordered by ID."""
    conn, _ = _open_db(db_path)
    try:
        if enabled_only:
            rows = conn.execute(
                "SELECT * FROM managed_albums WHERE enabled = 1 ORDER BY id ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM managed_albums ORDER BY id ASC"
            ).fetchall()
        return [_to_managed_album(row) for row in rows]
    finally:
        conn.close()


def get_album_item_counts_map(
    album_urls: Sequence[str],
    active_only: bool = True,
    db_path: str | None = None,
) -> dict[str, AlbumItemCounts]:
    """Return item type counters per album URL."""
    ordered_urls = [url for url in dict.fromkeys(album_urls) if str(url).strip()]
    if not ordered_urls:
        return {}

    raw_counts: dict[str, dict[str, int]] = {
        url: _empty_counts() for url in ordered_urls
    }
    conn, _ = _open_db(db_path)
    try:
        placeholders = ",".join(["?"] * len(ordered_urls))
        where_active = "AND ai.is_active = 1" if active_only else ""
        rows = conn.execute(
            f"""
            SELECT a.album_url, ai.media_type
            FROM albums a
            JOIN album_items ai ON ai.album_id = a.id
            WHERE a.album_url IN ({placeholders})
              {where_active}
            """,
            tuple(ordered_urls),
        ).fetchall()

        for row in rows:
            album_url = str(row["album_url"])
            if album_url not in raw_counts:
                continue
            bucket = _bucket_media_type(_coerce_text(row["media_type"]))
            raw_counts[album_url][bucket] += 1

        out: dict[str, AlbumItemCounts] = {}
        for album_url, counters in raw_counts.items():
            out[album_url] = AlbumItemCounts(
                image=counters["image"],
                video=counters["video"],
                archive=counters["archive"],
                other=counters["other"],
                total=(
                    counters["image"]
                    + counters["video"]
                    + counters["archive"]
                    + counters["other"]
                ),
            )
        return out
    finally:
        conn.close()


def list_album_media_items(
    album_url: str,
    include_removed: bool = True,
    db_path: str | None = None,
) -> list[AlbumMediaItem]:
    """List media items for one album URL."""
    conn, _ = _open_db(db_path)
    try:
        album_id = _get_album_id(conn, album_url)
        if album_id is None:
            return []

        where_removed = "" if include_removed else "AND is_active = 1"
        rows = conn.execute(
            f"""
            SELECT id, item_key, suggested_name, original_name, media_type,
                   size_bytes, is_active, is_downloaded, downloaded_path, removed_at,
                   direct_url, fallback_url, referer_url
            FROM album_items
            WHERE album_id = ?
              {where_removed}
            ORDER BY id ASC
            """,
            (album_id,),
        ).fetchall()
        return [_to_album_media_item(row) for row in rows]
    finally:
        conn.close()


def delete_album_media_item(
    album_url: str,
    media_item_id: int,
    delete_local_file: bool = False,
    allowed_root: str | None = None,
    db_path: str | None = None,
) -> MediaDeleteResult:
    """
    Delete one media row from DB, optionally deleting local file first.
    """
    conn, _ = _open_db(db_path)
    try:
        album_id = _get_album_id(conn, album_url)
        if album_id is None:
            return MediaDeleteResult(
                db_deleted=False,
                file_deleted=False,
                message="Album not found in DB.",
            )

        row = conn.execute(
            """
            SELECT id, downloaded_path
            FROM album_items
            WHERE album_id = ? AND id = ?
            LIMIT 1
            """,
            (album_id, media_item_id),
        ).fetchone()
        if not row:
            return MediaDeleteResult(
                db_deleted=False,
                file_deleted=False,
                message=f"Media ID {media_item_id} not found for this album.",
            )

        file_deleted = False
        if delete_local_file:
            local_path = _coerce_text(row["downloaded_path"])
            if local_path:
                abs_path = os.path.abspath(local_path)
                if allowed_root:
                    root = os.path.abspath(allowed_root)
                    try:
                        inside_root = os.path.commonpath([abs_path, root]) == root
                    except ValueError:
                        inside_root = False
                    if not inside_root:
                        return MediaDeleteResult(
                            db_deleted=False,
                            file_deleted=False,
                            message=(
                                f"Blocked local delete for media ID {media_item_id}: "
                                "file path is outside allowed album folder."
                            ),
                        )
                if os.path.exists(abs_path):
                    try:
                        os.remove(abs_path)
                        file_deleted = True
                    except OSError as error:
                        return MediaDeleteResult(
                            db_deleted=False,
                            file_deleted=False,
                            message=f"Failed deleting local file: {error}",
                        )

        with conn:
            cur = conn.execute(
                "DELETE FROM album_items WHERE album_id = ? AND id = ?",
                (album_id, media_item_id),
            )
        if cur.rowcount <= 0:
            return MediaDeleteResult(
                db_deleted=False,
                file_deleted=file_deleted,
                message=f"Media ID {media_item_id} was not deleted from DB.",
            )

        if delete_local_file:
            return MediaDeleteResult(
                db_deleted=True,
                file_deleted=file_deleted,
                message=f"Media ID {media_item_id} deleted from DB and local file checked.",
            )
        return MediaDeleteResult(
            db_deleted=True,
            file_deleted=False,
            message=f"Media ID {media_item_id} deleted from DB.",
        )
    finally:
        conn.close()


def get_managed_album(album_id: int, db_path: str | None = None) -> ManagedAlbum | None:
    """Get one managed album by numeric ID."""
    conn, _ = _open_db(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM managed_albums WHERE id = ? LIMIT 1", (album_id,)
        ).fetchone()
        if not row:
            return None
        return _to_managed_album(row)
    finally:
        conn.close()


def delete_managed_album(album_id: int, db_path: str | None = None) -> bool:
    """Remove one managed album config row by ID."""
    conn, _ = _open_db(db_path)
    try:
        with conn:
            cur = conn.execute("DELETE FROM managed_albums WHERE id = ?", (album_id,))
            return cur.rowcount > 0
    finally:
        conn.close()


def set_managed_album_remove_policy(
    album_id: int, delete_local_on_remote_remove: bool, db_path: str | None = None
) -> bool:
    """Toggle local deletion behavior when remote media is removed."""
    conn, _ = _open_db(db_path)
    try:
        with conn:
            cur = conn.execute(
                """
                UPDATE managed_albums
                SET delete_local_on_remote_remove = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    1 if delete_local_on_remote_remove else 0,
                    _utc_now(),
                    album_id,
                ),
            )
            return cur.rowcount > 0
    finally:
        conn.close()


def refresh_album_download_state(
    album_url: str, target_folder: str, db_path: str | None = None
) -> DownloadStateSummary:
    """
    Refresh `is_downloaded` flags in DB by scanning local folder.
    """
    folder = os.path.abspath(target_folder)
    try:
        entries = os.listdir(folder)
    except FileNotFoundError:
        entries = []

    conn, _ = _open_db(db_path)
    try:
        album_id = _get_album_id(conn, album_url)
        if album_id is None:
            return DownloadStateSummary(
                total_items=0, downloaded_items=0, missing_items=0
            )

        rows = conn.execute(
            """
            SELECT id, suggested_name, original_name, direct_url, fallback_url,
                   is_downloaded, downloaded_path
            FROM album_items
            WHERE album_id = ?
            """,
            (album_id,),
        ).fetchall()

        now = _utc_now()
        downloaded = 0
        missing = 0
        with conn:
            for row in rows:
                expected = _guess_expected_filename(row)
                found = _find_existing_file(folder, expected, entries)
                was_downloaded = bool(int(row["is_downloaded"]))

                if found:
                    downloaded += 1
                    conn.execute(
                        """
                        UPDATE album_items
                        SET is_downloaded = 1,
                            downloaded_path = ?,
                            downloaded_at = COALESCE(downloaded_at, ?),
                            local_missing_at = NULL,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (found, now, now, int(row["id"])),
                    )
                else:
                    missing += 1
                    if was_downloaded or row["downloaded_path"]:
                        conn.execute(
                            """
                            UPDATE album_items
                            SET is_downloaded = 0,
                                local_missing_at = COALESCE(local_missing_at, ?),
                                updated_at = ?
                            WHERE id = ?
                            """,
                            (now, now, int(row["id"])),
                        )

        return DownloadStateSummary(
            total_items=len(rows), downloaded_items=downloaded, missing_items=missing
        )
    finally:
        conn.close()


def apply_removed_item_policy(
    album_url: str,
    delete_local_on_remote_remove: bool,
    target_folder: str | None = None,
    db_path: str | None = None,
) -> RemovedPolicySummary:
    """
    Apply removed-item policy to locally downloaded files.

    Default behavior should keep files (`delete_local_on_remote_remove=False`) and flag
    them as retained.
    """
    folder = os.path.abspath(target_folder) if target_folder else ""
    conn, _ = _open_db(db_path)
    try:
        album_id = _get_album_id(conn, album_url)
        if album_id is None:
            return RemovedPolicySummary(
                retained_items=0, deleted_items=0, delete_errors=0
            )

        rows = conn.execute(
            """
            SELECT id, suggested_name, original_name, direct_url, fallback_url, downloaded_path
            FROM album_items
            WHERE album_id = ?
              AND is_active = 0
              AND is_downloaded = 1
              AND local_deleted_at IS NULL
            """,
            (album_id,),
        ).fetchall()
        if not rows:
            return RemovedPolicySummary(
                retained_items=0, deleted_items=0, delete_errors=0
            )

        now = _utc_now()
        retained = 0
        deleted = 0
        errors = 0
        with conn:
            if not delete_local_on_remote_remove:
                for row in rows:
                    retained += 1
                    conn.execute(
                        """
                        UPDATE album_items
                        SET retained_on_remove = 1, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, int(row["id"])),
                    )
                return RemovedPolicySummary(
                    retained_items=retained, deleted_items=0, delete_errors=0
                )

            folder_entries: list[str] = []
            if folder:
                try:
                    folder_entries = os.listdir(folder)
                except FileNotFoundError:
                    folder_entries = []

            for row in rows:
                row_id = int(row["id"])
                candidate = _coerce_text(row["downloaded_path"])
                if not candidate and folder:
                    expected = _guess_expected_filename(row)
                    guessed = _find_existing_file(folder, expected, folder_entries)
                    candidate = guessed or ""

                if not candidate:
                    conn.execute(
                        """
                        UPDATE album_items
                        SET is_downloaded = 0, retained_on_remove = 0,
                            local_deleted_at = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (now, now, row_id),
                    )
                    continue

                abs_path = os.path.abspath(candidate)
                if folder:
                    try:
                        inside_folder = os.path.commonpath([abs_path, folder]) == folder
                    except ValueError:
                        inside_folder = False
                    if not inside_folder:
                        errors += 1
                        continue

                try:
                    if os.path.exists(abs_path):
                        os.remove(abs_path)
                        deleted += 1
                except OSError:
                    errors += 1
                    continue

                conn.execute(
                    """
                    UPDATE album_items
                    SET is_downloaded = 0, retained_on_remove = 0,
                        local_deleted_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (now, now, row_id),
                )

        return RemovedPolicySummary(
            retained_items=retained,
            deleted_items=deleted,
            delete_errors=errors,
        )
    finally:
        conn.close()
