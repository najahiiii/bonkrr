"""User input functions for bunkrr."""
import os
import sys


DEFAULT_PARENT_FOLDER = 'downloads'

async def choices(prompt):
    """
    Prompt the user with a message and return based on their input.

    Args:
        prompt (str): The message to display to the user.

    Returns:
        None: If the user enters 'y'.
        None: If the user enters 'n' or leaves the input empty.

    Raises:
        SystemExit: If the user enters any other input.
    """
    i = input(prompt).strip().lower()
    if i == 'y':
        return
    if i == 'n' or not i:
        sys.exit(1)
    else:
        sys.exit(1)


def get_user_folder():
    """
    Prompts the user to enter an album folder name and returns the path of the folder.

    If the user enters a folder name, it is appended to the current working directory
    and the default parent folder.
    If the user leaves the input blank, the default parent folder is appended to
    the current working directory.

    Returns:
        str: The path of the album folder.
    """
    album_folder_input = input(
        "[?] Enter album folder name (or leave blank to use default): "
    ).strip()

    if album_folder_input.strip():
        album_folder = os.path.join(
            os.getcwd(),
            DEFAULT_PARENT_FOLDER,
            album_folder_input.strip())
    else:
        album_folder = os.path.join(os.getcwd(), DEFAULT_PARENT_FOLDER)

    print(f"[^] Download folder: {album_folder}")
    return album_folder
