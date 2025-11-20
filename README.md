# Bunkrr Media Downloader

Downloads media from Bunkr albums, following click‑through pages and CDN redirects automatically.

## Features

- Multiple album URLs or a file with URLs
- Asynchronous downloads with configurable concurrency
- Album pagination discovery (fetches all pages)
- Resolves Bunkr “Download” pages via API (data-file-id → final URL)
- Uses album titles for filenames; sanitizes and dedupes
- Skips files that already exist in the album folder

## Requirements

- Python 3.x
- `venv`
- `aiohttp`
- `beautifulsoup4`
- `tqdm`
- `fake_useragent`

## Installation

1. Clone and enter the repo:

   ```bash
   git clone https://github.com/najahiiii/bonkrr.git
   cd bonkrr
   ```

2. Create and activate a virtualenv:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

3. Install deps:

   ```bash
   pip install -r requirements.txt
   ```

## Usage

```bash
python3 -m bunkrr
```

Follow the prompts and paste one or more album URLs. A downloads folder is created with one subfolder per album.

## Environment Options

- `BUNKR_CONCURRENCY`
  - Integer; default `12`.
  - Controls the number of parallel downloads. Lower when hitting rate limits (e.g., `2–6`).
- `BUNKR_DEBUG`
  - `1`/`true` to print resolver steps (album pages, each GET hop, CDN trials, save paths).
- `BUNKR_LIMIT`
  - Integer; process only the first N items (handy with debug).

Notes:

- The downloader extracts `data-file-id` from file pages and asks the bunkr API for the decrypted final URL. It already retries on HTTP 429 with backoff; lowering concurrency helps if rate limits persist.
- Existing files are skipped before downloads start (including deduped variants like `Name (1).mp4`).

## Troubleshooting

- If many items fail to resolve, try lowering concurrency:
  - `BUNKR_CONCURRENCY=3 python3 -m bunkrr`
- For diagnosis on a small slice:
  - `BUNKR_DEBUG=1 BUNKR_LIMIT=5 python3 -m bunkrr`

## Contributors

- [Contributors](https://github.com/najahiiii/bonkrr/graphs/contributors)

## License

This project is licensed under the [MIT License](LICENSE).
