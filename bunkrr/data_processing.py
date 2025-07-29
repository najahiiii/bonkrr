"""Data processing functions for bunkrr."""
import os
import asyncio
from aiohttp import ClientSession, ClientTimeout, client_exceptions
from bs4 import BeautifulSoup
from tqdm import tqdm
from fake_useragent import UserAgent

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
                album_info = soup.find('div', class_='mb-12-xxx')
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


async def download_media(session, url, path):
    """
    Downloads media from the given URL and saves it to the specified path.

    Args:
        session (aiohttp.ClientSession): The aiohttp client session.
        url (str): The URL of the media to download.
        path (str): The path where the downloaded media will be saved.

    Returns:
        tuple: A tuple containing a boolean indicating whether the download was successful
               and an error message if the download failed.
    """
    file_path = os.path.join(path, os.path.basename(url))
    error_message = None

    try:
        headers = {"User-Agent": get_random_user_agent()}
        async with session.get(url, headers=headers) as response:
            if response.status == 200:
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

    Args:
        urls (list): A list of URLs of the images to be downloaded.
        album_folder (str): The folder where the downloaded images will be saved.

    Returns:
        tuple: A tuple containing three lists:
            - downloaded_files: URLs of the successfully downloaded images.
            - failed_files: URLs of the images that failed to download.
            - error_messages: Error messages corresponding to the failed downloads.
    """
    timeout = ClientTimeout(total=None)
    async with ClientSession(timeout=timeout) as session:
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

        async def download_media_wrapper(url):
            async with semaphore:
                return await download_media(session, url, album_folder)

        tasks = [download_media_wrapper(url) for url in urls]
        results = await asyncio.gather(*tasks)

        downloaded_files = [
            url for url, result in zip(urls, results) if result[0] is True
        ]
        failed_files = [
            url for url, result in zip(urls, results) if result[0] is False
        ]
        error_messages = [
            result[1] for result in results if result[1] is not None
        ]

        return downloaded_files, failed_files, error_messages
