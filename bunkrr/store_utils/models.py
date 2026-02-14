"""Data models for album SQLite store."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SyncSummary:
    """Summary of one album sync run."""

    db_path: str
    album_id: int
    total_items: int
    added_items: int
    updated_items: int
    removed_items: int


@dataclass(frozen=True)
class ManagedAlbum:
    """Managed album configuration row."""

    id: int
    album_url: str
    album_label: str
    target_folder: str
    delete_local_on_remote_remove: bool
    enabled: bool


@dataclass(frozen=True)
class AlbumItemCounts:
    """Per-album item type counters."""

    image: int
    video: int
    archive: int
    other: int
    total: int


@dataclass(frozen=True)
class AlbumMediaItem:
    """One media item row for managed album media view."""

    id: int
    item_key: str
    display_name: str
    media_type: str
    category: str
    size_bytes: int | None
    is_active: bool
    is_downloaded: bool
    downloaded_path: str
    removed_at: str | None
    direct_url: str = ""
    fallback_url: str = ""
    referer_url: str = ""


@dataclass(frozen=True)
class MediaDeleteResult:
    """Result for deleting one media item entry."""

    db_deleted: bool
    file_deleted: bool
    message: str


@dataclass(frozen=True)
class DownloadStateSummary:
    """Summary for local file presence refresh."""

    total_items: int
    downloaded_items: int
    missing_items: int


@dataclass(frozen=True)
class RemovedPolicySummary:
    """Summary for removed-item local policy."""

    retained_items: int
    deleted_items: int
    delete_errors: int
