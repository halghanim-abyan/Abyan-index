"""
set_db_url.py — one-time helper to write a local .env with your Supabase
DATABASE_URL, WITHOUT exposing the password on screen or in chat.

Run once:   python set_db_url.py
It asks for the database password (hidden input), URL-encodes any special
characters, and writes .env (which is gitignored). Re-run anytime to update.
"""

import getpass
import pathlib
import urllib.parse

PROJECT_REF = "awkuijdvfmmtynpvolxw"

# Supabase connection hosts (direct is what your dashboard showed):
DIRECT_HOST = f"db.{PROJECT_REF}.supabase.co"          # user: postgres
POOLER_HOST = "aws-0-ap-northeast-1.pooler.supabase.com"  # user: postgres.<ref>


def build_url(password: str, mode: str) -> str:
    enc = urllib.parse.quote(password, safe="")  # handle @ : / & etc. safely
    if mode == "pooler":
        user = f"postgres.{PROJECT_REF}"
        host = POOLER_HOST
    else:
        user = "postgres"
        host = DIRECT_HOST
    return (
        f"postgresql://{user}:{enc}@{host}:5432/postgres?sslmode=require"
    )


def main() -> None:
    print("Supabase DATABASE_URL setup")
    print("  1) direct  (db.<ref>.supabase.co)   [default]")
    print("  2) pooler  (aws-0-...pooler...)      [use if direct times out]")
    choice = input("Choose 1 or 2 [1]: ").strip() or "1"
    mode = "pooler" if choice == "2" else "direct"

    pw = getpass.getpass("Supabase database password (hidden): ").strip()
    if not pw:
        print("No password entered — aborted. Nothing written.")
        return

    url = build_url(pw, mode)
    env_path = pathlib.Path(__file__).resolve().parent / ".env"
    env_path.write_text(f"DATABASE_URL={url}\n", encoding="utf-8")

    # Confirm WITHOUT revealing the password.
    masked = url.replace(urllib.parse.quote(pw, safe=""), "********")
    print(f"\nWrote {env_path}")
    print(f"  DATABASE_URL = {masked}")
    print("Done. Tell the assistant 'تم'.")


if __name__ == "__main__":
    main()
