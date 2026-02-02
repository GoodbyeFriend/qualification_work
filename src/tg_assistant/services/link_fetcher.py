from __future__ import annotations

import re
from typing import Tuple

import aiohttp
from bs4 import BeautifulSoup


_URL_RE = re.compile(r"https?://[^\s<>\"]+")
_RE_SPACES = re.compile(r"\s{2,}")


def extract_urls(text: str) -> list[str]:
    return _URL_RE.findall(text or "")


def html_to_text(html: str, max_chars: int = 12000) -> tuple[str, str]:
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()

    text = " ".join(soup.stripped_strings)
    text = _RE_SPACES.sub(" ", text).strip()
    if max_chars:
        text = text[:max_chars]

    return title[:512], text


async def fetch_url_text(url: str, timeout_s: int = 25) -> tuple[str, str]:
    headers = {"User-Agent": "tg-assistant-bot/1.0"}
    timeout = aiohttp.ClientTimeout(total=timeout_s)

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url, allow_redirects=True) as r:
            r.raise_for_status()
            html = await r.text(errors="ignore")

    return html_to_text(html)

async def fetch_url_html(url: str, timeout_s: int = 25) -> str:
    headers = {"User-Agent": "tg-assistant-bot/1.0"}
    timeout = aiohttp.ClientTimeout(total=timeout_s)

    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url, allow_redirects=True) as r:
            r.raise_for_status()
            return await r.text(errors="ignore")