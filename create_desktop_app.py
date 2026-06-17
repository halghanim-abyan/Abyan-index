"""
create_desktop_app.py — Create a Windows Desktop shortcut for the Mutual Funds Radar dashboard.

Creates:
  1. launcher.bat   — batch file that launches Streamlit in the project directory
  2. Mutual Funds Radar.lnk — native Windows shortcut on the Desktop

Usage:
    python create_desktop_app.py

Requires:
    pip install pywin32
"""

import os
import sys

# ── Prerequisite check ────────────────────────────────────────────────────────
try:
    import win32com.client
except ImportError:
    print("[ERROR] The 'pywin32' package is required but not installed.")
    print()
    print("  Install it with:")
    print()
    print("      pip install pywin32")
    print()
    sys.exit(1)


# ── Paths ─────────────────────────────────────────────────────────────────────
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LAUNCHER_BAT = os.path.join(PROJECT_DIR, "launcher.bat")
PYTHON_EXE = sys.executable  # full path to the running Python interpreter
DASHBOARD_SCRIPT = "funds_dashboard.py"
SHORTCUT_NAME = "Mutual Funds Radar.lnk"

# Resolve the Desktop path dynamically (works for OneDrive-redirected Desktops too)
DESKTOP_DIR = os.path.join(os.environ["USERPROFILE"], "Desktop")
if not os.path.isdir(DESKTOP_DIR):
    # Fallback: OneDrive Desktop
    onedrive_desktop = os.path.join(os.environ.get("OneDrive", ""), "Desktop")
    if os.path.isdir(onedrive_desktop):
        DESKTOP_DIR = onedrive_desktop
    else:
        print(f"[ERROR] Cannot locate Desktop folder.")
        print(f"        Tried: {DESKTOP_DIR}")
        print(f"        Tried: {onedrive_desktop}")
        sys.exit(1)

SHORTCUT_PATH = os.path.join(DESKTOP_DIR, SHORTCUT_NAME)

# Icon: shell32.dll index 296 is a small chart/statistics icon
ICON_PATH = os.path.join(os.environ["SystemRoot"], "System32", "shell32.dll")
ICON_INDEX = 296


def create_launcher_bat() -> str:
    """Write launcher.bat that starts the Streamlit dashboard."""
    lines = [
        "@echo off",
        f'cd /d "{PROJECT_DIR}"',
        "echo.",
        "echo  ========================================",
        "echo    Mutual Funds Radar — Starting...",
        "echo  ========================================",
        "echo.",
        f'"{PYTHON_EXE}" -m streamlit run {DASHBOARD_SCRIPT}',
        "echo.",
        "echo  ========================================",
        "echo    Streamlit exited.  See any errors above.",
        "echo  ========================================",
        "pause",
    ]

    with open(LAUNCHER_BAT, "w", encoding="utf-8") as f:
        f.write("\r\n".join(lines) + "\r\n")

    return LAUNCHER_BAT


def create_shortcut(target_bat: str) -> str:
    """Create a .lnk shortcut on the Desktop pointing to the launcher bat."""
    shell = win32com.client.Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(SHORTCUT_PATH)

    shortcut.TargetPath = target_bat
    shortcut.WorkingDirectory = PROJECT_DIR
    shortcut.IconLocation = f"{ICON_PATH},{ICON_INDEX}"
    shortcut.Description = "Launch the Saudi Mutual Funds Radar Streamlit dashboard"
    shortcut.WindowStyle = 1  # Normal window

    shortcut.Save()
    return SHORTCUT_PATH


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    print()
    print("  Mutual Funds Radar — Desktop Shortcut Creator")
    print("  " + "=" * 48)
    print()

    # 1. Create launcher.bat
    bat_path = create_launcher_bat()
    print(f"  [+] Launcher created : {bat_path}")

    # 2. Create desktop shortcut
    lnk_path = create_shortcut(bat_path)
    print(f"  [+] Shortcut created : {lnk_path}")

    print()
    print("  Done! Double-click 'Mutual Funds Radar' on your Desktop to launch.")
    print()


if __name__ == "__main__":
    main()
