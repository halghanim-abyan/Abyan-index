"""
funds_doctor.py — Anomaly detector & sanitizer for mutual_funds.db.

Scans the nav_history table for impossible day-over-day NAV jumps
(> MAX_DOD_PCT% in a single day) which indicate data poisoning from
CSV backfill typos, scraper glitches, or corrupt records.

Actions:
  --scan        Report anomalies without modifying the database (default)
  --fix         Delete anomalous rows after confirmation
  --fix --yes   Delete anomalous rows without interactive prompt

The threshold is configurable via --threshold (default: 5%).

Usage:
    python funds_doctor.py                     # scan only (safe)
    python funds_doctor.py --fix               # delete bad rows (with prompt)
    python funds_doctor.py --fix --yes         # delete bad rows (no prompt)
    python funds_doctor.py --threshold 3       # stricter 3% threshold
"""

import argparse
import os
import sqlite3
import sys

# Fix Windows console encoding for Unicode box-drawing characters
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pandas as pd

DB_PATH = os.path.join(os.path.dirname(__file__), "mutual_funds.db")

# ── ANSI colors for terminal output ─────────────────────────────────────────
RED = "\033[91m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def load_data(db_path: str) -> pd.DataFrame:
    """Load nav_history ordered by fund + date."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    df = pd.read_sql_query(
        "SELECT id, date, fund_name, nav_price FROM nav_history "
        "ORDER BY fund_name, date",
        conn,
    )
    conn.close()
    return df


def detect_anomalies(df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    """
    Calculate day-over-day % change per fund and identify the *bad* rows.

    When a spike is detected (|DoD| > threshold), the question is: which
    row is the outlier — the current one or the previous one?  We look at
    the *next* row too.  If the price immediately snaps back (next row is
    close to prev), the current row is the bad one.  If the price stays
    at the new level, the previous row was the bad one (less common with
    point-anomaly poisoning).

    Returns a DataFrame of bad rows with columns:
      id, date, fund_name, nav_price, prev_price, next_price, dod_pct
    """
    df = df.sort_values(["fund_name", "date"]).reset_index(drop=True)

    df["prev_price"] = df.groupby("fund_name")["nav_price"].shift(1)
    df["next_price"] = df.groupby("fund_name")["nav_price"].shift(-1)
    df["prev_id"] = df.groupby("fund_name")["id"].shift(1)
    df["dod_pct"] = ((df["nav_price"] - df["prev_price"]) / df["prev_price"]) * 100

    flagged = df[df["dod_pct"].abs() > threshold].copy()

    bad_ids: set[int] = set()
    for _, row in flagged.iterrows():
        curr = row["nav_price"]
        prev = row["prev_price"]
        nxt = row["next_price"]

        if pd.isna(prev):
            continue  # first row per fund, skip

        # If next_price exists, decide which row is the outlier
        if pd.notna(nxt):
            dist_curr_from_neighbors = abs(curr - prev) + abs(curr - nxt)
            dist_prev_from_neighbors = abs(prev - curr) + abs(prev - nxt)
            if dist_curr_from_neighbors > dist_prev_from_neighbors:
                # Current price is far from both neighbors → it's the bad row
                bad_ids.add(int(row["id"]))
            else:
                bad_ids.add(int(row["prev_id"]))
        else:
            # Last row in fund — current is suspicious
            bad_ids.add(int(row["id"]))

    anomalies = df[df["id"].isin(bad_ids)].copy()
    anomalies = anomalies[["id", "date", "fund_name", "nav_price", "prev_price", "next_price", "dod_pct"]]

    return anomalies


def print_report(anomalies: pd.DataFrame, threshold: float, total_rows: int) -> None:
    """Pretty-print the anomaly report to console."""
    print(f"\n{BOLD}{'='*78}{RESET}")
    print(f"{BOLD}  FUNDS DOCTOR — NAV Anomaly Report{RESET}")
    print(f"{'='*78}")
    print(f"  Database rows scanned : {total_rows}")
    print(f"  DoD threshold         : {threshold}%")
    print(f"  Anomalies found       : {RED}{len(anomalies)}{RESET}" if len(anomalies) > 0
          else f"  Anomalies found       : {GREEN}0{RESET}")
    print(f"{'='*78}\n")

    if anomalies.empty:
        print(f"  {GREEN}All clear — no day-over-day jumps exceed {threshold}%.{RESET}\n")
        return

    # Print each anomaly
    print(f"  {'ID':>6}  {'Date':<12}  {'Fund':<34}  {'BAD NAV':>10}  {'Prev':>10}  {'Next':>10}  {'DoD %':>8}")
    print(f"  {'─'*6}  {'─'*12}  {'─'*34}  {'─'*10}  {'─'*10}  {'─'*10}  {'─'*8}")

    for _, row in anomalies.iterrows():
        dod = row["dod_pct"]
        nxt = row["next_price"]
        color = RED if abs(dod) > 10 else YELLOW
        nxt_str = f"{nxt:>10.4f}" if pd.notna(nxt) else f"{'—':>10}"
        prev_str = f"{row['prev_price']:>10.4f}" if pd.notna(row['prev_price']) else f"{'—':>10}"
        dod_str = f"{dod:>+7.2f}%" if pd.notna(dod) else f"{'—':>8}"
        print(
            f"  {int(row['id']):>6}  {row['date']:<12}  {row['fund_name']:<34}  "
            f"{color}{row['nav_price']:>10.4f}{RESET}  {prev_str}  {nxt_str}  "
            f"{color}{dod_str}{RESET}"
        )

    print()


def delete_anomalies(db_path: str, ids: list[int]) -> int:
    """Delete rows by ID. Returns count deleted."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL;")
    placeholders = ",".join("?" for _ in ids)
    cursor = conn.execute(
        f"DELETE FROM nav_history WHERE id IN ({placeholders})", ids
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted


def main():
    parser = argparse.ArgumentParser(
        description="Detect and remove NAV anomalies from mutual_funds.db",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Delete anomalous rows (default: scan-only, no changes)",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt (use with --fix)",
    )
    parser.add_argument(
        "--threshold", "-t",
        type=float,
        default=5.0,
        help="Max allowed day-over-day %% change (default: 5.0)",
    )
    parser.add_argument(
        "--db",
        default=DB_PATH,
        help=f"Database path (default: {DB_PATH})",
    )
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        print(f"{RED}Database not found: {args.db}{RESET}")
        sys.exit(1)

    # ── Load & detect ────────────────────────────────────────────────────────
    df = load_data(args.db)
    if df.empty:
        print(f"{YELLOW}Database is empty — nothing to scan.{RESET}")
        sys.exit(0)

    anomalies = detect_anomalies(df, args.threshold)
    print_report(anomalies, args.threshold, len(df))

    if anomalies.empty:
        sys.exit(0)

    # ── Fix mode ─────────────────────────────────────────────────────────────
    if not args.fix:
        print(f"  {CYAN}Run with --fix to delete these rows.{RESET}")
        print(f"  {CYAN}Run with --fix --yes to skip the confirmation prompt.{RESET}\n")
        sys.exit(0)

    bad_ids = anomalies["id"].astype(int).tolist()

    if not args.yes:
        answer = input(
            f"  {YELLOW}Delete {len(bad_ids)} anomalous row(s)? "
            f"This cannot be undone. [y/N]: {RESET}"
        )
        if answer.strip().lower() not in ("y", "yes"):
            print(f"  {CYAN}Aborted — no changes made.{RESET}\n")
            sys.exit(0)

    deleted = delete_anomalies(args.db, bad_ids)
    print(f"  {GREEN}Deleted {deleted} anomalous row(s) from {args.db}{RESET}\n")


if __name__ == "__main__":
    main()
