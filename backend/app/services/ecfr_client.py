"""eCFR API client with rate limiting and caching.

Fetches authoritative regulatory text from the Electronic Code of Federal
Regulations.  Section-level granularity (paragraph-level is not supported
by the API).

Endpoint pattern:
  https://www.ecfr.gov/api/versioner/v1/full/{date}/title-{N}.xml
    ?part={P}&section={S}

Rate limit: ~1,000 requests/hour (no authentication required).
"""

import asyncio
import datetime
import xml.etree.ElementTree as ET

import httpx

from app.config import settings

ECFR_BASE = "https://www.ecfr.gov/api/versioner/v1/full"
ECFR_STRUCTURE_BASE = "https://www.ecfr.gov/api/versioner/v1/structure/current"


class ECFRClient:
    """Async eCFR API client with bounded concurrency."""

    def __init__(self, max_concurrent: int | None = None):
        limit = max_concurrent or settings.engram_max_concurrent_fetches
        self._semaphore = asyncio.Semaphore(limit)
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Lazy-init a shared async HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=15.0)
        return self._client

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def fetch_section(
        self,
        title: int,
        part: str,
        section: str | None = None,
        date: str | None = None,
    ) -> str | None:
        """Fetch a single CFR section as plain text.

        Returns the extracted text content, or None on any failure.
        """
        assert isinstance(title, int), "title must be an integer"
        assert part, "part must be a non-empty string"

        if date is None:
            date = (datetime.date.today() - datetime.timedelta(days=3)).isoformat()

        params: dict[str, str] = {"part": part}
        if section:
            # eCFR requires fully qualified section: "36.304" not "304"
            if not section.startswith(part):
                section = f"{part}.{section}"
            params["section"] = section

        # eCFR only has snapshots up to its latest real publication date. A skewed
        # (or simply too-recent) `date` 404s, so fall back to progressively older
        # snapshots. Bounded, first 200 wins. Prevents silent summary-only degrade.
        candidates = [date]
        today = datetime.date.today()
        for years_back in (1, 2, 3):
            candidates.append(datetime.date(today.year - years_back, 1, 1).isoformat())

        async with self._semaphore:
            client = await self._get_client()
            for snapshot in candidates:
                url = f"{ECFR_BASE}/{snapshot}/title-{title}.xml"
                try:
                    resp = await client.get(url, params=params)
                except (httpx.HTTPError, Exception):
                    continue
                if resp.status_code == 200:
                    return parse_xml_to_text(resp.text)
            return None

    async def fetch_structure(self, title: int) -> dict | None:
        """Fetch the structural hierarchy of a CFR title (JSON)."""
        assert isinstance(title, int), "title must be an integer"

        url = f"{ECFR_STRUCTURE_BASE}/title-{title}.json"
        async with self._semaphore:
            client = await self._get_client()
            try:
                resp = await client.get(url)
                if resp.status_code != 200:
                    return None
                return resp.json()
            except (httpx.HTTPError, Exception):
                return None


def parse_xml_to_text(xml_content: str) -> str:
    """Extract clean plain text from eCFR XML response."""
    assert xml_content, "xml_content must be non-empty"
    root = ET.fromstring(xml_content)
    return ET.tostring(root, encoding="unicode", method="text").strip()


def estimate_tokens(text: str) -> int:
    """Rough token estimate: len(text) // 4."""
    return len(text) // 4


# Module-level singleton
_client: ECFRClient | None = None


def get_ecfr_client() -> ECFRClient:
    """Return the module-level eCFR client singleton."""
    global _client
    if _client is None:
        _client = ECFRClient()
    return _client
