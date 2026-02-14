"""This module contains the function to download images from bunkrr albums."""

# pylint: disable=broad-exception-caught,line-too-long,too-many-lines

import os
import re
import sys
from typing import List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit

from aiohttp import ClientSession

from bunkrr.banner import render_main_menu_banner
from bunkrr.data_processing import (
    MAX_CONCURRENT_DOWNLOADS,
    download_images_from_urls,
    fetch_data,
)
from bunkrr.store import (
    AlbumItemCounts,
    AlbumMediaItem,
    ManagedAlbum,
    MediaDeleteResult,
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
from bunkrr.utils import create_download_folder, get_user_folder, sanitize

ENABLE_SYNC_DB = os.environ.get("BUNKR_SYNC_DB", "1").lower() not in {
    "0",
    "false",
    "no",
    "off",
}
ENABLE_CLEAR_SCREEN = os.environ.get("BUNKR_CLEAR_SCREEN", "1").lower() not in {
    "0",
    "false",
    "no",
    "off",
}
ENABLE_PAUSE_ON_REFRESH = os.environ.get("BUNKR_PAUSE_ON_REFRESH", "1").lower() not in {
    "0",
    "false",
    "no",
    "off",
}
DEFAULT_MANAGED_FOLDER_ROOT = os.path.join(os.getcwd(), "downloads")


class UserAbortError(Exception):
    """Raised when user aborts interaction (Ctrl+C / EOF)."""


async def fetch_album_data(
    session: ClientSession,
    url: str,
    announce_prefix: str | None = "Downloading file(s) from album",
) -> Tuple[Optional[str], Optional[list]]:
    """
    Fetch album name and image data from the given URL.

    Args:
        session (ClientSession): The active HTTP client session.
        url (str): The album URL to fetch data from.

    Returns:
        Tuple[Optional[str], Optional[list]]:
            - album_name (str or None): The name of the album, or None if not found.
            - image_data (list or None): List of image data elements, or None if not found.
    """
    album_name = await fetch_data(session, url, "album-name")
    if album_name and announce_prefix:
        print(f"\n[*] {announce_prefix}: {album_name}")
    image_data = await fetch_data(session, url, "image-url")
    return album_name, image_data


def is_single_file_url(url: str) -> bool:
    """Detect bunkr single file URLs (/f/, /i/, /v/)."""
    return bool(re.search(r"/(f|i|v)/[A-Za-z0-9]+", url))


def build_download_urls(image_data: list, base_url: str) -> List[Tuple[str, ...]]:
    """
    Build a list of tuples containing full image URLs and suggested filenames.

    Args:
        image_data (list): List of HTML elements containing image info.

    Returns:
        List[Tuple[str, ...]]: Each tuple contains:
            - url (str): The full URL to the image.
            - suggested_name (str): The suggested filename for saving the image.
            - referer (str): Optional referer header to use.
            - fallback_url (str): Optional /f/<slug> URL for fallback resolution.
    """
    urls: List[Tuple[str, ...]] = []
    seen: set[str] = set()
    base_parts = urlsplit(base_url)
    base_origin = f"{base_parts.scheme}://{base_parts.netloc}"
    for data in image_data:
        if isinstance(data, dict) and "slug" in data:
            slug = data.get("slug")
            if not slug:
                continue
            origin = data.get("origin") or base_origin
            nice = str(data.get("original") or data.get("name") or "")
            cdn_origin = data.get("cdn_origin")
            cdn_endpoint = data.get("cdn_endpoint")
            referer = data.get("referer") or base_url
            fallback_url = urljoin(origin, f"/f/{slug}")
            if cdn_origin and cdn_endpoint:
                direct = urljoin(str(cdn_origin), str(cdn_endpoint))
            else:
                direct = fallback_url

            key = f"{direct}|{nice}"
            if key not in seen:
                seen.add(key)
                urls.append((direct, nice, referer, fallback_url))
            continue
        # Prefer the closest ancestor anchor; avoids picking pagination links
        href = None
        # Most layouts: the text box <div> sits next to an <a> (thumbnail link)
        a_sibling = data.find_previous_sibling("a", href=True)
        if a_sibling and a_sibling.get("href"):
            href = a_sibling.get("href")
        elif data.parent:
            # fallback: an <a> inside the same parent card
            a_in_parent = data.parent.find("a", href=True)
            if a_in_parent and a_in_parent.get("href"):
                href = a_in_parent.get("href")

        title_tag = data.find("p")
        nice = title_tag.text.strip() if title_tag else ""

        if href:
            # Normalize to absolute
            if href.startswith("?"):
                href = None
            else:
                href = urljoin(base_url, href)

        # Only keep item pages like /f/<id>, /i/<id>, /v/<id>
        if href and not re.search(r"/(f|i|v)/[A-Za-z0-9]+", href):
            href = None

        if href:
            if href not in seen:
                seen.add(href)
                urls.append((href, nice))
            continue

    return urls


def _extract_slug_from_url(url: str) -> str | None:
    """Extract /f/<slug> from URL when available."""
    match = re.search(r"/f/([A-Za-z0-9]+)", url)
    return match.group(1) if match else None


def build_sync_items(
    image_data: list, download_urls: List[Tuple[str, ...]]
) -> List[dict]:
    """
    Build normalized DB rows from parsed album data and resolved download URLs.
    """
    by_slug: dict[str, dict] = {}
    for data in image_data:
        if isinstance(data, dict):
            slug = str(data.get("slug") or "").strip()
            if slug:
                by_slug[slug] = data

    out: List[dict] = []
    seen: set[str] = set()
    for item in download_urls:
        if isinstance(item, (list, tuple)):
            direct_url = str(item[0]).strip() if len(item) >= 1 else ""
            suggested_name = str(item[1]).strip() if len(item) >= 2 and item[1] else ""
            referer = str(item[2]).strip() if len(item) >= 3 and item[2] else ""
            fallback_url = str(item[3]).strip() if len(item) >= 4 and item[3] else ""
        else:
            direct_url = str(item).strip()
            suggested_name = ""
            referer = ""
            fallback_url = ""

        slug = _extract_slug_from_url(fallback_url) or _extract_slug_from_url(
            direct_url
        )
        meta = by_slug.get(slug or "", {})

        key = slug or fallback_url or direct_url
        if not key or key in seen:
            continue
        seen.add(key)

        try:
            size_bytes = int(meta.get("size")) if meta.get("size") is not None else None
        except (TypeError, ValueError):
            size_bytes = None

        out.append(
            {
                "item_key": key,
                "slug": slug or "",
                "original_name": str(
                    meta.get("original") or meta.get("name") or suggested_name or ""
                ),
                "suggested_name": suggested_name,
                "media_type": str(meta.get("type") or ""),
                "size_bytes": size_bytes,
                "direct_url": direct_url,
                "fallback_url": fallback_url,
                "referer_url": referer,
                "cdn_origin": str(meta.get("cdn_origin") or ""),
                "cdn_endpoint": str(meta.get("cdn_endpoint") or ""),
                "thumbnail_url": str(meta.get("thumbnail") or ""),
            }
        )

    return out


def _summarize_items(image_data: list) -> tuple[dict[str, int], int]:
    """Count item types and sum sizes (bytes) from albumFiles entries."""
    counts = {"image": 0, "video": 0, "archive": 0, "other": 0}
    total_size = 0

    for data in image_data:
        media_type = ""
        size = 0
        if isinstance(data, dict):
            media_type = (data.get("type") or "").lower()
            try:
                size = int(data.get("size") or 0)
            except (TypeError, ValueError):
                size = 0
            ext_label = (data.get("extension") or "").lower()
        else:
            ext_label = ""

        if media_type.startswith("image/") or ext_label == "image":
            counts["image"] += 1
        elif media_type.startswith("video/") or ext_label == "video":
            counts["video"] += 1
        elif (
            "zip" in media_type
            or "rar" in media_type
            or "7z" in media_type
            or "tar" in media_type
            or ext_label == "archive"
        ):
            counts["archive"] += 1
        else:
            counts["other"] += 1

        if size > 0:
            total_size += size

    return counts, total_size


def _format_size(num_bytes: int) -> str:
    """Return human readable size string."""
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(num_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


async def download_album(
    session: ClientSession,
    url: str,
    parent_folder: str,
    folder_name: Optional[str] = None,
    use_parent_as_target: bool = False,
    delete_local_on_remote_remove: bool = False,
) -> Tuple[List[str], List[str], List[str]]:
    """
    Download all images from a single album URL into a specified folder.

    Args:
        session (ClientSession): The active HTTP client session.
        url (str): The album URL to download images from.
        parent_folder (str): The base directory path to save the album folder.
        folder_name (Optional[str], optional): Subfolder name inside parent_folder.
        Defaults to None.

    Returns:
        Tuple[List[str], List[str], List[str]]:
            - downloaded (List[str]): URLs that were successfully downloaded.
            - failed (List[str]): URLs that failed to download.
            - errors (List[str]): Error messages encountered during download.
    """
    album_name, image_data = await fetch_album_data(session, url)
    if not image_data:
        return [], [], []

    counts, total_size = _summarize_items(image_data)
    total_files = len(image_data)
    print(
        f"[*] Files: {total_files} "
        f"(image {counts['image']}, video {counts['video']}, archive {counts['archive']}, other {counts['other']}) "
        f"~{_format_size(total_size)}"
    )
    effective_conc = min(MAX_CONCURRENT_DOWNLOADS, total_files) if total_files else 0
    print(
        f"[*] Concurrency: using {effective_conc} workers "
        f"(max {MAX_CONCURRENT_DOWNLOADS}, set via BUNKR_CONCURRENCY)"
    )

    folder = folder_name or sanitize(album_name or "album")
    if use_parent_as_target:
        folder_path = await create_download_folder(parent_folder)
    else:
        # Avoid double-nesting when parent_folder already ends with the album folder
        parent_tail = os.path.basename(os.path.normpath(parent_folder))
        if parent_tail == folder:
            folder_path = await create_download_folder(parent_folder)
        else:
            folder_path = await create_download_folder(parent_folder, folder)
    download_urls = build_download_urls(image_data, url)
    if ENABLE_SYNC_DB:
        sync_items = build_sync_items(image_data, download_urls)
        try:
            sync_result = sync_album_items(url, album_name, sync_items)
            print(
                f"[*] Sync DB: {sync_result.total_items} item(s), "
                f"added {sync_result.added_items}, "
                f"updated {sync_result.updated_items}, "
                f"removed {sync_result.removed_items}"
            )
        except Exception as e:  # pragma: no cover - should not block downloads
            print(f"[!] Sync DB failed: {e}")
    print("[*] Starting downloads...")
    downloaded, failed, errors = await download_images_from_urls(
        download_urls, folder_path
    )
    if ENABLE_SYNC_DB:
        try:
            _print_post_sync_state(url, folder_path, delete_local_on_remote_remove)
        except Exception as e:  # pragma: no cover - should not block downloads
            print(f"[!] Sync DB post-process failed: {e}")

    return downloaded, failed, errors


def _print_post_sync_state(
    url: str,
    folder_path: str,
    delete_local_on_remote_remove: bool,
) -> None:
    """Refresh and print local state/policy summary after DB sync."""
    state = refresh_album_download_state(url, folder_path)
    policy = apply_removed_item_policy(
        url,
        delete_local_on_remote_remove=delete_local_on_remote_remove,
        target_folder=folder_path,
    )
    print(
        f"[*] Local state: downloaded {state.downloaded_items}/{state.total_items}, "
        f"missing {state.missing_items}"
    )
    if policy.deleted_items or policy.retained_items or policy.delete_errors:
        print(
            f"[*] Removed media policy: retained {policy.retained_items}, "
            f"deleted {policy.deleted_items}, errors {policy.delete_errors}"
        )


async def sync_album_only(
    session: ClientSession,
    url: str,
    target_folder: str,
    delete_local_on_remote_remove: bool = False,
) -> list[str]:
    """
    Sync album snapshot into DB without downloading media files.
    """
    errors: list[str] = []
    album_name, image_data = await fetch_album_data(
        session,
        url,
        announce_prefix="Syncing album metadata",
    )
    if not image_data:
        return [f"\n[!] Failed to sync album metadata from {url}"]

    counts, total_size = _summarize_items(image_data)
    total_files = len(image_data)
    print(
        f"[*] Files: {total_files} "
        f"(image {counts['image']}, video {counts['video']}, archive {counts['archive']}, other {counts['other']}) "
        f"~{_format_size(total_size)}"
    )

    if not ENABLE_SYNC_DB:
        print("[!] BUNKR_SYNC_DB is disabled; sync-only has no DB effect.")
        return errors

    try:
        download_urls = build_download_urls(image_data, url)
        sync_items = build_sync_items(image_data, download_urls)
        sync_result = sync_album_items(url, album_name, sync_items)
        print(
            f"[*] Sync DB: {sync_result.total_items} item(s), "
            f"added {sync_result.added_items}, "
            f"updated {sync_result.updated_items}, "
            f"removed {sync_result.removed_items}"
        )
    except Exception as e:  # pragma: no cover - should not block CLI
        errors.append(f"\n[!] Sync DB failed: {e}")
        return errors

    try:
        _print_post_sync_state(url, target_folder, delete_local_on_remote_remove)
    except Exception as e:  # pragma: no cover - should not block CLI
        errors.append(f"\n[!] Sync DB post-process failed: {e}")

    return errors


def _safe_input(prompt: str) -> str:
    """Input wrapper that normalizes Ctrl+C/EOF to UserAbortError."""
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError) as exc:
        raise UserAbortError from exc


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    """Simple yes/no parser with default."""
    raw = _safe_input(prompt).strip().lower()
    if not raw:
        return default
    return raw in {"y", "yes"}


def _clear_screen() -> None:
    """Clear terminal screen for TTY sessions."""
    if not ENABLE_CLEAR_SCREEN or not sys.stdout.isatty():
        return
    # ANSI clear + cursor home.
    print("\033[2J\033[H", end="", flush=True)


def _pause_before_refresh() -> None:
    """Pause so action output can be read before next clear-screen refresh."""
    if (
        not ENABLE_PAUSE_ON_REFRESH
        or not ENABLE_CLEAR_SCREEN
        or not sys.stdout.isatty()
    ):
        return
    try:
        _safe_input("\n[?] Press Enter to continue...")
    except UserAbortError:
        # Ignore abort here and continue to redraw menu.
        print()


def _read_album_urls(raw_input: str) -> list[str]:
    """Read URLs either from comma-separated input or a file path."""
    if os.path.isfile(raw_input):
        with open(raw_input, "r", encoding="utf-8") as file:
            return [line.strip() for line in file if line.strip()]
    return [u.strip() for u in raw_input.split(",") if u.strip()]


def _print_run_summary(
    total_downloaded: int, total_failed: int, errors: Sequence[str]
) -> None:
    print(
        f"\n[^] Downloaded: {total_downloaded} file{'s' if total_downloaded != 1 else ''}, "
        f"Failed: {total_failed} file{'s' if total_failed != 1 else ''}."
    )
    for error in errors:
        print(error)


def _print_managed_albums(albums: Sequence[ManagedAlbum]) -> None:
    """Print managed album table-like listing."""
    if not albums:
        print("[*] Managed album list is empty.")
        return

    counts_map = get_album_item_counts_map([album.album_url for album in albums])
    print("\n[*] Managed albums:")
    for album in albums:
        policy = "delete" if album.delete_local_on_remote_remove else "retain"
        counts = counts_map.get(
            album.album_url,
            AlbumItemCounts(image=0, video=0, archive=0, other=0, total=0),
        )
        print(
            f"  [{album.id}] {album.album_label} | {album.album_url}\n"
            f"      🖼️ {counts.image}  🎬 {counts.video}  📦 {counts.archive}  ❓ {counts.other}\n"
            f"      folder: {album.target_folder}\n"
            f"      on_remove: {policy}"
        )


def _parse_album_selection(
    raw: str, albums: Sequence[ManagedAlbum]
) -> list[ManagedAlbum]:
    """Parse `all` or comma-separated managed IDs."""
    if not raw:
        return []
    if raw.lower() in {"all", "*"}:
        return list(albums)

    by_id = {album.id: album for album in albums}
    selected: list[ManagedAlbum] = []
    seen: set[int] = set()
    for chunk in raw.split(","):
        token = chunk.strip()
        if not token or not token.isdigit():
            continue
        album_id = int(token)
        if album_id in by_id and album_id not in seen:
            seen.add(album_id)
            selected.append(by_id[album_id])
    return selected


def _parse_numeric_ids(raw: str) -> list[int]:
    """Parse comma-separated positive integer IDs."""
    ids: list[int] = []
    seen: set[int] = set()
    for chunk in raw.split(","):
        token = chunk.strip()
        if not token or not token.isdigit():
            continue
        value = int(token)
        if value > 0 and value not in seen:
            seen.add(value)
            ids.append(value)
    return ids


def _print_media_grouped(album: ManagedAlbum, items: Sequence[AlbumMediaItem]) -> None:
    """Print one managed album media list grouped by category."""
    if not items:
        print(f"[*] No media items in DB for '{album.album_label}'.")
        return

    groups: dict[str, list[AlbumMediaItem]] = {
        "image": [],
        "video": [],
        "archive": [],
        "other": [],
    }
    for item in items:
        groups.get(item.category, groups["other"]).append(item)

    print(f"\n[*] Media in [{album.id}] {album.album_label}")
    order = [
        ("image", "🖼️"),
        ("video", "🎬"),
        ("archive", "📦"),
        ("other", "❓"),
    ]
    for category, emoji in order:
        bucket = groups[category]
        if not bucket:
            continue
        print(f"\n  {emoji} ({len(bucket)})")
        for item in bucket:
            size = _format_size(item.size_bytes) if item.size_bytes else "?"
            remote = "🟢" if item.is_active else "⚪"
            local = "💾" if item.is_downloaded else "☁️"
            print(
                f"    [{item.id}] {item.display_name}\n"
                f"         {size}  {remote}  {local}"
            )


async def _manage_album_media() -> None:
    """Enter media-management view for one managed album."""
    try:
        albums = list_managed_albums(enabled_only=False)
        if not albums:
            print("[*] Managed album list is empty.")
            return

        _print_managed_albums(albums)
        raw_id = _safe_input("[?] Managed album ID to view media: ").strip()
        if not raw_id.isdigit():
            print("[!] Invalid ID.")
            return

        album = get_managed_album(int(raw_id))
        if not album:
            print("[!] Managed album not found.")
            return

        async with ClientSession() as session:
            while True:
                try:
                    _clear_screen()
                    items = list_album_media_items(
                        album.album_url, include_removed=True
                    )
                    _print_media_grouped(album, items)
                    action = (
                        _safe_input(
                            "\n[?] Media menu: [D]elete DB+file  [X] Delete DB only  [S]ync metadata  [B]ack: "
                        )
                        .strip()
                        .lower()
                    )
                    if action in {"", "b", "back", "q"}:
                        return
                    if action in {"s", "sync", "meta", "metadata"}:
                        folder = await create_download_folder(album.target_folder)
                        sync_errors = await sync_album_only(
                            session,
                            album.album_url,
                            folder,
                            delete_local_on_remote_remove=album.delete_local_on_remote_remove,
                        )
                        if sync_errors:
                            for err in sync_errors:
                                print(err)
                        else:
                            print("[*] Sync-only completed (no file downloads).")
                        _pause_before_refresh()
                        continue
                    if action not in {"d", "delete", "x", "db", "db-only"}:
                        print("[!] Unknown media action.")
                        _pause_before_refresh()
                        continue

                    delete_local = action in {"d", "delete"}
                    raw_media_ids = _safe_input(
                        "[?] Media ID(s), comma-separated: "
                    ).strip()
                    media_ids = _parse_numeric_ids(raw_media_ids)
                    if not media_ids:
                        print("[!] No valid media ID selected.")
                        _pause_before_refresh()
                        continue

                    for media_id in media_ids:
                        result: MediaDeleteResult = delete_album_media_item(
                            album_url=album.album_url,
                            media_item_id=media_id,
                            delete_local_file=delete_local,
                            allowed_root=album.target_folder if delete_local else None,
                        )
                        prefix = "[*]" if result.db_deleted else "[!]"
                        print(f"{prefix} {result.message}")
                    _pause_before_refresh()
                except (UserAbortError, KeyboardInterrupt, EOFError):
                    print("\n[!] Media action cancelled.")
                    return
                except Exception as error:
                    print(f"[!] Media action failed: {error}")
                    _pause_before_refresh()
    except (UserAbortError, KeyboardInterrupt, EOFError):
        print("\n[!] Media menu cancelled.")
    except Exception as error:
        print(f"[!] Media menu failed: {error}")


async def _quick_download_flow(raw_input: str | None = None) -> None:
    """Interactive quick downloader using URL(s) or URL-file input."""
    try:
        if raw_input is None:
            raw_input = _safe_input(
                "[?] Enter bunkr Album URLs (support comma-separated) "
                "or provide a file path: "
            ).strip()

        if not raw_input:
            print("[!] Input URL/path is empty.")
            return

        urls = _read_album_urls(raw_input)
        if not urls:
            print("[!] No URL found.")
            return

        single_urls = [u for u in urls if is_single_file_url(u)]
        if single_urls:
            print(
                "[!] Single file URLs are not supported. Please provide album URL(s)."
            )
            return

        total_downloaded = 0
        total_failed = 0
        all_errors: list[str] = []

        async with ClientSession() as session:
            if len(urls) == 1:
                parent_folder, custom = get_user_folder(
                    default_name=sanitize(
                        await fetch_data(session, urls[0], "album-name")
                    )
                    or "album"
                )
                downloaded, failed, errors = await download_album(
                    session,
                    urls[0],
                    parent_folder,
                    use_parent_as_target=custom,
                )
                total_downloaded += len(downloaded)
                total_failed += len(failed)
                all_errors.extend(errors)
            else:
                for count, url in enumerate(urls, start=1):
                    album_name = await fetch_data(session, url, "album-name")
                    safe_album = sanitize(album_name) if album_name else None
                    parent_folder, custom = get_user_folder(
                        default_name=safe_album or str(count)
                    )
                    downloaded, failed, errors = await download_album(
                        session,
                        url,
                        parent_folder,
                        folder_name=None if custom else safe_album or str(count),
                        use_parent_as_target=custom,
                    )
                    total_downloaded += len(downloaded)
                    total_failed += len(failed)
                    all_errors.extend(errors)

        _print_run_summary(total_downloaded, total_failed, all_errors)
    except (UserAbortError, KeyboardInterrupt, EOFError):
        print("\n[!] Quick download cancelled.")
    except Exception as error:
        print(f"[!] Quick download failed: {error}")


async def _managed_add_album(session: ClientSession) -> None:
    """Prompt add/update managed album entry."""
    try:
        album_url = _safe_input("[?] Album URL: ").strip()
        if not album_url:
            print("[!] Album URL cannot be empty.")
            return

        if is_single_file_url(album_url):
            print("[!] Use album URL format `/a/<id>`.")
            return

        fetched_name = await fetch_data(session, album_url, "album-name")
        default_label = (fetched_name or "").strip() or sanitize(
            urlsplit(album_url).path.rsplit("/", 1)[-1] or "album"
        )
        custom_label = _safe_input(
            f"[?] Album label (blank: '{default_label}'): "
        ).strip()
        label = custom_label or default_label

        default_folder = os.path.join(DEFAULT_MANAGED_FOLDER_ROOT, sanitize(label))
        custom_folder = _safe_input(
            f"[?] Target folder (blank: '{default_folder}'): "
        ).strip()
        if custom_folder:
            target_folder = (
                custom_folder
                if os.path.isabs(custom_folder)
                else os.path.abspath(os.path.join(os.getcwd(), custom_folder))
            )
        else:
            target_folder = os.path.abspath(default_folder)

        delete_on_remove = _ask_yes_no(
            "[?] Delete local file when media is removed remotely? (y/N): ",
            default=False,
        )
        managed = upsert_managed_album(
            album_url=album_url,
            album_label=label,
            target_folder=target_folder,
            delete_local_on_remote_remove=delete_on_remove,
        )
        print(
            f"[*] Managed album saved: [{managed.id}] {managed.album_label} -> {managed.target_folder}"
        )

        sync_and_download = _ask_yes_no(
            "[?] Sync and download now? (y/N): ",
            default=False,
        )
        folder = await create_download_folder(managed.target_folder)
        if sync_and_download:
            downloaded, failed, errors = await download_album(
                session,
                managed.album_url,
                folder,
                use_parent_as_target=True,
                delete_local_on_remote_remove=managed.delete_local_on_remote_remove,
            )
            _print_run_summary(len(downloaded), len(failed), errors)
            return

        sync_errors = await sync_album_only(
            session,
            managed.album_url,
            folder,
            delete_local_on_remote_remove=managed.delete_local_on_remote_remove,
        )
        if sync_errors:
            for err in sync_errors:
                print(err)
        else:
            print("[*] Sync-only completed (no file downloads).")
    except (UserAbortError, KeyboardInterrupt, EOFError):
        print("\n[!] Add managed album cancelled.")
    except Exception as error:
        print(f"[!] Add managed album failed: {error}")


def _managed_toggle_remove_policy() -> None:
    """Toggle on-remove delete policy for managed album."""
    try:
        raw_id = _safe_input("[?] Managed album ID: ").strip()
        if not raw_id.isdigit():
            print("[!] Invalid ID.")
            return
        album = get_managed_album(int(raw_id))
        if not album:
            print("[!] Managed album not found.")
            return

        next_value = not album.delete_local_on_remote_remove
        if not set_managed_album_remove_policy(album.id, next_value):
            print("[!] Failed to update remove policy.")
            return
        mode = "delete" if next_value else "retain"
        print(f"[*] Updated [{album.id}] policy to '{mode}' on remote remove.")
    except (UserAbortError, KeyboardInterrupt, EOFError):
        print("\n[!] Toggle policy cancelled.")
    except Exception as error:
        print(f"[!] Toggle policy failed: {error}")


def _managed_remove_album() -> None:
    """Delete managed album config row."""
    try:
        raw_id = _safe_input("[?] Managed album ID to remove: ").strip()
        if not raw_id.isdigit():
            print("[!] Invalid ID.")
            return

        album = get_managed_album(int(raw_id))
        if not album:
            print("[!] Managed album not found.")
            return

        if not _ask_yes_no(
            f"[?] Remove managed config for '{album.album_label}'? (y/N): ",
            default=False,
        ):
            print("[*] Cancelled.")
            return

        if delete_managed_album(album.id):
            print(f"[*] Managed album [{album.id}] removed.")
        else:
            print("[!] Failed to remove managed album.")
    except (UserAbortError, KeyboardInterrupt, EOFError):
        print("\n[!] Remove managed album cancelled.")
    except Exception as error:
        print(f"[!] Remove managed album failed: {error}")


async def _managed_sync_metadata() -> None:
    """Sync metadata only for selected managed album(s)."""
    try:
        albums = list_managed_albums(enabled_only=True)
        if not albums:
            print("[*] No managed album. Add one from managed menu first.")
            return

        _print_managed_albums(albums)
        selected_raw = _safe_input(
            "[?] Select album ID(s) to sync metadata (e.g. 1,2) or 'all': "
        ).strip()
        selected = _parse_album_selection(selected_raw, albums)
        if not selected:
            print("[!] No managed album selected.")
            return

        all_errors: list[str] = []
        async with ClientSession() as session:
            for album in selected:
                print(
                    f"\n[>] Sync metadata for managed album [{album.id}] {album.album_label}"
                )
                folder = await create_download_folder(album.target_folder)
                errors = await sync_album_only(
                    session,
                    album.album_url,
                    folder,
                    delete_local_on_remote_remove=album.delete_local_on_remote_remove,
                )
                all_errors.extend(errors)

        print(
            f"\n[^] Metadata sync processed: {len(selected)} album(s), "
            f"Issues: {len(all_errors)}."
        )
        for error in all_errors:
            print(error)
    except (UserAbortError, KeyboardInterrupt, EOFError):
        print("\n[!] Metadata sync cancelled.")
    except Exception as error:
        print(f"[!] Metadata sync failed: {error}")


async def _managed_album_menu() -> None:
    """Managed album CRUD menu."""
    async with ClientSession() as session:
        while True:
            try:
                _clear_screen()
                albums = list_managed_albums(enabled_only=False)
                _print_managed_albums(albums)
                action = (
                    _safe_input(
                        "\n[?] Managed menu: [A/1]dd  [M/2]edia  [S/3]ync metadata  [T/4]oggle remove policy  [R/5]emove  [B]ack: "
                    )
                    .strip()
                    .lower()
                )
                if action in {"", "b", "back", "q"}:
                    return
                if action in {"a", "add", "1"}:
                    await _managed_add_album(session)
                    _pause_before_refresh()
                    continue
                if action in {"m", "media", "2"}:
                    await _manage_album_media()
                    continue
                if action in {"s", "sync", "3"}:
                    await _managed_sync_metadata()
                    _pause_before_refresh()
                    continue
                if action in {"t", "toggle", "4"}:
                    _managed_toggle_remove_policy()
                    _pause_before_refresh()
                    continue
                if action in {"r", "remove", "5"}:
                    _managed_remove_album()
                    _pause_before_refresh()
                    continue
                print("[!] Unknown menu action.")
                _pause_before_refresh()
            except (UserAbortError, KeyboardInterrupt, EOFError):
                print("\n[!] Managed menu cancelled.")
                return
            except Exception as error:
                print(f"[!] Managed menu action failed: {error}")
                _pause_before_refresh()


async def _sync_managed_albums_flow() -> None:
    """Sync one or many managed albums selected by user."""
    try:
        albums = list_managed_albums(enabled_only=True)
        if not albums:
            print("[*] No managed album. Add one from managed menu first.")
            return

        _print_managed_albums(albums)
        selected_raw = _safe_input(
            "[?] Select album ID(s) to sync (e.g. 1,2) or 'all': "
        ).strip()
        selected = _parse_album_selection(selected_raw, albums)
        if not selected:
            print("[!] No managed album selected.")
            return

        total_downloaded = 0
        total_failed = 0
        all_errors: list[str] = []

        async with ClientSession() as session:
            for album in selected:
                print(f"\n[>] Sync managed album [{album.id}] {album.album_label}")
                folder = await create_download_folder(album.target_folder)
                downloaded, failed, errors = await download_album(
                    session,
                    album.album_url,
                    folder,
                    use_parent_as_target=True,
                    delete_local_on_remote_remove=album.delete_local_on_remote_remove,
                )
                total_downloaded += len(downloaded)
                total_failed += len(failed)
                all_errors.extend(errors)

        _print_run_summary(total_downloaded, total_failed, all_errors)
    except (UserAbortError, KeyboardInterrupt, EOFError):
        print("\n[!] Managed album sync cancelled.")
    except Exception as error:
        print(f"[!] Managed album sync failed: {error}")


async def downloader() -> None:
    """Interactive CLI entrypoint with quick-download and managed album sync menus."""
    while True:
        try:
            _clear_screen()
            print(render_main_menu_banner())
            entry = _safe_input(
                "\n[?] Input menu number or paste album URL / url-file path: "
            ).strip()

            # Backward-compatible: allow direct URL/file paste at main prompt.
            if os.path.isfile(entry) or "bunkr" in entry or "," in entry:
                await _quick_download_flow(entry)
                _pause_before_refresh()
                continue

            cmd = entry.lower()
            if cmd in {"", "1", "quick", "download"}:
                await _quick_download_flow()
                _pause_before_refresh()
                continue
            if cmd in {"2", "manage", "m"}:
                await _managed_album_menu()
                continue
            if cmd in {"3", "sync", "s"}:
                await _sync_managed_albums_flow()
                _pause_before_refresh()
                continue
            if cmd in {"4", "exit", "quit", "q", "n"}:
                break

            print("[!] Unknown command.")
            _pause_before_refresh()
        except (UserAbortError, KeyboardInterrupt, EOFError):
            print("\n[!] Exiting...")
            break
        except Exception as error:
            print(f"[!] Main menu action failed: {error}")
            _pause_before_refresh()
