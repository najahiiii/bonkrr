"""main module"""

import asyncio
import sys

from bunkrr.downloader import downloader as dl


async def main():
    """
    The main function that runs the program.
    """
    try:
        while True:
            await dl()
    except KeyboardInterrupt:
        print("\n[!] Exiting...")
        sys.exit(0)


if __name__ == "__main__":
    asyncio.run(main())
