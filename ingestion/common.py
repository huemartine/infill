"""Shared ingestion utilities: a retrying/rate-limited HTTP client, generic
pagination helpers, and the normalizers every extractor reuses (USPS-style
address normalization and owner-name normalization for absentee/assemblage
detection). Keeping these here keeps each extractor thin."""
from __future__ import annotations

import re
import time
from typing import Iterator, Optional

import httpx

_DEFAULT_TIMEOUT = 30.0
_MAX_RETRIES = 5
_BACKOFF_BASE = 0.5  # seconds; exponential with jitter-free cap


class HttpClient:
    """Thin wrapper over httpx with exponential backoff and a min-interval
    rate limit. Honors Retry-After on 429. One instance per extractor run."""

    def __init__(self, min_interval_s: float = 0.0, headers: Optional[dict] = None):
        self._client = httpx.Client(timeout=_DEFAULT_TIMEOUT, headers=headers or {})
        self._min_interval = min_interval_s
        self._last_call = 0.0

    def get(self, url: str, params: Optional[dict] = None) -> httpx.Response:
        for attempt in range(_MAX_RETRIES):
            self._throttle()
            try:
                resp = self._client.get(url, params=params)
            except httpx.TransportError:
                if attempt == _MAX_RETRIES - 1:
                    raise
                time.sleep(_BACKOFF_BASE * (2 ** attempt))
                continue
            if resp.status_code == 429:
                time.sleep(float(resp.headers.get("Retry-After", _BACKOFF_BASE * (2 ** attempt))))
                continue
            if resp.status_code >= 500:
                time.sleep(_BACKOFF_BASE * (2 ** attempt))
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError(f"GET failed after {_MAX_RETRIES} retries: {url}")

    def _throttle(self) -> None:
        if self._min_interval:
            delta = time.monotonic() - self._last_call
            if delta < self._min_interval:
                time.sleep(self._min_interval - delta)
        self._last_call = time.monotonic()

    def close(self) -> None:
        self._client.close()


def paginate_offset(
    client: HttpClient,
    url: str,
    base_params: dict,
    *,
    offset_key: str,
    count_key: str,
    page_size: int,
    records_path,
) -> Iterator[list[dict]]:
    """Generic offset pagination (ArcGIS resultOffset, Socrata $offset).

    `records_path` extracts the record list from a parsed JSON response,
    so the same loop serves Esri ``features`` and flat Socrata arrays.
    Stops when a page returns fewer than `page_size` records.
    """
    offset = 0
    while True:
        params = {**base_params, offset_key: offset, count_key: page_size}
        records = records_path(client.get(url, params=params).json())
        if not records:
            return
        yield records
        if len(records) < page_size:
            return
        offset += page_size


# --- normalizers -----------------------------------------------------------

_STREET_SUFFIX = {
    "street": "st", "avenue": "ave", "boulevard": "blvd", "drive": "dr",
    "road": "rd", "lane": "ln", "court": "ct", "place": "pl", "terrace": "ter",
}
_DIRECTIONALS = {"north": "n", "south": "s", "east": "e", "west": "w"}


def normalize_address(addr: Optional[str]) -> Optional[str]:
    """Light USPS-style normalization for fallback address matching: uppercase,
    collapse whitespace, standardize suffixes/directionals, strip punctuation."""
    if not addr:
        return None
    s = re.sub(r"[.,]", " ", addr.lower())
    s = re.sub(r"\s+", " ", s).strip()
    tokens = [_STREET_SUFFIX.get(t, _DIRECTIONALS.get(t, t)) for t in s.split(" ")]
    return " ".join(tokens).upper()


_OWNER_SUFFIX = re.compile(r"\b(llc|inc|corp|co|ltd|lp|llp|trust|company)\b\.?", re.I)


def normalize_owner_name(name: Optional[str]) -> Optional[str]:
    """Normalize owner names so assemblage/absentee logic can group reliably:
    uppercase, strip entity suffixes and punctuation, collapse whitespace."""
    if not name:
        return None
    s = _OWNER_SUFFIX.sub("", name)
    s = re.sub(r"[^a-zA-Z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip().upper() or None
