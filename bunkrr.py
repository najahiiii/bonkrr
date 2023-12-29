"""script to download media from bunkrr Album."""
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from validators import url as validate_url
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm
from fake_useragent import UserAgent

DEFAULT_PARENT_FOLDER = 'downloads'


def validate_input(func):
    """
    Decorator function that validates the input of a given function.

    Args:
        func: The function to be decorated.

    Returns:
        The decorated function.
    """
    def wrapper(*args, **kwargs):
        while True:
            try:
                return func(*args, **kwargs)
            except ValueError as ve:
                print(f"[!] Error: {ve}")
    return wrapper


@validate_input
def get_user_input():
    """
    Prompts the user to enter the bunkrr Album URL and album folder name.
    Validates the input and returns the base URL and album folder path.

    Raises:
        ValueError: If the bunkrr Album URL is empty or has an invalid format.

    Returns:
        tuple: A tuple containing the base URL and album folder path.
    """
    print("-----------------------------------------")
    base_url = input("[?] Enter bunkrr Album URL: ")
    if not base_url:
        raise ValueError("Bunkrr Album URL cannot be empty!")
    if not validate_url(base_url):
        raise ValueError("Invalid URL format! Please enter a valid URL.")
    album_folder_input = input("[?] Enter album folder name: ")

    if album_folder_input.strip():
        album_folder = os.path.join(
            os.getcwd(),
            DEFAULT_PARENT_FOLDER,
            album_folder_input.strip())
    else:
        album_folder = os.path.join(os.getcwd(), DEFAULT_PARENT_FOLDER)

    print(f"[^] Download folder: {album_folder}")
    print("-----------------------------------------")
    return base_url, album_folder


def fetch_image_data(base_url):
    """
    Fetches image data from a given base URL.

    Args:
        base_url (str): The base URL to fetch image data from.

    Returns:
        list: A list of image data extracted from the HTML content.

    Raises:
        requests.RequestException: If there is an error while making the HTTP request.
    """
    try:
        response = requests.get(base_url, timeout=None)
        response.raise_for_status()

        soup = BeautifulSoup(response.content, 'html.parser')
        data = soup.find_all('div', class_='grid-images_box')
        if not data:
            print("[!] Failed to grab file URLs.")
            return None
    except requests.RequestException as e:
        print(f"[!] Error: {e}")
        return None

    return data


def create_download_folder(base_path):
    """
    Create a download folder at the specified base path if it doesn't exist.

    Args:
        base_path (str): The base path where the download folder will be created.

    Returns:
        str: The path of the created download folder.
    """
    path = os.path.join(os.getcwd(), base_path)

    if not os.path.exists(path):
        os.makedirs(path)

    return path


def download_media(args):
    """
    Downloads media from a given URL and saves it to a specified path.

    Args:
        args (tuple): A tuple containing the following elements:
            - urls (str): The URL of the media to be downloaded.
            - path (str): The path where the downloaded media will be saved.
            - headers (dict): Optional headers to be included in the request.

    Returns:
        tuple: A tuple containing the following elements:
            - success (bool): Indicates whether the download was successful.
            - file_path (str): The path of the downloaded file if successful,
            or an error message if unsuccessful.
    """
    urls, path, headers = args
    file_path = os.path.join(path, os.path.basename(urls))

    try:
        response = requests.get(
            urls,
            headers=headers,
            stream=True,
            timeout=None)
        if response.status_code == 200:
            file_size = int(response.headers.get('content-length', 0))

            with open(file_path, "wb") as file, tqdm(
                desc=os.path.basename(file_path),
                total=file_size,
                unit='B',
                unit_scale=True,
                unit_divisor=1024,
                leave=False
            ) as progress_bar:
                for data in response.iter_content(chunk_size=1024):
                    file.write(data)
                    progress_bar.update(len(data))

            return True, file_path
        return (
            False, f"[!] Failed to download '{file_path}'. Status code: {response.status_code}"
        )
    except requests.exceptions.RequestException as e:
        return False, f"[!] Failed to download '{file_path}': {e}"
    except IOError as e:
        return False, f"[!] Failed to download '{file_path}': {e}"


def generate_download_urls(d):
    """
    Generate download URLs for the given data.

    Args:
        d (list): A list of data.

    Returns:
        list: A list of download URLs.
    """
    urls = [
        data.find('img')['src'].replace('/thumbs/', '/').rsplit('.', 1)[0] +
        os.path.splitext(data.find('p').text.strip())[1]
        for data in d
    ]
    return urls


def download_images_from_urls(urls, album_folder):
    """
    Download images from a list of URLs and save them in the specified album folder.

    Args:
        urls (list): A list of URLs pointing to the images to be downloaded.
        album_folder (str): The path to the folder where the downloaded images will be saved.

    Returns:
        tuple: A tuple containing two lists:
            - A list of successfully downloaded files.
            - A list of files that failed to download.

    Example:
        urls = ['https://example.com/image1.jpg', 'https://example.com/image2.jpg']
        album_folder = '/path/to/album'
        downloaded_files, failed_files = download_images_from_urls(urls, album_folder)
    """
    user_agent = UserAgent()
    headers = {"User-Agent": user_agent.random}

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_results = {
            executor.submit(download_media,
                            (url,
                             album_folder,
                             headers)
                            ): url for url in urls
        }

        results = [future.result() for future in as_completed(future_results)]

        downloaded_files = [message for success, message in results if success]
        failed_files = [message for success, message in results if not success]

        downloaded_count = len(downloaded_files)
        failed_count = len(failed_files)

        downloaded_plural = 'file' if downloaded_count <= 1 else 'files'
        failed_plural = 'file' if failed_count <= 1 else 'files'

        print(f"\n[^] Downloaded: {downloaded_count} {downloaded_plural}, "
              f"Failed: {failed_count} {failed_plural}.")

    return downloaded_files, failed_files


if __name__ == "__main__":
    while True:
        while True:
            url, folder_name = get_user_input()
            image_data = fetch_image_data(url)
            if image_data is not None:
                break
            user_choice = input(
                "[!] Error fetching image data, Do you want to retry? (Y/N, default N): "
            ).lower() or 'n'
            if user_choice not in ['y', 'yes']:
                sys.exit(1)

        folder_path = create_download_folder(folder_name)
        download_urls = generate_download_urls(image_data)
        download_images_from_urls(download_urls, folder_path)
        download_again = input(
            "[?] Do you want to download again? (Y/N, default N): ").lower() or 'n'

        if download_again not in ['y', 'yes']:
            break
