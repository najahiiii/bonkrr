"""This module contains the function to download images from bunkrr albums."""

import os
import re
from typing import List, Optional, Tuple
from urllib.parse import urljoin

from aiohttp import ClientSession

from bunkrr.data_processing import (
    create_download_folder,
    download_images_from_urls,
    fetch_data,
)
from bunkrr.utils import choices, get_user_folder, sanitize


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
    """
    urls: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for data in image_data:
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

        # Fallback to old heuristic using thumbnail src if anchor not found
        # Avoid using thumbnail URLs as they are not the actual files
        # If no anchor was found, skip this entry to prevent noise
        continue

        # If all else fails, skip this entry
        continue

    return urls


async def download_album(
    session: ClientSession,
    url: str,
    parent_folder: str,
    folder_name: Optional[str] = None,
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

    folder = folder_name or sanitize(album_name or "album")
    # Avoid double-nesting when parent_folder already ends with the album folder
    parent_tail = os.path.basename(os.path.normpath(parent_folder))
    if parent_tail == folder:
        folder_path = await create_download_folder(parent_folder)
    else:
        folder_path = await create_download_folder(parent_folder, folder)
    download_urls = build_download_urls(image_data, url)
    print(f"[*] Found {len(download_urls)} file(s). Starting downloads...")

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
                parent_folder = get_user_folder(
                    default_name=sanitize(
                        await fetch_data(session, urls[0], "album-name")
                    )
                    or "album"
                )
                downloaded, failed, errors = await download_album(
                    session, urls[0], parent_folder
                )
                total_downloaded += len(downloaded)
                total_failed += len(failed)
                all_errors.extend(errors)
            else:
                for count, url in enumerate(urls, start=1):
                    parent_folder = get_user_folder()
                    downloaded, failed, errors = await download_album(
                        session, url, parent_folder, folder_name=str(count)
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
