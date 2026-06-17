"""Refresh the rental asking-price nowcast from public listing pages.

This is a faster market signal than Ejar because it uses asking rents from live
listing pages. It is not a replacement for authenticated Ejar contract data.

Usage:
    python rental_nowcast.py --refresh
    python rental_nowcast.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import logging

from db_setup import DB_PATH

logging.disable(logging.WARNING)
from modules.mod_real_estate import refresh_rental_listing_nowcast
logging.disable(logging.NOTSET)


def _build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Refresh rental listing nowcast snapshots.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Fetch public listing pages and save a snapshot to inflation_index.db.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch and parse pages without saving rows.",
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help=f"Database path (default: {DB_PATH})",
    )
    return parser


def main() -> None:
    args = _build_argparser().parse_args()
    if not args.refresh and not args.dry_run:
        raise SystemExit("Pass --refresh to save a snapshot, or --dry-run to test parsing.")

    summary = refresh_rental_listing_nowcast(args.db, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
