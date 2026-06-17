"""Import official GASTAT CPI category index series for proxy basket items.

Some app basket items are services where a daily retail quote is not publicly
available. For those, GASTAT's CPI category index is a better official source
than a flat placeholder. This importer maps remaining non-supermarket proxy
items to the closest official COICOP CPI category and writes the historical
index series into ``daily_prices``.
"""

from __future__ import annotations

import argparse
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from db_setup import DB_PATH, create_tables, get_connection, migrate_schema, seed_data


GASTAT_CPI_SOURCE_NAME = "GASTAT CPI Category Index"
DEFAULT_PUBLICATION_PAGE = "https://www.stats.gov.sa/en/w/consumer-price-index-december-2025-1"
DEFAULT_XLSX_URL = (
    "https://www.stats.gov.sa/documents/20117/2435267/"
    "CPI+Tables-Dec+2025-AR-EN+%281%29.xlsx/"
    "3865df0c-cb89-b40a-b208-626578cd5fea?t=1768384779590"
)
DEFAULT_PUBLICATION_PERIOD = "2025-12"


@dataclass(frozen=True)
class CpiSeries:
    label: str
    code: str
    values: dict[str, float]


def download_xlsx(url: str = DEFAULT_XLSX_URL, output_path: str | Path | None = None) -> Path:
    output = Path(output_path or Path("debug_artifacts") / "gastat_cpi_latest.xlsx")
    output.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        output.write_bytes(response.read())
    return output


def parse_cpi_index_series(
    xlsx_path: str | Path,
    sheet_name: str = "5.1 ",
) -> dict[str, CpiSeries]:
    raw = pd.read_excel(xlsx_path, sheet_name=sheet_name, header=None)
    series: dict[str, CpiSeries] = {}

    # GASTAT's 5.1 sheet has COICOP codes on row 8 and English labels on row 10.
    for col in range(4, raw.shape[1]):
        label = raw.iloc[10, col]
        code = raw.iloc[8, col]
        if pd.isna(label):
            continue
        values: dict[str, float] = {}
        for row_idx in range(11, raw.shape[0]):
            year = raw.iloc[row_idx, 0]
            month = raw.iloc[row_idx, 1]
            value = raw.iloc[row_idx, col]
            if pd.isna(year) or pd.isna(month) or pd.isna(value):
                continue
            try:
                values[f"{int(year)}-{int(month):02d}-01"] = float(value)
            except (TypeError, ValueError):
                continue
        if values:
            clean_label = " ".join(str(label).strip().split())
            series[clean_label] = CpiSeries(
                label=clean_label,
                code="" if pd.isna(code) else str(code).strip(),
                values=values,
            )
    return series


def _cpi_label_for_item(item_name: str, category: str) -> str:
    name = item_name.lower()

    if category == "Housing":
        if "maintenance" in name or "repair" in name or "pest" in name or "security" in name:
            return "Services for the maintenance, repair and security of the dwelling"
        return "Actual rentals paid by tenants for main residence"

    if category == "Utilities":
        if "electric" in name:
            return "Electricity"
        if "water" in name:
            return "Water supply"
        if "sewer" in name:
            return "Sewage collection"
        if "waste" in name:
            return "Refuse collection"
        if "gas" in name:
            return "Gas"
        return "Other services relating to the dwelling n.e.c."

    if category == "Transport":
        if "gasoline" in name or "diesel" in name:
            return "Fuels and lubricants for personal transport equipment"
        if "oil change" in name or "tire" in name or "battery" in name or "brake" in name or "wash" in name:
            return "Maintenance and repair of personal transport equipment"
        if "parking" in name or "registration" in name or "inspection" in name or "roadside" in name:
            return "Other services in respect of personal transport equipment"
        if "flight" in name:
            return "Passenger transport by air"
        if "train" in name or "metro" in name:
            return "Passenger transport by railway"
        if "courier" in name or "parcel" in name:
            return "Postal and courier services"
        if "insurance" in name:
            return "Insurance connected with transport"
        return "Passenger transport by road"

    if category == "Communication":
        if "fiber" in name or "internet" in name or "cloud" in name or "storage" in name:
            return "Internet access provision services and net storage services"
        if "landline" in name:
            return "Fixed communication services"
        if "repair" in name:
            return "Repair and rental of information and communication equipment"
        if "smartphone" in name or "router" in name or "charger" in name or "earbuds" in name:
            return "Other information and communication equipment and accessories"
        return "Mobile communication services"

    if category == "Health":
        if "dental" in name or "orthodontic" in name:
            return "Outpatient dental services"
        if "x-ray" in name or "ultrasound" in name or "blood test" in name:
            return "Diagnostic imaging services and medical laboratory services"
        if "medicine" in name or "tablet" in name or "antacid" in name:
            return "Medicines"
        if "glasses" in name or "lenses" in name:
            return "Medical products"
        if "hospital" in name or "room" in name:
            return "Inpatient curative and rehabilitative services"
        return "Other outpatient care services"

    if category == "Education":
        if "kindergarten" in name or "primary" in name or "nursery" in name or "childcare" in name:
            return "Early childhood and primary education"
        if "secondary" in name:
            return "Secondary education"
        if "university" in name:
            return "Tertiary education"
        if "stationery" in name or "printing" in name:
            return "Stationery and drawing materials"
        if "uniform" in name:
            return "Clothing"
        return "Education not defined by level"

    if category == "Recreation":
        if "cinema" in name or "event" in name:
            return "Services provided by cinemas, theatres and concert venues"
        if "museum" in name:
            return "Services provided by museums, libraries, and cultural sites"
        if "book" in name:
            return "Books"
        if "newspaper" in name:
            return "Miscellaneous printed matter"
        if "toy" in name or "game" in name or "gaming" in name:
            return "Games, toys and hobbies"
        if "pet" in name:
            return "Veterinary and other services for pets"
        if "photo" in name:
            return "Photographic services"
        if "music" in name or "art" in name:
            return "Other cultural services"
        return "Recreational and sporting services"

    if category == "Restaurants":
        if "canteen" in name:
            return "Canteens, cafeterias and refectories"
        return "Restaurants, cafés and the like"

    if category == "Hotels":
        if "breakfast" in name:
            return "Restaurants, cafés and the like"
        return "Accommodation services"

    if category == "Clothing":
        if "laundry" in name or "cleaning" in name or "tailor" in name or "repair" in name:
            return "Cleaning, repair, tailoring and hire of clothing"
        if "scarf" in name or "cap" in name or "hat" in name:
            return "Other articles of clothing and clothing accessories"
        return "Clothing"

    if category == "Footwear":
        return "Footwear of all types"

    if category == "Furniture":
        if "refrigerator" in name or "washing" in name or "dishwasher" in name or "air conditioner" in name:
            return "Major household appliances"
        if "kettle" in name or "microwave" in name or "vacuum" in name or "air purifier" in name:
            return "Small household appliances"
        if "repair" in name or "installation" in name or "assembly" in name:
            return "Repair, installation and hire of household appliances"
        if "curtain" in name or "bedding" in name or "towel" in name:
            return "Household textiles"
        if "cookware" in name or "dinnerware" in name:
            return "Glassware, tableware and household utensils"
        return "Furniture, furnishings and loose carpets"

    if category == "HouseholdServices":
        if "laundry" in name or "ironing" in name:
            return "Domestic services and household services"
        if "moving" in name or "storage" in name:
            return "Other transport of goods"
        return "Services for the maintenance, repair and security of the dwelling"

    if category == "Insurance":
        if "health" in name or "medical" in name:
            return "Insurance connected with health"
        if "motor" in name or "travel" in name:
            return "Insurance connected with transport"
        return "Life and accident insurance"

    if category == "FinancialServices":
        if "bank" in name or "atm" in name or "transfer" in name or "card" in name:
            return "Explicit charges by deposit-taking corporations"
        return "Other financial services"

    if category == "PersonalServices":
        if "hair" in name or "beard" in name or "manicure" in name or "pedicure" in name or "spa" in name:
            return "Hairdressing salons and personal grooming establishments"
        if "watch" in name or "jewelry" in name:
            return "Jewellery and watches"
        if "bag" in name:
            return "Travel goods and child-related products and other personal effects n.e.c."
        if "care" in name or "cosmetic" in name:
            return "Other appliances, articles and products for personal care"
        return "Other services"

    return "General Index"


def _external_items_without_average_price(
    conn: sqlite3.Connection,
    carry_to_date: str,
) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        SELECT i.id, i.name, i.category
          FROM items i
         WHERE COALESCE(i.source_type, 'supermarket') <> 'supermarket'
           AND NOT EXISTS (
                SELECT 1
                  FROM daily_prices dp
                 WHERE dp.item_id = i.id
                   AND dp.date = ?
                   AND dp.store_name = 'GASTAT Average Prices'
                   AND dp.scrape_status = 'ok'
                   AND dp.price IS NOT NULL
           )
         ORDER BY i.category, i.name
        """,
        (carry_to_date,),
    ).fetchall()


def _latest_value(series: CpiSeries) -> tuple[str, float]:
    latest_date = sorted(series.values)[-1]
    return latest_date, series.values[latest_date]


def _upsert_cpi_observation(
    conn: sqlite3.Connection,
    *,
    run_date: str,
    item_id: int,
    value: float,
    series: CpiSeries,
    source_period: str,
    observed_at: str,
    carried: bool,
) -> None:
    tier = "gastat_cpi_latest_carried" if carried else "gastat_cpi_category_index"
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
            GASTAT_CPI_SOURCE_NAME,
            value,
            observed_at,
            tier,
            f"GASTAT CPI category index: {series.label} ({series.code})",
            (
                "Official GASTAT CPI category index, base 2023=100. "
                "Used for non-supermarket representative basket items where a public retail quote is unavailable. "
                f"Source publication period: {source_period}. "
                f"Publication page: {DEFAULT_PUBLICATION_PAGE}"
            ),
        ),
    )


def _disable_proxy(
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
               failure_reason = 'replaced_by_gastat_cpi_category_index',
               observed_at = ?,
               match_notes = 'External proxy retained for audit but excluded because a GASTAT CPI category index is available.'
         WHERE date = ?
           AND item_id = ?
           AND store_name = 'External CPI Proxy'
        """,
        (observed_at, run_date, item_id),
    )
    return cursor.rowcount


def import_gastat_cpi_indices(
    *,
    db_path: str = DB_PATH,
    xlsx_path: str | Path | None = None,
    xlsx_url: str = DEFAULT_XLSX_URL,
    publication_period: str = DEFAULT_PUBLICATION_PERIOD,
    carry_to_date: str | None = None,
    apply: bool = True,
) -> dict[str, int | str]:
    source_path = Path(xlsx_path) if xlsx_path else download_xlsx(xlsx_url)
    cpi_series = parse_cpi_index_series(source_path)
    observed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    carry_to_date = carry_to_date or date.today().isoformat()

    conn = get_connection(db_path)
    try:
        create_tables(conn)
        migrate_schema(conn)
        seed_data(conn)
        target_rows = _external_items_without_average_price(conn, carry_to_date)

        mappings: list[tuple[int, str, CpiSeries]] = []
        missing_labels: set[str] = set()
        for row in target_rows:
            label = _cpi_label_for_item(row["name"], row["category"])
            series = cpi_series.get(label)
            if series is None:
                missing_labels.add(label)
                continue
            mappings.append((int(row["id"]), row["name"], series))

        if not apply:
            return {
                "mode": "dry-run",
                "source_file": str(source_path),
                "cpi_series": len(cpi_series),
                "target_items": len(target_rows),
                "mapped_items": len(mappings),
                "missing_labels": len(missing_labels),
                "monthly_rows_written": 0,
                "carry_rows_written": 0,
                "proxy_rows_disabled": 0,
            }

        monthly_rows_written = 0
        carry_rows_written = 0
        proxy_rows_disabled = 0

        for item_id, _item_name, series in mappings:
            for month_date, value in series.values.items():
                _upsert_cpi_observation(
                    conn,
                    run_date=month_date,
                    item_id=item_id,
                    value=value,
                    series=series,
                    source_period=publication_period,
                    observed_at=observed_at,
                    carried=False,
                )
                monthly_rows_written += 1

            latest_month, latest_index = _latest_value(series)
            _upsert_cpi_observation(
                conn,
                run_date=carry_to_date,
                item_id=item_id,
                value=latest_index,
                series=series,
                source_period=latest_month[:7],
                observed_at=observed_at,
                carried=True,
            )
            carry_rows_written += 1
            proxy_rows_disabled += _disable_proxy(
                conn,
                item_id=item_id,
                run_date=carry_to_date,
                observed_at=observed_at,
            )

        conn.commit()
        return {
            "mode": "apply",
            "source_file": str(source_path),
            "cpi_series": len(cpi_series),
            "target_items": len(target_rows),
            "mapped_items": len(mappings),
            "missing_labels": len(missing_labels),
            "monthly_rows_written": monthly_rows_written,
            "carry_rows_written": carry_rows_written,
            "proxy_rows_disabled": proxy_rows_disabled,
        }
    finally:
        conn.close()


def _print_report(report: dict[str, int | str]) -> None:
    print("=" * 72)
    print(f"  GASTAT CPI Category Importer - {str(report['mode']).upper()}")
    print("=" * 72)
    for key in [
        "source_file",
        "cpi_series",
        "target_items",
        "mapped_items",
        "missing_labels",
        "monthly_rows_written",
        "carry_rows_written",
        "proxy_rows_disabled",
    ]:
        print(f"  {key.replace('_', ' ').title():24}: {report[key]}")
    print("=" * 72)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import official GASTAT CPI category indices for remaining proxy basket items.",
    )
    parser.add_argument("--db", default=DB_PATH, help=f"Database path (default: {DB_PATH})")
    parser.add_argument("--xlsx", default=None, help="Use a local CPI XLSX instead of downloading")
    parser.add_argument("--url", default=DEFAULT_XLSX_URL, help="Official CPI XLSX URL")
    parser.add_argument("--publication-period", default=DEFAULT_PUBLICATION_PERIOD)
    parser.add_argument("--carry-to-date", default=date.today().isoformat())
    parser.add_argument("--dry-run", action="store_true", help="Preview without database writes")
    return parser


def main(argv: Iterable[str] | None = None) -> None:
    args = _build_parser().parse_args(list(argv) if argv is not None else None)
    report = import_gastat_cpi_indices(
        db_path=args.db,
        xlsx_path=args.xlsx,
        xlsx_url=args.url,
        publication_period=args.publication_period,
        carry_to_date=args.carry_to_date,
        apply=not args.dry_run,
    )
    _print_report(report)


if __name__ == "__main__":
    main()
