"""script to download media from bunkrr Album."""
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm
from fake_useragent import UserAgent


def get_user_input():
    """
    Prompt the user to enter the bunkrr Album URL and download folder path.

    Returns:
    - base_url (str): The bunkrr Album URL entered by the user.
    - base_path (str): The download folder path entered by the user.
    """
    print("-----------------------------------------")
    base_url = input("[?] Enter bunkrr Album URL: ")
    base_path = input("[?] Enter download folder: ")
    print("-----------------------------------------")
    return base_url, base_path


def download_media(base_url, file_path, headers):
    """
    Download media from the given URL and save it to the specified file path.

    Args:
    - base_url (str): The URL of the media to be downloaded.
    - file_path (str): The file path where the media will be saved.
    - headers (dict): HTTP headers to be included in the request.

    Returns:
    - success (bool): True if the download is successful, False otherwise.
    - file_path (str): The file path where the media is saved if the download is
      successful, or an error message if the download fails.
    """
    try:
        response = requests.get(
            base_url,
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
        else:
            return(
                False,
                f"[!] Failed to download '{file_path}'. Status code: {response.status_code}"
            )
    except requests.exceptions.RequestException as e:
        return False, f"[!] Failed to download '{file_path}': {e}"
    except IOError as e:
        return False, f"[!] Failed to download '{file_path}': {e}"


def main(base_url, base_path):
    """
    Main function to download media from the bunkrr Album.

    Args:
    - base_url (str): The bunkrr Album URL.
    - base_path (str): The download folder path.

    Prints the number of downloaded files and failed files.
    """
    response = requests.get(base_url, timeout=None)
    if response.status_code != 200:
        print("[!] Failed to open URL.")
        return

    soup = BeautifulSoup(response.content, 'html.parser')
    image_data = soup.find_all('div', class_='grid-images_box')
    if not image_data:
        print("[!] Failed grab file url.")
        return

    folder_path = os.path.join(os.getcwd(), base_path)

    if not os.path.exists(folder_path):
        os.makedirs(folder_path)

    download_urls = []
    for data in image_data:
        img_src = data.find('img')['src']
        first_paragraph = data.find('p').text.strip()
        file_extension = os.path.splitext(first_paragraph)[1]
        modified_url = img_src.replace(
            '/thumbs/', '/').rsplit('.', 1)[0] + file_extension
        download_urls.append(modified_url)

    user_agent = UserAgent()
    headers = {"User-Agent": user_agent.random}

    with ThreadPoolExecutor(max_workers=10) as executor:
        future_results = {
            executor.submit(
                download_media,
                url,
                os.path.join(folder_path, os.path.basename(url)),
                headers
            ): url for url in download_urls
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


if __name__ == "__main__":
    url, folder_name = get_user_input()
    main(url, folder_name)
