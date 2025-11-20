"""Data processing functions for bunkrr."""

# pylint: disable=broad-exception-caught,line-too-long

import asyncio
import os
import re
from typing import Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiohttp import ClientResponse, ClientSession, ClientTimeout, client_exceptions
from bs4 import BeautifulSoup
from tqdm import tqdm

from bunkrr.api import resolve_bunkr_url
from bunkrr.utils import dedupe_path, get_filename, get_random_user_agent

MAX_CONCURRENT_DOWNLOADS = int(os.environ.get("BUNKR_CONCURRENCY", "12") or 12)

# Debug flag controlled by env var BUNKR_DEBUG
DEBUG = os.environ.get("BUNKR_DEBUG", "").lower() in {"1", "true", "yes", "on"}
LIMIT = 0
try:
    LIMIT = int(os.environ.get("BUNKR_LIMIT", "0") or 0)
except ValueError:
    LIMIT = 0


def dbg(msg: str) -> None:
    """
    Print a debug message when the DEBUG flag is enabled.

    Args:
        msg (str): Message to print when debug logging is active.

    Returns:
        None
    """
    if DEBUG:
        print(f"[debug] {msg}")


def _media_save_path(
    target_dir: str,
    response_url: str,
    suggested_name: str | None,
    headers: Mapping[str, str],
) -> tuple[str, int]:
    """Compute a unique destination path and reported size for a response."""
    base = get_filename(response_url, suggested_name, headers)
    file_path = dedupe_path(os.path.join(target_dir, base))
    file_size = int(headers.get("content-length", 0))
    return file_path, file_size


async def _stream_response_to_file(
    response: ClientResponse, file_path: str, file_size: int
) -> None:
    """Stream response content to disk with a progress bar."""
    dbg(f"Saving to '{file_path}' size={file_size if file_size else 'unknown'}")
    with open(file_path, "wb") as file, tqdm(
        desc=os.path.basename(file_path),
        total=file_size,
        unit="B",
        unit_scale=True,
        unit_divisor=1024,
        leave=False,
    ) as progress_bar:
        while chunk := await response.content.read(1024):
            file.write(chunk)
            progress_bar.update(len(chunk))


async def fetch_data(
    session: ClientSession, base_url: str, data_type: str
) -> str | list | None:
    """
    Fetches either image data or album information from a given URL.

    Args:
        session (aiohttp.ClientSession): The aiohttp client session.
        base_url (str): The base URL to fetch data from.
        data_type (str): Type of data to fetch ('image' or 'album').

    Returns:
        str or list: The name of the album if 'album' type or a list of image data.
    """
    try:
        async with session.get(base_url) as response:
            response.raise_for_status()
            html = await response.text()

            soup = BeautifulSoup(html, "html.parser")
            if data_type == "album-name":
                album_info = soup.find("div", class_="sm:text-lg")
                if album_info:
                    album_name = album_info.find("h1").text.strip()
                    return album_name
                return None
            if data_type == "image-url":

                def extract_blocks(markup: str) -> tuple[BeautifulSoup, list]:
                    s = BeautifulSoup(markup, "html.parser")
                    out = []
                    out.extend(s.find_all("div", class_="grid-images_box-txt"))
                    out.extend(s.find_all("div", class_="grid-videos_box-txt"))
                    return s, out

                def block_item_id(block) -> str | None:
                    # Prefer the thumbnail anchor next to the text box
                    a = block.find_previous_sibling("a", href=True)
                    if not a and block.parent:
                        a = block.parent.find("a", href=True)
                    href = a.get("href") if a else None
                    if not href:
                        return None
                    m = re.search(r"/(f|i|v)/([A-Za-z0-9]+)", href)
                    if not m:
                        return None
                    return f"{m.group(1)}/{m.group(2)}"

                _, blocks1 = extract_blocks(html)
                seen_ids: set[str] = set()
                unique_blocks: list = []
                for b in blocks1:
                    bid = block_item_id(b)
                    if bid and bid not in seen_ids:
                        seen_ids.add(bid)
                        unique_blocks.append(b)
                dbg(
                    f"Album page 1: found {len(unique_blocks)} unique items (raw {len(blocks1)})"
                )
                blocks = unique_blocks

                # Strategy 1: detect pagination count from links
                page_nums: list[int] = []
                for a in soup.find_all("a", href=True):
                    m = re.search(r"[?&]page=(\\d+)", a["href"])
                    if m:
                        try:
                            page_nums.append(int(m.group(1)))
                        except ValueError:
                            pass

                fetched_pages = {1}

                def with_page(u: str, page_num: int) -> str:
                    parts = urlsplit(u)
                    q = dict(parse_qsl(parts.query))
                    q["page"] = str(page_num)
                    return urlunsplit(
                        (
                            parts.scheme,
                            parts.netloc,
                            parts.path,
                            urlencode(q),
                            parts.fragment,
                        )
                    )

                if page_nums:
                    max_page = max(page_nums)
                    for pnum in range(2, max_page + 1):
                        page_url = with_page(base_url, pnum)
                        try:
                            async with session.get(page_url) as r2:
                                r2.raise_for_status()
                                html2 = await r2.text()
                                _, raw_blocks = extract_blocks(html2)
                                added = 0
                                for b in raw_blocks:
                                    bid = block_item_id(b)
                                    if bid and bid not in seen_ids:
                                        seen_ids.add(bid)
                                        blocks.append(b)
                                        added += 1
                                fetched_pages.add(pnum)
                                dbg(
                                    f"Album page {pnum}: added {added} new items (raw {len(raw_blocks)})"
                                )
                        except Exception:
                            continue

                # Strategy 2 (fallback): probe subsequent pages until empty
                if fetched_pages == {1}:
                    for pnum in range(2, 201):  # practical upper bound
                        page_url = with_page(base_url, pnum)
                        try:
                            async with session.get(page_url) as r2:
                                if r2.status >= 400:
                                    break
                                html2 = await r2.text()
                                _, raw_blocks = extract_blocks(html2)
                                added = 0
                                for b in raw_blocks:
                                    bid = block_item_id(b)
                                    if bid and bid not in seen_ids:
                                        seen_ids.add(bid)
                                        blocks.append(b)
                                        added += 1
                                fetched_pages.add(pnum)
                                dbg(
                                    f"Album page {pnum}: added {added} new items (probe raw {len(raw_blocks)})"
                                )
                                if added == 0:
                                    break
                        except Exception:
                            break

                if not blocks:
                    print("\n[!] Failed to grab file URLs.")
                    return None
                return blocks
    except client_exceptions.InvalidURL as e:
        print(f"\n[!] Invalid URL: {e}")
        return None
    except client_exceptions.ClientError as ce:
        print(f"\n[!] Client error: {ce}")
        return None


async def download_media(
    session: ClientSession, url: str, path: str, suggested_name: str | None = None
) -> tuple[bool, str | None]:
    """
    Resolve final media URL using file id + API, then download to disk.

    Args:
        session (ClientSession): The aiohttp client session.
        url (str): The media URL.
        path (str): The local directory path to save the media.
        suggested_name (Optional[str]): Optional filename suggestion.

    Returns:
        Tuple[bool, Optional[str]]: (Success flag, error message if any).
    """
    file_path = None
    try:
        headers = {
            "User-Agent": get_random_user_agent(),
            "Referer": "https://bunkr.ac/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.7",
            "Connection": "keep-alive",
        }

        dbg(f"Start download: suggested='{suggested_name}', url='{url}'")
        initial_resp = None
        attempt = 0
        while True:
            initial_resp = await session.get(url, headers=headers, allow_redirects=True)
            dbg(
                f"GET {url} -> {initial_resp.status} {initial_resp.headers.get('Content-Type','')}"
            )
            if initial_resp.status == 429:
                if attempt == 0 or attempt % 3 == 0:
                    tqdm.write(
                        f"[~] 429 on initial fetch (attempt {attempt + 1}) for {url}"
                    )
                retry_after = initial_resp.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after and str(retry_after).isdigit()
                    else 5 * (2**attempt)
                )
                await initial_resp.release()
                await asyncio.sleep(delay)
                attempt += 1
                continue
            break

        if initial_resp is None:
            return False, f"\n[!] Failed to initiate request to {url}"

        async with initial_resp:
            if initial_resp.status not in (200, 206):
                return False, f"\n[!] HTTP {initial_resp.status} at {url}"

            ctype = initial_resp.headers.get("Content-Type", "")
            if "text/html" not in ctype.lower():
                file_path, file_size = _media_save_path(
                    path, str(initial_resp.url), suggested_name, initial_resp.headers
                )
                await _stream_response_to_file(initial_resp, file_path, file_size)
                return True, None

            html = await initial_resp.text()
            soup = BeautifulSoup(html, "html.parser")
            fid = None
            node = soup.find(attrs={"data-file-id": True}) or soup.find(
                attrs={"data-id": True}
            )
            if node:
                fid = node.get("data-file-id") or node.get("data-id")
            if not fid:
                m_id = re.search(r'data-file-id\s*=\s*"([^"]+)"', html)
                if m_id:
                    fid = m_id.group(1)

            if not fid:
                return False, f"\n[!] Could not find file id for {url}"

            try:
                final_url = await resolve_bunkr_url(
                    fid, ogname=suggested_name, session=session
                )
            except Exception as e:  # pragma: no cover - API failure path
                return False, f"\n[!] Failed to resolve file id {fid}: {e}"

            dbg(f"Resolved via API: {fid} -> {final_url}")
            attempt = 0
            while True:
                async with session.get(
                    final_url, headers=headers, allow_redirects=True
                ) as media_resp:
                    dbg(
                        f"GET {final_url} -> {media_resp.status} {media_resp.headers.get('Content-Type','')}"
                    )
                    if media_resp.status == 429:
                        if attempt == 0 or attempt % 3 == 0:
                            tqdm.write(
                                f"[~] Got 429 on media fetch (attempt {attempt + 1}) for {final_url}"
                            )
                        retry_after = media_resp.headers.get("Retry-After")
                        delay = (
                            float(retry_after)
                            if retry_after and str(retry_after).isdigit()
                            else 1.5 * (2**attempt)
                        )
                        await asyncio.sleep(delay)
                        attempt += 1
                        continue
                    if media_resp.status not in (200, 206):
                        return False, f"\n[!] HTTP {media_resp.status} at {final_url}"
                    if (
                        "text/html"
                        in media_resp.headers.get("Content-Type", "").lower()
                    ):
                        return (
                            False,
                            f"\n[!] Expected media but got HTML at {final_url}",
                        )
                    file_path, file_size = _media_save_path(
                        path, str(media_resp.url), suggested_name, media_resp.headers
                    )
                    await _stream_response_to_file(media_resp, file_path, file_size)
                    return True, None
                return False, f"\n[!] HTTP 429 rate limit at {final_url} after retries"

    except (asyncio.TimeoutError, client_exceptions.ServerTimeoutError) as e:
        target = file_path if file_path else (suggested_name or url)
        return False, f"\n[!] Timeout downloading '{target}': {e}"


async def download_images_from_urls(
    urls: Sequence[str | tuple[str, str]], album_folder: str
) -> tuple[list[str], list[str], list[str]]:
    """
    Downloads images from a list of URLs asynchronously.

    Accepts either:
        - ["https://.../uuid.mp4", ...]  (old behavior)
        - [("https://.../uuid.mp4", "Pretty Name.mp4"), ...]  (new, preferred)

    Args:
        urls (List[str or Tuple[str, str]]): List of URLs or (URL, filename) tuples.
        album_folder (str): Target folder path for downloads.

    Returns:
        Tuple[List[str], List[str], List[str]]:
            - List of successfully downloaded URLs.
            - List of failed URLs.
            - List of error messages.
    """
    # Finite timeouts to avoid hanging forever (no overall cap, but idle/read capped)
    timeout = ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=300)

    def exists_with_dedupe(folder: str, base: str) -> bool:
        path = os.path.join(folder, base)
        if os.path.exists(path):
            return True
        root, ext = os.path.splitext(base)
        try:
            names = os.listdir(folder)
        except FileNotFoundError:
            return False
        pattern = re.compile(re.escape(root) + r" \(\d+\)" + re.escape(ext) + r"$")
        return any((n == base or pattern.match(n)) for n in names)

    filtered = []
    skipped = []
    for item in urls:
        if isinstance(item, (list, tuple)) and len(item) >= 2 and item[1]:
            u, nice = item[0], item[1]
            try:
                expected = get_filename(str(u), str(nice), {})
            except Exception:
                expected = None
            if expected and exists_with_dedupe(album_folder, expected):
                skipped.append(item)
                dbg(f"Skipping existing file: {expected}")
                continue
        filtered.append(item)

    if skipped:
        dbg(f"Skipping {len(skipped)} existing file(s)")

    async with ClientSession(timeout=timeout) as session:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

        async def download_media_wrapper(
            item: str | Sequence[str],
        ) -> tuple[bool, str | None]:
            # item can be a string (url) or a (url, nice_name) tuple
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                url, nice = item[0], item[1]
            else:
                url, nice = item, None
            async with semaphore:
                return await download_media(
                    session, url, album_folder, suggested_name=nice
                )

        active_urls = filtered
        if 0 < LIMIT < len(active_urls):
            dbg(
                f"Limiting downloads to first {LIMIT} items out of {len(active_urls)} (skipped {len(skipped)})"
            )
            active_urls = active_urls[:LIMIT]
        tasks = [download_media_wrapper(item) for item in active_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        downloaded_files = []
        failed_files = []
        error_messages = []

        for item, result in zip(active_urls, results):
            url = (
                item[0] if isinstance(item, (list, tuple)) and len(item) >= 1 else item
            )
            if isinstance(result, Exception):
                failed_files.append(url)
                error_messages.append(f"\n[!] Error downloading '{url}': {result}")
                continue
            ok, err = result
            if ok:
                downloaded_files.append(url)
            else:
                failed_files.append(url)
                if err:
                    error_messages.append(err)

        return downloaded_files, failed_files, error_messages
