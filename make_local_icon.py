"""
make_local_icon.py — Create a local 1-click shortcut for the Streamlit dashboard.

Places everything in the project folder itself (no Desktop writes,
no OneDrive conflicts, no special permissions needed).

Zero external dependencies — uses a temporary VBScript + cscript.exe.

Usage:
    python make_local_icon.py

Creates (in the same folder as this script):
    run_dashboard.bat
    تشغيل الرادار.lnk
"""

import os
import subprocess
import sys
import tempfile

# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
BAT_PATH = os.path.join(PROJECT_DIR, "run_dashboard.bat")
LNK_PATH = os.path.join(PROJECT_DIR, "\u062a\u0634\u063a\u064a\u0644 \u0627\u0644\u0631\u0627\u062f\u0627\u0631.lnk")
ICON = os.path.join(os.environ["SystemRoot"], "System32", "imageres.dll") + ",15"


def create_bat():
    with open(BAT_PATH, "w", encoding="utf-8") as f:
        f.write('@echo off\r\ncd /d "%~dp0"\r\npython -m streamlit run funds_dashboard.py\r\n')
    print(f"  [+] {BAT_PATH}")


def create_shortcut():
    vbs = (
        'Set ws = CreateObject("WScript.Shell")\r\n'
        f'Set sc = ws.CreateShortcut("{LNK_PATH}")\r\n'
        f'sc.TargetPath = "{BAT_PATH}"\r\n'
        f'sc.WorkingDirectory = "{PROJECT_DIR}"\r\n'
        f'sc.IconLocation = "{ICON}"\r\n'
        'sc.Description = "Launch Mutual Funds Radar"\r\n'
        'sc.WindowStyle = 1\r\n'
        'sc.Save\r\n'
    )

    fd, vbs_path = tempfile.mkstemp(suffix=".vbs")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(vbs)

        result = subprocess.run(
            ["cscript", "//Nologo", vbs_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"  [x] VBScript error:\n{result.stderr.strip()}")
            sys.exit(1)

        print(f"  [+] {LNK_PATH}")
    finally:
        if os.path.exists(vbs_path):
            os.remove(vbs_path)


def main():
    print()
    print("  Mutual Funds Radar — Local Shortcut Creator")
    print("  " + "=" * 46)
    print()
    create_bat()
    create_shortcut()
    print()
    print("  Done! Double-click '\u062a\u0634\u063a\u064a\u0644 \u0627\u0644\u0631\u0627\u062f\u0627\u0631' in this folder to launch.")
    print()


if __name__ == "__main__":
    main()
