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


def get_user_folder(default_name=None):
    """
    Ask user to enter album folder name. If left blank, use default_name or fallback to 'downloads'.

    Returns:
        str: Path to the folder
    """
    album = input("[?] Enter album folder name (leave blank for auto): ").strip()
    cwd = os.getcwd()

    if album:
        album_folder = os.path.join(cwd, DEFAULT_PARENT_FOLDER, album)
    elif default_name:
        album_folder = os.path.join(cwd, DEFAULT_PARENT_FOLDER, default_name)
    else:
        album_folder = os.path.join(cwd, DEFAULT_PARENT_FOLDER)

    return album_folder
