"""Data processing functions for bunkrr."""

# pylint: disable=broad-exception-caught,line-too-long

import asyncio
import json
import os
import re
from typing import Mapping, Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from aiohttp import ClientResponse, ClientSession, ClientTimeout, client_exceptions
from bs4 import BeautifulSoup
from tqdm import tqdm

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
        position=1,
    ) as progress_bar:
        while chunk := await response.content.read(1024):
            file.write(chunk)
            progress_bar.update(len(chunk))


async def fetch_data(
    session: ClientSession, base_url: str, data_type: str
) -> str | list | None:
    """Wrapper: instantiate a BunkrClient to fetch album name or media blocks."""
    return await BunkrClient(session).fetch_data(base_url, data_type)


class BunkrClient:
    """Client wrapper for bunkr album parsing and media downloads."""

    def __init__(
        self,
        session: ClientSession,
        max_concurrent: int = MAX_CONCURRENT_DOWNLOADS,
        limit: int = LIMIT,
    ) -> None:
        self.session = session
        self.max_concurrent = max_concurrent
        self.limit = limit

    async def fetch_data(self, base_url: str, data_type: str) -> str | list | None:
        """
        Fetch album info or media blocks from a bunkr album page.

        Uses the `?advanced=1` view to get the full list in a single page, avoiding
        pagination probes.
        """

        def with_advanced(u: str) -> str:
            parts = urlsplit(u)
            q = dict(parse_qsl(parts.query))
            q.pop("page", None)
            q["advanced"] = "1"
            return urlunsplit(
                (parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment)
            )

        target_url = with_advanced(base_url)
        headers = {
            "User-Agent": get_random_user_agent(),
            "Referer": "https://bunkr.ac/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.7",
            "Connection": "keep-alive",
        }

        try:
            parts = urlsplit(target_url)
            hosts = [parts.netloc]
            if parts.netloc.startswith("bunkr"):
                for alt in ("bunkr.si", "bunkrr.su", "bunkr.is"):
                    if alt not in hosts:
                        hosts.append(alt)

            html = None
            soup = None
            last_exc: Exception | None = None
            used_host = None

            for host in hosts:
                candidate_url = urlunsplit(
                    (parts.scheme, host, parts.path, parts.query, parts.fragment)
                )
                try:
                    async with self.session.get(
                        candidate_url, headers=headers
                    ) as response:
                        response.raise_for_status()
                        html = await response.text()
                        soup = BeautifulSoup(html, "html.parser")
                        used_host = host
                        if host != parts.netloc:
                            dbg(f"Fetched via fallback host {host}")
                        break
                except Exception as e:  # pragma: no cover - network fallback path
                    last_exc = e
                    dbg(f"Fetch attempt failed for {candidate_url}: {e}")
                    continue

            if soup is None:
                if last_exc:
                    raise last_exc
                return None

            origin = urlunsplit((parts.scheme, used_host or parts.netloc, "", "", ""))

            if data_type == "album-name":
                album_info = soup.find("div", class_="sm:text-lg")
                if album_info:
                    album_name = album_info.find("h1").text.strip()
                    return album_name
                return None

            if data_type == "image-url":

                def parse_album_files(doc: BeautifulSoup) -> list[dict]:
                    def normalize_album_json(raw: str) -> str:
                        # Quote keys, drop trailing commas, and repair invalid escape sequences.
                        out = re.sub(r"(?m)^(\s*)([A-Za-z0-9_]+):", r'\1"\2":', raw)
                        out = re.sub(r",\s*([}\]])", r"\1", out)
                        out = out.replace("\\'", "'")
                        # Any backslash not forming a valid JSON escape is doubled to stay parseable.
                        out = re.sub(r"\\(?![\\\\\"/bfnrtu])", r"\\\\", out)
                        return out

                    for script in doc.find_all("script"):
                        text = script.string or script.get_text()
                        if not text or "window.albumFiles" not in text:
                            continue
                        m = re.search(r"window\.albumFiles\s*=\s*(\[.*?]);", text, re.S)
                        if not m:
                            continue
                        normalized = normalize_album_json(m.group(1))
                        try:
                            return json.loads(normalized)
                        except json.JSONDecodeError:
                            continue
                    return []

                album_files = parse_album_files(soup)
                if album_files:
                    blocks = []
                    for f in album_files:
                        slug = f.get("slug")
                        if not slug:
                            continue
                        blocks.append(
                            {
                                "slug": slug,
                                "original": f.get("original") or f.get("name") or "",
                                "origin": origin,
                                "cdn_endpoint": f.get("cdnEndpoint"),
                                "cdn_origin": None,
                                "referer": target_url,
                                "type": f.get("type"),
                                "size": f.get("size"),
                            }
                        )
                        thumb = f.get("thumbnail")
                        if thumb:
                            try:
                                tparts = urlsplit(str(thumb))
                                blocks[-1][
                                    "cdn_origin"
                                ] = f"{tparts.scheme}://{tparts.netloc}"
                            except Exception:
                                pass
                        if not blocks[-1]["cdn_origin"] and f.get("cdnEndpoint"):
                            # Fallback to album host if thumbnail missing
                            blocks[-1]["cdn_origin"] = origin
                    dbg(f"Album advanced JSON: found {len(blocks)} item(s)")
                    if not blocks:
                        print("\n[!] Failed to grab file URLs.")
                        return None
                    return blocks

                def extract_blocks(doc: BeautifulSoup) -> list:
                    out = []
                    out.extend(doc.find_all("div", class_="grid-images_box-txt"))
                    out.extend(doc.find_all("div", class_="grid-videos_box-txt"))
                    return out

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

                raw_blocks = extract_blocks(soup)
                seen_ids: set[str] = set()
                blocks: list = []
                for b in raw_blocks:
                    bid = block_item_id(b)
                    if bid and bid not in seen_ids:
                        seen_ids.add(bid)
                        blocks.append(b)

                dbg(
                    f"Album advanced view: found {len(blocks)} unique items (raw {len(raw_blocks)})"
                )

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
        self,
        url: str,
        path: str,
        suggested_name: str | None = None,
        referer: str | None = None,
        fallback_url: str | None = None,
        _used_fallback: bool = False,
    ) -> tuple[bool, str | None]:
        """
        Download a media URL (or fallback) to disk.
        """
        file_path = None
        try:
            headers = {
                "User-Agent": get_random_user_agent(),
                "Referer": referer or "https://bunkr.ac/",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.7",
                "Connection": "keep-alive",
            }

            dbg(f"Start download: suggested='{suggested_name}', url='{url}'")
            initial_resp = None
            attempt = 0
            while True:
                initial_resp = await self.session.get(
                    url, headers=headers, allow_redirects=True
                )
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
                    if fallback_url and not _used_fallback:
                        return await self.download_media(
                            fallback_url,
                            path,
                            suggested_name,
                            referer=referer,
                            fallback_url=None,
                            _used_fallback=True,
                        )
                    return False, f"\n[!] HTTP {initial_resp.status} at {url}"

                ctype = initial_resp.headers.get("Content-Type", "")
                if "text/html" not in ctype.lower():
                    file_path, file_size = _media_save_path(
                        path,
                        str(initial_resp.url),
                        suggested_name,
                        initial_resp.headers,
                    )
                    await _stream_response_to_file(initial_resp, file_path, file_size)
                    return True, None

                if fallback_url and not _used_fallback:
                    return await self.download_media(
                        fallback_url,
                        path,
                        suggested_name,
                        referer=referer,
                        fallback_url=None,
                        _used_fallback=True,
                    )
                return (
                    False,
                    f"\n[!] Expected media but got HTML at {initial_resp.url}",
                )

        except (asyncio.TimeoutError, client_exceptions.ServerTimeoutError) as e:
            target = file_path if file_path else (suggested_name or url)
            return False, f"\n[!] Timeout downloading '{target}': {e}"

    async def download_images_from_urls(
        self, urls: Sequence[str | tuple[str, str]], album_folder: str
    ) -> tuple[list[str], list[str], list[str]]:
        """
        Downloads media from a list of URLs asynchronously using the client's session.
        """

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

        active_urls = filtered
        if 0 < self.limit < len(active_urls):
            dbg(
                f"Limiting downloads to first {self.limit} items out of {len(active_urls)} (skipped {len(skipped)})"
            )
            active_urls = active_urls[: self.limit]

        semaphore = asyncio.Semaphore(self.max_concurrent)
        progress_bar = tqdm(
            total=len(active_urls),
            desc="Files",
            unit="file",
            leave=False,
            position=0,
        )
        pbar_lock = asyncio.Lock()

        async def download_media_wrapper(
            item: str | Sequence[str],
        ) -> tuple[bool, str | None]:
            # item can be a string (url) or a (url, nice_name) tuple
            referer = None
            fallback_url = None
            if isinstance(item, (list, tuple)):
                url = item[0]
                nice = item[1] if len(item) >= 2 else None
                referer = item[2] if len(item) >= 3 else None
                fallback_url = item[3] if len(item) >= 4 else None
            else:
                url, nice = item, None
            async with semaphore:
                result = await self.download_media(
                    url,
                    album_folder,
                    suggested_name=nice,
                    referer=referer,
                    fallback_url=fallback_url,
                )
            async with pbar_lock:
                progress_bar.update(1)
            return result

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

        progress_bar.close()

        return downloaded_files, failed_files, error_messages


async def download_media(
    session: ClientSession,
    url: str,
    path: str,
    suggested_name: str | None = None,
    referer: str | None = None,
    fallback_url: str | None = None,
    _used_fallback: bool = False,
) -> tuple[bool, str | None]:
    """Wrapper: use a transient BunkrClient to download a single media URL."""
    return await BunkrClient(session).download_media(
        url,
        path,
        suggested_name=suggested_name,
        referer=referer,
        fallback_url=fallback_url,
        _used_fallback=_used_fallback,
    )


async def download_images_from_urls(
    urls: Sequence[str | tuple[str, str]], album_folder: str
) -> tuple[list[str], list[str], list[str]]:
    """Wrapper: create a client with its own session to download a batch of media URLs."""
    # Finite timeouts to avoid hanging forever (no overall cap, but idle/read capped)
    timeout = ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=300)
    async with ClientSession(timeout=timeout) as session:
        client = BunkrClient(session)
        return await client.download_images_from_urls(urls, album_folder)
