"""Asynchronous health checks of the registered services.

Each source type has a cheap, read-only probe (GetCapabilities, /collections,
or a plain GET). Results are written back into the registry ``status`` field.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import date

from .registry import Source

log = logging.getLogger(__name__)

# A Mozilla "compatible" token keeps picky WAFs/CDNs (e.g. ArcGIS portals that
# 403 a bare bot string) from blocking the probe, while still identifying us.
USER_AGENT = ("Mozilla/5.0 (compatible; geodatacatalog-explorer/1.0; "
              "+https://pypi.org/project/geodatacatalog/)")


def probe_url(source: Source) -> str:
    """URL used to check this source, derived from its type."""
    test_url = source.test.get("url")
    if test_url:
        return test_url
    ep = source.endpoint.rstrip("/")
    if source.type == "stac":
        return f"{ep}/collections?limit=1"
    if source.type in ("wfs", "wms", "wcs", "wmts", "csw"):
        sep = "&" if "?" in ep else "?"
        return f"{ep}{sep}SERVICE={source.type.upper()}&REQUEST=GetCapabilities"
    if source.type == "xyz":
        return source.endpoint.format(z=1, x=1, y=1)
    return source.endpoint  # rest / registry


def _evaluate(source: Source, http_status: int, body: bytes) -> tuple[str, str]:
    """Map an HTTP response to a (state, detail) pair."""
    if http_status in (401, 403):
        if source.auth != "none":
            return "auth_required", f"HTTP {http_status} (credentials required)"
        return "fail", f"HTTP {http_status}"
    if http_status >= 400:
        return "fail", f"HTTP {http_status}"

    head = body[:4096].decode("utf-8", errors="replace")
    if source.type == "stac":
        if "collections" in head or '"type"' in head:
            return "ok", "STAC /collections OK"
        return "fail", "200 but no STAC JSON"
    if source.type in ("wfs", "wms", "wcs", "wmts", "csw"):
        if "Capabilities" in head:
            return "ok", "GetCapabilities OK"
        if "ExceptionReport" in head or "ServiceException" in head:
            return "fail", "OGC exception"
        return "fail", "200 but no capabilities document"
    return "ok", f"HTTP {http_status}"


async def _probe_one(source: Source, session, timeout: int) -> Source:
    import aiohttp

    url = probe_url(source)
    t0 = time.perf_counter()
    try:
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=timeout), allow_redirects=True
        ) as resp:
            body = await resp.content.read(4096)
            state, detail = _evaluate(source, resp.status, body)
    except asyncio.TimeoutError:
        state, detail = "fail", f"timeout after {timeout}s"
    except Exception as e:
        state, detail = "fail", f"{type(e).__name__}: {e}"

    latency_ms = round((time.perf_counter() - t0) * 1000)
    source.status = {
        "state": state,
        "latency_ms": latency_ms,
        "checked_at": date.today().isoformat(),
        "detail": detail,
    }
    icon = {"ok": "✓", "fail": "✗", "auth_required": "🔐"}.get(state, "?")
    log.info(f"  {icon} {source.id:<24} {state:<13} {latency_ms:>6} ms  {detail}")
    return source


async def _probe_all(sources: list[Source], concurrency: int, timeout: int) -> None:
    import aiohttp

    connector = aiohttp.TCPConnector(limit=concurrency, ssl=False)
    headers = {"User-Agent": USER_AGENT}
    async with aiohttp.ClientSession(connector=connector, headers=headers) as session:
        sem = asyncio.Semaphore(concurrency)

        async def _bounded(src):
            async with sem:
                return await _probe_one(src, session, timeout)

        await asyncio.gather(*[_bounded(s) for s in sources])


def _run_async(coro):
    """Run ``coro`` to completion, even inside an already-running loop (e.g. Jupyter)."""
    try:
        asyncio.get_running_loop()
        import nest_asyncio
        nest_asyncio.apply()
        return asyncio.get_event_loop().run_until_complete(coro)
    except ImportError:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)


def check_sources(sources: list[Source], concurrency: int = 10,
                  timeout: int = 20) -> dict[str, int]:
    """Probe every source in place and return a ``{state: count}`` summary."""
    log.info(f"Checking {len(sources)} sources (concurrency={concurrency}, timeout={timeout}s)")
    _run_async(_probe_all(sources, concurrency, timeout))
    summary: dict[str, int] = {}
    for s in sources:
        state = (s.status or {}).get("state", "unknown")
        summary[state] = summary.get(state, 0) + 1
    return summary
