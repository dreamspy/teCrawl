class Cancelled(Exception):
    """Raised when a run's cooperative cancel flag was set, so a Stop click
    unwinds a network-bound wait immediately instead of running it to
    completion. Lives in its own module (no other project imports) so both
    discover.py and discogs_scrape.py can raise/catch the same exception
    without a circular import between them."""
