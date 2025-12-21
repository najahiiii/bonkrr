"""API helper to resolve bunkr file ids into final media URLs."""

# pylint: disable=broad-exception-caught

import asyncio
import base64
from typing import Optional
from urllib.parse import quote

from aiohttp import ClientSession, ClientTimeout, client_exceptions

from bunkrr.utils import get_random_user_agent

API_URL = "https://apidl.bunkr.ru/api/_001_v2"


def _b64_to_bytes(b64_str: str) -> bytes:
    """Decode base64 string to raw bytes."""
    return base64.b64decode(b64_str)


def _xor_with_key(data: bytes, key: str) -> str:
    """XOR every byte of `data` with repeating `key` bytes, return UTF-8 string."""
    key_bytes = key.encode("utf-8")
    out = bytearray(len(data))

    for i, b in enumerate(data):
        out[i] = b ^ key_bytes[i % len(key_bytes)]

    return out.decode("utf-8", errors="replace")


async def resolve_bunkr_url(
    file_id: str,
    ogname: Optional[str] = None,
    session: Optional[ClientSession] = None,
    max_retries: int = 3,
    backoff_base: float = 1.5,
) -> str:
    """
    Resolve a bunkr file id to a final media URL via the bunkr API.

    Args:
        file_id (str): The file identifier (from data-file-id / data-id).
        ogname (Optional[str]): Optional original filename to append as ?n=<ogname>.
        session (Optional[ClientSession]): Reusable session; if not provided, a new one is created.
        max_retries (int): Retry attempts on rate limiting (429) or transient failures.
        backoff_base (float): Base seconds for exponential backoff when no Retry-After is provided.

    Returns:
        str: Decrypted, directly accessible media URL.
    """
    timeout = ClientTimeout(total=None, connect=30, sock_connect=30, sock_read=300)
    headers = {
        "Content-Type": "application/json",
        "Accept": "*/*",
        "User-Agent": get_random_user_agent(),
        "Origin": "https://get.bunkrr.su",
        "Referer": f"https://get.bunkrr.su/file/{file_id}",
        "Accept-Language": "en-US,en;q=0.8",
    }

    async def _call_with_retry(sess: ClientSession) -> dict:
        last_exc: Exception | None = None
        for attempt in range(max_retries):
            try:
                async with sess.post(
                    API_URL, json={"id": file_id}, headers=headers
                ) as resp:
                    if resp.status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        delay = (
                            float(retry_after)
                            if retry_after and retry_after.isdigit()
                            else backoff_base * (2**attempt)
                        )
                        await asyncio.sleep(delay)
                        continue
                    resp.raise_for_status()
                    return await resp.json()
            except client_exceptions.ClientError as e:
                last_exc = e
                delay = backoff_base * (2**attempt)
                await asyncio.sleep(delay)
            except Exception as e:
                last_exc = e
                break
        if last_exc:
            raise last_exc
        raise RuntimeError("Unexpected retry loop exit")

    if session:
        data = await _call_with_retry(session)
    else:  # pragma: no cover - fallback path
        async with ClientSession(timeout=timeout) as sess:
            data = await _call_with_retry(sess)

    if not data.get("encrypted"):
        raise ValueError(f"Invalid response: {data}")

    timestamp = data["timestamp"]
    enc_url = data["url"]

    key = f"SECRET_KEY_{timestamp // 3600}"
    enc_bytes = _b64_to_bytes(enc_url)
    dec_url = _xor_with_key(enc_bytes, key)

    if ogname:
        sep = "&" if "?" in dec_url else "?"
        dec_url = f"{dec_url}{sep}n={quote(ogname)}"

    return dec_url
