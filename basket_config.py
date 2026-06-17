"""
basket_config.py
================

Single source of truth for the Saudi Daily Inflation Index basket.

HOW TO ADD A NEW ITEM
---------------------
Just append one entry to the BASKET list below. Each entry is a dict with:

    name      str   – unique product name (used as the DB key — keep stable)
    category  str   – one of CATEGORIES below (or add your own)
    weight    float – RELATIVE weight inside the basket. It is auto-normalised
                      so all weights together sum to 1.0. So 0.11 / 0.14 / 0.05
                      work, but you can also use plain ratios (1, 2, 3) — both
                      produce the same normalised result.
    urls      dict  – store-name → product URL (Panda + Danube + Ninja).
                      Use the supermarket's *search* URL whenever possible — it
                      is more resilient to product-page reshuffles than a
                      direct PDP link.

Example — adding "Almarai Laban 1L":

    {
        "name":     "Almarai Laban 1L",
        "category": "Dairy",
        "weight":   0.04,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=لبن+المراعي+1+لتر",
            "Danube": "https://danube.sa/ar/search?query=لبن+المراعي+1+لتر",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=لبن+المراعي+1+لتر",
        },
    },

That's it. Re-run `python db_setup.py` (or `python main.py --setup`) and the
new item is added to the database; future scraper runs pick it up
automatically.

CATEGORY → CPI MAPPING
----------------------
Food sub-indices (GASTAT Saudi Arabia):
    Grains, Meat, Fish, Dairy, Oils, Fruits, Vegetables,
    Sugar, Beverages, Canned, Spices, Legumes, Dates

Non-food sub-indices:
    PersonalCare, HomeCleaning
"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus

from external_basket import build_non_supermarket_basket
from market_sources import daily_market_urls_for_item

CATEGORIES: tuple[str, ...] = (
    # Food
    "Grains", "Meat", "Fish", "Dairy", "Oils", "Fruits", "Vegetables",
    "Sugar", "Beverages", "Canned", "Spices", "Legumes", "Dates",
    # Non-food (Core CPI personal-care + household sub-indices)
    "PersonalCare", "HomeCleaning",
    # Non-supermarket CPI divisions and services
    "Housing", "Utilities", "Transport", "Communication", "Health",
    "Education", "Recreation", "Restaurants", "Hotels", "Clothing",
    "Footwear", "Furniture", "HouseholdServices", "Insurance",
    "FinancialServices", "PersonalServices",
)

# ══════════════════════════════════════════════════════════════════════════════
#  THE BASKET — add new items at the bottom of this list, one block each.
# ══════════════════════════════════════════════════════════════════════════════

BASKET: list[dict[str, Any]] = [
    # ─── 1. Grains ──────────────────────────────────────────────────────────
    {
        "name":     "Abu Kass Basmati Rice 5kg",
        "category": "Grains",
        "weight":   0.11,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=ارز+بسمتي+ابو+كاس+5+كيلو",
            "Danube": "https://danube.sa/ar/search?query=ارز+بسمتي+ابو+كاس+5+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/category/rice",
        },
    },

    # ─── 2. Meat ────────────────────────────────────────────────────────────
    {
        "name":     "Alwataniah Chicken 1000g",
        "category": "Meat",
        "weight":   0.14,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=دجاج+الوطنية+1000+جرام",
            "Danube": "https://danube.sa/ar/search?query=دجاج+الوطنية+1000+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/category/chicken",
        },
    },

    # ─── 3. Fish & Seafood ──────────────────────────────────────────────────
    {
        "name":     "Americana Shrimps 400g",
        "category": "Fish",
        "weight":   0.05,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=روبيان+امريكانا+400+جرام",
            "Danube": "https://danube.sa/ar/search?query=روبيان+امريكانا+400+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/category/frozen-food",
        },
    },

    # ─── 4. Dairy & Eggs ────────────────────────────────────────────────────
    {
        "name":     "Almarai Fresh Milk 2L",
        "category": "Dairy",
        "weight":   0.12,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=حليب+المراعي+طازج+2+لتر",
            "Danube": "https://danube.sa/ar/search?query=حليب+المراعي+طازج+2+لتر",
            "Ninja":  "https://ananinja.com/sa/ar/category/milk",
        },
    },

    # ─── 5. Oils & Fats ─────────────────────────────────────────────────────
    {
        "name":     "Afia Corn Oil 1.5L",
        "category": "Oils",
        "weight":   0.07,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=زيت+ذرة+افيا+1.5+لتر",
            "Danube": "https://danube.sa/ar/search?query=زيت+ذرة+افيا+1.5+لتر",
            "Ninja":  "https://ananinja.com/sa/ar/category/oil",
        },
    },

    # ─── 6. Fruits ──────────────────────────────────────────────────────────
    {
        "name":     "Local Bananas 1kg",
        "category": "Fruits",
        "weight":   0.07,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=موز+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=موز+طازج+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/category/fruits",
        },
    },

    # ─── 7. Vegetables ──────────────────────────────────────────────────────
    {
        "name":     "Local Tomatoes 1kg",
        "category": "Vegetables",
        "weight":   0.09,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=طماطم+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=طماطم+طازجة+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/category/vegetables",
        },
    },

    # ─── 8. Sugar & Sweets ──────────────────────────────────────────────────
    {
        "name":     "Saudia White Sugar 1kg",
        "category": "Sugar",
        "weight":   0.06,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=سكر+ابيض+السعودية+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=سكر+ابيض+السعودية+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/category/sugar",
        },
    },

    # ─── 9. Beverages ───────────────────────────────────────────────────────
    {
        "name":     "Nescafe Classic 200g",
        "category": "Beverages",
        "weight":   0.08,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=نسكافيه+كلاسيك+200+جرام",
            "Danube": "https://danube.sa/ar/search?query=نسكافيه+كلاسيك+200+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/category/coffee",
        },
    },

    # ─── 10. Canned Food ────────────────────────────────────────────────────
    {
        "name":     "Goody Tuna 185g",
        "category": "Canned",
        "weight":   0.06,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=تونة+قودي+185+جرام",
            "Danube": "https://danube.sa/ar/search?query=تونة+قودي+185+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/category/canned-food",
        },
    },

    # ─── 11. Spices & Nuts ──────────────────────────────────────────────────
    {
        "name":     "Majdi Cardamom 50g",
        "category": "Spices",
        "weight":   0.07,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=هيل+مجدي+50+جرام",
            "Danube": "https://danube.sa/ar/search?query=هيل+مجدي+50+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/category/spices",
        },
    },

    # ─── 12. Legumes ────────────────────────────────────────────────────────
    {
        "name":     "Al Doha Red Lentils 1kg",
        "category": "Legumes",
        "weight":   0.08,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=عدس+احمر+الدوحة+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=عدس+احمر+الدوحة+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/category/legumes",
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    #  EXPANSION BATCH 1 — 15 essential Saudi grocery staples (items 13–27)
    #  Categories: Grains (incl. bakery), Meat, Dairy, Vegetables,
    #              Canned (pantry), Legumes
    # ══════════════════════════════════════════════════════════════════════════

    # ─── 13. Al Tayebat Arabic Pita Bread 6P  [Grains / Bakery] ─────────────
    {
        "name":     "Al Tayebat Arabic Pita Bread 6P",
        "category": "Grains",
        "weight":   0.05,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=خبز+عربي+الطيبات+6+حبات",
            "Danube": "https://danube.sa/ar/search?query=خبز+عربي+الطيبات+6+حبات",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=خبز+عربي+الطيبات+6+حبات",
        },
    },

    # ─── 14. L'usine White Sliced Bread 600g  [Grains / Bakery] ─────────────
    {
        "name":     "Lusine White Sliced Bread 600g",
        "category": "Grains",
        "weight":   0.04,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=خبز+توست+لوزين+ابيض+600+جرام",
            "Danube": "https://danube.sa/ar/search?query=خبز+توست+لوزين+ابيض+600+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=خبز+توست+لوزين+ابيض+600+جرام",
        },
    },

    # ─── 15. Almarai Cheddar Cheese Triangles 8P  [Dairy] ───────────────────
    {
        "name":     "Almarai Cheddar Cheese Triangles 8P",
        "category": "Dairy",
        "weight":   0.04,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=جبنة+المراعي+المثلثات+8+حبات",
            "Danube": "https://danube.sa/ar/search?query=جبنة+المراعي+المثلثات+8+حبات",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=جبنة+المراعي+المثلثات+8+حبات",
        },
    },

    # ─── 16. Al Fakhama Tomato Paste 8x135g  [Canned / Pantry] ──────────────
    {
        "name":     "Al Fakhama Tomato Paste 8x135g",
        "category": "Canned",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=معجون+طماطم+الفخامة+8+حبات+135+جرام",
            "Danube": "https://danube.sa/ar/search?query=معجون+طماطم+الفخامة+8+حبات+135+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=معجون+طماطم+الفخامة+8+حبات+135+جرام",
        },
    },

    # ─── 17. Kuwaiti Flour No.1 1kg  [Grains / Pantry] ──────────────────────
    {
        "name":     "Kuwaiti Flour No.1 1kg",
        "category": "Grains",
        "weight":   0.04,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=طحين+كويتي+رقم+1+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=طحين+كويتي+رقم+1+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=طحين+كويتي+رقم+1+1+كيلو",
        },
    },

    # ─── 18. Sunbullah Frozen Minced Meat 400g  [Meat & Poultry] ────────────
    {
        "name":     "Sunbullah Frozen Minced Meat 400g",
        "category": "Meat",
        "weight":   0.05,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=لحم+مفروم+السنبلة+مجمد+400+جرام",
            "Danube": "https://danube.sa/ar/search?query=لحم+مفروم+السنبلة+مجمد+400+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=لحم+مفروم+السنبلة+مجمد+400+جرام",
        },
    },

    # ─── 19. Almarai Greek Yogurt 1kg  [Dairy] ──────────────────────────────
    {
        "name":     "Almarai Greek Yogurt 1kg",
        "category": "Dairy",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=زبادي+يوناني+المراعي+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=زبادي+يوناني+المراعي+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=زبادي+يوناني+المراعي+1+كيلو",
        },
    },

    # ─── 20. Almarai Brown Eggs 30P  [Dairy] ────────────────────────────────
    {
        "name":     "Almarai Brown Eggs 30P",
        "category": "Dairy",
        "weight":   0.05,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=بيض+المراعي+بني+30+حبة",
            "Danube": "https://danube.sa/ar/search?query=بيض+المراعي+بني+30+حبة",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=بيض+المراعي+بني+30+حبة",
        },
    },

    # ─── 21. Local Fresh Cucumbers 1kg  [Vegetables] ────────────────────────
    {
        "name":     "Local Fresh Cucumbers 1kg",
        "category": "Vegetables",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=خيار+طازج+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=خيار+طازج+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=خيار+طازج+1+كيلو",
        },
    },

    # ─── 22. Local Yellow Onions 1kg  [Vegetables] ──────────────────────────
    {
        "name":     "Local Yellow Onions 1kg",
        "category": "Vegetables",
        "weight":   0.04,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=بصل+اصفر+طازج+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=بصل+اصفر+طازج+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=بصل+اصفر+طازج+1+كيلو",
        },
    },

    # ─── 23. Local Fresh Potatoes 1kg  [Vegetables] ─────────────────────────
    {
        "name":     "Local Fresh Potatoes 1kg",
        "category": "Vegetables",
        "weight":   0.04,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=بطاطس+طازج+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=بطاطس+طازج+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=بطاطس+طازج+1+كيلو",
        },
    },

    # ─── 24. Lurpak Salted Butter 200g  [Dairy] ─────────────────────────────
    {
        "name":     "Lurpak Salted Butter 200g",
        "category": "Dairy",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=زبدة+لورباك+مملحة+200+جرام",
            "Danube": "https://danube.sa/ar/search?query=زبدة+لورباك+مملحة+200+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=زبدة+لورباك+مملحة+200+جرام",
        },
    },

    # ─── 25. Al Wadi Al Akhdar Cooked Chickpeas 1kg  [Legumes] ──────────────
    {
        "name":     "Al Wadi Al Akhdar Cooked Chickpeas 1kg",
        "category": "Legumes",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=حمص+الوادي+الاخضر+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=حمص+الوادي+الاخضر+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=حمص+الوادي+الاخضر+1+كيلو",
        },
    },

    # ─── 26. Al Walima Long Grain Rice 5kg  [Grains] ────────────────────────
    {
        "name":     "Al Walima Long Grain Rice 5kg",
        "category": "Grains",
        "weight":   0.04,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=ارز+الوليمة+طويل+الحبة+5+كيلو",
            "Danube": "https://danube.sa/ar/search?query=ارز+الوليمة+طويل+الحبة+5+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=ارز+الوليمة+طويل+الحبة+5+كيلو",
        },
    },

    # ─── 27. Saudia Long-Life UHT Milk 1L  [Dairy] ──────────────────────────
    {
        "name":     "Saudia Long-Life UHT Milk 1L",
        "category": "Dairy",
        "weight":   0.04,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=حليب+السعودية+طويل+الاجل+1+لتر",
            "Danube": "https://danube.sa/ar/search?query=حليب+السعودية+طويل+الاجل+1+لتر",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=حليب+السعودية+طويل+الاجل+1+لتر",
        },
    },

    # ══════════════════════════════════════════════════════════════════════════
    #  EXPANSION BATCH 2 — 23 items (items 28–50)
    #  Coffee/Tea, Dates & Sweets, Bottled Water, Personal Care,
    #  Home Cleaning, Fresh Meats & Local Fish
    # ══════════════════════════════════════════════════════════════════════════

    # ─── 28. Al Khair Saudi Coffee with Bahar 250g  [Beverages / Coffee] ────
    {
        "name":     "Al Khair Saudi Coffee Bahar 250g",
        "category": "Beverages",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=قهوة+سعودية+الخير+بهارات+250+جرام",
            "Danube": "https://danube.sa/ar/search?query=قهوة+سعودية+الخير+بهارات+250+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=قهوة+سعودية+الخير+بهارات+250+جرام",
        },
    },

    # ─── 29. Rabea Premium Loose Tea 400g  [Beverages / Tea] ────────────────
    {
        "name":     "Rabea Premium Loose Tea 400g",
        "category": "Beverages",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=شاي+ربيع+سائب+400+جرام",
            "Danube": "https://danube.sa/ar/search?query=شاي+ربيع+سائب+400+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=شاي+ربيع+سائب+400+جرام",
        },
    },

    # ─── 30. Lipton Yellow Label Tea Bags 100P  [Beverages / Tea] ───────────
    {
        "name":     "Lipton Yellow Label Tea Bags 100P",
        "category": "Beverages",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=شاي+ليبتون+اصفر+100+كيس",
            "Danube": "https://danube.sa/ar/search?query=شاي+ليبتون+اصفر+100+كيس",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=شاي+ليبتون+اصفر+100+كيس",
        },
    },

    # ─── 31. Maatouk Turkish Coffee 200g  [Beverages / Coffee] ──────────────
    {
        "name":     "Maatouk Turkish Coffee 200g",
        "category": "Beverages",
        "weight":   0.02,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=قهوة+تركية+معتوق+200+جرام",
            "Danube": "https://danube.sa/ar/search?query=قهوة+تركية+معتوق+200+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=قهوة+تركية+معتوق+200+جرام",
        },
    },

    # ─── 32. Bateel Khalas Dates 1kg  [Dates] ───────────────────────────────
    {
        "name":     "Bateel Khalas Dates 1kg",
        "category": "Dates",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=تمر+خلاص+باتيل+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=تمر+خلاص+باتيل+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=تمر+خلاص+باتيل+1+كيلو",
        },
    },

    # ─── 33. Sukkari Premium Dates 1kg  [Dates] ─────────────────────────────
    {
        "name":     "Sukkari Premium Dates 1kg",
        "category": "Dates",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=تمر+سكري+فاخر+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=تمر+سكري+فاخر+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=تمر+سكري+فاخر+1+كيلو",
        },
    },

    # ─── 34. Krinos Halawa Tahini 500g  [Sugar / Sweets] ────────────────────
    {
        "name":     "Krinos Halawa Tahini 500g",
        "category": "Sugar",
        "weight":   0.02,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=حلاوة+طحينية+كرينوس+500+جرام",
            "Danube": "https://danube.sa/ar/search?query=حلاوة+طحينية+كرينوس+500+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=حلاوة+طحينية+كرينوس+500+جرام",
        },
    },

    # ─── 35. Al Shifa Pure Natural Honey 250g  [Sugar / Sweets] ─────────────
    {
        "name":     "Al Shifa Pure Natural Honey 250g",
        "category": "Sugar",
        "weight":   0.02,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=عسل+الشفاء+طبيعي+250+جرام",
            "Danube": "https://danube.sa/ar/search?query=عسل+الشفاء+طبيعي+250+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=عسل+الشفاء+طبيعي+250+جرام",
        },
    },

    # ─── 36. Nova Mineral Water 1.5L 6P  [Beverages / Water] ────────────────
    {
        "name":     "Nova Mineral Water 1.5L 6P",
        "category": "Beverages",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=مياه+نوفا+1.5+لتر+6+حبات",
            "Danube": "https://danube.sa/ar/search?query=مياه+نوفا+1.5+لتر+6+حبات",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=مياه+نوفا+1.5+لتر+6+حبات",
        },
    },

    # ─── 37. Berain Bottled Water 600ml 12P  [Beverages / Water] ────────────
    {
        "name":     "Berain Bottled Water 600ml 12P",
        "category": "Beverages",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=مياه+بيرين+600+مل+12+حبة",
            "Danube": "https://danube.sa/ar/search?query=مياه+بيرين+600+مل+12+حبة",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=مياه+بيرين+600+مل+12+حبة",
        },
    },

    # ─── 38. Aquafina Mineral Water 330ml 12P  [Beverages / Water] ──────────
    {
        "name":     "Aquafina Mineral Water 330ml 12P",
        "category": "Beverages",
        "weight":   0.02,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=مياه+اكوافينا+330+مل+12+حبة",
            "Danube": "https://danube.sa/ar/search?query=مياه+اكوافينا+330+مل+12+حبة",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=مياه+اكوافينا+330+مل+12+حبة",
        },
    },

    # ─── 39. Dettol Antiseptic Bar Soap 165g  [PersonalCare] ────────────────
    {
        "name":     "Dettol Antiseptic Bar Soap 165g",
        "category": "PersonalCare",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=صابون+ديتول+مطهر+165+جرام",
            "Danube": "https://danube.sa/ar/search?query=صابون+ديتول+مطهر+165+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=صابون+ديتول+مطهر+165+جرام",
        },
    },

    # ─── 40. Pantene Pro-V Classic Shampoo 700ml  [PersonalCare] ────────────
    {
        "name":     "Pantene Pro-V Classic Shampoo 700ml",
        "category": "PersonalCare",
        "weight":   0.02,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=شامبو+بانتين+برو+في+700+مل",
            "Danube": "https://danube.sa/ar/search?query=شامبو+بانتين+برو+في+700+مل",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=شامبو+بانتين+برو+في+700+مل",
        },
    },

    # ─── 41. Colgate Total Toothpaste 100ml  [PersonalCare] ─────────────────
    {
        "name":     "Colgate Total Toothpaste 100ml",
        "category": "PersonalCare",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=معجون+اسنان+كولجيت+توتال+100+مل",
            "Danube": "https://danube.sa/ar/search?query=معجون+اسنان+كولجيت+توتال+100+مل",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=معجون+اسنان+كولجيت+توتال+100+مل",
        },
    },

    # ─── 42. Always Cotton Soft Pads 16P  [PersonalCare] ────────────────────
    {
        "name":     "Always Cotton Soft Pads 16P",
        "category": "PersonalCare",
        "weight":   0.02,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=فوط+صحية+اولويز+قطن+16+حبة",
            "Danube": "https://danube.sa/ar/search?query=فوط+صحية+اولويز+قطن+16+حبة",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=فوط+صحية+اولويز+قطن+16+حبة",
        },
    },

    # ─── 43. Tide Original Powder Detergent 6kg  [HomeCleaning] ─────────────
    {
        "name":     "Tide Original Powder Detergent 6kg",
        "category": "HomeCleaning",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=مسحوق+غسيل+تايد+اصلي+6+كيلو",
            "Danube": "https://danube.sa/ar/search?query=مسحوق+غسيل+تايد+اصلي+6+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=مسحوق+غسيل+تايد+اصلي+6+كيلو",
        },
    },

    # ─── 44. Fairy Original Dish Liquid 750ml  [HomeCleaning] ───────────────
    {
        "name":     "Fairy Original Dish Liquid 750ml",
        "category": "HomeCleaning",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=سائل+اطباق+فيري+اصلي+750+مل",
            "Danube": "https://danube.sa/ar/search?query=سائل+اطباق+فيري+اصلي+750+مل",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=سائل+اطباق+فيري+اصلي+750+مل",
        },
    },

    # ─── 45. Clorox Original Bleach 950ml  [HomeCleaning] ───────────────────
    {
        "name":     "Clorox Original Bleach 950ml",
        "category": "HomeCleaning",
        "weight":   0.02,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=كلوركس+مبيض+اصلي+950+مل",
            "Danube": "https://danube.sa/ar/search?query=كلوركس+مبيض+اصلي+950+مل",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=كلوركس+مبيض+اصلي+950+مل",
        },
    },

    # ─── 46. Finish Quantum Dishwasher Tablets 32P  [HomeCleaning] ──────────
    {
        "name":     "Finish Quantum Dishwasher Tablets 32P",
        "category": "HomeCleaning",
        "weight":   0.02,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=فينش+كوانتم+اقراص+غسالة+صحون+32+حبة",
            "Danube": "https://danube.sa/ar/search?query=فينش+كوانتم+اقراص+غسالة+صحون+32+حبة",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=فينش+كوانتم+اقراص+غسالة+صحون+32+حبة",
        },
    },

    # ─── 47. Local Naemi Fresh Lamb 1kg  [Meat / Fresh] ─────────────────────
    {
        "name":     "Local Naemi Fresh Lamb 1kg",
        "category": "Meat",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=لحم+نعيمي+طازج+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=لحم+نعيمي+طازج+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=لحم+نعيمي+طازج+1+كيلو",
        },
    },

    # ─── 48. Local Fresh Beef 1kg  [Meat / Fresh] ───────────────────────────
    {
        "name":     "Local Fresh Beef 1kg",
        "category": "Meat",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=لحم+بقري+طازج+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=لحم+بقري+طازج+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=لحم+بقري+طازج+1+كيلو",
        },
    },

    # ─── 49. Fresh Norwegian Salmon Fillet 500g  [Fish / Fresh] ─────────────
    {
        "name":     "Fresh Norwegian Salmon Fillet 500g",
        "category": "Fish",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=سمك+سلمون+نرويجي+طازج+500+جرام",
            "Danube": "https://danube.sa/ar/search?query=سمك+سلمون+نرويجي+طازج+500+جرام",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=سمك+سلمون+نرويجي+طازج+500+جرام",
        },
    },

    # ─── 50. Local Fresh Hamour Fish 1kg  [Fish / Fresh] ────────────────────
    {
        "name":     "Local Fresh Hamour Fish 1kg",
        "category": "Fish",
        "weight":   0.03,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=سمك+هامور+طازج+1+كيلو",
            "Danube": "https://danube.sa/ar/search?query=سمك+هامور+طازج+1+كيلو",
            "Ninja":  "https://ananinja.com/sa/ar/search?q=سمك+هامور+طازج+1+كيلو",
        },
    },

    # ---------------------------------------------------------------------
    #  GASTAT Average Prices alignment batch 2 (items 51-60)
    #  Supermarket-observable staples chosen from the public Average Prices
    #  style of goods/services. This is still an app basket, not the full
    #  official CPI basket.
    # ---------------------------------------------------------------------
    {
        "name":     "Local Apples 1kg",
        "category": "Fruits",
        "weight":   0.025,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=apples+1kg",
            "Danube": "https://danube.sa/ar/search?query=apples+1kg",
            "Ninja":  "https://ananinja.com/sa/ar/category/fruits-vegetables",
        },
    },
    {
        "name":     "Local Oranges 1kg",
        "category": "Fruits",
        "weight":   0.025,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=oranges+1kg",
            "Danube": "https://danube.sa/ar/search?query=oranges+1kg",
            "Ninja":  "https://ananinja.com/sa/ar/category/fruits-vegetables",
        },
    },
    {
        "name":     "Local Lemons 1kg",
        "category": "Fruits",
        "weight":   0.015,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=lemons+1kg",
            "Danube": "https://danube.sa/ar/search?query=lemons+1kg",
            "Ninja":  "https://ananinja.com/sa/ar/category/fruits-vegetables",
        },
    },
    {
        "name":     "Fresh Laban 1L",
        "category": "Dairy",
        "weight":   0.025,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=laban+1l",
            "Danube": "https://danube.sa/ar/search?query=laban+1l",
            "Ninja":  "https://ananinja.com/sa/ar/category/milk",
        },
    },
    {
        "name":     "Evaporated Milk 170g",
        "category": "Dairy",
        "weight":   0.015,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=evaporated+milk+170g",
            "Danube": "https://danube.sa/ar/search?query=evaporated+milk+170g",
            "Ninja":  "https://ananinja.com/sa/ar/category/milk",
        },
    },
    {
        "name":     "Macaroni 500g",
        "category": "Grains",
        "weight":   0.020,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=macaroni+500g",
            "Danube": "https://danube.sa/ar/search?query=macaroni+500g",
            "Ninja":  "https://ananinja.com/sa/ar/category/pasta-rice-grains",
        },
    },
    {
        "name":     "Spaghetti 500g",
        "category": "Grains",
        "weight":   0.020,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=spaghetti+500g",
            "Danube": "https://danube.sa/ar/search?query=spaghetti+500g",
            "Ninja":  "https://ananinja.com/sa/ar/category/pasta-rice-grains",
        },
    },
    {
        "name":     "Oats 500g",
        "category": "Grains",
        "weight":   0.015,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=oats+500g",
            "Danube": "https://danube.sa/ar/search?query=oats+500g",
            "Ninja":  "https://ananinja.com/sa/ar/category/spreads-honey-cereals",
        },
    },
    {
        "name":     "Canned Sweet Corn 340g",
        "category": "Canned",
        "weight":   0.015,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=sweet+corn+340g",
            "Danube": "https://danube.sa/ar/search?query=sweet+corn+340g",
            "Ninja":  "https://ananinja.com/sa/ar/category/canned-food",
        },
    },
    {
        "name":     "Facial Tissue 200 Sheets",
        "category": "PersonalCare",
        "weight":   0.015,
        "urls": {
            "Panda":  "https://panda.sa/ar/search?q=facial+tissue+200+sheets",
            "Danube": "https://danube.sa/ar/search?query=facial+tissue+200+sheets",
            "Ninja":  "https://ananinja.com/sa/ar/category/tissues",
        },
    },
]


# ══════════════════════════════════════════════════════════════════════════════
#  Loader helpers — used by db_setup.py
# ══════════════════════════════════════════════════════════════════════════════

BASKET.extend(build_non_supermarket_basket())


def normalized_basket() -> list[dict[str, Any]]:
    """Return BASKET with weights divided by their sum so they total 1.0.

    Lets the user add items without manually rebalancing every weight.
    Raises ValueError if the basket is empty or all weights are zero.
    """
    if not BASKET:
        raise ValueError("basket_config.BASKET is empty — nothing to seed.")

    total = sum(float(item.get("weight", 0)) for item in BASKET)
    if total <= 0:
        raise ValueError("Sum of basket weights must be > 0.")

    out: list[dict[str, Any]] = []
    for item in BASKET:
        normalised = float(item["weight"]) / total
        urls = dict(item.get("urls", {}))
        if urls:
            urls.setdefault(
                "Tamimi",
                "https://shop.tamimimarkets.com/api/layout/search?"
                f"q={quote_plus(item['name'])}",
            )
        urls.update(daily_market_urls_for_item(item["name"]))
        out.append({
            "name":     item["name"],
            "category": item["category"],
            "weight":   normalised,
            "urls":     urls,
            "source":   dict(item.get("source", {})),
        })

    # Defensive sanity check.
    s = sum(it["weight"] for it in out)
    if abs(s - 1.0) > 1e-9:
        raise ValueError(f"Normalisation failed: weights sum to {s} not 1.0")

    return out


def basket_stats() -> dict[str, Any]:
    """Quick summary — used by health checks and the dashboard footer."""
    nb = normalized_basket()
    by_cat: dict[str, dict[str, Any]] = {}
    for item in nb:
        c = item["category"]
        if c not in by_cat:
            by_cat[c] = {"items": 0, "weight": 0.0}
        by_cat[c]["items"] += 1
        by_cat[c]["weight"] += item["weight"]
    return {
        "item_count":         len(nb),
        "category_count":     len(by_cat),
        "stores_per_item":    max((len(it["urls"]) for it in nb), default=0),
        "by_category":        by_cat,
    }


if __name__ == "__main__":
    import json
    stats = basket_stats()
    print(json.dumps(stats, indent=2, ensure_ascii=False))
