"""Proxy fetching + concurrent liveness testing.

Adapted from the switcher project's `proxy_scanner.py`.
"""

from __future__ import annotations

import concurrent.futures
import time
from typing import Callable, Dict, List, Optional

import requests


PROXY_LIST_URL = (
    "https://cdn.jsdelivr.net/gh/proxyscrape/free-proxy-list@main/proxies/all/data.json"
)


def fetch_all_proxies(timeout: int = 10) -> List[Dict]:
    """Download the master proxy list (JSON) from the ProxyScrape CDN."""
    try:
        r = requests.get(PROXY_LIST_URL, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception:
        return []


def test_single_proxy(proxy_info: Dict, timeout: int = 5) -> Optional[Dict]:
    """Return {url, country, protocol, latency_ms} on success, else None."""
    protocol = (proxy_info.get("protocol") or "http").lower()
    ip = proxy_info.get("ip")
    port = proxy_info.get("port")
    if not ip or not port:
        return None

    proxy_url = f"{protocol}://{ip}:{port}"
    proxies = {"http": proxy_url, "https": proxy_url}

    try:
        start = time.time()
        r = requests.get(
            "https://api.ipify.org?format=json",
            proxies=proxies,
            timeout=timeout,
        )
        if r.status_code == 200:
            latency = round((time.time() - start) * 1000)
            return {
                "url": proxy_url,
                "country": (proxy_info.get("country_code") or "??").upper(),
                "protocol": protocol.upper(),
                "latency_ms": latency,
            }
    except Exception:
        pass
    return None


def get_working_proxies(
    target_count: int = 5,
    countries: Optional[List[str]] = None,
    protocols: Optional[List[str]] = None,
    max_threads: int = 30,
    test_timeout: int = 5,
    log: Callable[[str], None] = print,
) -> List[str]:
    """Fetch, filter, test concurrently, and return only live proxy URLs."""
    countries_lc = [c.lower() for c in (countries or [])]
    protocols_lc = [p.lower() for p in (protocols or ["socks5", "http"])]

    log("Downloading global proxy list…")
    all_proxies = fetch_all_proxies()
    if not all_proxies:
        log("! Could not download proxy list.")
        return []

    log(f"Filtering {len(all_proxies)} proxies…")
    candidates: List[Dict] = []
    for p in all_proxies:
        cc = (p.get("country_code") or "").lower()
        proto = (p.get("protocol") or "").lower()
        if countries_lc and cc not in countries_lc:
            continue
        if protocols_lc and proto not in protocols_lc:
            continue
        candidates.append(p)

    # Prefer high uptime + low latency
    candidates.sort(
        key=lambda x: (-x.get("uptime_percent", 0), x.get("latency_ms", 9999))
    )
    log(f"Testing {len(candidates)} candidates (stopping at {target_count} good ones)…")

    working: List[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_threads) as ex:
        futures = {ex.submit(test_single_proxy, p, test_timeout): p for p in candidates}
        for fut in concurrent.futures.as_completed(futures):
            res = fut.result()
            if not res:
                continue
            working.append(res["url"])
            log(f"  [+] {res['url']}  [{res['country']}]  {res['latency_ms']}ms")
            if len(working) >= target_count:
                ex.shutdown(wait=False, cancel_futures=True)
                break

    log(f"Done — {len(working)} working proxy(ies).")
    return working