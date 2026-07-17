"""Scrape the "Recommendations" carousel from a Discogs release page — the
6th discovery angle (see TODO.md). There's no public API for this; it's a
client-hydrated widget, not part of the REST API, and plain HTTP scraping is
blocked by Cloudflare's Managed Challenge (confirmed with cloudscraper
2026-04-19). A real headless Chromium via Playwright gets through it.

Cards live in a stable `<section id="release-recommendations">`; the CSS
module class names inside it are build-hashed and not safe to depend on, but
each card's thumbnail link carries a stable `aria-label="Artist - Title"`
right next to `href="/release/<id>-<slug>"` — that's all this parses."""

import re
import time

from . import cache

_LAST_REQUEST = [0.0]
_MIN_INTERVAL = 4.0  # slow and polite — this is UI scraping, not the REST API
_CACHE_TTL = 30 * 24 * 3600  # recommendations barely change; well beyond the API's 7d cache

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_CARD_RE = re.compile(r'href="/(?:release|master)/(\d+)[^"]*"[^>]*aria-label="([^"]+)"')


class ScrapeUnavailable(Exception):
    """Playwright (or its Chromium build) isn't installed."""


def _throttle() -> None:
    elapsed = time.time() - _LAST_REQUEST[0]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _LAST_REQUEST[0] = time.time()


def recommendations(release_id: int, limit: int = 10) -> list[tuple[str, str, int]]:
    """(artist, title, release_id) tuples from the seed release's Discogs
    Recommendations carousel. Empty list if the release has none, or if the
    section can't be located (page layout changed)."""
    url = f"https://www.discogs.com/release/{release_id}"
    params = {"scrape": "recommendations"}
    cached = cache.get(url, params, ttl=_CACHE_TTL)
    if cached is not None:
        return [tuple(item) for item in cached["items"]][:limit]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ScrapeUnavailable(
            "playwright not installed — run: "
            "pip install playwright && playwright install chromium"
        ) from e

    _throttle()
    items = _scrape(sync_playwright, url)
    cache.put(url, params, {"items": items})
    return items[:limit]


def _scrape(sync_playwright, url: str) -> list[list]:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = browser.new_page(user_agent=_UA, locale="en-US")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_selector("#release-recommendations", timeout=10000)
            except Exception:
                return []  # no recommendations section on this release
            page.wait_for_timeout(1500)  # let the carousel finish hydrating
            html = page.content()
        finally:
            browser.close()

    start = html.find('id="release-recommendations"')
    if start == -1:
        return []
    section_start = html.rfind("<section", 0, start)
    end = html.find("</section>", start)
    section = html[
        section_start if section_start != -1 else start : end if end != -1 else start + 20000
    ]

    seen: set[int] = set()
    out: list[list] = []
    for rid_str, label in _CARD_RE.findall(section):
        rid = int(rid_str)
        if rid in seen:
            continue
        seen.add(rid)
        artist, sep, title = label.partition(" - ")
        if not sep:
            continue
        out.append([artist.strip(), title.strip(), rid])
    return out
