"""This module contains the function to download images from bunkrr albums."""

# pylint: disable=line-too-long

import os
import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlsplit

from aiohttp import ClientSession

from bunkrr.data_processing import (
    MAX_CONCURRENT_DOWNLOADS,
    download_images_from_urls,
    fetch_data,
)
from bunkrr.utils import choices, create_download_folder, get_user_folder, sanitize


async def fetch_album_data(
    session: ClientSession, url: str
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
    if album_name:
        print(f"\n[*] Downloading file(s) from album: {album_name}")
    image_data = await fetch_data(session, url, "image-url")
    return album_name, image_data


def build_download_urls(image_data: list, base_url: str) -> List[Tuple[str, str]]:
    """
    Build a list of tuples containing full image URLs and suggested filenames.

    Args:
        image_data (list): List of HTML elements containing image info.

    Returns:
        List[Tuple[str, str]]: Each tuple contains:
            - url (str): The full URL to the image.
            - suggested_name (str): The suggested filename for saving the image.
            - referer (str): Optional referer header to use.
            - fallback_url (str): Optional /f/<slug> URL for fallback resolution.
    """
    urls: List[Tuple[str, str]] = []
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
) -> Tuple[int, int, List[str]]:
    """
    Download all images from a single album URL into a specified folder.

    Args:
        session (ClientSession): The active HTTP client session.
        url (str): The album URL to download images from.
        parent_folder (str): The base directory path to save the album folder.
        folder_name (Optional[str], optional): Subfolder name inside parent_folder.
        Defaults to None.

    Returns:
        Tuple[int, int, List[str]]:
            - downloaded_count (int): Number of successfully downloaded files.
            - failed_count (int): Number of failed downloads.
            - errors (List[str]): List of error messages encountered during download.
    """
    album_name, image_data = await fetch_album_data(session, url)
    if not image_data:
        return 0, 0, []

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
    print("[*] Starting downloads...")

    return await download_images_from_urls(download_urls, folder_path)


async def downloader() -> None:
    """
    Interactive downloader for bunkr albums.

    Prompts the user to enter bunkr album URLs or a file path containing URLs.
    Downloads images from the specified albums and saves them to user-designated folders.
    Continues prompting until the user chooses to exit.

    Returns:
        None
    """
    while True:
        raw_input = input(
            "[?] Enter bunkr Album URLs (Support multiple URLs separated by comma)"
            " or provide a file path: "
        ).strip()
        if os.path.isfile(raw_input):
            with open(raw_input, "r", encoding="utf-8") as f:
                urls = [line.strip() for line in f if line.strip()]
        else:
            urls = [u.strip() for u in raw_input.split(",") if u.strip()]

        total_downloaded = 0
        total_failed = 0
        all_errors = []

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

        print(
            f"\n[^] Downloaded: {total_downloaded} file{'s' if total_downloaded != 1 else ''}, "
            f"Failed: {total_failed} file{'s' if total_failed != 1 else ''}."
        )
        for error in all_errors:
            print(error)

        if not choices("[?] Do you want to download again? (Y/N, default N): "):
            break
