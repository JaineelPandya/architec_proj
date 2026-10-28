import asyncio
import logging
import random
from urllib.parse import urlencode, urlparse, urlunparse

import httpx
from curl_cffi import requests as cf_requests

from app.core.config import settings

logger = logging.getLogger(__name__)

PROXY_AGGREGATOR_URL = "https://proxy.scrapeops.io/v1/"
MAX_RETRIES = 3

USER_AGENTS = [
    # Chrome 153 - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",

    # Chrome 152 - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",

    # Chrome 153 - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",

    # Chrome 152 - macOS
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36",

    # Chrome 153 - Linux
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",

    # Firefox - Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:142.0) "
    "Gecko/20100101 Firefox/142.0",

    # Edge
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36 "
    "Edg/153.0.0.0",
]


def get_headers(headers: dict | None = None) -> dict:
    """
    Return request headers with a randomly selected User-Agent.
    Caller-provided headers override defaults.
    """
    request_headers = {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/webp,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }

    if headers:
        request_headers.update(headers)

    return request_headers


# Static fallback headers kept for backwards compatibility
COMMON_HEADERS = {
    "User-Agent": USER_AGENTS[0],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

class ProxyError(Exception):
    pass


def _build_full_target_url(url: str, params: dict | None) -> str:
    if not params:
        return url
    parsed = urlparse(url)
    existing_query = parsed.query
    new_query = urlencode(params)
    if existing_query:
        combined_query = f"{existing_query}&{new_query}"
    else:
        combined_query = new_query
    return urlunparse(
        (parsed.scheme, parsed.netloc, parsed.path, parsed.params, combined_query, parsed.fragment)
    )


async def fetch_with_proxy(
    client: httpx.AsyncClient | cf_requests.AsyncSession,
    target_url: str,
    method: str = "GET",
    params: dict | None = None,
    headers: dict | None = None,
    data: dict | str | bytes | None = None,
    json: dict | None = None,
    timeout: int = 30,
    scrapeops_options: dict | None = None,
    retries: int = MAX_RETRIES,
    via_proxy: bool = True,
):
    headers = get_headers(headers)
    full_target_url = _build_full_target_url(target_url, params)
    logger.info(
    f"[HTTP] Requesting URL={full_target_url} | User_Agent={headers.get('User-Agent')}"
)
    if not settings.SCRAPEOPS_API_KEY or not via_proxy:
        if settings.SCRAPEOPS_API_KEY and not via_proxy:
            logger.debug(f"via_proxy=False — fetching directly (cookies preserved): {full_target_url}")
        else:
            logger.warning(
                f"SCRAPEOPS_API_KEY is not set. Fetching directly (without proxy): {full_target_url}"
            )
        kwargs = {"method": method, "url": full_target_url, "headers": headers, "timeout": timeout}
        if data is not None:
            kwargs["data"] = data
        if json is not None:
            kwargs["json"] = json
        if not hasattr(client, "request"):
            raise ValueError("Unsupported client type")

        attempt = 0
        last_exc = None
        while attempt < retries:
            try:
                response = await client.request(**kwargs)
                if response.status_code in (429, 502, 503, 504, 530):
                    raise Exception(f"HTTP {response.status_code} Blocked/Rate-limited")
                return response
            except Exception as e:
                attempt += 1
                last_exc = e
                if attempt < retries:
                    await asyncio.sleep(0.5 * (2 ** (attempt - 1)))
        
        if last_exc:
            logger.warning(f"[Direct Fetch] All {retries} retries failed for {full_target_url}: {last_exc}")
            raise last_exc

    proxy_params = {
        "api_key": settings.SCRAPEOPS_API_KEY.get_secret_value(),
        "url": full_target_url,
        "keep_headers": "true",
    }
    if scrapeops_options:
        proxy_params.update(scrapeops_options)

    kwargs = {
        "method": method,
        "url": PROXY_AGGREGATOR_URL,
        "params": proxy_params,
        "headers": headers,
        "timeout": timeout,
    }
    if data is not None:
        kwargs["data"] = data
    if json is not None:
        kwargs["json"] = json

    attempt = 0
    last_exception = None
    while attempt < retries:
        try:
            if hasattr(client, "request"):
                response = await client.request(**kwargs)
            else:
                raise ValueError("Unsupported client type")
            if getattr(response, "raise_for_status", None):
                response.raise_for_status()
            elif response.status_code >= 400:
                raise Exception(f"HTTP {response.status_code}")
            return response
        except Exception as e:
            attempt += 1
            last_exception = e
            logger.warning(
                f"[Proxy] Retry {attempt}/{retries} for {full_target_url} failed: {e}",
                exc_info=True if attempt == retries else False,
            )
            if attempt < retries:
                await asyncio.sleep(2 ** attempt)

    logger.error(f"[Proxy] All {retries} retries failed for {full_target_url}. Attempting direct fetch as fallback...")
    try:
        direct_url = target_url
        if params:
            query_string = "&".join(f"{k}={v}" for k, v in params.items())
            direct_url = f"{target_url}?{query_string}"
        from curl_cffi.requests import AsyncSession
        async with AsyncSession() as session:
            response = await session.request(
                method=method,
                url=direct_url,
                headers=headers,
                timeout=timeout,
                impersonate="chrome123",
                **({"data": data} if data is not None else {}),
                **({"json": json} if json is not None else {}),
            )
        logger.info(f"[Proxy] Direct fallback succeeded for {direct_url} (curl_cffi)")
        return response
    except Exception as fallback_exc:
        logger.error(f"[Proxy] Direct fallback also failed for: {fallback_exc}")
        if last_exception is not None:
            raise last_exception
        raise fallback_exc
