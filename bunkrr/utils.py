"""This modules contains common utils"""
import re
import urllib.parse

def sanitize(name: str) -> str:
    """
    Sanitize a string to be safe for folder/file names.
    """
    return re.sub(r'[\\/*?:"<>|]', "_", name) if name else "album"


def filename_from_content_disposition(cd: str) -> str | None:
    """Extract a filename from a Content-Disposition header.

    Supports RFC 5987 (filename*) and fallback filename= forms.
    Returns None if no filename is found.
    """
    if not cd:
        return None

    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", cd, flags=re.I)
    if m:
        return urllib.parse.unquote(m.group(2)).strip().strip('"')

    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, flags=re.I)
    if m:
        return m.group(1).strip()

    m = re.search(r'filename\s*=\s*([^;]+)', cd, flags=re.I)
    if m:
        return m.group(1).strip().strip('"')

    return None


def dedupe_path(path: str) -> str:
    """If path exists, append ' (1)', ' (2)', ... before the extension."""
    import os
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while True:
        candidate = f"{root} ({i}){ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1
