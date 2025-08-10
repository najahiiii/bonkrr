"""Data processing functions for bunkrr."""
import os
import asyncio
from aiohttp import ClientSession, ClientTimeout, client_exceptions
from bs4 import BeautifulSoup
from tqdm import tqdm
from fake_useragent import UserAgent
from bunkrr.utils import sanitize, filename_from_content_disposition, dedupe_path


MAX_CONCURRENT_DOWNLOADS = 16

def get_random_user_agent():
    """
    Returns a random user agent string.

    :return: A random user agent string.
    """
    ua = UserAgent()
    return ua.random


async def fetch_data(session, base_url, data_type):
    """
    Fetches either image data or album information from a given URL.

    Args:
        session (aiohttp.ClientSession): The aiohttp client session.
        base_url (str): The base URL to fetch data from.
        data_type (str): Type of data to fetch ('image' or 'album').

    Returns:
        str or list: The name of the album if 'album' type or a list of image data.
    """
    try:
        async with session.get(base_url) as response:
            response.raise_for_status()
            html = await response.text()

            soup = BeautifulSoup(html, 'html.parser')
            if data_type == 'album-name':
                album_info = soup.find('div', class_='sm:text-lg')
                if album_info:
                    album_name = album_info.find('h1').text.strip()
                    return album_name
                return None
            if data_type == 'image-url':
                data = soup.find_all('div', class_='grid-images_box-txt')
                if not data:
                    print("\n[!] Failed to grab file URLs.")
                    return None
                return data
    except client_exceptions.InvalidURL as e:
        print(f"\n[!] Invalid URL: {e}")
        return None
    except client_exceptions.ClientError as ce:
        print(f"\n[!] Client error: {ce}")
        return None


async def create_download_folder(base_path, *args):
    """
    Create a download folder at the specified base path.

    Args:
        base_path (str): The base path where the download folder should be created.
        *args: Variable number of arguments representing the folder name or subdirectories.

    Returns:
        str: The path of the created download folder.

    """
    if len(args) == 1:
        folder_name = args[0]
        path = os.path.join(base_path, folder_name)
        if not os.path.exists(path):
            os.makedirs(path)
    else:
        folder_name = os.path.join(base_path, *args)
        if not os.path.exists(folder_name):
            os.makedirs(folder_name)
        path = folder_name

    return path


async def download_media(session, url, path, suggested_name=None):
    """
    Downloads media from the given URL and saves it to the specified path.
    ...
    """
    error_message = None

    try:
        headers = {"User-Agent": get_random_user_agent()}
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
                # Prefer the human-readable filename passed from the album HTML
                fallback = os.path.basename(url)
                ext = os.path.splitext(fallback)[1]

                if suggested_name:
                    base = sanitize(suggested_name)
                    if not os.path.splitext(base)[1] and ext:
                        base = base + ext
                else:
                    # fallback to server header (if any), then URL basename
                    cd = response.headers.get('Content-Disposition') or response.headers.get('content-disposition')
                    pretty = filename_from_content_disposition(cd)
                    if pretty:
                        base = sanitize(pretty)
                        if not os.path.splitext(base)[1] and ext:
                            base = base + ext
                    else:
                        base = sanitize(fallback)

                file_path = os.path.join(path, base)
                file_path = dedupe_path(file_path)

                file_size = int(response.headers.get('content-length', 0))
                with open(file_path, "wb") as file, tqdm(
                    desc=os.path.basename(file_path),
                    total=file_size,
                    unit='B',
                    unit_scale=True,
                    unit_divisor=1024,
                    leave=False
                ) as progress_bar:
                    while True:
                        chunk = await response.content.read(1024)
                        if not chunk:
                            break
                        file.write(chunk)
                        progress_bar.update(len(chunk))
                return True, None

            return False, None

    except client_exceptions.ClientError as e:
        error_message = f"\n[!] Failed to download '{file_path}': {e}"

    return False, error_message


async def download_images_from_urls(urls, album_folder):
    """
    Downloads images from a list of URLs asynchronously.

    Accepts either:
      - ["https://.../uuid.mp4", ...]  (old behavior)
      - [("https://.../uuid.mp4", "Pretty Name.mp4"), ...]  (new, preferred)
    """
    timeout = ClientTimeout(total=None)
    async with ClientSession(timeout=timeout) as session:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

        async def download_media_wrapper(item):
            # item can be a string (url) or a (url, nice_name) tuple
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                url, nice = item[0], item[1]
            else:
                url, nice = item, None
            async with semaphore:
                return await download_media(session, url, album_folder, suggested_name=nice)

        tasks = [download_media_wrapper(item) for item in urls]
        results = await asyncio.gather(*tasks)

        downloaded_files = [
            (item[0] if isinstance(item, (list, tuple)) and len(item) >= 1 else item)
            for item, result in zip(urls, results) if result[0] is True
        ]
        failed_files = [
            (item[0] if isinstance(item, (list, tuple)) and len(item) >= 1 else item)
            for item, result in zip(urls, results) if result[0] is False
        ]
        error_messages = [result[1] for result in results if result[1] is not None]

        return downloaded_files, failed_files, error_messages
