# Bunkrr Media Downloader

This Python script allows users to download media files from bunkrr Albums.

## Description

The script fetches image data from a bunkrr Album URL provided by the user and proceeds to download the images into a specified folder.

## Features

- **Multiple URL Support**: Download media from multiple bunkrr album URLs provided by the user.
- **Asynchronous Download**: Downloads media asynchronously to improve efficiency.
- **Folder Organization**: Saves downloaded media into separate folders for each album.

## Requirements

- Python 3.x
- `venv`
- `aiohttp`
- `beautifulsoup4`
- `tqdm`
- `fake_useragent`

## Installation

1. Clone the repository:

   ```bash
   git clone https://github.com/najahiiii/bonkrr.git
   ```

2. Navigate to the project directory:

   ```bash
   cd bonkrr
   ```

3. Create and activate a virtual environment:

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   ```

4. Install the required packages inside the virtual environment:

   ```bash
   pip install -r requirements.txt
   ```

## Usage

1. Run the script:

   ```bash
   python3 -m bunkrr
   ```

2. Enter the bunkrr Album URL and the download folder path as prompted.
3. The script will begin fetching and downloading the media files. The progress will be displayed.

## Contributors

- [Contributors](https://github.com/najahiiii/bonkrr/graphs/contributors)

## License

This project is licensed under the [MIT License](https://github.com/najahiiii/bonkrr/blob/main/LICENSE).
