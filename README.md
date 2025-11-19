# Bunkrr Media Downloader

Downloads media from Bunkr albums, following click‑through pages and CDN redirects automatically.

## Features

- Multiple album URLs or a file with URLs
- Asynchronous downloads with configurable concurrency
- Album pagination discovery (fetches all pages)
- Resolves Bunkr “Download” pages and follows to CDN
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
  - Controls the number of parallel downloads. Lower when hitting rate limits (e.g., `3–6`).
- `BUNKR_DEBUG`
  - `1`/`true` to print resolver steps (album pages, each GET hop, CDN trials, save paths).
- `BUNKR_LIMIT`
  - Integer; process only the first N items (handy with debug).
- `BUNKR_CDN_EXTRA`
  - Comma/space‑separated list of additional CDN hosts to try for this run.
  - Accepts either a subdomain prefix (`beer`) or a full hostname (`c1.cache8.st`).
- `BUNKR_CDN_FILE`
  - Path to a file listing extra CDN hosts, one per line (comments with `#` allowed).
  - Defaults to `cdn_hosts.txt` in the current directory.
  - Set to empty (`""`) to disable creating/updating the file.

Notes:

- The downloader auto‑discovers the file path from the album’s “get” page, then probes CDN hosts. The first working host in a session becomes the preferred host and is tried first next time. Discovered hosts are appended to `cdn_hosts.txt` unless disabled.
- Existing files are skipped before downloads start (including deduped variants like `Name (1).mp4`).

## Adding CDN Hosts

You can supply new CDN hosts in three ways (highest priority first):

1) Preferred (auto‑learned): once a host works (e.g., `rum.bunkr.ru`), it is tried first for the rest of the session and written to `cdn_hosts.txt`.

2) Built‑in + extras: the downloader ships with a list of known hosts; add more for a single run via:
  `BUNKR_CDN_EXTRA="maple nachos bacon" python3 -m bunkrr`

3) Persistent file: maintain `cdn_hosts.txt` with one host per line (prefix like `beer` or full host like `c1.cache8.st`). Example:

```text
# extra bunkr subdomains
maple
rice

# external CDNs
mlk-bk.cdn.gigachad-cdn.ru
c1.cache8.st
```

Disable persistence by running with `BUNKR_CDN_FILE=""`.

## Troubleshooting

- If many items fail to resolve, try lowering concurrency:
  - `BUNKR_CONCURRENCY=4 python3 -m bunkrr`
- If you see a new CDN host in your browser (e.g., `maple.bunkr.ru`), add it via:
  - `BUNKR_CDN_EXTRA="maple" python3 -m bunkrr` or add `maple` to `cdn_hosts.txt`.
- For diagnosis on a small slice:
  - `BUNKR_DEBUG=1 BUNKR_LIMIT=5 python3 -m bunkrr`

## Contributors

- [Contributors](https://github.com/najahiiii/bonkrr/graphs/contributors)

## License

This project is licensed under the [MIT License](LICENSE).
