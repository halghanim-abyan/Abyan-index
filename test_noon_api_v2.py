"""
test_noon_api_v2.py — Phase 2: Bypass Cloudflare via curl_cffi TLS fingerprinting.

Python `requests` uses OpenSSL with a TLS fingerprint (JA3) that Cloudflare
instantly recognises as non-browser → connection hangs or 403.

`curl_cffi` impersonates Chrome's exact TLS handshake (cipher suites, extensions,
ALPN, etc.) so Cloudflare sees it as a real Chrome browser.

Strategy:
  1. Hit the search page with curl_cffi (Chrome impersonation) and extract
     __NEXT_DATA__ JSON embedded in the SSR HTML.
  2. Try the internal _svc API endpoints with the same TLS fingerprint.

Run:  python test_noon_api_v2.py
"""

import json
import re
import sys
from curl_cffi import requests as cffi_requests

QUERY = "حليب المراعي طازج 2 لتر"

HEADERS = {
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
    "Referer":         "https://www.noon.com/saudi-ar/",
    "Cache-Control":   "no-cache",
}

JSON_HEADERS = {
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
    "Referer":         "https://www.noon.com/saudi-ar/",
    "Origin":          "https://www.noon.com",
    "X-Locale":        "ar-sa",
    "X-Content":       "V6",
    "X-Platform":      "web",
}

DIVIDER = "─" * 70


def probe_ssr_page():
    """
    Strategy 1: Load the full search page HTML and extract __NEXT_DATA__.

    Next.js embeds the initial page data in a <script id="__NEXT_DATA__"> tag.
    This contains the exact same product JSON the React app hydrates from.
    """
    print(f"\n{DIVIDER}")
    print("  STRATEGY 1: SSR HTML → __NEXT_DATA__ extraction")
    print(f"{DIVIDER}")

    url = f"https://www.noon.com/saudi-ar/search?q={QUERY}"
    print(f"  GET {url}")
    print(f"  TLS impersonation: chrome131")

    try:
        resp = cffi_requests.get(
            url,
            headers=HEADERS,
            impersonate="chrome131",
            timeout=30,
        )
        print(f"  Status: {resp.status_code}")
        print(f"  Content-Type: {resp.headers.get('Content-Type', '(none)')}")
        print(f"  Body size: {len(resp.content):,} bytes")

        # Check for Cloudflare challenge.
        if resp.status_code == 403:
            print("  ⚠️  403 Forbidden — Cloudflare blocked this request")
            return
        if "just a moment" in resp.text.lower() or "cf-challenge" in resp.text.lower():
            print("  ⚠️  Cloudflare JS challenge page detected")
            return

        # Try extracting __NEXT_DATA__.
        match = re.search(
            r'<script\s+id="__NEXT_DATA__"\s+type="application/json">\s*({.+?})\s*</script>',
            resp.text,
            re.DOTALL,
        )
        if match:
            data = json.loads(match.group(1))
            print(f"  ✅ __NEXT_DATA__ found! ({len(match.group(1)):,} chars)")

            # Navigate the Next.js data structure to find products.
            # Common paths: props.pageProps.searchResult, props.pageProps.catalog
            page_props = data.get("props", {}).get("pageProps", {})
            print(f"  pageProps keys: {list(page_props.keys())[:15]}")

            # Try common product data locations.
            products = None
            for path in [
                "searchResult.hits",
                "catalog.hits",
                "searchResults.hits",
                "initialData.hits",
                "results",
                "hits",
                "products",
            ]:
                obj = page_props
                for key in path.split("."):
                    if isinstance(obj, dict) and key in obj:
                        obj = obj[key]
                    else:
                        obj = None
                        break
                if obj and isinstance(obj, list) and len(obj) > 0:
                    products = obj
                    print(f"  Found products at: pageProps.{path}  ({len(obj)} items)")
                    break

            if products:
                for i, p in enumerate(products[:5]):
                    title = (
                        p.get("title") or p.get("name") or
                        p.get("title_ar") or p.get("name_ar") or
                        str(p)[:120]
                    )
                    price = p.get("price") or p.get("sale_price") or p.get("currentPrice") or ""
                    if isinstance(price, dict):
                        price = price.get("now") or price.get("value") or price
                    sku = p.get("sku") or p.get("productId") or ""
                    print(f"    [{i+1}] {title}")
                    print(f"        Price: {price}  SKU: {sku}")
                print(f"\n  ✅ SUCCESS — __NEXT_DATA__ contains product search results!")
                print(f"     We can parse this in scraper.py without Playwright!")
            else:
                # Dump the top-level keys to help us find the right path.
                print(f"\n  Products not found at expected paths.")
                print(f"  Dumping pageProps structure (top 2 levels):")
                for k, v in page_props.items():
                    if isinstance(v, dict):
                        print(f"    '{k}': dict with keys {list(v.keys())[:10]}")
                    elif isinstance(v, list):
                        print(f"    '{k}': list with {len(v)} items")
                        if v and isinstance(v[0], dict):
                            print(f"      [0] keys: {list(v[0].keys())[:10]}")
                    else:
                        val_str = str(v)[:80]
                        print(f"    '{k}': {type(v).__name__} = {val_str}")
        else:
            print("  __NEXT_DATA__ not found in HTML.")
            # Maybe it's not Next.js — check what we got.
            if "<title>" in resp.text:
                title_match = re.search(r"<title>(.*?)</title>", resp.text)
                if title_match:
                    print(f"  Page title: {title_match.group(1)[:100]}")
            # Show a snippet of the HTML to understand the page structure.
            print(f"\n  HTML snippet (first 1000 chars):")
            print(f"  {resp.text[:1000]}")

    except Exception as e:
        print(f"  ❌ Error: {type(e).__name__}: {e}")


def probe_api_with_cffi():
    """
    Strategy 2: Hit the internal API with curl_cffi TLS impersonation.
    """
    endpoints = [
        ("/_svc/catalog/api/v3/search", {"q": QUERY, "limit": "5", "page": "1", "sort": "popularity"}),
        ("/_svc/catalog/api/search",    {"q": QUERY, "limit": "5", "page": "1"}),
    ]

    for path, params in endpoints:
        print(f"\n{DIVIDER}")
        print(f"  STRATEGY 2: API {path} (curl_cffi)")
        print(f"{DIVIDER}")

        url = f"https://www.noon.com{path}"
        try:
            resp = cffi_requests.get(
                url,
                headers=JSON_HEADERS,
                params=params,
                impersonate="chrome131",
                timeout=30,
            )
            print(f"  Status: {resp.status_code}")
            print(f"  Content-Type: {resp.headers.get('Content-Type', '(none)')}")
            print(f"  Body size: {len(resp.content):,} bytes")

            try:
                data = resp.json()
                print(f"  JSON keys: {list(data.keys())[:15]}")

                # Look for products.
                for key in ["hits", "results", "products", "data"]:
                    if key in data:
                        val = data[key]
                        if isinstance(val, list) and len(val) > 0:
                            print(f"  Found '{key}': {len(val)} items")
                            for i, p in enumerate(val[:3]):
                                title = p.get("title") or p.get("name") or str(p)[:100]
                                print(f"    [{i+1}] {title}")
                            print(f"  ✅ SUCCESS!")
                            break
                        elif isinstance(val, dict):
                            print(f"  '{key}' is dict with keys: {list(val.keys())[:10]}")
                else:
                    # Dump entire response (truncated).
                    txt = json.dumps(data, ensure_ascii=False, indent=2)
                    print(f"  Response:\n  {txt[:1500]}")

            except (json.JSONDecodeError, ValueError):
                if "just a moment" in resp.text.lower():
                    print("  ⚠️  Cloudflare challenge page")
                else:
                    print(f"  Body (first 500 chars): {resp.text[:500]}")

        except Exception as e:
            print(f"  ❌ Error: {type(e).__name__}: {e}")


def main():
    print("=" * 70)
    print("  NOON API RECON v2 — curl_cffi TLS impersonation")
    print(f"  Query: {QUERY}")
    print("=" * 70)

    probe_ssr_page()
    probe_api_with_cffi()

    print(f"\n{'=' * 70}")
    print("  RECON v2 COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
