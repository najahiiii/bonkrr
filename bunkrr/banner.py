"""CLI banner utilities."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Sequence

VERSION_PATH = Path(__file__).resolve().parent.parent / "VERSION"

_ICON_ASCII = (
    "        +++++++++++++++++++",
    "        +++++++++++++++++++",
    "        ++++           ++++",
    "        +++++ ++++++  +++++",
    "        ++ + +++++++++ + ++",
    "        ++  +++ +++ +++  ++",
    "        ++ +++++++++++++ ++",
    "        ++ ++ +++++++ ++ ++",
    "        ++ ++++++ ++++++ ++",
    "        ++ +++++   +++++ ++",
    "        ++ ++++++ ++++++ ++",
    "        ++ ++ +++++++ ++ ++",
    "        ++ +++++++++++++ ++",
    "        ++  +++ +++ +++  ++",
    "        ++ + +++++++++ + ++",
    "        +++++ ++++++  +++++",
    "        ++++           ++++",
    "        +++++++++++++++++++",
    "        +++++++++++++++++++",
)

_BUNKR_ASCII = (
    " ____  _   _ _   _ _  __ ____  ",
    "| __ )| | | | \\ | | |/ /|  _ \\ ",
    "|  _ \\| | | |  \\| | ' / | |_) |",
    "| |_) | |_| | |\\  | . \\ |  _ < ",
    "|____/ \\___/|_| \\_|_|\\_\\|_| \\_\\",
)


def _pad_lines(lines: tuple[str, ...], target_height: int, centered: bool) -> list[str]:
    """Pad lines to target height. Use vertical-centering when requested."""
    out = list(lines)
    missing = target_height - len(out)
    if missing <= 0:
        return out

    if centered:
        top = missing // 2
        bottom = missing - top
        return ([""] * top) + out + ([""] * bottom)

    out.extend([""] * missing)
    return out


def _read_cli_version() -> str:
    """
    Read CLI version from VERSION file.

    Rules:
    - `v1.1.0` -> rendered as `v1.1.0-<md5[:7]>`
    - `...HASH` placeholder -> replace HASH with md5[:7]
    - already-suffixed values (e.g. `v1.1.0-abc1234`) are returned as-is
    """
    try:
        raw_bytes = VERSION_PATH.read_bytes()
        raw_text = raw_bytes.decode("utf-8", errors="replace").strip()
    except OSError:
        return "unknown"

    if not raw_text:
        return "unknown"

    digest7 = hashlib.md5(raw_bytes).hexdigest()[:7]  # nosec B324
    normalized = raw_text.replace(" - HASH", "-HASH")
    if normalized.endswith("-HASH"):
        return f"{normalized[:-4]}{digest7}"

    # If caller already provided a non-placeholder suffix, keep it.
    if "-" in normalized:
        tail = normalized.rsplit("-", 1)[-1]
        if tail and tail.upper() != "HASH":
            return normalized

    return f"{normalized}-{digest7}"


def render_banner(
    separator: str = " | ",
    extra_right_lines: Sequence[str] | None = None,
) -> str:
    """Render banner as `ascii icon | ascii teks`."""
    right_block = list(_BUNKR_ASCII)
    if extra_right_lines:
        right_block.extend(str(line) for line in extra_right_lines)

    right_block_tuple = tuple(right_block)
    height = max(len(_ICON_ASCII), len(right_block_tuple))
    left = _pad_lines(_ICON_ASCII, height, centered=False)
    right = _pad_lines(right_block_tuple, height, centered=False)
    left_width = max(len(line) for line in left)

    rows = [
        f"{left_line.ljust(left_width)}{separator}{right_line}"
        for left_line, right_line in zip(left, right)
    ]
    return "\n".join(rows)


def render_main_menu_banner(separator: str = " | ") -> str:
    """Render banner and use empty right-side area for CLI main menu hints."""
    version = _read_cli_version()
    title_width = max(len(line) for line in _BUNKR_ASCII)
    version_line = version.rjust(title_width)
    menu_lines = (
        version_line,
        "",
        "Main menu:",
        "[1] Quick download",
        "[2] Manage albums",
        "[3] Sync managed albums",
        "[4] Exit",
        "",
        "Input options:",
        "- Paste album URL directly",
        "- Paste url-file path",
        "- Paste comma-separated URLs",
        "",
        "Select menu / input URL below.",
        "",
    )
    return render_banner(separator=separator, extra_right_lines=menu_lines)


def print_banner() -> None:
    """Print rendered banner."""
    print(render_banner())
