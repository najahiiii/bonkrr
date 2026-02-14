# Bunkrr Media Downloader

Yet another CLI downloader for Bunkr albums with local metadata store, managed album sync, and media management.

## Features

- Quick download from album URL(s) or URL file path
- Managed album library (add/list/remove)
- Sync metadata only (without downloading files)
- Sync managed albums with download
- Media management per album (grouped by category: image, video, archive, other)
- Media download actions from managed media view:
  - download missing items
  - download by category
  - download by selected item(s)
- Per-album remove policy when remote media disappears:
  - `retain` local file (default)
  - `delete` local file
- SQLite store in project root: `albums.db` (WAL mode)
- Existing files are detected/skipped and tracked

## Requirements

- Python 3
- `aiohttp`
- `beautifulsoup4`
- `tqdm`
- `fake-useragent`

Recommended: use a virtual environment before installing dependencies.

```bash
python3 -m venv .venv
source .venv/bin/activate
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run

```bash
python3 -m bunkrr
```

Main menu:

- `[1] Quick download`
- `[2] Manage albums`
- `[3] Sync managed albums`
- `[4] Exit`

You can also paste album URL or URL-file path directly at the main prompt.

## Managed Albums

From `Manage albums`, available actions:

- `[A] Add`
- `[M] Media`
- `[S] Sync metadata`
- `[T] Toggle remove policy`
- `[R] Remove`
- `[B] Back`

Action input supports both shortcut and keyword, for example:

- `A` or `add`
- `M` or `media`
- `S` or `sync`
- `T` or `toggle`
- `R` or `remove`
- `B`, `back`, or `q`

### Add flow

When adding an album:

- Set URL, label, and target folder
- Choose remove policy (`y/N` to delete local file on remote removal)
- Prompt `Sync and download now? (y/N)`:
  - `N` -> sync metadata only
  - `Y` -> sync + download files

### Media flow

Media view is grouped by category and supports:

- `L`: download missing media items
- `K`: download by category (`image`, `video`, `archive`, `other`)
  - available categories are shown dynamically with media counts
- `I`: download by selected media item(s)
  - supports alias and DB ID selection
  - example inputs: `V1`, `123`, `V1-V3`, `all`
- `D`: delete DB row and local file
- `X`: delete DB row only
- `S`: sync metadata for selected managed album
- `B`: back

Media item display format:

- Alias + DB ID, example: `[V1/123]`
- Legend:
  - remote `🟢` active / `⚪` removed
  - local `💾` downloaded / `☁️` missing

## Data Store

- Default DB path: `./albums.db`
- Override path with `BUNKR_DB_PATH`
- Journal mode: `WAL`

## Environment Variables

- `BUNKR_CONCURRENCY` (default `12`): max parallel downloads
- `BUNKR_DEBUG`: debug resolver/download logging
- `BUNKR_LIMIT`: process only first N items (debug/testing)
- `BUNKR_SYNC_DB` (default enabled): enable metadata DB sync
- `BUNKR_DB_PATH`: custom SQLite DB path
- `BUNKR_CLEAR_SCREEN` (default enabled): clear terminal between menu redraws
- `BUNKR_PAUSE_ON_REFRESH` (default enabled): pause after actions before clear

## Notes

- Always use a virtual environment (`.venv`) to avoid dependency conflicts with global Python packages, check [Requirements](#requirements).
- Single-file Bunkr URLs (`/f/`, `/i/`, `/v/`) are rejected in album flows.
- Folder/file names are sanitized and deduped.
- Downloader resolves Bunkr file pages and CDN redirects automatically.

## Contributors

- [Contributors](https://github.com/najahiiii/bonkrr/graphs/contributors)

## License

This project is licensed under the [MIT License](LICENSE).
