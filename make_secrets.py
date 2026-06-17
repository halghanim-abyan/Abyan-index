"""
make_secrets.py — print a ready-to-paste Streamlit Cloud "Secrets" block.

Reads DATABASE_URL from your local .env (never shown to anyone but you, on
your own screen) and asks for a dashboard login password, then prints the
complete TOML to paste into Streamlit Cloud → Advanced settings → Secrets.

Run on YOUR machine:   python make_secrets.py
Then copy the block between the ==== lines into Streamlit, and DELETE the
generated streamlit_secrets.txt afterwards (it is gitignored, never pushed).
"""

import os
import pathlib

from dotenv import load_dotenv

load_dotenv()

db = (os.environ.get("DATABASE_URL") or "").strip()
if not db:
    raise SystemExit("ERROR: DATABASE_URL not found in .env. Run set_db_url.py first.")

pw = input("Choose a dashboard login password for user 'hadi': ").strip() or "CHANGE_ME"

content = f'''DATABASE_URL = "{db}"

[auth]
cookie_key = "abyan-terminal-key-2026"

[auth.users]
hadi = "{pw}"
'''

out = pathlib.Path(__file__).resolve().parent / "streamlit_secrets.txt"
out.write_text(content, encoding="utf-8")

print("\n==================  COPY EVERYTHING BELOW INTO STREAMLIT SECRETS  ==================\n")
print(content)
print("====================================================================================")
print(f"(also saved to {out.name} for easy copy — DELETE it after pasting into Streamlit)")
