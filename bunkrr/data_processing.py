"""Data processing functions for bunkrr."""

import asyncio
import os
import re
from typing import Iterable, Sequence

from aiohttp import ClientSession, ClientTimeout, client_exceptions
from bs4 import BeautifulSoup
from fake_useragent import UserAgent
from tqdm import tqdm

from bunkrr.utils import dedupe_path, get_filename

MAX_CONCURRENT_DOWNLOADS = int(os.environ.get("BUNKR_CONCURRENCY", "12") or 12)

# Debug flag controlled by env var BUNKR_DEBUG
DEBUG = os.environ.get("BUNKR_DEBUG", "").lower() in {"1", "true", "yes", "on"}
LIMIT = 0
try:
    LIMIT = int(os.environ.get("BUNKR_LIMIT", "0") or 0)
except ValueError:
    LIMIT = 0

# Known CDN subdomains observed in the wild. We keep a preferred host to
# try first once discovered for the session (seems stable per run/day).
CDN_CANDIDATES = [
    "beer",
    "kebab",
    "soup",
    "ramen",
    "wiener",
    "rum",
    "meatballs",
    "taquito",
    "cake",
    "maple",
    "rice",
    "nachos",
    "bacon",
    "mlk-bk.cdn.gigachad-cdn.ru",
    "c1.cache8.st",
    "pizza",
    "sushi",
    "pasta",
    "steak",
    "fries",
    "burger",
    "wine",
    "vodka",
    "gin",
]
extra = os.environ.get("BUNKR_CDN_EXTRA", "").strip()
if extra:
    for name in re.split(r"[\s,;]+", extra):
        n = name.strip().lower()
        if n and n not in CDN_CANDIDATES:
            CDN_CANDIDATES.append(n)
CDN_PREFERRED: str | None = None  # full hostname if known (e.g., beer.bunkr.ru)


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


def iter_cdn_hosts() -> Iterable[str]:
    """
    Iterate possible CDN hostnames, preferring any discovered host first.

    Yields:
        str: CDN hostname or prefix to try (preferred host first, then unique
        candidates from CDN_CANDIDATES).
    """
    seen: set[str] = set()
    if CDN_PREFERRED:
        # Try preferred host first
        seen.add(CDN_PREFERRED)
        yield CDN_PREFERRED
    for h in CDN_CANDIDATES:
        # Build a canonical host string to avoid duplicates across forms
        host = h if "." in h else f"{h}.bunkr.ru"
        if host in seen:
            continue
        seen.add(host)
        yield h


# Allow adding CDN hosts from a local file (one name per line)
CDN_HOSTS_FILE = os.environ.get(
    "BUNKR_CDN_FILE", os.path.join(os.getcwd(), "cdn_hosts.txt")
)
try:
    if os.path.isfile(CDN_HOSTS_FILE):
        with open(CDN_HOSTS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                n = line.strip().split("#", 1)[0].strip().lower()
                if n and n not in CDN_CANDIDATES:
                    CDN_CANDIDATES.append(n)
        dbg(f"Loaded extra CDN hosts from {CDN_HOSTS_FILE}")
except OSError:
    pass


def remember_cdn_host(host_or_prefix: str) -> None:
    """
    Persist a discovered CDN host/prefix in memory and on disk for reuse.

    Args:
        host_or_prefix (str): CDN hostname or prefix observed during requests.

    Returns:
        None
    """
    try:
        p = host_or_prefix.strip().lower()
        if not p:
            return
        if p not in CDN_CANDIDATES:
            CDN_CANDIDATES.append(p)
        # Append to file if not already present
        if CDN_HOSTS_FILE:
            try:
                existing = set()
                if os.path.isfile(CDN_HOSTS_FILE):
                    with open(CDN_HOSTS_FILE, "r", encoding="utf-8") as i:
                        existing = {ln.strip().lower() for ln in i if ln.strip()}
                if p not in existing:
                    with open(CDN_HOSTS_FILE, "a", encoding="utf-8") as i:
                        f.write(p + "\n")
            except OSError:
                pass
    except (AttributeError, OSError):
        pass


def get_random_user_agent() -> str:
    """
    Returns a random user agent string.

    Returns:
        str: A random user agent string.
    """
    ua = UserAgent()
    return ua.random


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
                def extract_blocks(markup: str):
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

                soup1, blocks1 = extract_blocks(html)
                seen_ids: set[str] = set()
                unique_blocks: list = []
                for b in blocks1:
                    bid = block_item_id(b)
                    if bid and bid not in seen_ids:
                        seen_ids.add(bid)
                        unique_blocks.append(b)
                dbg(f"Album page 1: found {len(unique_blocks)} unique items (raw {len(blocks1)})")
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

                if page_nums:
                    max_page = max(page_nums)
                    def with_page(u: str, n: int) -> str:
                        parts = urlsplit(u)
                        q = dict(parse_qsl(parts.query))
                        q["page"] = str(n)
                        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
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
                                dbg(f"Album page {pnum}: added {added} new items (raw {len(raw_blocks)})")
                        except Exception:
                            continue

                # Strategy 2 (fallback): probe subsequent pages until empty
                if fetched_pages == {1}:
                    def with_page(u: str, n: int) -> str:
                        parts = urlsplit(u)
                        q = dict(parse_qsl(parts.query))
                        q["page"] = str(n)
                        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))

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
                                dbg(f"Album page {pnum}: added {added} new items (probe raw {len(raw_blocks)})")
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


async def create_download_folder(base_path: str, *args: str) -> str:
    """
    Create a download folder at the specified base path.

    Args:
        base_path (str): The base path where the download folder should be created.
        *args: Variable number of arguments representing the folder name or subdirectories.

    Returns:
        str: The path of the created download folder.

    """
    if len(args) == 1:
        folder_name = args[0]
        path = os.path.join(base_path, folder_name)
        if not os.path.exists(path):
            os.makedirs(path)
    else:
        folder_name = os.path.join(base_path, *args)
        if not os.path.exists(folder_name):
            os.makedirs(folder_name)
        path = folder_name

    return path


async def download_media(
    session: ClientSession, url: str, path: str, suggested_name: str | None = None
) -> tuple[bool, str | None]:
    """
    Downloads media from the given URL and saves it to the specified path.

    Args:
        session (ClientSession): The aiohttp client session.
        url (str): The media URL.
        path (str): The local directory path to save the media.
        suggested_name (Optional[str]): Optional filename suggestion.

    Returns:
        Tuple[bool, Optional[str]]: (Success flag, error message if any).
    """
    try:
        headers = {"User-Agent": get_random_user_agent()}
        async with session.get(url, headers=headers) as response:
            if response.status != 200:
                return False, None

            base = get_filename(url, suggested_name, response.headers)
            file_path = dedupe_path(os.path.join(path, base))
            file_size = int(response.headers.get("content-length", 0))

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

            return True, None

    except client_exceptions.ClientError as e:
        return False, f"\n[!] Failed to download '{file_path}': {e}"


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
    timeout = ClientTimeout(total=None)
    async with ClientSession(timeout=timeout) as session:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

        async def download_media_wrapper(item):
            # item can be a string (url) or a (url, nice_name) tuple
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                url, nice = item[0], item[1]
            else:
                url, nice = item, None
            async with semaphore:
                return await download_media(
                    session, url, album_folder, suggested_name=nice
                )

        tasks = [download_media_wrapper(item) for item in urls]
        results = await asyncio.gather(*tasks)

        downloaded_files = [
            (item[0] if isinstance(item, (list, tuple)) and len(item) >= 1 else item)
            for item, result in zip(urls, results)
            if result[0] is True
        ]
        failed_files = [
            (item[0] if isinstance(item, (list, tuple)) and len(item) >= 1 else item)
            for item, result in zip(urls, results)
            if result[0] is False
        ]
        error_messages = [result[1] for result in results if result[1] is not None]

        return downloaded_files, failed_files, error_messages
