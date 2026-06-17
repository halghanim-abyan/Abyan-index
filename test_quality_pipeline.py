"""
Small regression checks for the quality-aware inflation pipeline.

Run with:
    python test_quality_pipeline.py
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from calculator import (
    MAX_CONSECUTIVE_OOS_DAYS,
    _apply_promo_filter,
    _attach_price_relatives,
    _compute_index_for_date,
    _rebase_index_series,
    _smooth_price_relatives,
)
from scraper import (
    MATCH_TIER_GASTAT_REPRESENTATIVE,
    SCRAPE_STATUS_BLOCKED,
    SCRAPE_STATUS_OK,
    SCRAPE_STATUS_OOS,
    SCRAPE_STATUS_TIMEOUT,
)
from scraper import (
    _ninja_category_url_for_item,
    _price_candidates,
    _parse_price_text,
    _scrape_result,
    _clean_search_query,
    _extract_from_amazon_html,
    _extract_from_noon_html,
    _extract_from_tamimi_payload,
    classify_title_match,
    is_title_match,
)


def _row(day: date, item_id: int, price: float | None, status: str) -> dict:
    return {
        "date": day.isoformat(),
        "item_id": item_id,
        "store_name": "TestStore",
        "price": price,
        "scrape_status": status,
        "item_name": f"Item {item_id}",
        "category": "Test",
        "weight_percentage": 0.5,
    }


def test_oos_carry_is_bounded_and_failures_do_not_carry() -> None:
    start = date(2026, 1, 1)
    rows = [_row(start, 1, 10.0, SCRAPE_STATUS_OK)]
    for offset in range(1, MAX_CONSECUTIVE_OOS_DAYS + 2):
        rows.append(_row(start + timedelta(days=offset), 1, None, SCRAPE_STATUS_OOS))
    rows.append(_row(start + timedelta(days=MAX_CONSECUTIVE_OOS_DAYS + 2), 1, None, SCRAPE_STATUS_TIMEOUT))

    cleaned = _apply_promo_filter(pd.DataFrame(rows))
    clean = cleaned["clean_price"].tolist()
    imputed = cleaned["imputed"].tolist()

    assert clean[0] == 10.0
    assert clean[1:1 + MAX_CONSECUTIVE_OOS_DAYS] == [10.0] * MAX_CONSECUTIVE_OOS_DAYS
    assert imputed[1:1 + MAX_CONSECUTIVE_OOS_DAYS] == [True] * MAX_CONSECUTIVE_OOS_DAYS
    assert pd.isna(clean[1 + MAX_CONSECUTIVE_OOS_DAYS])
    assert pd.isna(clean[-1])
    assert imputed[-1] == False


def test_low_coverage_day_is_refused() -> None:
    start = date(2026, 1, 1)
    rows = []
    for item_id in range(1, 6):
        rows.append(_row(start, item_id, 10.0, SCRAPE_STATUS_OK))
        rows.append(_row(start + timedelta(days=1), item_id, 10.0, SCRAPE_STATUS_OK))

    # Four of five basket items fail on day 3, leaving 20% coverage.
    rows.append(_row(start + timedelta(days=2), 1, 10.0, SCRAPE_STATUS_OK))
    for item_id in range(2, 6):
        rows.append(_row(start + timedelta(days=2), item_id, None, SCRAPE_STATUS_BLOCKED))

    cleaned = _apply_promo_filter(pd.DataFrame(rows))
    enriched = _attach_price_relatives(cleaned)
    enriched = _smooth_price_relatives(enriched, window=1)

    assert _compute_index_for_date(enriched, (start + timedelta(days=1)).isoformat()) is not None
    assert _compute_index_for_date(enriched, (start + timedelta(days=2)).isoformat()) is None


def test_index_series_is_rebased_to_app_base_date() -> None:
    rebased = _rebase_index_series(
        {
            "2026-06-03": 118.787,
            "2026-06-04": 121.16274,
        },
        base_date="2026-06-03",
    )

    assert rebased["2026-06-03"] == 100.0
    assert rebased["2026-06-04"] == 102.0


def test_scrape_result_status_normalization() -> None:
    ok = _scrape_result(1, "Panda", 12.5, SCRAPE_STATUS_TIMEOUT, "ignored")
    timeout = _scrape_result(1, "Panda", None, SCRAPE_STATUS_TIMEOUT, "playwright_timeout")
    blocked = _scrape_result(1, "Panda", None, SCRAPE_STATUS_BLOCKED, "search_bar_missing")

    assert ok.scrape_status == SCRAPE_STATUS_OK
    assert ok.failure_reason is None
    assert timeout.scrape_status == SCRAPE_STATUS_TIMEOUT
    assert timeout.failure_reason == "playwright_timeout"
    assert blocked.scrape_status == SCRAPE_STATUS_BLOCKED


def test_tamimi_api_payload_extracts_verified_current_price() -> None:
    payload = {
        "data": {
            "page": {
                "layouts": [
                    {
                        "name": "ProductCollection",
                        "value": {
                            "collection": {
                                "product": [
                                    {
                                        "brand": {"name": "Almarai"},
                                        "name": "Fresh Milk Full Fat",
                                        "variants": [
                                            {
                                                "fullName": "Fresh Milk Full Fat-2L",
                                                "storeSpecificData": [
                                                    {"mrp": "12.5", "discount": "1.5", "stock": 4},
                                                ],
                                            },
                                        ],
                                    },
                                ],
                            },
                        },
                    },
                ],
            },
        },
    }

    result = _extract_from_tamimi_payload(payload, "Almarai Fresh Milk 2L")

    assert result.scrape_status == SCRAPE_STATUS_OK
    assert result.price == 11.0
    assert result.observed_title == "Almarai Fresh Milk Full Fat-2L"


def test_noon_html_extracts_non_supermarket_representative_price() -> None:
    html = r'''
    <script>
    self.__next_f.push([1,"{\"brand\":\"Noon East\",\"name\":\"9 Piece Cookware Set - Aluminum Pots And Pans - Non-Stick Surface\",\"price\":220,\"sale_price\":173.5,\"url\":\"cookware-set\",\"is_buyable\":true}"]);
    </script>
    '''

    result = _extract_from_noon_html(html, "Cookware set")

    assert result.scrape_status == SCRAPE_STATUS_OK
    assert result.price == 173.5
    assert result.match_tier == MATCH_TIER_GASTAT_REPRESENTATIVE
    assert "Cookware Set" in result.observed_title


def test_amazon_html_extracts_book_or_stationery_price() -> None:
    html = """
    <div data-component-type="s-search-result">
      <h2><span>Math Textbook Student Book</span></h2>
      <span class="a-price"><span class="a-offscreen">SAR 97.70</span></span>
    </div>
    """

    result = _extract_from_amazon_html(html, "Textbook bundle - secondary")

    assert result.scrape_status == SCRAPE_STATUS_OK
    assert result.price == 97.7
    assert result.match_tier == MATCH_TIER_GASTAT_REPRESENTATIVE
    assert "Textbook" in result.observed_title


def test_stacked_and_arabic_price_text_parses_as_decimal() -> None:
    assert _price_candidates("9\n95")[0] == 9.95
    assert _parse_price_text("20\n25", "Alwataniah Chicken 1000g", "Danube") == 20.25
    assert _parse_price_text("٩٫٩٥", "Local Tomatoes 1kg", "Danube") == 9.95


def test_title_match_requires_brand_and_pack_size() -> None:
    assert is_title_match(
        "Nova Mineral Water 1.5L 6P",
        "Nove Mineral Water 1.5 L Pack of 6",
    )
    assert not is_title_match(
        "Fairy Original Dish Liquid 750ml",
        "Jif Dishwashing Liquid Anti-Bacterial Mint & Lemon 750ml",
    )
    assert not is_title_match(
        "Finish Quantum Dishwasher Tablets 32P",
        "Finish Quantum All in 1 Dishwasher Tablets, Lemon Sparkle Scent, 90 Tabs",
    )
    assert not is_title_match(
        "Local Fresh Beef 1kg",
        "Seara Mortadella Beef Pepper 1kg",
    )
    assert not is_title_match(
        "Local Fresh Beef 1kg",
        "Hb Beef Mort Olives",
    )
    assert is_title_match(
        "Local Yellow Onions 1kg",
        "Brown/Yellow Onions",
    )
    assert not is_title_match(
        "Local Fresh Potatoes 1kg",
        "Sweet Potatoes",
    )
    assert not is_title_match(
        "Colgate Total Toothpaste 100ml",
        "Colgate Advanced White Toothpaste 2x100ml",
    )
    assert is_title_match(
        "Local Apples 1kg",
        "Royal Gala Apples",
    )
    assert is_title_match(
        "Facial Tissue 200 Sheets",
        "Fine Facial Tissues 200 Sheets",
    )
    assert is_title_match(
        "Macaroni 500g",
        "Barilla Fusilli Pasta 500g",
    )
    assert is_title_match(
        "Oats 500g",
        "كويكر شوفان أبيض 500 غرام",
    )
    assert is_title_match(
        "Facial Tissue 200 Sheets",
        "فاين مناديل للوجه 200 مناديل",
    )
    assert not is_title_match(
        "Facial Tissue 200 Sheets",
        "فاين مناديل للوجه 200 مناديل 5 عبوات",
    )
    assert not is_title_match(
        "Facial Tissue 200 Sheets",
        "Fine Facial Tissues 100 Sheets",
    )


def test_representative_substitutes_are_audited_and_bounded() -> None:
    tomato = classify_title_match(
        "Al Fakhama Tomato Paste 8x135g",
        "Heinz Tomato Paste 8X135G",
    )
    eggs = classify_title_match(
        "Almarai Brown Eggs 30P",
        "Al Watania Eggs in Plastic Plate 30pcs",
    )
    fairy_near_size = classify_title_match(
        "Fairy Original Dish Liquid 750ml",
        "Jif Dishwashing Liquid Anti-Bacterial Mint & Lemon 730ml",
    )

    assert not is_title_match("Al Fakhama Tomato Paste 8x135g", "Heinz Tomato Paste 8X135G")
    assert tomato.matched
    assert tomato.match_tier == MATCH_TIER_GASTAT_REPRESENTATIVE
    assert eggs.matched
    assert eggs.match_tier == MATCH_TIER_GASTAT_REPRESENTATIVE
    assert fairy_near_size.matched
    assert fairy_near_size.match_tier == MATCH_TIER_GASTAT_REPRESENTATIVE

    assert not classify_title_match(
        "Colgate Total Toothpaste 100ml",
        "Colgate Advanced White Toothpaste 2x100ml",
    ).matched
    assert not classify_title_match(
        "Finish Quantum Dishwasher Tablets 32P",
        "Finish Quantum All in 1 Dishwasher Tablets, Lemon Sparkle Scent, 90 Tabs",
    ).matched
    aquafina_alt = classify_title_match(
        "Aquafina Mineral Water 330ml 12P",
        "Nova Water 12 × 330ml",
    )
    assert aquafina_alt.matched
    assert aquafina_alt.match_tier == MATCH_TIER_GASTAT_REPRESENTATIVE


def test_representative_items_use_generic_search_queries() -> None:
    assert _clean_search_query("Saudia White Sugar 1kg") == "White Sugar 1kg"
    assert _clean_search_query("Al Wadi Al Akhdar Cooked Chickpeas 1kg") == "Cooked Chickpeas 1kg"
    assert _clean_search_query("Berain Bottled Water 600ml 12P") == "Water 600ml 12 pcs"
    assert _clean_search_query("Facial Tissue 200 Sheets") == "Facial Tissue 200 Sheets"


def test_ninja_search_items_resolve_to_real_categories() -> None:
    assert _ninja_category_url_for_item("Almarai Fresh Milk 2L").endswith("/milk")
    assert _ninja_category_url_for_item("Nova Mineral Water 1.5L 6P").endswith("/water-ice")
    assert _ninja_category_url_for_item("Fairy Original Dish Liquid 750ml").endswith(
        "/cleaning-supplies-gnf"
    )
    assert _ninja_category_url_for_item("Macaroni 500g").endswith("/pasta-rice-grains")
    assert _ninja_category_url_for_item("Facial Tissue 200 Sheets").endswith("/tissues")


if __name__ == "__main__":
    test_oos_carry_is_bounded_and_failures_do_not_carry()
    test_low_coverage_day_is_refused()
    test_index_series_is_rebased_to_app_base_date()
    test_scrape_result_status_normalization()
    test_tamimi_api_payload_extracts_verified_current_price()
    test_noon_html_extracts_non_supermarket_representative_price()
    test_amazon_html_extracts_book_or_stationery_price()
    test_stacked_and_arabic_price_text_parses_as_decimal()
    test_title_match_requires_brand_and_pack_size()
    test_representative_substitutes_are_audited_and_bounded()
    test_representative_items_use_generic_search_queries()
    test_ninja_search_items_resolve_to_real_categories()
    print("quality pipeline tests passed")
