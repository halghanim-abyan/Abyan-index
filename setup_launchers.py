"""
setup_launchers.py — Create Windows Desktop Shortcuts for Streamlit dashboards.

Generates for each dashboard:
  1. A .bat file  — runs `python -m streamlit run <script>` with the correct cwd
  2. A .vbs file  — executes the .bat silently (no visible CMD window)
  3. A .lnk file  — Windows Desktop Shortcut pointing to the .vbs

The .lnk shortcuts are placed on the user's Desktop.
The .bat and .vbs files are stored in a `launchers/` subfolder.

Usage:
    python setup_launchers.py           # create all shortcuts
    python setup_launchers.py --remove  # remove shortcuts and launcher files

Requirements: Python only (uses PowerShell for .lnk creation — no pip dependencies).
"""

import os
import subprocess
import sys
import textwrap

# ── Configuration ───────────────────────────────────────────────────────────

# Project root = directory containing this script
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
LAUNCHERS_DIR = os.path.join(PROJECT_DIR, "launchers")
DESKTOP_DIR = os.path.join(os.path.expanduser("~"), "Desktop")
PYTHON_EXE = sys.executable

DASHBOARDS = [
    {
        "name": "Inflation Index",
        "script": "dashboard.py",
        "port": 8501,
        "icon_idx": 0,   # shell32.dll icon index (chart icon)
    },
    {
        "name": "Mutual Funds Radar",
        "script": "funds_dashboard.py",
        "port": 8502,
        "icon_idx": 0,
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# FILE GENERATORS
# ══════════════════════════════════════════════════════════════════════════════

def create_bat(dashboard: dict) -> str:
    """
    Create a .bat file that:
      1. Changes to the project directory (so SQLite paths resolve)
      2. Launches Streamlit on a dedicated port
      3. Opens the browser automatically
    Returns the full path to the .bat file.
    """
    bat_name = dashboard["name"].lower().replace(" ", "_") + ".bat"
    bat_path = os.path.join(LAUNCHERS_DIR, bat_name)

    # Use python -m streamlit (streamlit CLI is not always in PATH)
    content = textwrap.dedent(f"""\
        @echo off
        cd /d "{PROJECT_DIR}"
        "{PYTHON_EXE}" -m streamlit run "{dashboard['script']}" ^
            --server.port {dashboard['port']} ^
            --server.headless false ^
            --browser.gatherUsageStats false
    """)

    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(content)

    return bat_path


def create_vbs(dashboard: dict, bat_path: str) -> str:
    """
    Create a .vbs wrapper that runs the .bat file with WindowStyle = 0
    (completely hidden — no black CMD window).
    Returns the full path to the .vbs file.
    """
    vbs_name = dashboard["name"].lower().replace(" ", "_") + ".vbs"
    vbs_path = os.path.join(LAUNCHERS_DIR, vbs_name)

    # WindowStyle: 0 = hidden, 1 = normal, 7 = minimized
    # The second argument (True/False) = whether to wait for completion
    # VBS requires doubled quotes inside strings — build line by line
    dq = chr(34)  # double-quote character
    lines = [
        'Set WshShell = CreateObject("WScript.Shell")',
        f'WshShell.CurrentDirectory = "{PROJECT_DIR}"',
        f'WshShell.Run {dq}{dq}{dq}{bat_path}{dq}{dq}{dq}, 0, False',
        'Set WshShell = Nothing',
    ]
    content = "\r\n".join(lines) + "\r\n"

    with open(vbs_path, "w", encoding="utf-8") as f:
        f.write(content)

    return vbs_path


def create_lnk(dashboard: dict, vbs_path: str) -> str:
    """
    Create a .lnk shortcut on the Desktop using PowerShell.
    No external Python dependencies required.
    Returns the full path to the .lnk file.
    """
    lnk_name = dashboard["name"] + ".lnk"
    lnk_path = os.path.join(DESKTOP_DIR, lnk_name)

    # PowerShell script to create the shortcut
    # Use single quotes in PS for paths to avoid escaping issues
    ps_script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{lnk_path}'); "
        "$sc.TargetPath = 'wscript.exe'; "
        f"$sc.Arguments = '\"\"\"' + '{vbs_path}' + '\"\"\"'; "
        f"$sc.WorkingDirectory = '{PROJECT_DIR}'; "
        f"$sc.Description = '{dashboard['name']} Dashboard'; "
        f"$sc.IconLocation = 'shell32.dll,{dashboard['icon_idx']}'; "
        "$sc.Save()"
    )

    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"  ERROR creating shortcut: {result.stderr.strip()}")
        return ""

    return lnk_path


# ══════════════════════════════════════════════════════════════════════════════
# STOP HELPER
# ══════════════════════════════════════════════════════════════════════════════

def create_stop_bat() -> str:
    """Create a stop_all.bat that kills all Streamlit processes."""
    stop_path = os.path.join(LAUNCHERS_DIR, "stop_all.bat")
    content = textwrap.dedent("""\
        @echo off
        echo Stopping all Streamlit dashboards...
        taskkill /F /IM "python.exe" /FI "WINDOWTITLE eq *streamlit*" >nul 2>&1
        taskkill /F /IM "python3.13.exe" /FI "WINDOWTITLE eq *streamlit*" >nul 2>&1
        REM Fallback: kill by port
        for %%p in (8501 8502 8504) do (
            for /f "tokens=5" %%a in ('netstat -ano ^| findstr :%%p ^| findstr LISTENING') do (
                taskkill /F /PID %%a >nul 2>&1
            )
        )
        echo Done.
        timeout /t 2 >nul
    """)
    with open(stop_path, "w", encoding="utf-8") as f:
        f.write(content)
    return stop_path


def create_stop_shortcut(stop_bat_path: str) -> str:
    """Create a desktop shortcut for the stop script."""
    lnk_path = os.path.join(DESKTOP_DIR, "Stop All Dashboards.lnk")
    ps_script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{lnk_path}'); "
        f"$sc.TargetPath = '{stop_bat_path}'; "
        f"$sc.WorkingDirectory = '{PROJECT_DIR}'; "
        "$sc.Description = 'Stop all Streamlit dashboard servers'; "
        "$sc.IconLocation = 'shell32.dll,131'; "
        "$sc.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True, text=True,
    )
    return lnk_path


# ══════════════════════════════════════════════════════════════════════════════
# REMOVE
# ══════════════════════════════════════════════════════════════════════════════

def remove_all():
    """Remove all generated launcher files and desktop shortcuts."""
    removed = 0

    # Desktop shortcuts
    for dashboard in DASHBOARDS:
        lnk = os.path.join(DESKTOP_DIR, dashboard["name"] + ".lnk")
        if os.path.isfile(lnk):
            os.remove(lnk)
            print(f"  Removed: {lnk}")
            removed += 1

    stop_lnk = os.path.join(DESKTOP_DIR, "Stop All Dashboards.lnk")
    if os.path.isfile(stop_lnk):
        os.remove(stop_lnk)
        print(f"  Removed: {stop_lnk}")
        removed += 1

    # Launchers directory
    if os.path.isdir(LAUNCHERS_DIR):
        for f in os.listdir(LAUNCHERS_DIR):
            fp = os.path.join(LAUNCHERS_DIR, f)
            os.remove(fp)
            print(f"  Removed: {fp}")
            removed += 1
        os.rmdir(LAUNCHERS_DIR)
        print(f"  Removed: {LAUNCHERS_DIR}/")

    print(f"\nCleaned up {removed} files.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    if "--remove" in sys.argv:
        print("=== Removing launcher files and desktop shortcuts ===\n")
        remove_all()
        return

    print("=" * 60)
    print("  Desktop Launcher Setup for Streamlit Dashboards")
    print("=" * 60)
    print(f"  Project dir : {PROJECT_DIR}")
    print(f"  Python      : {PYTHON_EXE}")
    print(f"  Desktop     : {DESKTOP_DIR}")
    print(f"  Launchers   : {LAUNCHERS_DIR}")
    print()

    # Create launchers directory
    os.makedirs(LAUNCHERS_DIR, exist_ok=True)

    # Generate files for each dashboard
    for dashboard in DASHBOARDS:
        name = dashboard["name"]
        print(f"[{name}]")

        bat_path = create_bat(dashboard)
        print(f"  .bat : {bat_path}")

        vbs_path = create_vbs(dashboard, bat_path)
        print(f"  .vbs : {vbs_path}")

        lnk_path = create_lnk(dashboard, vbs_path)
        if lnk_path:
            print(f"  .lnk : {lnk_path}")
        print()

    # Stop-all shortcut
    print("[Stop All Dashboards]")
    stop_bat = create_stop_bat()
    print(f"  .bat : {stop_bat}")
    stop_lnk = create_stop_shortcut(stop_bat)
    print(f"  .lnk : {stop_lnk}")

    print()
    print("=" * 60)
    print(f"  Setup complete! You now have {len(DASHBOARDS) + 1} shortcuts on your Desktop:")
    print()
    for d in DASHBOARDS:
        print(f"    {d['name']:<26}  (port {d['port']})")
    print(f"    {'Stop All Dashboards':<26}  (kills all servers)")
    print()
    print("  To remove everything: python setup_launchers.py --remove")
    print("=" * 60)


if __name__ == "__main__":
    main()
