"""Public album store facade built from smaller store utility modules."""

from __future__ import annotations

from bunkrr.store_utils.models import (
    AlbumItemCounts,
    AlbumMediaItem,
    DownloadStateSummary,
    ManagedAlbum,
    MediaDeleteResult,
    RemovedPolicySummary,
    SyncSummary,
)
from bunkrr.store_utils.operations import (
    apply_removed_item_policy,
    delete_album_media_item,
    delete_managed_album,
    get_album_item_counts_map,
    get_managed_album,
    list_album_media_items,
    list_managed_albums,
    refresh_album_download_state,
    set_managed_album_remove_policy,
    sync_album_items,
    upsert_managed_album,
)

__all__ = [
    "AlbumItemCounts",
    "AlbumMediaItem",
    "DownloadStateSummary",
    "ManagedAlbum",
    "MediaDeleteResult",
    "RemovedPolicySummary",
    "SyncSummary",
    "apply_removed_item_policy",
    "delete_album_media_item",
    "delete_managed_album",
    "get_album_item_counts_map",
    "get_managed_album",
    "list_album_media_items",
    "list_managed_albums",
    "refresh_album_download_state",
    "set_managed_album_remove_policy",
    "sync_album_items",
    "upsert_managed_album",
]
