"""Small services for rate limiting and safe sponsorship downloads."""

import hashlib

from django.core.cache import cache


INQUIRY_LIMIT = 5
INQUIRY_WINDOW_SECONDS = 60 * 60


def inquiry_rate_limit_exceeded(remote_address):
    """Apply a fixed-window per-IP limit without storing raw addresses in cache."""
    address_digest = hashlib.sha256(remote_address.encode("utf-8")).hexdigest()
    cache_key = f"partners:inquiry:{address_digest}"
    if cache.add(cache_key, 1, timeout=INQUIRY_WINDOW_SECONDS):
        return False
    return cache.incr(cache_key) > INQUIRY_LIMIT
