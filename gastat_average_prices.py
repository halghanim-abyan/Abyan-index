"""Import official GASTAT Average Prices observations.

The General Authority for Statistics publishes monthly ``Average Prices of
Goods and Services`` tables. This importer reads the official XLSX table and
adds matched observations to ``daily_prices`` as an audited source named
``GASTAT Average Prices``.

The import deliberately uses a curated mapping instead of fuzzy matching. A
bad official match is worse than no match, especially when the row contributes
to an inflation index.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from db_setup import DB_PATH, create_tables, get_connection, migrate_schema, seed_data


GASTAT_SOURCE_NAME = "GASTAT Average Prices"
DEFAULT_PUBLICATION_PAGE = (
    "https://www.stats.gov.sa/w/average-prices-of-goods-and-services-december-2025-1"
    "?p_l_back_url=%2Fen%2Fsearch%3Fq%3DPrices&p_l_back_url_title=Search"
)
DEFAULT_XLSX_URL = (
    "https://www.stats.gov.sa/documents/20117/2435267/"
    "APGS+Tables-Dec+2025-AR-EN.xlsx/"
    "1f634a2f-5680-0b8e-0032-d454417f9429?t=1768454127205"
)
DEFAULT_PUBLICATION_PERIOD = "2025-12"
DEFAULT_YEAR = 2025
MONTH_COLUMNS = {
    1: 3,
    2: 4,
    3: 5,
    4: 6,
    5: 7,
    6: 8,
    7: 9,
    8: 10,
    9: 11,
    10: 12,
    11: 13,
    12: 14,
}


@dataclass(frozen=True)
class GastatPriceRow:
    item: str
    unit: str
    monthly_prices: dict[str, float]


# Current app basket item -> official GASTAT APGS item.
# Keep this conservative. Add new rows only when the official item is a clear
# representative match for the basket item.
GASTAT_ITEM_MAP: dict[str, str] = {
    # Supermarket basket.
    "Abu Kass Basmati Rice 5kg": "Maza Indian Rice( Abu Kas)",
    "Alwataniah Chicken 1000g": "Local frozen chicken (Al wataniya)",
    "Americana Shrimps 400g": "Fresh peeled Shrimp",
    "Almarai Fresh Milk 2L": "Local Fresh Milk (Al Marai)",
    "Afia Corn Oil 1.5L": "Corn oil, (cooking), Afia",
    "Local Bananas 1kg": "Philippines Banana,  Alsharbatli",
    "Local Tomatoes 1kg": "Local tomatoes",
    "Saudia White Sugar 1kg": "Soft sugar  (AlOsra)",
    "Nescafe Classic 200g": "Instant Coffee  (Nescaf)",
    "Majdi Cardamom 50g": "Indian Cardomom",
    "Al Doha Red Lentils 1kg": "Lentils",
    "Al Tayebat Arabic Pita Bread 6P": "White Bread",
    "Lusine White Sliced Bread 600g": "White Bread",
    "Almarai Cheddar Cheese Triangles 8P": "triangle Cheese (Lavache quri)",
    "Al Fakhama Tomato Paste 8x135g": "Local Tomato Paste (Saudia)",
    "Kuwaiti Flour No.1 1kg": "White Local Flour(Grain Silos)",
    "Almarai Greek Yogurt 1kg": "Yoghurt, (Al Saffi)",
    "Almarai Brown Eggs 30P": "Local Eggs",
    "Local Fresh Cucumbers 1kg": "Local Cucumbers",
    "Local Yellow Onions 1kg": "Local Onion",
    "Local Fresh Potatoes 1kg": "Medium local Potatoes",
    "Lurpak Salted Butter 200g": "Butter( Lurpak)",
    "Al Walima Long Grain Rice 5kg": "Basmati White Indian Rice (Al Mehideb)",
    "Saudia Long-Life UHT Milk 1L": "Local Fresh  Milk (Al Safi)",
    "Al Khair Saudi Coffee Bahar 250g": "Coffee beans, Loqmati",
    "Rabea Premium Loose Tea 400g": "black loose Tea (Rabea)",
    "Lipton Yellow Label Tea Bags 100P": "black loose Tea (Rabea)",
    "Maatouk Turkish Coffee 200g": "Coffee beans, Hrari",
    "Bateel Khalas Dates 1kg": "Ekhilas Dates, (Maknoz)",
    "Sukkari Premium Dates 1kg": "Dates( Rotab)",
    "Al Shifa Pure Natural Honey 250g": "imported Honey (Langilies)",
    "Nova Mineral Water 1.5L 6P": "Water",
    "Berain Bottled Water 600ml 12P": "Water",
    "Aquafina Mineral Water 330ml 12P": "Water",
    "Tide Original Powder Detergent 6kg": "clothes powder Soap   (Tide)",
    "Fairy Original Dish Liquid 750ml": "utensils liquid Soap (Fairy)",
    "Clorox Original Bleach 950ml": "Bleach for Clothes (Clorox)",
    "Local Naemi Fresh Lamb 1kg": "Fresh Sheep Meat",
    "Local Fresh Beef 1kg": "Fresh Cattle Meat",
    "Local Fresh Hamour Fish 1kg": "Fresh Fish  (Grouper)",
    "Local Apples 1kg": "American red Apples",
    "Local Oranges 1kg": "Abu Sorra egyptian Orange",
    "Local Lemons 1kg": "medium African Lemon",
    "Evaporated Milk 170g": "Evaporated Milk  (Boni)",
    "Macaroni 500g": "Perfetto Local Macaroni",
    "Spaghetti 500g": "Perfetto Noodles",
    "Oats 500g": "Oats Soup (Quaker)",
    "Canned Sweet Corn 340g": "Sweet corn",
    "Facial Tissue 200 Sheets": "local Tissue paper (Fine)",

    # Non-supermarket representative items.
    "General practitioner consultation": "General physician examination",
    "Specialist consultation - internal medicine": "General physician examination",
    "Specialist consultation - pediatrics": "Pediatrician examination",
    "Dental consultation": "Dentist examination with one tooth extracted",
    "Dental cleaning service": "Dentist examination with one tooth extracted",
    "Dental filling service": "Dentist examination with one tooth extracted",
    "Engine oil change - sedan": "Oil change",
    "Engine oil change - SUV": "Oil change",
    "Car wash - basic": "Car fix (mechanics)",
    "Car wash - premium": "Car fix (mechanics)",
    "Wheel alignment service": "Car fix (mechanics)",
    "Hotel room - Riyadh weekday 3 star": "Hotel accommodation",
    "Hotel room - Riyadh weekend 4 star": "Hotel accommodation",
    "Hotel room - Jeddah weekday 3 star": "Hotel accommodation",
    "Hotel room - Jeddah weekend 4 star": "Hotel accommodation",
    "Hotel room - Makkah weekday 3 star": "Hotel accommodation",
    "Hotel room - Makkah weekend 4 star": "Hotel accommodation",
    "Hotel room - Madinah weekday 3 star": "Hotel accommodation",
    "Hotel room - Dammam weekday 4 star": "Hotel accommodation",
    "Serviced apartment - Riyadh daily": "Furnished apartment",
    "Serviced apartment - Jeddah daily": "Furnished apartment",
    "Resort stay - weekend night": "Hotel accommodation",
    "Men thobe - standard": "Men summer dress (Al Aseel)",
    "Men trousers - casual": "Mens long trousers, (Al Aseel)",
    "Men underwear pack": "short sleeve undershirt (Al Aseel)",
    "Women abaya - standard": "Women's Abaya",
    "Laundry service - shirt": "Laundry and ironing expenses",
    "Tailoring alteration": "Sewing for men and boys",
    "Clothing repair": "Sewing for women and girls",
    "Laundry service - kilogram": "Laundry and ironing expenses",
    "Ironing service - shirt": "Laundry and ironing expenses",
    "Men haircut": "Hairdresser for men and boys",
    "Beard trim": "Hairdresser for men and boys",
    "Child haircut": "Hairdresser for men and boys",
    "Wedding hall rental - basic": "parties and weddings expenses",
    "Catering meal per person": "The cost of cooking the carcass",
}


def _norm(value: str) -> str:
    return " ".join(str(value).strip().lower().split())


def download_xlsx(url: str = DEFAULT_XLSX_URL, output_path: str | Path | None = None) -> Path:
    output = Path(output_path or Path("debug_artifacts") / "gastat_apgs_latest.xlsx")
    output.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        output.write_bytes(response.read())
    return output


def parse_monthly_average_prices(
    xlsx_path: str | Path,
    year: int = DEFAULT_YEAR,
    sheet_name: str = "5",
) -> dict[str, GastatPriceRow]:
    raw = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=None)
    rows: dict[str, GastatPriceRow] = {}
    for _, row in raw.iterrows():
        official_item = row.iloc[17] if len(row) > 17 else None
        unit = row.iloc[16] if len(row) > 16 else None
        level = row.iloc[18] if len(row) > 18 else None
        if pd.isna(official_item) or pd.isna(level) or str(level).strip() != "6":
            continue

        monthly: dict[str, float] = {}
        for month, col in MONTH_COLUMNS.items():
            value = row.iloc[col] if col < len(row) else None
            if pd.isna(value):
                continue
            try:
                monthly[f"{year}-{month:02d}-01"] = float(value)
            except (TypeError, ValueError):
                continue

        if monthly:
            rows[_norm(str(official_item))] = GastatPriceRow(
                item=str(official_item).strip(),
                unit="" if pd.isna(unit) else str(unit).strip(),
                monthly_prices=monthly,
            )
    return rows


def _latest_monthly_value(row: GastatPriceRow) -> tuple[str, float]:
    latest_date = sorted(row.monthly_prices)[-1]
    return latest_date, row.monthly_prices[latest_date]


def _fetch_item_ids(conn: sqlite3.Connection) -> dict[str, tuple[int, str]]:
    rows = conn.execute("SELECT id, name, source_type FROM items").fetchall()
    return {name: (int(item_id), source_type) for item_id, name, source_type in rows}


def _disable_replaced_proxy(
    conn: sqlite3.Connection,
    *,
    item_id: int,
    run_date: str,
    observed_at: str,
) -> int:
    cursor = conn.execute(
        """
        UPDATE daily_prices
           SET price = NULL,
               scrape_status = 'not_found',
               failure_reason = 'replaced_by_gastat_average_prices',
               observed_at = ?,
               match_notes = 'External proxy retained for audit but excluded because a GASTAT Average Prices observation is available.'
         WHERE date = ?
           AND item_id = ?
           AND store_name = 'External CPI Proxy'
        """,
        (observed_at, run_date, item_id),
    )
    return cursor.rowcount


def _upsert_observation(
    conn: sqlite3.Connection,
    *,
    run_date: str,
    item_id: int,
    price: float,
    official: GastatPriceRow,
    source_period: str,
    observed_at: str,
    carried: bool,
) -> None:
    tier = "gastat_latest_monthly_carried" if carried else "gastat_average_price"
    title_suffix = f"{source_period} monthly average" if carried else "monthly average"
    conn.execute(
        """
        INSERT INTO daily_prices (
            date, item_id, store_name, price, scrape_status, failure_reason,
            observed_at, match_tier, observed_title, match_notes
        )
        VALUES (?, ?, ?, ?, 'ok', NULL, ?, ?, ?, ?)
        ON CONFLICT(date, item_id, store_name) DO UPDATE SET
            price = excluded.price,
            scrape_status = excluded.scrape_status,
            failure_reason = excluded.failure_reason,
            observed_at = excluded.observed_at,
            match_tier = excluded.match_tier,
            observed_title = excluded.observed_title,
            match_notes = excluded.match_notes
        """,
        (
            run_date,
            item_id,
            GASTAT_SOURCE_NAME,
            price,
            observed_at,
            tier,
            f"GASTAT APGS: {official.item} ({official.unit}) - {title_suffix}",
            (
                "Official GASTAT Average Prices of Goods and Services. "
                f"Mapped app basket item to official APGS item '{official.item}'. "
                f"Source publication period: {source_period}. "
                f"Publication page: {DEFAULT_PUBLICATION_PAGE}"
            ),
        ),
    )


def import_gastat_average_prices(
    *,
    db_path: str = DB_PATH,
    xlsx_path: str | Path | None = None,
    xlsx_url: str = DEFAULT_XLSX_URL,
    publication_period: str = DEFAULT_PUBLICATION_PERIOD,
    carry_to_date: str | None = None,
    apply: bool = True,
) -> dict[str, int | str]:
    source_path = Path(xlsx_path) if xlsx_path else download_xlsx(xlsx_url)
    official_rows = parse_monthly_average_prices(source_path)
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    conn = get_connection(db_path)
    try:
        create_tables(conn)
        migrate_schema(conn)
        seed_data(conn)
        item_ids = _fetch_item_ids(conn)

        matched: list[tuple[str, int, str, GastatPriceRow]] = []
        missing_items = 0
        missing_official_rows = 0
        for app_item, official_item in GASTAT_ITEM_MAP.items():
            item_info = item_ids.get(app_item)
            if item_info is None:
                missing_items += 1
                continue
            official = official_rows.get(_norm(official_item))
            if official is None:
                missing_official_rows += 1
                continue
            item_id, source_type = item_info
            matched.append((app_item, item_id, source_type, official))

        if not apply:
            return {
                "mode": "dry-run",
                "source_file": str(source_path),
                "official_rows": len(official_rows),
                "mapped_items": len(matched),
                "missing_items": missing_items,
                "missing_official_rows": missing_official_rows,
                "monthly_rows_written": 0,
                "carry_rows_written": 0,
                "proxy_rows_disabled": 0,
            }

        monthly_rows_written = 0
        carry_rows_written = 0
        proxy_rows_disabled = 0

        for _app_item, item_id, source_type, official in matched:
            for month_date, price in official.monthly_prices.items():
                _upsert_observation(
                    conn,
                    run_date=month_date,
                    item_id=item_id,
                    price=price,
                    official=official,
                    source_period=publication_period,
                    observed_at=observed_at,
                    carried=False,
                )
                monthly_rows_written += 1

            if carry_to_date:
                latest_month, latest_price = _latest_monthly_value(official)
                _upsert_observation(
                    conn,
                    run_date=carry_to_date,
                    item_id=item_id,
                    price=latest_price,
                    official=official,
                    source_period=latest_month[:7],
                    observed_at=observed_at,
                    carried=True,
                )
                carry_rows_written += 1
                if source_type != "supermarket":
                    proxy_rows_disabled += _disable_replaced_proxy(
                        conn,
                        item_id=item_id,
                        run_date=carry_to_date,
                        observed_at=observed_at,
                    )

        conn.commit()
        return {
            "mode": "apply",
            "source_file": str(source_path),
            "official_rows": len(official_rows),
            "mapped_items": len(matched),
            "missing_items": missing_items,
            "missing_official_rows": missing_official_rows,
            "monthly_rows_written": monthly_rows_written,
            "carry_rows_written": carry_rows_written,
            "proxy_rows_disabled": proxy_rows_disabled,
        }
    finally:
        conn.close()


def _print_report(report: dict[str, int | str]) -> None:
    print("=" * 72)
    print(f"  GASTAT Average Prices Importer - {str(report['mode']).upper()}")
    print("=" * 72)
    for key in [
        "source_file",
        "official_rows",
        "mapped_items",
        "missing_items",
        "missing_official_rows",
        "monthly_rows_written",
        "carry_rows_written",
        "proxy_rows_disabled",
    ]:
        print(f"  {key.replace('_', ' ').title():24}: {report[key]}")
    print("=" * 72)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import official GASTAT Average Prices rows into daily_prices.",
    )
    parser.add_argument("--db", default=DB_PATH, help=f"Database path (default: {DB_PATH})")
    parser.add_argument("--xlsx", default=None, help="Use a local APGS XLSX instead of downloading")
    parser.add_argument("--url", default=DEFAULT_XLSX_URL, help="Official APGS XLSX URL")
    parser.add_argument("--publication-period", default=DEFAULT_PUBLICATION_PERIOD)
    parser.add_argument(
        "--carry-to-date",
        default=date.today().isoformat(),
        help="Also write the latest monthly value to this app date (default: today).",
    )
    parser.add_argument(
        "--no-carry",
        action="store_true",
        help="Only write official monthly dates; do not carry latest monthly value to today.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Preview without database writes")
    return parser


def main(argv: Iterable[str] | None = None) -> None:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    report = import_gastat_average_prices(
        db_path=args.db,
        xlsx_path=args.xlsx,
        xlsx_url=args.url,
        publication_period=args.publication_period,
        carry_to_date=None if args.no_carry else args.carry_to_date,
        apply=not args.dry_run,
    )
    _print_report(report)


if __name__ == "__main__":
    main()
