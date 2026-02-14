"""Fetch Bunkr album HTML (normal/advanced), save it, and print debug report."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
ANALYSIS_DIR = ROOT / "analysis"
DEFAULT_ALBUM_URL = "https://bunkr.cr/a/jf4tCB0V"
DEFAULT_TIMEOUT = 30

_FILE_LINK_RE = re.compile(r"/(f|i|v)/[A-Za-z0-9]+")
_ALBUM_FILES_RE = re.compile(r"window\.albumFiles\s*=\s*(\[.*?]);", re.S)
_ALBUM_ID_RE = re.compile(r"/a/([A-Za-z0-9]+)")


def normalize_album_json(raw: str) -> str:
    """Convert JS-like object array text into valid JSON."""
    out = re.sub(r"(?m)^(\s*)([A-Za-z0-9_]+):", r'\1"\2":', raw)
    out = re.sub(r",\s*([}\]])", r"\1", out)
    out = out.replace("\\'", "'")
    out = re.sub(r"\\(?![\\\"/bfnrtu])", r"\\\\", out)
    return out


def parse_album_files(text: str) -> tuple[list[dict[str, Any]] | None, str | None]:
    """Extract and decode `window.albumFiles` from HTML text."""
    match = _ALBUM_FILES_RE.search(text)
    if not match:
        return None, "not_found"

    raw = match.group(1)
    normalized = normalize_album_json(raw)
    try:
        return json.loads(normalized), None
    except json.JSONDecodeError as error:
        return None, f"json_error: {error}"


def _summarize_album_files(
    items: list[dict[str, Any]],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    summary = {"total": len(items), "image": 0, "video": 0, "archive": 0, "other": 0}
    total_size = 0

    for item in items:
        media_type = str(item.get("type") or "").lower()
        if media_type.startswith("image/"):
            summary["image"] += 1
        elif media_type.startswith("video/"):
            summary["video"] += 1
        elif any(ext in media_type for ext in ("zip", "rar", "7z", "tar")):
            summary["archive"] += 1
        else:
            summary["other"] += 1
        try:
            total_size += int(item.get("size") or 0)
        except (TypeError, ValueError):
            pass

    summary["total_size_bytes"] = total_size
    sample = [
        {
            "slug": item.get("slug"),
            "name": item.get("name"),
            "original": item.get("original"),
            "type": item.get("type"),
            "size": item.get("size"),
            "cdnEndpoint": item.get("cdnEndpoint"),
            "thumbnail": item.get("thumbnail"),
        }
        for item in items[:3]
    ]
    return summary, sample


def build_variant_urls(album_url: str) -> tuple[str, str]:
    """Return `(normal_url, advanced_url)` for one album URL."""
    parts = urlsplit(album_url)
    base = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["advanced"] = "1"
    advanced = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), "")
    )
    return base, advanced


def get_album_key(album_url: str) -> str:
    """Extract album slug/key from `/a/<key>`."""
    match = _ALBUM_ID_RE.search(album_url)
    if match:
        return match.group(1)
    return "album"


def fetch_html(url: str, timeout: int) -> tuple[str, dict[str, Any] | None, str | None]:
    """Fetch one HTML page. Returns `(html, meta, error)`."""
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection": "keep-alive",
    }
    req = Request(url, headers=headers)
    try:
        with urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            html = raw.decode(charset, errors="replace")
            meta = {
                "http_status": getattr(resp, "status", None),
                "final_url": resp.geturl(),
                "content_type": resp.headers.get("Content-Type"),
                "response_size_bytes": len(raw),
            }
            return html, meta, None
    except HTTPError as error:
        return "", None, f"http_error: {error.code} {error.reason}"
    except URLError as error:
        return "", None, f"url_error: {error.reason}"
    except TimeoutError as error:
        return "", None, f"timeout_error: {error}"
    except (OSError, ValueError, LookupError) as error:
        return "", None, f"fetch_error: {error}"


def parse_html_report(
    html: str, source_url: str, saved_file: Path, fetch_meta: dict[str, Any]
) -> dict[str, Any]:
    """Parse one HTML string and return debug info."""
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.text.strip() if soup.title else None
    h1 = soup.find("h1")
    h1_text = h1.get_text(strip=True) if h1 else None

    meta: dict[str, str] = {}
    for tag in soup.find_all("meta"):
        key = tag.get("property") or tag.get("name")
        value = tag.get("content")
        if key and value:
            meta[key] = value

    scripts_src = [script.get("src") for script in soup.find_all("script", src=True)]
    external_domains = sorted(
        {urlparse(src).netloc for src in scripts_src if src and urlparse(src).netloc}
    )

    hrefs = [anchor.get("href") for anchor in soup.find_all("a", href=True)]
    file_links = sorted({href for href in hrefs if href and _FILE_LINK_RE.search(href)})

    image_sources = [image.get("src") for image in soup.find_all("img", src=True)]
    banner_div = soup.find("div", attrs={"style": re.compile(r"background:\s*red")})
    banner_text = banner_div.get_text(" ", strip=True) if banner_div else None

    album_files, album_files_error = parse_album_files(html)
    album_files_summary = None
    album_files_sample = None
    if album_files is not None:
        album_files_summary, album_files_sample = _summarize_album_files(album_files)

    return {
        "source_url": source_url,
        "saved_file": str(saved_file),
        "saved_size_bytes": saved_file.stat().st_size if saved_file.is_file() else 0,
        "fetch": fetch_meta,
        "title": title,
        "h1": h1_text,
        "meta_selected": {
            key: meta.get(key)
            for key in (
                "description",
                "og:type",
                "og:title",
                "og:description",
                "og:image",
                "og:url",
            )
        },
        "banner_notice": banner_text,
        "script_count": len(scripts_src),
        "scripts_src": scripts_src,
        "external_script_domains": external_domains,
        "file_like_links_found": file_links,
        "image_sources_count": len([src for src in image_sources if src]),
        "albumFiles_present": album_files is not None,
        "albumFiles_error": album_files_error,
        "albumFiles_summary": album_files_summary,
        "albumFiles_sample": album_files_sample,
    }


def debug_album(album_url: str, timeout: int) -> dict[str, Any]:
    """Fetch and parse normal+advanced album pages."""
    ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
    normal_url, advanced_url = build_variant_urls(album_url)
    album_key = get_album_key(normal_url)

    targets = (
        ("normal", normal_url, ANALYSIS_DIR / f"album_{album_key}.html"),
        (
            "advanced",
            advanced_url,
            ANALYSIS_DIR / f"album_{album_key}_advanced.html",
        ),
    )

    report: dict[str, Any] = {}
    for label, target_url, out_file in targets:
        html, fetch_meta, fetch_error = fetch_html(target_url, timeout=timeout)
        if fetch_error:
            report[label] = {
                "source_url": target_url,
                "saved_file": str(out_file),
                "error": fetch_error,
            }
            continue

        out_file.write_text(html, encoding="utf-8")
        report[label] = parse_html_report(
            html=html,
            source_url=target_url,
            saved_file=out_file,
            fetch_meta=fetch_meta or {},
        )

    parsed_path = ANALYSIS_DIR / f"album_{album_key}_parsed.json"
    parsed_path.write_text(
        json.dumps(report, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    report["_parsed_output_file"] = str(parsed_path)
    return report


def main() -> None:
    """CLI entrypoint for fetching and debugging one album page."""
    parser = argparse.ArgumentParser(
        description="Fetch Bunkr album page source and print debug parse report."
    )
    parser.add_argument(
        "album_url",
        nargs="?",
        default=DEFAULT_ALBUM_URL,
        help=f"Album URL (default: {DEFAULT_ALBUM_URL})",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Network timeout in seconds (default: {DEFAULT_TIMEOUT})",
    )
    args = parser.parse_args()

    report = debug_album(args.album_url, timeout=args.timeout)
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
