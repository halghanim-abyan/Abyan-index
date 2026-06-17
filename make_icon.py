"""
make_icon.py — Create a 1-click Desktop shortcut for the Streamlit dashboard.

Pure standard-library solution (no pywin32). Uses a temporary VBScript
to create the .lnk file via the Windows Script Host COM object.

Usage:
    python make_icon.py

Creates:
    1. run_dashboard.bat        — in the project folder
    2. رادار الصناديق.lnk   — on the Desktop
"""

import os
import subprocess
import sys
import tempfile

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
BAT_PATH = os.path.join(PROJECT_DIR, "run_dashboard.bat")
SHORTCUT_NAME = "\u0631\u0627\u062f\u0627\u0631 \u0627\u0644\u0635\u0646\u0627\u062f\u064a\u0642.lnk"  # رادار الصناديق.lnk

# Desktop detection (handles OneDrive-redirected desktops)
DESKTOP = os.path.join(os.environ["USERPROFILE"], "Desktop")
if not os.path.isdir(DESKTOP):
    DESKTOP = os.path.join(os.environ.get("OneDrive", ""), "Desktop")
if not os.path.isdir(DESKTOP):
    print("[ERROR] Could not locate the Desktop folder.")
    sys.exit(1)

LNK_PATH = os.path.join(DESKTOP, SHORTCUT_NAME)

# Icon: imageres.dll index 15 — a monitor/graph icon
ICON = os.path.join(os.environ["SystemRoot"], "System32", "imageres.dll") + ",15"


def create_bat():
    """Write the launcher batch file."""
    content = (
        '@echo off\r\n'
        'cd /d "%~dp0"\r\n'
        'python -m streamlit run funds_dashboard.py\r\n'
    )
    with open(BAT_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"  [+] Created: {BAT_PATH}")


def create_shortcut():
    """Generate a temp VBScript, run it to create the .lnk, then clean up."""

    # VBScript that creates the shortcut via WScript.Shell
    # Paths are injected as literals — backslashes doubled for VBS string escaping.
    vbs = (
        'Set ws = CreateObject("WScript.Shell")\r\n'
        f'Set sc = ws.CreateShortcut("{LNK_PATH}")\r\n'
        f'sc.TargetPath = "{BAT_PATH}"\r\n'
        f'sc.WorkingDirectory = "{PROJECT_DIR}"\r\n'
        f'sc.IconLocation = "{ICON}"\r\n'
        'sc.Description = "Launch Mutual Funds Radar Dashboard"\r\n'
        'sc.WindowStyle = 1\r\n'
        'sc.Save\r\n'
    )

    # Write to a temp file, execute, then delete
    fd, vbs_path = tempfile.mkstemp(suffix=".vbs")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(vbs)

        result = subprocess.run(
            ["cscript", "//Nologo", vbs_path],
            capture_output=True,
            text=True,
        )

        if result.returncode != 0:
            print(f"  [x] VBScript error:\n{result.stderr.strip()}")
            sys.exit(1)

        print(f"  [+] Created: {LNK_PATH}")

    finally:
        # Always clean up the temp VBS file
        if os.path.exists(vbs_path):
            os.remove(vbs_path)


def main():
    print()
    print("  Mutual Funds Radar — Desktop Shortcut Creator")
    print("  " + "=" * 48)
    print()

    create_bat()
    create_shortcut()

    print()
    print("  Done! Double-click '\u0631\u0627\u062f\u0627\u0631 \u0627\u0644\u0635\u0646\u0627\u062f\u064a\u0642' on your Desktop.")
    print()


if __name__ == "__main__":
    main()
