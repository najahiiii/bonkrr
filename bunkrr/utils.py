"""This modules contains common utils"""

import os
import re
import sys
import urllib.parse
from typing import Mapping, Optional

DEFAULT_PARENT_FOLDER = "downloads"


def choices(prompt: str) -> Optional[None]:
    """
    Prompt the user with a message and handle their input.

    Args:
        prompt (str): The message to display to the user.

    Returns:
        None: If the user enters 'y'.

    Raises:
        SystemExit: If the user enters 'n', empty input, or any other invalid input.
    """
    i = input(prompt).strip().lower()
    if i == "y":
        return
    if i == "n" or not i:
        sys.exit(1)
    else:
        sys.exit(1)


def get_user_folder(default_name: Optional[str] = None) -> str:
    """
    Prompt user to enter album folder name. If left blank, use `default_name` or
    fallback to 'downloads'.

    Args:
        default_name (Optional[str]): Optional default folder name to use if user input is blank.

    Returns:
        str: Full path to the album folder, relative to current working directory.
    """
    album = input("[?] Enter album folder name (leave blank for auto): ").strip()
    cwd = os.getcwd()

    if album:
        album_folder = os.path.join(cwd, DEFAULT_PARENT_FOLDER, album)
    elif default_name:
        album_folder = os.path.join(cwd, DEFAULT_PARENT_FOLDER, default_name)
    else:
        album_folder = os.path.join(cwd, DEFAULT_PARENT_FOLDER)

    return album_folder


def sanitize(name: Optional[str]) -> str:
    """
    Sanitize a string to be safe for folder/file names by replacing invalid
    characters with underscores. If input is None or empty, returns "album".

    Args:
        name (Optional[str]): The input string to sanitize.

    Returns:
        str: A sanitized string safe to use as filename or folder name.
    """
    return re.sub(r'[\\/*?:"<>|]', "_", name) if name else "album"


def extract_filename(cd: Optional[str]) -> Optional[str]:
    """
    Extract a filename from a Content-Disposition header.

    Supports RFC 5987 (filename*) and fallback filename= forms.
    Returns None if no filename is found.

    Args:
        cd (Optional[str]): The Content-Disposition header value.

    Returns:
        Optional[str]: Extracted filename or None if not found.
    """
    if not cd:
        return None

    m = re.search(r"filename\*\s*=\s*([^']*)'[^']*'([^;]+)", cd, flags=re.I)
    if m:
        return urllib.parse.unquote(m.group(2)).strip().strip('"')

    m = re.search(r'filename\s*=\s*"([^"]+)"', cd, flags=re.I)
    if m:
        return m.group(1).strip()

    m = re.search(r"filename\s*=\s*([^;]+)", cd, flags=re.I)
    if m:
        return m.group(1).strip().strip('"')

    return None


def dedupe_path(path: str) -> str:
    """
    Generate a non-conflicting file path by appending ' (1)', ' (2)', etc.
    before the file extension if the path already exists.

    Args:
        path (str): Original file path.

    Returns:
        str: A unique file path that does not yet exist.
    """
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    i = 1
    while True:
        candidate = f"{root} ({i}){ext}"
        if not os.path.exists(candidate):
            return candidate
        i += 1


def get_filename(
    url: str, suggested_name: Optional[str], headers: Mapping[str, str]
) -> str:
    """
    Determine the filename to use for saving a downloaded file.

    This function tries to generate a clean, sanitized filename based on:
    - A suggested name (if provided),
    - The Content-Disposition header (if present),
    - Or the URL basename as a fallback.
    It also ensures the filename has an appropriate file extension.

    Args:
        url (str): The URL of the file to download.
        suggested_name (Optional[str]): Optional suggested filename from external source.
        headers (Mapping[str, str]): HTTP response headers.

    Returns:
        str: A sanitized filename with an appropriate extension.
    """
    fallback = os.path.basename(url)
    ext = os.path.splitext(fallback)[1]

    if suggested_name:
        base = sanitize(suggested_name)
        if not os.path.splitext(base)[1] and ext:
            base += ext
    else:
        cd = headers.get("Content-Disposition") or headers.get("content-disposition")
        pretty = extract_filename(cd)
        base = sanitize(pretty) if pretty else sanitize(fallback)
        if not os.path.splitext(base)[1] and ext:
            base += ext

    return base
