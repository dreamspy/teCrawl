"""Scrape the "Recommendations" carousel from a Discogs release page — the
6th discovery angle (see TODO.md). There's no public API for this; it's a
client-hydrated widget, not part of the REST API, and plain HTTP scraping is
blocked by Cloudflare's Managed Challenge (confirmed with cloudscraper
2026-04-19). A real Chromium via Playwright gets through it, but only in the
exact configuration described at _CHALLENGE_TITLE below — the defaults do not.

Cards live in a stable `<section id="release-recommendations">`; the CSS
module class names inside it are build-hashed and not safe to depend on, but
each card's thumbnail link carries a stable `aria-label="Artist - Title"`
right next to `href="/release/<id>-<slug>"` — that's all this parses."""

import html
import re
import time

from . import cache, config

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
# two, while a rec-less one waits out this timeout. (The page does carry a
# positive signal: its embedded GraphQL state in `#dsdata` has a
# `recommendations({"first":10})` object with a `totalCount`. Parsing that
# instead of the DOM would skip this wait entirely and be sturdier than the
# aria-label regex — the obvious next move if this markup ever shifts.)
# One load only: a rec-having
# release whose render is flaky enough to miss the wait would just double this
# cost on a reload for little gain, and the short empty-cache TTL re-scrapes it
# within hours anyway. This clock starts after the Cloudflare challenge has
# cleared, so it measures hydration only.
_CARD_WAIT_MS = 8000

# There used to be a page.route("**/*") handler here that aborted ad/tracker
# hosts and un-parsed assets (images/fonts/media) to load the page lean. It is
# gone because request interception is itself a bot signal: with routing on,
# the Cloudflare challenge never clears; with the identical browser, UA and
# timing and routing off, it clears in seconds. A/B'd three ways 2026-08-28 —
# no routing passed, host-only routing failed, host+resource-type routing
# failed. Do not reintroduce it. Its original purpose (letting `networkidle`
# fire) is moot anyway: this navigates with wait_until="commit" and waits on a
# selector, so nothing depends on the network going quiet.

# Cloudflare's Managed Challenge sits in front of the release page and has to
# be passed before anything is parseable. Two things decide whether headless
# Chromium gets through (both A/B'd against discogs.com 2026-08-28; the third,
# request interception, is covered in the note above):
#
#   1. It must be the *full* Chromium build in new-headless mode
#      (channel="chromium"), not Playwright's default `chromium_headless_shell`.
#      The shell never passes — 45 s of waiting still leaves "Just a moment…".
#   2. Its user agent must not advertise `HeadlessChrome`, and must not
#      contradict the browser's real version either. The old hardcoded
#      `Chrome/124` string did the second: the browser is Chrome 149 and still
#      sends Sec-CH-UA client hints saying so, and a UA/client-hint version
#      mismatch is its own bot signal. Deriving the UA from the browser and
#      only dropping the word "Headless" keeps the two consistent for free,
#      and stays correct across Chromium upgrades.
#
# Passing the challenge yields a `cf_clearance` cookie. Persisting it (below)
# matters a lot: a fresh context re-solves the challenge on every release, and
# a 25-seed run doing that back-to-back gets clamped down on. With the cookie
# reused, one pass covers the whole run.
_CHALLENGE_TITLE = "Just a moment"
_CHALLENGE_WAIT_S = 30.0
_STATE_FILE = "discogs_cf_state.json"

# Waiting out the challenge costs up to _CHALLENGE_WAIT_S per release, so a run
# against an IP Cloudflare has decided to distrust would otherwise burn ~14 min
# of a 25-seed dig on nothing. Mirrors spotify.py's parking: once it's clear
# we're being refused rather than unlucky, stop asking for a while.
_PARK_AFTER_BLOCKS = 2
_PARK_SECONDS = 10 * 60
_BLOCKED = {"streak": 0, "until": 0.0}

_CARD_RE = re.compile(r'href="/(?:release|master)/(\d+)[^"]*"[^>]*aria-label="([^"]+)"')


class ScrapeUnavailable(Exception):
    """The scrape couldn't be attempted or couldn't be trusted — Playwright (or
    its Chromium build) isn't installed, or Cloudflare held us at the challenge.
    Distinct from "scraped fine, this release has no recommendations", which is
    an empty list. Callers warn and move on; nothing gets cached either way, so
    a transient block doesn't poison the release for the cache's lifetime."""


def _throttle() -> None:
    elapsed = time.time() - _LAST_REQUEST[0]
    if elapsed < _MIN_INTERVAL:
        time.sleep(_MIN_INTERVAL - elapsed)
    _LAST_REQUEST[0] = time.time()


def recommendations(release_id: int, limit: int = 10) -> list[tuple[str, str, int]]:
    """(artist, title, release_id) tuples from the seed release's Discogs
    Recommendations carousel. Empty list if the release has none, or if the
    section can't be located (page layout changed). Raises ScrapeUnavailable
    if the page was never reached — Cloudflare blocked us, or Chromium isn't
    installed — so "no recommendations" is never inferred from "couldn't
    look", and a block is never cached as an empty."""
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

    if _BLOCKED["until"] > time.time():
        raise ScrapeUnavailable(
            "Discogs is refusing the scraper (Cloudflare challenge) — "
            "Recommendations parked for this run"
        )

    _throttle()
    try:
        items = _scrape(sync_playwright, url)
    except ScrapeUnavailable:
        _BLOCKED["streak"] += 1
        if _BLOCKED["streak"] >= _PARK_AFTER_BLOCKS:
            _BLOCKED["until"] = time.time() + _PARK_SECONDS
        raise  # already a precise diagnosis (e.g. the Cloudflare block)
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
    _BLOCKED["streak"] = 0
    cache.put(url, params, {"items": items})
    # _scrape builds lists (JSON round-trips as lists anyway); tuple them so a
    # cache hit and a fresh scrape hand back the same shape.
    return [tuple(item) for item in items[:limit]]


def _user_agent(browser) -> str:
    """The browser's own UA with the headless marker stripped — see the note by
    _CHALLENGE_TITLE for why it's derived rather than hardcoded."""
    page = browser.new_page()
    try:
        ua = page.evaluate("navigator.userAgent")
    finally:
        page.close()
    return ua.replace("HeadlessChrome", "Chrome")


def _pass_challenge(page) -> bool:
    """Wait out Cloudflare's interstitial. It self-clears in a few seconds when
    the browser passes and never clears when it doesn't, so polling the title
    is both the fastest exit and the only available "we're blocked" signal —
    there's no error status to read; the challenge is served as a 200."""
    deadline = time.time() + _CHALLENGE_WAIT_S
    while time.time() < deadline:
        try:
            if _CHALLENGE_TITLE not in page.title():
                return True
        except Exception:
            pass  # mid-navigation; the challenge redirecting is a good sign
        time.sleep(1.0)
    try:
        return _CHALLENGE_TITLE not in page.title()
    except Exception:
        return False


def _scrape(sync_playwright, url: str) -> list[list]:
    """Load the release page and parse the Recommendations carousel. Returns []
    when the release has no recommendations (nothing renders within the wait),
    and raises ScrapeUnavailable when Cloudflare never let us onto the page —
    the two look identical in the DOM and must not be conflated."""
    state_path = config.CACHE_DIR / _STATE_FILE
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            channel="chromium",
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            context = browser.new_context(
                user_agent=_user_agent(browser),
                locale="en-US",
                storage_state=str(state_path) if state_path.exists() else None,
            )
            try:
                page = context.new_page()
                page.goto(url, wait_until="commit", timeout=30000)
                if not _pass_challenge(page):
                    raise ScrapeUnavailable(
                        "Discogs held the scraper at a Cloudflare challenge — "
                        "Recommendations skipped for this release"
                    )
                try:
                    page.wait_for_selector(
                        '#release-recommendations a[href^="/release/"], '
                        '#release-recommendations a[href^="/master/"]',
                        state="attached",
                        timeout=_CARD_WAIT_MS,
                    )
                except Exception:
                    pass  # no cards rendered in time — parse confirms empty
                items = _parse(page.content())
                _save_state(context, state_path)
                return items
            finally:
                context.close()
        finally:
            browser.close()


def _save_state(context, state_path) -> None:
    """Persist cf_clearance so the rest of the run rides on one challenge pass.
    Best-effort: a failure here costs a re-solve, not the scrape."""
    try:
        config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        context.storage_state(path=str(state_path))
    except Exception:
        pass


def _parse(page_html: str) -> list[list]:
    start = page_html.find('id="release-recommendations"')
    if start == -1:
        return []
    section_start = page_html.rfind("<section", 0, start)
    end = page_html.find("</section>", start)
    section = page_html[
        section_start if section_start != -1 else start : end if end != -1 else start + 20000
    ]

    seen: set[int] = set()
    out: list[list] = []
    for rid_str, label in _CARD_RE.findall(section):
        rid = int(rid_str)
        if rid in seen:
            continue
        seen.add(rid)
        # aria-label is HTML-escaped in the source, and these become search
        # queries downstream — "H&amp;M" would never match the label H&M.
        artist, sep, title = html.unescape(label).partition(" - ")
        if not sep:
            continue
        out.append([artist.strip(), title.strip(), rid])
    return out
