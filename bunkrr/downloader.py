"""This module contains the function to download images from bunkrr albums."""
import os
from aiohttp import ClientSession
from bunkrr.user_input import get_user_folder, choices
from bunkrr.data_processing import (
    fetch_data,
    create_download_folder,
    download_images_from_urls
)

async def downloader():
    """
    Downloads images from bunkrr albums.

    This function prompts the user to enter bunkrr album
    URLs or provide a file path containing the URLs.
    It then downloads the images from the specified albums and saves them to the user's folder.

    Returns:
        None
    """
    while True:
        urls = input(
            "[?] Enter bunkrr Album URLs (Support multiple URLs separated by comma)"
            " or provide a file path: "
        ).strip()
        if os.path.isfile(urls):
            with open(urls, 'r', encoding='utf-8') as file:
                urls = file.read().splitlines()
        else:
            urls = urls.split(',')
        urls = [url.strip() for url in urls]

        parent_folder = get_user_folder()
        downloaded_total = 0
        failed_total = 0
        error_messages = []

        if len(urls) == 1:
            album_info = None
            async with ClientSession() as session:
                album_info = await fetch_data(session, urls[0], 'album-name')
                if album_info:
                    print(f"\n[*] Downloading file(s) from album: {album_info}")
                image_data = await fetch_data(session, urls[0], 'image-url')
                if image_data is not None:
                    folder_path = await create_download_folder(parent_folder)
                    download_urls = [
                        data.find('img')['src'].replace('/thumbs/', '/').rsplit('.', 1)[0] +
                        os.path.splitext(data.find('p').text.strip())[1] for data in image_data
                    ]
                    downloaded, failed, errors = await download_images_from_urls(
                        download_urls, folder_path
                    )
                    downloaded_total += len(downloaded)
                    failed_total += len(failed)
                    error_messages.extend(errors)

        else:
            count = 1
            for url in urls:
                async with ClientSession() as session:
                    album_info = await fetch_data(session, url, 'album-name')
                    if album_info:
                        print(
                            f"\n[*] Downloading file(s) from album: {album_info}")
                    image_data = await fetch_data(session, url, 'image-url')
                    if image_data is not None:
                        folder_name = str(count)
                        folder_path = await create_download_folder(parent_folder, folder_name)
                        download_urls = [
                            data.find('img')['src'].replace('/thumbs/', '/').rsplit('.', 1)[0] +
                            os.path.splitext(data.find('p').text.strip())[1] for data in image_data
                        ]
                        downloaded, failed, errors = await download_images_from_urls(
                            download_urls, folder_path
                        )
                        downloaded_total += len(downloaded)
                        failed_total += len(failed)
                        error_messages.extend(errors)
                        count += 1

        downloaded_plural = 'file' if downloaded_total <= 1 else 'files'
        failed_plural = 'file' if failed_total <= 1 else 'files'

        print(f"\n[^] Downloaded: {downloaded_total} {downloaded_plural}, "
              f"Failed: {failed_total} {failed_plural}.")

        for error_message in error_messages:
            print(error_message)

        await choices("[?] Do you want to download again? (Y/N, default N): ")
