# Bunkrr Media Downloader

This Python script allows users to download media files from bunkrr Albums.

## Description

The script fetches image data from a bunkrr Album URL provided by the user and proceeds to download the images into a specified folder.

## Features

- Fetches image data from the provided bunkrr Album URL
- Downloads images concurrently into a specified folder
- Provides progress bar for each download

## Requirements

- Python 3.x
- `requests`
- `beautifulsoup4`
- `tqdm`
- `fake-useragent`

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/najahiiii/bonkrr.git
   ```
2. Navigate to the project directory:
   ```bash
   cd bonkrr
   ```
3. Install the required packages:
   ```bash
   python3 -m pip install -r requirements.txt
   ```

## Usage

1. Run the script:
   ```bash
   python3 bunkrr.py
   ```
2. Enter the bunkrr Album URL and the download folder path as prompted.
3. The script will begin fetching and downloading the media files. The progress will be displayed.

## Contributors
   - <a href="https://github.com/najahiiii/bonkrr/graphs/contributors" target="_blank">Contributors</a>.

## License
This project is licensed under the <a href="https://github.com/najahiiii/bonkrr/blob/main/LICENSE" target="_blank">MIT License</a>.