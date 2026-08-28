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
# An *empty* scrape is ambiguous: the release may genuinely have no
# recommendations, or the async-hydrated carousel just didn't finish loading
# before we grabbed the DOM (a real, ~50%-of-loads race — see _scrape). Trust
# an empty result only briefly so a transient miss re-scrapes and self-heals,
# instead of poisoning the seed for a full month.
_EMPTY_CACHE_TTL = 6 * 3600
# The carousel hydrates from an async GraphQL fetch after the page's initial
# load. We wait for an actual card link to appear (state="attached"): that fires
# the instant the cards render, so a rec-having release returns in a second or
# two, while a rec-less one waits out this timeout (there's no positive "no
# recommendations" signal — only absence). Blocking the ad/tracker hosts (below)
# lightens the page so cards render promptly. One load only: a rec-having
# release whose render is flaky enough to miss the wait would just double this
# cost on a reload for little gain, and the short empty-cache TTL re-scrapes it
# within hours anyway. (Discogs occasionally serves a slow Cloudflare check;
# that variance is external and bounded by the goto timeout, not by retries.)
_CARD_WAIT_MS = 8000

# Hosts that serve ads, analytics, or consent/RUM beacons on the release page.
# None carry recommendation data; several poll indefinitely, so aborting them is
# what lets networkidle actually fire. Substring match against the request URL.
_BLOCK_HOSTS = (
    "ad-delivery.net", "doubleclick.net", "btloader.com", "dns-finder.com",
    "cloudflareinsights.com", "cookielaw.org", "onetrust.com", "adnxs.com",
    "google-analytics.com", "googletagmanager.com", "criteo", "facebook",
    "ad.doubleclick", "quantserve.com", "scorecardresearch.com",
)
# Thumbnails/fonts/media are never parsed (only the anchors' aria-labels are),
# so dropping them cuts load time and network chatter further.
_BLOCK_TYPES = {"image", "media", "font"}

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
        items = [tuple(item) for item in cached["items"]]
        # A cached *non-empty* result is trustworthy for the full month. A
        # cached empty is only honoured if it's still within the short
        # empty-TTL; older empties fall through and re-scrape.
        if items or cache.get(url, params, ttl=_EMPTY_CACHE_TTL) is not None:
            return items[:limit]

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as e:
        raise ScrapeUnavailable(
            "playwright not installed — run: "
            "pip install playwright && playwright install chromium"
        ) from e

    _throttle()
    try:
        items = _scrape(sync_playwright, url)
    except Exception as e:
        # Playwright imports fine but its Chromium build was never downloaded
        # (fresh install, or an upgrade that bumped the pinned build). That's
        # a setup state, not a scrape failure, and the driver reports it as a
        # multi-line ASCII banner — replace it with the command that fixes it.
        msg = str(e)
        if "Executable doesn't exist" in msg or "playwright install" in msg:
            raise ScrapeUnavailable(
                "Playwright's Chromium isn't installed — run: playwright install chromium"
            ) from e
        raise
    cache.put(url, params, {"items": items})
    return items[:limit]


def _block_noise(route) -> None:
    """Abort ad/tracker/beacon requests and un-parsed assets so the release
    page loads lean and the cards render promptly; let everything else through."""
    req = route.request
    if req.resource_type in _BLOCK_TYPES or any(h in req.url for h in _BLOCK_HOSTS):
        route.abort()
    else:
        route.continue_()


def _scrape(sync_playwright, url: str) -> list[list]:
    """Load the release page and parse the Recommendations carousel. Returns []
    when the release has no recommendations (nothing renders within the wait)."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = browser.new_page(user_agent=_UA, locale="en-US")
            page.route("**/*", _block_noise)
            try:
                page.goto(url, wait_until="commit", timeout=30000)
                try:
                    page.wait_for_selector(
                        '#release-recommendations a[href^="/release/"], '
                        '#release-recommendations a[href^="/master/"]',
                        state="attached",
                        timeout=_CARD_WAIT_MS,
                    )
                except Exception:
                    pass  # no cards rendered in time — parse confirms empty
                return _parse(page.content())
            finally:
                page.close()
        finally:
            browser.close()


def _parse(html: str) -> list[list]:
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
