"""This modules contains common utils"""
import re

def sanitize(name: str) -> str:
    """
    Sanitize a string to be safe for folder/file names.
    """
    return re.sub(r'[\\/*?:"<>|]', "_", name) if name else "album"
