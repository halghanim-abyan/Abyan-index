# tadawul_data/

Drop daily Tadawul foreign-ownership exports here as **CSV** or **Excel** (`.xlsx` / `.xls`).

## Expected columns

The processor auto-detects common header aliases, but the canonical names are:

| Canonical | Required | Accepted aliases (case-insensitive) |
|-----------|----------|-------------------------------------|
| `Ticker` | yes | Symbol, Code, Stock_Code, Tadawul_Code |
| `Sector` | recommended | Industry, Sector_Name, GICS_Sector |
| `Foreign_Ownership_Pct_Today` | yes | Today_Pct, Foreign_Pct_Today |
| `Foreign_Ownership_Pct_Yesterday` | yes | Yesterday_Pct, Prev_Foreign_Pct |
| `Total_Shares` | yes | Shares_Outstanding, Issued_Shares |
| `Daily_Close_Price` | yes | Close, Close_Price, Last_Price |
| `Company_Name` | optional | Name, Issuer |

Percentages can be stored as `15.42` (the processor treats them as 0–100 and divides by 100 internally).

## Net Flow formula

```
Net Flow (SAR) = (Pct_Today - Pct_Yesterday) / 100
               * Total_Shares
               * Daily_Close_Price
```

- **Positive** → Accumulation (foreign buying)
- **Negative** → Distribution (foreign selling)
- **Zero / NaN** → Neutral (or dropped if inputs are missing)

## File naming

Include an `YYYY-MM-DD` date anywhere in the filename so the API can infer `as_of_date`:

```
tadawul_foreign_ownership_2026-04-22.csv
tadawul_2026_04_22.xlsx
```

If no date is present, the file's modification timestamp is used.

## Selection rule

The API always processes the **most recently modified** file in this folder, unless a specific file is requested via `?file=...`.
