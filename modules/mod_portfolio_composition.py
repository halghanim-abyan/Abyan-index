"""
mod_portfolio_composition.py — Portfolio Composition drill-down.

SINGLE SOURCE OF TRUTH
======================
Portfolio names, risk tiers and holdings are derived directly from
`mod_robo.PORTFOLIOS`. This module wraps that catalogue with Arabic ETF
metadata and presents it in the shape expected by:

    * GET /api/portfolio/{name}   (tadawul_api.py)
    * GET /api/portfolios          (tadawul_api.py)
    * "تحليل المحافظ" page         (main_app.py)

Because both the Robo page and the Composition page now read from the
same PORTFOLIOS list, Arabic names will always match exactly (e.g.
"أبيان النمو الفائق" — including the definite article ال) and the
`/api/portfolio/{name}` endpoint can never desync from the Robo UI.
"""

from __future__ import annotations

from typing import Any

from . import mod_robo


# ---------------------------------------------------------------------------
# ETF Dictionary — Arabic descriptive names for every ticker referenced in
# mod_robo.PORTFOLIOS. Unknown tickers fall back to a generic placeholder.
# ---------------------------------------------------------------------------

ETF_DICTIONARY: dict[str, dict[str, str]] = {
    # ── US-listed Shariah / sector ETFs ──────────────────────────────────
    "SPTE": {
        "name_ar": "صندوق مؤشر قطاع التقنية",
        "name_en": "SP Funds S&P Global Technology ETF",
        "category": "Sector — Technology",
    },
    "HLAL": {
        "name_ar": "صندوق أسهم أمريكية متوافق مع الشريعة",
        "name_en": "Wahed FTSE USA Shariah ETF",
        "category": "Equity — US Shariah",
    },
    "SPUS": {
        "name_ar": "صندوق الأسهم الأمريكية الشرعي",
        "name_en": "SP Funds S&P 500 Shariah Industry Exclusions ETF",
        "category": "Equity — US Shariah",
    },
    "SPWO": {
        "name_ar": "صندوق الأسهم العالمية الشرعي",
        "name_en": "SP Funds S&P World ex-US Shariah ETF",
        "category": "Equity — Global Shariah",
    },
    "SPSK": {
        "name_ar": "صندوق صكوك إسلامية",
        "name_en": "SP Funds Dow Jones Global Sukuk ETF",
        "category": "Fixed Income — Sukuk",
    },
    "SPRE": {
        "name_ar": "صندوق العقارات العالمي الشرعي",
        "name_en": "SP Funds S&P Global REIT Shariah ETF",
        "category": "Real Estate — Shariah",
    },
    # ── Commodity / alternative ETFs ─────────────────────────────────────
    "GLD": {
        "name_ar": "صندوق إس بي دي آر للذهب",
        "name_en": "SPDR Gold Shares ETF",
        "category": "Commodity — Gold",
    },
    "SLV": {
        "name_ar": "صندوق آي شيرز للفضة",
        "name_en": "iShares Silver Trust ETF",
        "category": "Commodity — Silver",
    },
    "IBIT": {
        "name_ar": "صندوق آي شيرز للبيتكوين الفوري",
        "name_en": "iShares Bitcoin Trust ETF",
        "category": "Alternative — Bitcoin",
    },
    # ── Saudi / Tadawul ───────────────────────────────────────────────────
    "1120.SR": {
        "name_ar": "مصرف الراجحي",
        "name_en": "Al Rajhi Bank (Tadawul)",
        "category": "Equity — Saudi (Banking)",
    },
    "STEF": {
        "name_ar": "صندوق الراجحي للأسهم السعودية",
        "name_en": "Al Rajhi Saudi Equity ETF",
        "category": "Equity — Saudi",
    },
    "UMMA": {
        "name_ar": "صندوق وحيد الإسلامي للأسهم العالمية",
        "name_en": "Wahed Dow Jones Islamic World ETF",
        "category": "Equity — Global Shariah",
    },
    "ITFS": {
        "name_ar": "صندوق صكوك دولية",
        "name_en": "International Sukuk ETF",
        "category": "Fixed Income — Sukuk",
    },
    "SRTF": {
        "name_ar": "صندوق الراجحي للريت",
        "name_en": "Al Rajhi REIT ETF",
        "category": "Real Estate — Saudi",
    },
    # ── UCITS (London-listed) Shariah ETFs ───────────────────────────────
    "ISDW.L": {
        "name_ar": "صندوق آي شيرز للأسهم العالمية الشرعي (UCITS)",
        "name_en": "iShares MSCI World Islamic UCITS ETF",
        "category": "Equity — Global Shariah (UCITS)",
    },
    "ISDE.L": {
        "name_ar": "صندوق آي شيرز للأسهم الناشئة الشرعي (UCITS)",
        "name_en": "iShares MSCI EM Islamic UCITS ETF",
        "category": "Equity — Emerging Shariah (UCITS)",
    },
    # ── Legacy tickers kept for backward compatibility ───────────────────
    "SPUK": {
        "name_ar": "صندوق الأسهم البريطانية الشرعي",
        "name_en": "SP Funds S&P UK Shariah ETF",
        "category": "Equity — UK",
    },
    "SHV": {
        "name_ar": "صندوق سندات الخزانة قصيرة الأجل",
        "name_en": "iShares Short Treasury Bond ETF",
        "category": "Fixed Income — Short-Term Treasury",
    },
}


# ---------------------------------------------------------------------------
# Portfolio Database — projected from mod_robo.PORTFOLIOS at import time.
# mod_robo stores holdings as fractions (0-1); this module exposes them as
# percentages (0-100), which is what callers historically consumed.
# ---------------------------------------------------------------------------

def _build_portfolios_db() -> dict[str, dict[str, Any]]:
    """Project mod_robo.PORTFOLIOS into the shape this module exposes.

    The Arabic key in the returned dict is taken VERBATIM from mod_robo so
    the two modules can never drift apart (no more "أبيان نمو فائق" vs
    "أبيان النمو الفائق" mismatch).
    """
    db: dict[str, dict[str, Any]] = {}
    for p in mod_robo.PORTFOLIOS:
        name_ar = p["name"]
        risk_tier = p.get("risk_tier", "")
        risk_ar = mod_robo.TIER_LABELS_AR.get(risk_tier, "")
        db[name_ar] = {
            "name_ar":  name_ar,
            "name_en":  p.get("name_en", name_ar),
            "risk_ar":  risk_ar,
            "tag":      p.get("tag", ""),
            "holdings": {t: round(w * 100.0, 4) for t, w in p["holdings"].items()},
        }
    return db


PORTFOLIOS_DB: dict[str, dict[str, Any]] = _build_portfolios_db()


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------

def list_portfolio_names() -> list[str]:
    """Return all portfolio names (Arabic) in insertion order."""
    return list(PORTFOLIOS_DB.keys())


def get_etf_info(ticker: str) -> dict[str, str]:
    """Return {name_ar, name_en, category} for a ticker. Graceful fallback for unknowns."""
    t = ticker.strip().upper()
    if t in ETF_DICTIONARY:
        return ETF_DICTIONARY[t]
    return {
        "name_ar": f"صندوق {t}",
        "name_en": t,
        "category": "Unknown",
    }


def get_portfolio_composition(name: str) -> dict[str, Any] | None:
    """Resolve a portfolio by Arabic or English name.

    Returns a rich dict:
        {
          "name_ar": "...",
          "name_en": "...",
          "risk_ar": "...",
          "tag":     "...",
          "num_funds": 3,
          "total_weight_pct": 100.0,
          "is_valid": True,          # True iff total weight ≈ 100%
          "holdings": [
              {"ticker": "SPTE",
               "name_ar": "...",
               "name_en": "...",
               "category": "...",
               "weight_pct": 45.0},
              ...
          ]
        }
    Returns None if no portfolio matches.
    """
    if not name:
        return None

    needle = name.strip().lower()

    match_key: str | None = None
    for key, cfg in PORTFOLIOS_DB.items():
        if (key.strip().lower() == needle
                or cfg.get("name_en", "").strip().lower() == needle):
            match_key = key
            break

    if match_key is None:
        return None

    cfg = PORTFOLIOS_DB[match_key]
    holdings_raw: dict[str, float] = cfg["holdings"]

    holdings_out: list[dict[str, Any]] = []
    for ticker, weight in holdings_raw.items():
        info = get_etf_info(ticker)
        holdings_out.append({
            "ticker":     ticker.upper(),
            "name_ar":    info["name_ar"],
            "name_en":    info["name_en"],
            "category":   info["category"],
            "weight_pct": round(float(weight), 2),
        })

    total_weight = round(sum(h["weight_pct"] for h in holdings_out), 2)

    return {
        "name_ar":          cfg["name_ar"],
        "name_en":          cfg.get("name_en", match_key),
        "risk_ar":          cfg.get("risk_ar", ""),
        "tag":              cfg.get("tag", ""),
        "num_funds":        len(holdings_out),
        "total_weight_pct": total_weight,
        "is_valid":         abs(total_weight - 100.0) <= 0.5,
        "holdings":         holdings_out,
    }


def list_portfolios_summary() -> list[dict[str, Any]]:
    """Lightweight summary of every portfolio — for /api/portfolios index."""
    summaries: list[dict[str, Any]] = []
    for name in PORTFOLIOS_DB:
        comp = get_portfolio_composition(name)
        if comp is None:
            continue
        summaries.append({
            "name_ar":          comp["name_ar"],
            "name_en":          comp["name_en"],
            "risk_ar":          comp["risk_ar"],
            "tag":              comp["tag"],
            "num_funds":        comp["num_funds"],
            "total_weight_pct": comp["total_weight_pct"],
            "is_valid":         comp["is_valid"],
        })
    return summaries


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json
    for n in list_portfolio_names():
        print(json.dumps(get_portfolio_composition(n), indent=2, ensure_ascii=False))
        print("---")
