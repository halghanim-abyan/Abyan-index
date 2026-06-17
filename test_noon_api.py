"""
test_noon_api.py — Probe Noon's internal search API to bypass Cloudflare.

E-commerce SPAs load product data from JSON APIs, then render client-side.
If we can call the API directly with the right headers, we skip the browser
entirely — no Cloudflare challenge, no Playwright, no headless detection.

This script tries multiple known Noon API patterns and prints the results.
Run:  python test_noon_api.py
"""

import json
import requests
import sys

# ── The search query we want to test ─────────────────────────────────────────
QUERY = "حليب المراعي طازج 2 لتر"

# ── Headers that mimic a real browser / Noon mobile-app request ──────────────
# Noon's API checks these headers.  Missing or wrong values → 403 / empty body.
BROWSER_HEADERS = {
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8",
    "Referer":         "https://www.noon.com/saudi-ar/",
    "Origin":          "https://www.noon.com",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    # Noon sets this custom header in its XHR calls (visible in DevTools Network tab).
    "X-Locale":        "ar-sa",
    "X-Content":       "V6",
    "X-Platform":      "web",
}

# Noon's mobile app uses a slightly different UA and may hit a different host.
MOBILE_HEADERS = {
    **BROWSER_HEADERS,
    "User-Agent": (
        "Mozilla/5.0 (Linux; Android 14; SM-S918B) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Mobile Safari/537.36"
    ),
}


# ── Candidate API endpoints ──────────────────────────────────────────────────
# Noon has changed their internal API paths over the years.  We try several
# known patterns and report which ones respond.
ENDPOINTS = [
    # Pattern 1: The modern Next.js BFF (Backend-For-Frontend) proxy.
    # This is what the browser's JS calls after the page shell loads.
    {
        "name":    "/_svc/catalog/api/v3/search (catalog svc)",
        "url":     "https://www.noon.com/_svc/catalog/api/v3/search",
        "params":  {"q": QUERY, "limit": "5", "page": "1", "sort": "popularity"},
        "headers": BROWSER_HEADERS,
    },
    # Pattern 2: Older /api/search path.
    {
        "name":    "/_svc/catalog/api/search (legacy)",
        "url":     "https://www.noon.com/_svc/catalog/api/search",
        "params":  {"q": QUERY, "limit": "5", "page": "1"},
        "headers": BROWSER_HEADERS,
    },
    # Pattern 3: The public-facing /saudi-ar/ search page itself, but requesting
    # JSON via Accept header.  Some Next.js apps serve JSON for fetch() calls.
    {
        "name":    "/saudi-ar/search (page as JSON)",
        "url":     "https://www.noon.com/saudi-ar/search",
        "params":  {"q": QUERY},
        "headers": {**BROWSER_HEADERS, "Accept": "application/json"},
    },
    # Pattern 4: Next.js data route (__next/data).  The buildId changes per deploy
    # but we try a known pattern.  Even if it 404s, the error shape is informative.
    {
        "name":    "/_next/data/[buildId]/search.json",
        "url":     "https://www.noon.com/_next/data/build-id/saudi-ar/search.json",
        "params":  {"q": QUERY},
        "headers": BROWSER_HEADERS,
    },
    # Pattern 5: Mobile-app API (different host sometimes used by Noon's apps).
    {
        "name":    "/api/search (mobile UA)",
        "url":     "https://www.noon.com/_svc/catalog/api/v3/search",
        "params":  {"q": QUERY, "limit": "5", "page": "1"},
        "headers": MOBILE_HEADERS,
    },
    # Pattern 6: GraphQL endpoint (some Noon regions use this).
    {
        "name":    "/graphql (product search)",
        "url":     "https://www.noon.com/graphql",
        "params":  {},
        "headers": {
            **BROWSER_HEADERS,
            "Content-Type": "application/json",
        },
        "method":  "POST",
        "json_body": {
            "query": """
                query SearchProducts($query: String!) {
                    searchProducts(query: $query, limit: 5) {
                        hits { title price { now currency } }
                    }
                }
            """,
            "variables": {"query": QUERY},
        },
    },
]


def _pretty(data, max_lines: int = 40) -> str:
    """Pretty-print JSON, truncated to max_lines."""
    txt = json.dumps(data, ensure_ascii=False, indent=2)
    lines = txt.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines]) + f"\n  ... ({len(lines) - max_lines} more lines)"
    return txt


def probe_endpoint(ep: dict) -> None:
    """Try one API endpoint and print the result."""
    name   = ep["name"]
    url    = ep["url"]
    params = ep.get("params", {})
    method = ep.get("method", "GET")

    print(f"\n{'─' * 70}")
    print(f"  PROBE: {name}")
    print(f"  {method} {url}")
    if params:
        print(f"  params: { {k: v[:40] + '...' if len(str(v)) > 40 else v for k, v in params.items()} }")
    print(f"{'─' * 70}")

    try:
        if method == "POST":
            resp = requests.post(
                url,
                headers=ep["headers"],
                json=ep.get("json_body", {}),
                timeout=20,
            )
        else:
            resp = requests.get(
                url,
                headers=ep["headers"],
                params=params,
                timeout=20,
            )

        print(f"  Status:       {resp.status_code}")
        print(f"  Content-Type: {resp.headers.get('Content-Type', '(none)')}")
        print(f"  Body size:    {len(resp.content):,} bytes")

        # Try to parse as JSON.
        try:
            data = resp.json()
            # Look for product hits in common response shapes.
            hits = None
            for key_path in [
                "results",
                "hits",
                "data.hits",
                "data.results",
                "nbHits",
                "searchResults",
                "products",
            ]:
                obj = data
                for k in key_path.split("."):
                    if isinstance(obj, dict) and k in obj:
                        obj = obj[k]
                    else:
                        obj = None
                        break
                if obj is not None:
                    hits = obj
                    print(f"  Found data at key: '{key_path}'")
                    break

            if hits and isinstance(hits, list):
                print(f"  Product count: {len(hits)}")
                # Print first 3 products if they look like product objects.
                for i, item in enumerate(hits[:3]):
                    title = (
                        item.get("title") or item.get("name") or
                        item.get("title_ar") or item.get("productTitle") or
                        str(item)[:100]
                    )
                    price = (
                        item.get("price") or item.get("sale_price") or
                        item.get("currentPrice") or ""
                    )
                    if isinstance(price, dict):
                        price = price.get("now") or price.get("value") or price
                    print(f"  [{i+1}] {title}  —  {price}")
                print(f"\n  ✅ SUCCESS — This endpoint returns product data!")
            else:
                print(f"\n  Response JSON (excerpt):")
                print(f"  {_pretty(data)}")

        except (json.JSONDecodeError, ValueError):
            # Not JSON — show first 500 chars of the body.
            body_preview = resp.text[:500]
            if "<html" in body_preview.lower():
                print(f"  Body: HTML page (likely Cloudflare challenge or redirect)")
                # Check for CF challenge markers.
                if "cf-challenge" in resp.text.lower() or "just a moment" in resp.text.lower():
                    print(f"  ⚠️  Cloudflare challenge detected!")
                elif "ray id" in resp.text.lower():
                    print(f"  ⚠️  Cloudflare block page (Ray ID present)")
            else:
                print(f"  Body (first 500 chars): {body_preview}")

    except requests.exceptions.ConnectionError as e:
        print(f"  ❌ Connection error: {e}")
    except requests.exceptions.Timeout:
        print(f"  ❌ Request timed out (20s)")
    except Exception as e:
        print(f"  ❌ Error: {type(e).__name__}: {e}")


def main():
    print("=" * 70)
    print("  NOON API RECONNAISSANCE")
    print(f"  Query: {QUERY}")
    print("=" * 70)

    for ep in ENDPOINTS:
        probe_endpoint(ep)

    print(f"\n{'=' * 70}")
    print("  RECON COMPLETE")
    print("=" * 70)
    print("\nNext steps:")
    print("  • If any endpoint returned ✅ SUCCESS, we can build a direct API")
    print("    scraper in scraper.py (_scrape_noon_api) that skips Playwright.")
    print("  • If all returned Cloudflare / 403, we'll need to try:")
    print("    - curl_cffi (impersonates Chrome TLS fingerprint)")
    print("    - Extracting __NEXT_DATA__ from the SSR HTML")
    print("    - Using a residential proxy")


if __name__ == "__main__":
    main()
