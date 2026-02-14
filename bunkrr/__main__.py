"""main module"""

# pylint: disable=broad-exception-caught

import asyncio
import sys

from bunkrr.downloader import downloader as dl


async def main():
    """
    The main function that runs the program.
    """
    await dl()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n[!] Exiting...")
        sys.exit(0)
    except EOFError:
        print("\n[!] Input stream closed. Exiting...")
        sys.exit(0)
    except Exception as error:  # pragma: no cover - top-level safety net
        print(f"\n[!] Unexpected fatal error: {error}")
        sys.exit(1)
