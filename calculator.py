"""
calculator.py — Daily inflation index using a TRUE Laspeyres-style CPI.

PIPELINE
========
  1. Pull the full price history (raw) from `daily_prices`.
  2. Apply the PROMO FILTER (see _apply_promo_filter): any per-(item, store)
     price that drops MORE THAN 10% in a single day vs. the last known
     non-promo price is classified as a "temporary promotional sale" and
     replaced with that last good price for index purposes. Original rows
     in `daily_prices` are LEFT UNTOUCHED so the audit trail is preserved.
  3. Determine per-(item, store) BASE PRICES = the first non-null clean_price
     observed in chronological order. The earliest date in the database is
     the Base Period (Index = 100). Items that join later "link" into the
     index at price_relative = 1.0 on their first observation, which is the
     standard CPI linking convention.
  4. Compute per-(item, store) price relatives: clean_price / base_price.
  5. Average price relatives across stores → one PR per item per day.
  6. Combine per-item PR with basket weights, RENORMALIZING the weights
     across items reporting that day so a missing item does not
     artificially deflate the index.
  7. Multiply by INDEX_BASE_VALUE (=100) to get the final index. By
     construction Index = 100 on the base period.
  8. Persist into `daily_index` (UPSERT on date).

CLI
===
    python calculator.py                    # compute today's index
    python calculator.py --date 2026-04-15  # compute a specific past day
    python calculator.py --rebuild          # recompute ALL days from raw history
                                            # (use after switching to the
                                            # Laspeyres formula to replace the
                                            # legacy absolute-Riyal values).
"""

from __future__ import annotations

import argparse
import logging
import statistics
from datetime import date

import pandas as pd

from db_setup import create_tables, get_connection, migrate_schema, DB_PATH

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger(__name__)


# ── Tuning knobs ──────────────────────────────────────────────────────────
# A per-day, per-(item, store) drop bigger than this fraction is treated as
# a temporary promotional sale and filtered out of the index.
PROMO_DROP_THRESHOLD: float = 0.10   # 10%

# Scale of the index on the base period. Standard CPI convention = 100.
INDEX_BASE_VALUE: float = 100.0

# Base-price anomaly screen.  The very first scraped price for an (item, store)
# can be a misread (e.g. 5.00 SAR for tuna that is really 12.50 SAR).  If we
# blindly trusted that as the BASE, the price-relative on day 2 would jump to
# 2.5 and inject a fake 150 % "inflation" reading into the index.  To protect
# against this we validate every candidate first reading against the median
# of the next BASE_PRICE_LOOKAHEAD_DAYS observations: if the candidate sits
# outside ±BASE_PRICE_VALIDITY_TOLERANCE of that median, we REJECT it and
# advance to the next candidate.  Rejected pre-base rows are dropped from the
# basket on the days they cover (price_relative = NaN) — strict Laspeyres.
BASE_PRICE_LOOKAHEAD_DAYS: int = 5
BASE_PRICE_VALIDITY_TOLERANCE: float = 0.25   # 25 %

# Trailing window (in data points) for the rolling-MEDIAN smoother applied to
# per-(item, store) price relatives AFTER the symmetric volatility filter.
#
# Balanced window: wide enough to be a solid secondary defense, but the
# PRIMARY promo/stock-out suppression now comes from the structural
# Transitory Spike Reversion Filter (see _apply_spike_reversion_filter),
# NOT from the rolling median. Round-trip excursions are removed at the
# clean_price level before relatives are even computed, so this median only
# polishes the residue. A genuine NON-reverting shift survives the reversion
# filter and surfaces here within a few days.
PRICE_RELATIVE_SMOOTH_WINDOW: int = 7

# Second-tier smoother applied to the FINAL daily index series — damps any
# residual cross-item correlated wobble. Set to 1 to disable.
INDEX_SMOOTH_WINDOW: int = 3

# ── Transitory Spike Reversion Filter knobs ───────────────────────────────
# A "spike" is any per-(item, store) clean_price excursion that departs from
# the tracked baseline by more than REVERSION_TOLERANCE and then RETURNS to
# within ±REVERSION_TOLERANCE of that SAME baseline within REVERSION_WINDOW_DAYS.
# Such round-trips are the signature of a temporary supermarket promo or a
# stock-out substitution — NOT macroeconomic inflation — so the whole peak is
# retrospectively overwritten with the baseline price. An excursion that does
# NOT return within the window is treated as a genuine level shift and kept.
REVERSION_WINDOW_DAYS: int = 10
REVERSION_TOLERANCE:   float = 0.03   # ±3 %

# Maximum consecutive missing-scrape days (price=None) for which the volatility
# filter is allowed to forward-fill the last good baseline.  Beyond this, the
# (item, store) drops out of the basket for the affected days — its weight is
# redistributed by renormalization rather than being represented by a stale
# value.  This prevents a single recent reading from injecting a phantom
# plateau into the smoothed series when the scraper hasn't run for a while.
MAX_CONSECUTIVE_OOS_DAYS: int = 7

# Minimum basket-item coverage required before a daily index is persisted.
# Below this threshold the day is treated as data-quality failure rather than
# a valid market signal.
MIN_DAILY_ITEM_COVERAGE: float = 0.80

STATUS_OK = "ok"
STATUS_OOS = "oos"
NON_CARRY_STATUSES = {"not_found", "timeout", "error", "blocked"}

# Cross-store ghost-reading guard.  On any given day the SAME item is scraped
# at up to three stores.  A scraper mis-read (wrong product, bulk carton,
# concatenated digits — e.g. the 509 SAR "Lusine bread" at Danube vs 7 SAR
# elsewhere) shows up as one store's price being wildly out of line with the
# others.  For every (item, date) we compute each store's price against the
# MEDIAN of the OTHER stores for that same item+day; if it exceeds this
# multiple (in either direction) it is voided to NaN before the index math.
# Needs at least 2 stores reporting to have something to compare against.
CROSS_STORE_RATIO_LIMIT: float = 5.0
CROSS_STORE_EXEMPT_STORES = {"GASTAT Average Prices", "GASTAT CPI Category Index"}

# Two-of-N confirmation rule for baseline shifts.
#
# The legacy filter accepted a single in-threshold move and immediately
# advanced the baseline — a one-shot scrape reading at 17.99 (when the true
# price was 16.99) was enough to lock the index at a higher level until the
# next confirming day arrived.  We now require a **second** observation close
# to the proposed new level before the baseline actually moves: the first
# anomalous-but-in-threshold reading is held as PENDING and the OLD baseline
# is carried.  Genuine shifts surface with one day of lag; single-shot drifts
# never enter the index.
#
# • BASELINE_FLAT_BAND: moves smaller than this are treated as noise and the
#   raw value is accepted at the current baseline (no pending needed).
# • BASELINE_CONFIRM_BAND: the new candidate is confirmed when the next
#   reading sits within this fraction of the pending value.
BASELINE_FLAT_BAND:    float = 0.005   # 0.5 %
BASELINE_CONFIRM_BAND: float = 0.05    # 5 %


# ══════════════════════════════════════════════════════════════════════════════
#  Cross-store sanity filter (ghost-reading guard)
# ══════════════════════════════════════════════════════════════════════════════

def _apply_cross_store_filter(
    history_df: pd.DataFrame,
    ratio_limit: float = CROSS_STORE_RATIO_LIMIT,
) -> pd.DataFrame:
    """Void any store price that is wildly out of line with the SAME item's
    price at other stores on the SAME day (a "ghost reading").

    Algorithm per (item_id, date) group, by number of reporting stores n:

      • n < 2 — nothing to cross-check; left untouched.

      • n >= 3 — compare each store to the GROUP median (robust against a
        single extreme outlier). Drop any reading outside
        [median / ratio_limit, median × ratio_limit].  A leave-one-out
        median would mis-fire here: for [7.50, 7.75, 509] the *good* 7.75's
        leave-one-out median is median(7.50, 509)=258, which would wrongly
        flag it low. The group median (7.75) correctly keeps the two good
        prices and drops only the 509.

      • n == 2 — a divergent pair is statistically ambiguous (each value is
        "out of line" relative to the other), so a symmetric rule would void
        BOTH and lose the good reading. Ghost readings in this pipeline are
        overwhelmingly the absurd-HIGH kind (wrong product, bulk carton,
        concatenated digits — e.g. 509 SAR bread vs 7.75). So when the two
        prices differ by more than ratio_limit, we drop only the HIGHER one.
        (For unambiguous arbitration, ensure ≥ 3 stores report — that is the
        purpose of the Danube multi-card scraper fix.)

    Returns a COPY; an audit list of the dropped ghosts is attached to
    ``.attrs["cross_store_ghosts"]``.
    """
    out = history_df.copy()
    out.attrs["cross_store_ghosts"] = []
    if out.empty or "price" not in out.columns:
        return out

    ghosts: list[dict] = []
    drop_row_labels: list = []

    def _record(label, peer_ref, direction):
        row = out.loc[label]
        ghosts.append({
            "item_id":     int(row["item_id"]),
            "item_name":   row.get("item_name", ""),
            "store":       row.get("store_name", ""),
            "date":        row["date"],
            "price":       float(row["price"]),
            "peer_median": round(float(peer_ref), 2),
            "direction":   direction,
        })
        drop_row_labels.append(label)

    for (_item_id, _run_date), grp in out.groupby(["item_id", "date"], sort=False):
        valid = grp[
            ~grp["store_name"].isin(CROSS_STORE_EXEMPT_STORES)
        ].dropna(subset=["price"])
        n = len(valid)
        if n < 2:
            continue

        prices = valid["price"].astype(float)

        if n == 2:
            hi_label = prices.idxmax()
            lo_label = prices.idxmin()
            hi, lo = prices[hi_label], prices[lo_label]
            if lo > 0 and hi > ratio_limit * lo:
                # Ambiguous pair — drop the higher (dominant ghost mode).
                _record(hi_label, lo, "high")
            continue

        # n >= 3 — robust group-median band.
        med = float(prices.median())
        if med <= 0:
            continue
        for label, price in prices.items():
            if price > ratio_limit * med:
                _record(label, med, "high")
            elif price < med / ratio_limit:
                _record(label, med, "low")

    if drop_row_labels:
        out.loc[drop_row_labels, "price"] = float("nan")

    out.attrs["cross_store_ghosts"] = ghosts
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  Promotional-sale filter
# ══════════════════════════════════════════════════════════════════════════════

def _apply_promo_filter(
    history_df: pd.DataFrame,
    threshold: float = PROMO_DROP_THRESHOLD,
) -> pd.DataFrame:
    """Baseline tracker with confirmation + bounded OOS carry-forward.

    This stage produces a continuous per-(item, store) ``clean_price`` series
    plus an ``imputed`` flag. It does NOT decide what is a "promo" — that is
    now the job of the downstream Transitory Spike Reversion Filter, which can
    tell a transient round-trip from a permanent level shift. Here we only:

      1. SEED — lookahead-validate the first reading (rejects a misread first
         price); rows before the seed are out-of-basket (clean_price = NaN).

      2. CONFIRM — a real reading that departs the baseline by more than the
         flat band is held PENDING for one observation; the baseline only
         advances once a SECOND reading confirms the new level (within the
         confirm band). This stops a single-day scrape spike from moving the
         baseline, WITHOUT a hard ±threshold clamp. Critically, this means a
         genuine LARGE permanent step (e.g. milk +13.6 %) is now accepted on
         confirmation instead of being permanently rejected as a "promo" — the
         bug that pinned the index at 100.0000.

      3. IMPUTE — only a confirmed ``scrape_status='oos'`` gap inherits the
         LAST KNOWN valid price, capped at ``MAX_CONSECUTIVE_OOS_DAYS``.
         Scrape failures (not_found / timeout / error / blocked) do NOT carry
         stale prices forward and therefore drop out for that date.

    Returns the frame with three new columns:
        clean_price : float — continuous imputed price the index should use
        is_promo    : bool  — True iff this real reading is held UNCONFIRMED
        imputed     : bool  — True iff this day's price came from forward-fill

    ``.attrs["rejected_first_readings"]`` records lookahead-rejected seeds.
    """
    if history_df.empty:
        out = history_df.copy()
        out["clean_price"] = []
        out["is_promo"] = []
        out["imputed"] = []
        out.attrs["rejected_first_readings"] = []
        return out

    df = history_df.copy().sort_values(["item_id", "store_name", "date"]).reset_index(drop=True)

    # ── Pass 1: per-pair lookahead validation finds the SEED baseline. ──
    # seed_info[(item, store)] = (df_row_index_of_seed, seed_price)
    seed_info: dict[tuple[int, str], tuple[int, float]] = {}
    rejected_first_readings: list[dict] = []

    for (item_id, store_name), grp in df.groupby(["item_id", "store_name"], sort=False):
        non_null = grp.dropna(subset=["price"])
        if non_null.empty:
            continue
        raw_prices = non_null["price"].astype(float).tolist()
        if store_name in CROSS_STORE_EXEMPT_STORES:
            base_idx = 0
        else:
            base_idx = _find_first_valid_base_idx(raw_prices)
        if base_idx is None:
            continue
        seed_row_index = non_null.index[base_idx]
        seed_info[(item_id, store_name)] = (int(seed_row_index), float(raw_prices[base_idx]))
        if base_idx > 0:
            for rej_offset in range(base_idx):
                rej_row = non_null.iloc[rej_offset]
                rejected_first_readings.append({
                    "item_id":   int(item_id),
                    "item_name": rej_row.get("item_name", ""),
                    "store":     store_name,
                    "date":      rej_row["date"],
                    "rejected":  float(rej_row["price"]),
                    "base":      float(raw_prices[base_idx]),
                })

    # ── Pass 2: walk every row. Confirmation gate + bounded OOS carry. ──
    clean_prices:  list[float | None] = []
    is_promo_flags: list[bool] = []
    imputed_flags:  list[bool] = []
    baseline: dict[tuple[int, str], float] = {}
    pending:  dict[tuple[int, str], float] = {}   # tentative new level
    oos_streaks: dict[tuple[int, str], int] = {}

    for row_idx, row in enumerate(df.itertuples(index=False)):
        key = (row.item_id, row.store_name)
        row_status = getattr(row, "scrape_status", None)
        if not row_status:
            row_status = STATUS_OK if not pd.isna(row.price) else "not_found"
        row_status = str(row_status)
        seed = seed_info.get(key)
        if seed is None:
            clean_prices.append(None); is_promo_flags.append(False); imputed_flags.append(False)
            continue

        seed_pos, seed_price = seed
        if row_idx < seed_pos:
            # Pre-seed — out of basket (strict Laspeyres).
            clean_prices.append(None); is_promo_flags.append(False); imputed_flags.append(False)
            continue

        current = row.price
        last_good = baseline.get(key)

        # ── IMPUTE: only confirmed OOS can carry the last good price, capped. ──
        if current is None or pd.isna(current):
            if row_status == STATUS_OOS and last_good is not None:
                streak = oos_streaks.get(key, 0) + 1
                oos_streaks[key] = streak
                if streak <= MAX_CONSECUTIVE_OOS_DAYS:
                    clean_prices.append(last_good)
                    is_promo_flags.append(False)
                    imputed_flags.append(True)
                    continue

            # Scrape failures and over-cap OOS gaps are not represented by
            # stale prices. They stay out of basket for this date.
            clean_prices.append(None)
            is_promo_flags.append(False)
            imputed_flags.append(False)
            # A data gap must NOT confirm a pending shift.
            continue

        current_f = float(current)
        oos_streaks[key] = 0

        if row.store_name in CROSS_STORE_EXEMPT_STORES:
            baseline[key] = current_f
            clean_prices.append(current_f)
            is_promo_flags.append(False); imputed_flags.append(False)
            pending.pop(key, None)
            continue

        # First real row at/after the seed → anchor the baseline.
        if last_good is None:
            baseline[key] = seed_price
            clean_prices.append(seed_price)
            is_promo_flags.append(False); imputed_flags.append(False)
            continue

        change_pct = (current_f - last_good) / last_good if last_good > 0 else 0.0

        # Flat band — within noise; accept at baseline (tracks slow drift).
        if abs(change_pct) < BASELINE_FLAT_BAND:
            baseline[key] = current_f
            clean_prices.append(current_f)
            is_promo_flags.append(False); imputed_flags.append(False)
            pending.pop(key, None)
            continue

        # Beyond flat band → two-of-N confirmation (applies to ALL move sizes,
        # large or small — there is no hard ±threshold clamp any more).
        pending_val = pending.get(key)
        if (
            pending_val is not None
            and pending_val > 0
            and abs(current_f - pending_val) / pending_val < BASELINE_CONFIRM_BAND
        ):
            # Confirmed: a second reading near the new level → shift baseline.
            baseline[key] = current_f
            clean_prices.append(current_f)
            is_promo_flags.append(False); imputed_flags.append(False)
            pending.pop(key, None)
        else:
            # First sighting of a new level — hold pending, carry baseline.
            pending[key] = current_f
            clean_prices.append(last_good)
            is_promo_flags.append(True)   # unconfirmed this day
            imputed_flags.append(False)

    df["clean_price"] = clean_prices
    df["is_promo"]    = is_promo_flags
    df["imputed"]     = imputed_flags
    df.attrs["rejected_first_readings"] = rejected_first_readings
    return df


# ══════════════════════════════════════════════════════════════════════════════
#  Laspeyres CPI: per-(item, store) price relatives, weighted aggregation
# ══════════════════════════════════════════════════════════════════════════════

def _find_first_valid_base_idx(
    prices: list[float],
    lookahead: int = BASE_PRICE_LOOKAHEAD_DAYS,
    tolerance: float = BASE_PRICE_VALIDITY_TOLERANCE,
) -> int | None:
    """Pick the index of the FIRST scraped price that is plausibly the true
    "settled" price for an (item, store) pair.

    A candidate at position `i` is accepted when it sits within ±`tolerance`
    of the MEDIAN of the next `lookahead` non-null prices.  If the candidate
    is way off (e.g. a single-day scrape error: 5.00 SAR for tuna that is
    really 12.50 SAR), it is rejected and the next candidate is considered.

    Returns the index inside `prices` of the validated base, or `None` if
    `prices` is empty.  When only one reading exists, that reading is
    accepted unconditionally — no data to validate against.
    """
    n = len(prices)
    if n == 0:
        return None
    if n == 1:
        return 0

    for i in range(n):
        candidate = prices[i]
        window = prices[i + 1 : i + 1 + lookahead]
        if not window:
            # Exhausted future readings — accept the candidate (best we can do).
            return i
        med = statistics.median(window)
        if med > 0 and abs(candidate - med) / med <= tolerance:
            return i

    # No candidate ever validated — fall back to index 0 so the item is
    # not silently dropped (its later anomaly will still be visible).
    return 0


def _apply_spike_reversion_filter(
    cleaned_df: pd.DataFrame,
    window_days: int = REVERSION_WINDOW_DAYS,
    tolerance: float = REVERSION_TOLERANCE,
) -> pd.DataFrame:
    """Transitory Spike Reversion Filter — the STRUCTURAL promo/stock-out guard.

    Walks each (item_id, store_name) clean_price trajectory chronologically and
    classifies every excursion away from the tracked baseline as either:

      • TRANSITORY (round-trip): the price departs the baseline by more than
        ``tolerance`` and RETURNS to within ±``tolerance`` of that SAME
        baseline within ``window_days`` calendar days. The entire peak is
        retrospectively overwritten with the baseline price — this is the
        fingerprint of a weekly supermarket discount rotation or a stock-out
        substitution, not inflation.

      • STRUCTURAL (level shift): the price departs and does NOT return within
        the window. The new level is accepted as the baseline going forward and
        the values are kept — this is a genuine, persistent price change.

    A slow creep where each step stays within ±``tolerance`` of the running
    baseline is treated as genuine drift (the baseline tracks it) and is never
    flattened — so a real progressive macro trend survives intact.

    Operates on ``clean_price`` (already promo-filtered). Returns a COPY with
    the round-trip peaks overwritten; an audit list of every reverted peak is
    attached to ``.attrs["spike_reversions"]``. Upstream audit attrs are
    preserved.
    """
    out = cleaned_df.copy()
    # Preserve upstream audit trails.
    out.attrs["rejected_first_readings"] = cleaned_df.attrs.get("rejected_first_readings", [])
    out.attrs["cross_store_ghosts"]      = cleaned_df.attrs.get("cross_store_ghosts", [])
    out.attrs["spike_reversions"]        = []

    if out.empty or "clean_price" not in out.columns:
        return out

    out = out.sort_values(["item_id", "store_name", "date"]).reset_index(drop=True)
    # Parse each unique date once for calendar-day distance checks.
    unique_dates = {d: pd.Timestamp(d) for d in out["date"].unique()}

    reversions: list[dict] = []

    has_imputed = "imputed" in out.columns

    for (item_id, store_name), grp in out.groupby(["item_id", "store_name"], sort=False):
        if store_name in CROSS_STORE_EXEMPT_STORES:
            continue
        grp = grp.sort_values("date")
        g_labels = grp.index.tolist()
        g_dates  = [unique_dates[d] for d in grp["date"].tolist()]
        g_clean  = grp["clean_price"].tolist()
        if has_imputed:
            g_imp = grp["imputed"].tolist()
        else:
            g_imp = [False] * len(grp)

        # REAL readings only drive departure/return logic (point #2: a price
        # that disappears into None — now forward-filled & imputed=True — is
        # NOT a real price move and must not count as a departure OR a return).
        real_pos = [
            p for p in range(len(grp))
            if g_clean[p] is not None and not pd.isna(g_clean[p]) and not g_imp[p]
        ]
        if len(real_pos) < 3:
            continue  # need baseline + peak + return among REAL readings

        baseline = float(g_clean[real_pos[0]])
        r = 0
        m = len(real_pos)
        while r < m:
            pos = real_pos[r]
            price = float(g_clean[pos])
            if baseline <= 0:
                baseline = price if price > 0 else baseline
                r += 1
                continue

            if abs(price - baseline) / baseline <= tolerance:
                baseline = price            # track slow genuine drift
                r += 1
                continue

            # Departure → search forward (REAL readings) for a return within window.
            start_date = g_dates[pos]
            ret_r = None
            for s in range(r + 1, m):
                sp = real_pos[s]
                if (g_dates[sp] - start_date).days > window_days:
                    break
                if abs(float(g_clean[sp]) - baseline) / baseline <= tolerance:
                    ret_r = s
                    break

            if ret_r is not None:
                # TRANSITORY round-trip → overwrite the WHOLE span (incl. any
                # imputed OOS days inside it) with the baseline price.
                ret_pos = real_pos[ret_r]
                span_prices = [float(g_clean[real_pos[k]]) for k in range(r, ret_r)]
                peak_val = max(span_prices, key=lambda p: abs(p - baseline))
                for gp in range(pos, ret_pos):           # absolute group positions
                    if g_clean[gp] is not None and not pd.isna(g_clean[gp]):
                        out.at[g_labels[gp], "clean_price"] = baseline
                reversions.append({
                    "item_id":   int(item_id),
                    "item_name": grp.iloc[0].get("item_name", ""),
                    "store":     store_name,
                    "from_date": grp.iloc[pos]["date"],
                    "to_date":   grp.iloc[ret_pos - 1]["date"],
                    "peak":      round(float(peak_val), 2),
                    "baseline":  round(float(baseline), 2),
                    "days":      int((g_dates[ret_pos] - start_date).days),
                })
                r = ret_r          # resume from the (real) return reading
            else:
                # STRUCTURAL level shift → accept new baseline, keep values.
                baseline = price
                r += 1

    out.attrs["spike_reversions"] = reversions
    return out


def _attach_price_relatives(cleaned_df: pd.DataFrame) -> pd.DataFrame:
    """Add `base_price` and `price_relative` columns to the cleaned history.

    The volatility filter (``_apply_promo_filter``) has already done the
    heavy lifting: it ran lookahead validation per (item, store) and set
    ``clean_price = NaN`` for every row BEFORE the validated seed.  So
    the "first non-null clean_price" is GUARANTEED to be the lookahead-
    validated base — no further screening needed here.

    Strict-Laspeyres semantics inherited from the filter:
        • Items/stores with no validated seed → clean_price all NaN →
          base_price NaN → price_relative NaN → excluded forever.
        • Pre-seed rows → clean_price NaN → price_relative NaN → not in
          the basket on those days.
        • From the seed forward → price_relative = clean_price / base.
    """
    if cleaned_df.empty:
        out = cleaned_df.copy()
        out["base_price"] = []
        out["price_relative"] = []
        # Preserve any audit log that the filter attached.
        out.attrs.setdefault("rejected_first_readings", [])
        return out

    df = cleaned_df.copy().sort_values(["item_id", "store_name", "date"]).reset_index(drop=True)

    # First non-NaN clean_price per (item, store) IS the validated seed.
    valid = df.dropna(subset=["clean_price"])
    base_prices = (
        valid.groupby(["item_id", "store_name"], as_index=False)
             .agg(base_price=("clean_price", "first"))
    )
    df = df.merge(base_prices, on=["item_id", "store_name"], how="left")

    # price_relative = clean_price / base_price, guarded against base ≤ 0.
    safe_base = df["base_price"].where(df["base_price"] > 0)
    df["price_relative"] = df["clean_price"] / safe_base

    # Pass-through every upstream audit log.
    df.attrs["rejected_first_readings"] = cleaned_df.attrs.get("rejected_first_readings", [])
    df.attrs["cross_store_ghosts"]      = cleaned_df.attrs.get("cross_store_ghosts", [])
    df.attrs["spike_reversions"]        = cleaned_df.attrs.get("spike_reversions", [])
    return df


def _smooth_price_relatives(
    enriched_df: pd.DataFrame,
    window: int = PRICE_RELATIVE_SMOOTH_WINDOW,
) -> pd.DataFrame:
    """Add `price_relative_smooth` — a TRAILING rolling-median of price_relative
    over the last `window` data points, computed PER (item_id, store_name).

    Purpose: Hadi's "ghost-spike" suppression.  The symmetric volatility filter
    already rejects extreme single-day moves on the raw price level, but residual
    within-threshold oscillations (chicken cycling 17→18.50→17 across a weekend,
    a single-day stock-out blip that drops a store from the basket and immediately
    re-enters, a multi-day promo expiry that snaps back) still inject 0.5-1.5 pp
    ghost spikes into the daily index.  A trailing median over the last `window`
    observations:

      • completely suppresses 1-(window//2) day spikes (majority-rule),
      • is robust against asymmetric outliers (mean would average the spike in;
        median ignores it as long as the spike is the minority),
      • is causal — smoothed PR for date D uses only data ≤ D, so today's index
        can be computed without waiting for tomorrow,
      • preserves the Day-1 = 100 anchor — at the base date the window contains
        only the base day itself, so smoothed PR equals raw PR (= 1.0).

    Pre-seed rows (where price_relative is NaN) propagate NaN through the
    rolling window; they remain out-of-basket on those dates.
    """
    def _carry_attrs(target: pd.DataFrame) -> pd.DataFrame:
        target.attrs["rejected_first_readings"] = enriched_df.attrs.get("rejected_first_readings", [])
        target.attrs["cross_store_ghosts"]      = enriched_df.attrs.get("cross_store_ghosts", [])
        target.attrs["spike_reversions"]        = enriched_df.attrs.get("spike_reversions", [])
        return target

    if enriched_df.empty:
        out = enriched_df.copy()
        out["price_relative_smooth"] = []
        return _carry_attrs(out)

    # Disabled (window <= 1) ⇒ smoothed column equals raw PR.
    if window <= 1:
        out = enriched_df.copy()
        out["price_relative_smooth"] = out["price_relative"]
        return _carry_attrs(out)

    df = enriched_df.sort_values(["item_id", "store_name", "date"]).reset_index(drop=True)
    df["price_relative_smooth"] = (
        df.groupby(["item_id", "store_name"])["price_relative"]
          .transform(lambda s: s.rolling(window=window, min_periods=1).median())
    )
    df.loc[
        df["store_name"].isin(CROSS_STORE_EXEMPT_STORES),
        "price_relative_smooth",
    ] = df["price_relative"]
    return _carry_attrs(df)


def _smooth_index_series(
    index_by_date: dict[str, float],
    window: int = INDEX_SMOOTH_WINDOW,
) -> dict[str, float]:
    """Apply a TRAILING rolling-median smoother to the finished daily index.

    Second-tier safety net: after per-item smoothing, an aggregate residual
    can still appear when several items happen to spike in the same week
    (weekly stocking cycle).  A short trailing median over the daily index
    values absorbs that without lagging real long-term shifts noticeably.

    Returns a NEW dict keyed by the same dates.  When `window <= 1` the
    input is passed through unchanged.
    """
    if not index_by_date or window <= 1:
        return dict(index_by_date)

    dates = sorted(index_by_date.keys())
    values = [float(index_by_date[d]) for d in dates]
    s = pd.Series(values, index=dates)
    smoothed = s.rolling(window=window, min_periods=1).median()
    return {d: round(float(v), 4) for d, v in zip(smoothed.index, smoothed.values)}


def _rebase_index_series(
    index_by_date: dict[str, float],
    base_date: str | None = None,
) -> dict[str, float]:
    """Scale an index series so its app base date is exactly 100.

    Some official inputs, especially GASTAT CPI category series, already come
    as indices with their own base period. The app should still show its own
    first tracked day as 100 and report later movement relative to that day.
    """
    if not index_by_date:
        return {}

    dates = sorted(index_by_date.keys())
    chosen_base_date = base_date if base_date in index_by_date else dates[0]
    base_value = float(index_by_date[chosen_base_date])
    if base_value <= 0:
        log.warning(
            "Cannot rebase index series because base value on %s is %.4f.",
            chosen_base_date,
            base_value,
        )
        return dict(index_by_date)

    return {
        d: round(INDEX_BASE_VALUE * float(v) / base_value, 4)
        for d, v in index_by_date.items()
    }


def _smooth_and_rebase_from_app_base(
    raw_index: dict[str, float],
    app_base_date: str | None,
) -> dict[str, float]:
    """Smooth only the app-era series, then rebase to the app base date.

    The database also contains older official monthly GASTAT rows. Letting the
    final rolling-median smoother look back into those pre-app dates can distort
    the first daily app reading (base day), making day two appear to jump by
    several points. The daily app index should start cleanly at its own base
    date, so the final smoother is scoped to dates >= app_base_date.
    """
    if not raw_index:
        return {}
    if app_base_date is None:
        app_base_date = sorted(raw_index)[0]
    app_index = {
        d: v
        for d, v in raw_index.items()
        if d >= app_base_date
    }
    if not app_index:
        return {}
    smoothed_index = _smooth_index_series(app_index, window=INDEX_SMOOTH_WINDOW)
    return _rebase_index_series(smoothed_index, app_base_date)


def _select_app_base_date(
    conn,
    index_by_date: dict[str, float],
    run_date: str | None = None,
) -> str | None:
    """Pick the date that should remain fixed at index=100 for normal runs."""
    if not index_by_date:
        return None

    row = conn.execute("SELECT MIN(date) FROM daily_index").fetchone()
    existing_base_date = row[0] if row else None
    if existing_base_date in index_by_date:
        return str(existing_base_date)
    if run_date in index_by_date:
        return str(run_date)
    return sorted(index_by_date.keys())[0]


def _compute_index_for_date(
    enriched_df: pd.DataFrame,
    run_date: str,
) -> float | None:
    """Combine per-item price relatives with the basket weights for one day.

    Strict Laspeyres basket: only rows with BOTH a validated `base_price`
    AND a usable price relative contribute.  Items whose first valid
    reading lies in the future (or whose entire series was anomalous) are
    silently excluded today.  The remaining items' weights are renormalised
    so the active basket still sums to 1.0.

    Prefers the SMOOTHED price_relative column (output of
    ``_smooth_price_relatives``) when present, falling back to the raw
    ``price_relative`` for backward compatibility.

      1. Filter `enriched_df` to `run_date`.
      2. Drop rows without a validated baseline or PR.
      3. Average the (smoothed) price relative per item across stores.
      4. RENORMALISE basket weights across the items reporting today.
      5. Index = INDEX_BASE_VALUE × Σ (renormalised_weight × avg_pr).
    """
    day = enriched_df[enriched_df["date"] == run_date]
    if day.empty:
        return None

    # Use the smoothed PR if it has been attached, otherwise the raw PR.
    pr_col = (
        "price_relative_smooth"
        if "price_relative_smooth" in day.columns
        else "price_relative"
    )

    # Strict basket — must have both a validated base AND a usable PR.
    active = day.dropna(subset=[pr_col, "base_price"])
    if active.empty:
        return None

    total_items = int(enriched_df["item_id"].nunique())
    active_items = int(active["item_id"].nunique())
    coverage = (active_items / total_items) if total_items else 0.0
    if coverage < MIN_DAILY_ITEM_COVERAGE:
        log.warning(
            "Coverage %.1f%% on %s (%d/%d basket items) is below %.0f%%; index not computed.",
            coverage * 100,
            run_date,
            active_items,
            total_items,
            MIN_DAILY_ITEM_COVERAGE * 100,
        )
        return None

    per_item = (
        active.groupby(["item_id", "weight_percentage"], as_index=False)
              .agg(avg_pr=(pr_col, "mean"))
    )

    if per_item.empty:
        return None

    weight_sum = float(per_item["weight_percentage"].sum())
    if weight_sum <= 0:
        return None
    if active_items < total_items:
        log.warning(
            "Only %d/%d basket items in the active basket on %s — weights renormalized.",
            active_items, total_items, run_date,
        )

    per_item["normalized_weight"] = per_item["weight_percentage"] / weight_sum
    weighted_pr = float((per_item["normalized_weight"] * per_item["avg_pr"]).sum())
    return round(INDEX_BASE_VALUE * weighted_pr, 4)


# ══════════════════════════════════════════════════════════════════════════════
#  Persistence helpers
# ══════════════════════════════════════════════════════════════════════════════

def _load_history(conn) -> pd.DataFrame:
    """Pull the full price history joined with item metadata."""
    return pd.read_sql_query(
        """
        SELECT dp.date,
               dp.item_id,
               dp.store_name,
               dp.price,
               COALESCE(
                   dp.scrape_status,
                   CASE WHEN dp.price IS NULL THEN 'not_found' ELSE 'ok' END
               ) AS scrape_status,
               dp.failure_reason,
               dp.observed_at,
               i.name        AS item_name,
               i.category,
               i.weight_percentage
          FROM daily_prices dp
          JOIN items i ON i.id = dp.item_id
         ORDER BY dp.date, dp.item_id, dp.store_name
        """,
        conn,
    )


def _upsert_index(conn, run_date: str, index_value: float) -> None:
    conn.execute(
        """
        INSERT INTO daily_index (date, index_value)
        VALUES (?, ?)
        ON CONFLICT(date) DO UPDATE SET index_value = excluded.index_value
        """,
        (run_date, index_value),
    )
    conn.commit()


def _prepare_enriched_history(raw: pd.DataFrame) -> pd.DataFrame:
    """Run the full raw → sanity → promo → relatives → smoothed pipeline.

    Stages (each pure-functional):
      0. ``_apply_cross_store_filter`` — void per-(item, day) ghost readings
         that are > ratio_limit× out of line with the same item at other
         stores (e.g. the 509 SAR "Lusine bread" vs 7 SAR elsewhere).
      1. ``_apply_promo_filter``    — symmetric ±threshold volatility filter
         anchored at a lookahead-validated seed per (item, store), with a
         two-of-N baseline-shift confirmation and an OOS forward-fill cap.
      2. ``_attach_price_relatives`` — base_price + raw price_relative
         per (item, store, date).
      3. ``_smooth_price_relatives`` — trailing rolling-median smoother that
         absorbs short ghost spikes from promos, OOS, or scrape misses.
    """
    sane     = _apply_cross_store_filter(raw, ratio_limit=CROSS_STORE_RATIO_LIMIT)
    cleaned  = _apply_promo_filter(sane, threshold=PROMO_DROP_THRESHOLD)
    # Carry the cross-store audit list forward for logging.
    cleaned.attrs["cross_store_ghosts"] = sane.attrs.get("cross_store_ghosts", [])
    # Structural promo/stock-out guard: flatten transitory round-trip spikes
    # at the clean_price level BEFORE relatives are computed.
    reverted = _apply_spike_reversion_filter(
        cleaned, window_days=REVERSION_WINDOW_DAYS, tolerance=REVERSION_TOLERANCE,
    )
    enriched = _attach_price_relatives(reverted)
    return _smooth_price_relatives(enriched, window=PRICE_RELATIVE_SMOOTH_WINDOW)


def _log_rejected_first_readings(enriched: pd.DataFrame, max_lines: int = 20) -> None:
    """Surface the lookahead-validated base-price rejections to the run log.

    Reads the audit list stashed by ``_attach_price_relatives`` on
    ``enriched.attrs["rejected_first_readings"]`` and prints one line per
    rejected (item, store, date), capped at ``max_lines`` to avoid log
    spam on a freshly-scraped database.
    """
    rejected: list[dict] = enriched.attrs.get("rejected_first_readings", []) or []
    if not rejected:
        return

    log.warning(
        "Rejected %d anomalous early readings (lookahead validity guard, "
        "±%.0f%% vs %d-day median):",
        len(rejected),
        BASE_PRICE_VALIDITY_TOLERANCE * 100,
        BASE_PRICE_LOOKAHEAD_DAYS,
    )
    for r in rejected[:max_lines]:
        log.warning(
            "  %-32s  @ %-7s  %s  raw=SAR %7.2f  validated_base=SAR %7.2f",
            (r.get("item_name") or f"item#{r.get('item_id')}")[:32],
            r.get("store", ""),
            r.get("date", ""),
            r.get("rejected", float("nan")),
            r.get("base", float("nan")),
        )
    if len(rejected) > max_lines:
        log.warning("  ... and %d more rejections suppressed.", len(rejected) - max_lines)


def _log_cross_store_ghosts(enriched: pd.DataFrame, max_lines: int = 20) -> None:
    """Surface the cross-store ghost readings voided before the index math.

    Reads ``enriched.attrs["cross_store_ghosts"]`` (set by
    ``_apply_cross_store_filter``) and prints one line per dropped reading.
    """
    ghosts: list[dict] = enriched.attrs.get("cross_store_ghosts", []) or []
    if not ghosts:
        return

    log.warning(
        "Voided %d cross-store ghost readings (>%.0f× peer median):",
        len(ghosts), CROSS_STORE_RATIO_LIMIT,
    )
    for g in ghosts[:max_lines]:
        log.warning(
            "  %-32s  @ %-7s  %s  price=SAR %8.2f  peer_median=SAR %7.2f  (%s)",
            (g.get("item_name") or f"item#{g.get('item_id')}")[:32],
            g.get("store", ""),
            g.get("date", ""),
            g.get("price", float("nan")),
            g.get("peer_median", float("nan")),
            g.get("direction", ""),
        )
    if len(ghosts) > max_lines:
        log.warning("  ... and %d more ghosts suppressed.", len(ghosts) - max_lines)


def _log_spike_reversions(enriched: pd.DataFrame, max_lines: int = 20) -> None:
    """Surface the transitory round-trip peaks flattened by the reversion filter."""
    revs: list[dict] = enriched.attrs.get("spike_reversions", []) or []
    if not revs:
        return

    log.warning(
        "Reverted %d transitory spikes (round-trip to ±%.0f%% baseline within %d days):",
        len(revs), REVERSION_TOLERANCE * 100, REVERSION_WINDOW_DAYS,
    )
    for r in revs[:max_lines]:
        log.warning(
            "  %-32s  @ %-7s  %s → %s  peak=SAR %7.2f  baseline=SAR %7.2f  (%dd)",
            (r.get("item_name") or f"item#{r.get('item_id')}")[:32],
            r.get("store", ""),
            r.get("from_date", ""),
            r.get("to_date", ""),
            r.get("peak", float("nan")),
            r.get("baseline", float("nan")),
            r.get("days", 0),
        )
    if len(revs) > max_lines:
        log.warning("  ... and %d more reversions suppressed.", len(revs) - max_lines)


# ══════════════════════════════════════════════════════════════════════════════
#  Public API
# ══════════════════════════════════════════════════════════════════════════════

def calculate_daily_index(
    run_date: str | None = None,
    db_path: str = DB_PATH,
) -> float | None:
    """Compute and persist the Laspeyres-style inflation index for `run_date`.

    The full history is loaded so the per-(item, store) base price and
    promo baseline are computed from the canonical chronological data —
    not just the latest day. Cheap in practice (history is small).

    Returns the computed index, or None when no price data was available.
    """
    if run_date is None:
        run_date = date.today().isoformat()

    conn = get_connection(db_path)
    try:
        create_tables(conn)
        migrate_schema(conn)
        raw = _load_history(conn)
        if raw.empty:
            log.warning("daily_prices is empty — nothing to compute.")
            return None

        enriched = _prepare_enriched_history(raw)

        # Anomaly audit log: surface rejected first readings (scrape errors
        # whose base assignment was shifted forward by lookahead validation).
        _log_rejected_first_readings(enriched)
        _log_cross_store_ghosts(enriched)
        _log_spike_reversions(enriched)

        # Promo audit log.
        promo_today = enriched[(enriched["date"] == run_date) & (enriched["is_promo"])]
        if not promo_today.empty:
            log.info("Filtered %d promotional rows on %s:", len(promo_today), run_date)
            for r in promo_today.itertuples(index=False):
                pct = (1 - r.price / r.clean_price) * 100 if r.clean_price else 0
                log.info(
                    "  %-32s  @ %-7s  raw=SAR %6.2f  carried=SAR %6.2f  (-%4.1f%%)",
                    r.item_name, r.store_name, r.price, r.clean_price, pct,
                )

        # Renormalization audit: warn when not all basket items reported.
        day_df = enriched[enriched["date"] == run_date]
        active_items = int(day_df.dropna(subset=["price_relative"])["item_id"].nunique())
        total_items  = int(enriched["item_id"].nunique())
        if active_items and active_items < total_items:
            log.warning(
                "Only %d/%d basket items in the active basket on %s — weights renormalized.",
                active_items, total_items, run_date,
            )

        # Compute the raw index for every available date so the index-level
        # smoother (second-tier ghost-spike absorber) has a series to work
        # with.  The full series is cheap to recompute (small data set) and
        # guarantees today's value reflects the smoothed trend.
        unique_dates: list[str] = sorted(enriched["date"].unique().tolist())
        raw_index: dict[str, float] = {}
        for d in unique_dates:
            v = _compute_index_for_date(enriched, d)
            if v is not None:
                raw_index[d] = v

        app_base_date = _select_app_base_date(conn, raw_index, run_date)
        rebased_index = _smooth_and_rebase_from_app_base(raw_index, app_base_date)
        index_value = rebased_index.get(run_date)
        if index_value is None:
            log.warning("No price data for %s — index not computed.", run_date)
            return None

        log.info(
            "Daily Inflation Index (Laspeyres, app-base=%s=100, PR-smooth=%d, "
            "idx-smooth=%d) for %s = %.4f",
            app_base_date,
            PRICE_RELATIVE_SMOOTH_WINDOW,
            INDEX_SMOOTH_WINDOW,
            run_date,
            index_value,
        )
        _upsert_index(conn, run_date, index_value)
        return index_value
    finally:
        conn.close()


def rebuild_index_history(db_path: str = DB_PATH) -> int:
    """Recompute every historical daily_index row from scratch.

    Run this once after switching to the Laspeyres formula so that the
    stored `daily_index` reflects the new base = 100 convention instead
    of the legacy absolute-Riyal sums.

    Returns the number of dates recomputed.
    """
    conn = get_connection(db_path)
    try:
        create_tables(conn)
        migrate_schema(conn)
        raw = _load_history(conn)
        if raw.empty:
            log.warning("daily_prices is empty — nothing to rebuild.")
            return 0

        enriched = _prepare_enriched_history(raw)
        unique_dates: list[str] = sorted(enriched["date"].unique().tolist())

        log.info(
            "Rebuilding daily_index for %d candidate dates (app base index = %.1f, "
            "PR-smooth window = %d days, idx-smooth window = %d days)...",
            len(unique_dates), INDEX_BASE_VALUE,
            PRICE_RELATIVE_SMOOTH_WINDOW, INDEX_SMOOTH_WINDOW,
        )
        total_promos = int(enriched["is_promo"].sum())
        log.info("  (promo filter dropped %d rows across history)", total_promos)

        _log_rejected_first_readings(enriched)
        _log_cross_store_ghosts(enriched)
        _log_spike_reversions(enriched)

        # Pass 1: raw per-day Laspeyres index using smoothed PRs.
        raw_index: dict[str, float] = {}
        for d in unique_dates:
            v = _compute_index_for_date(enriched, d)
            if v is not None:
                raw_index[d] = v

        # Pass 2: trailing rolling-median over the daily index series itself,
        # absorbing any residual cross-item ghost spikes (multi-item weekly
        # stocking cycles, correlated promo expiries) that survived the
        # per-(item, store) smoothing.
        smoothed_index = _smooth_index_series(raw_index, window=INDEX_SMOOTH_WINDOW)
        rebased_index = _rebase_index_series(smoothed_index)
        if rebased_index:
            log.info(
                "  App base period: %s = %.1f",
                sorted(rebased_index.keys())[0],
                INDEX_BASE_VALUE,
            )

        rebuilt = 0
        for d in unique_dates:
            v = rebased_index.get(d)
            if v is None:
                continue
            _upsert_index(conn, d, v)
            log.info("  %s -> %.4f", d, v)
            rebuilt += 1
        return rebuilt
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════════════════
#  CLI
# ══════════════════════════════════════════════════════════════════════════════

def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Compute the Saudi Daily Inflation Index (Laspeyres CPI, base=100)."
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--date", type=str, default=None,
                   help="ISO date to compute (default: today).")
    g.add_argument("--rebuild", action="store_true",
                   help="Recompute the entire historical index using the "
                        "Laspeyres CPI formula. Use this once after upgrading "
                        "from the legacy absolute-price index.")
    return p


if __name__ == "__main__":
    args = _build_argparser().parse_args()
    if args.rebuild:
        n = rebuild_index_history()
        log.info("Rebuild complete — %d days recomputed.", n)
    else:
        calculate_daily_index(run_date=args.date)
