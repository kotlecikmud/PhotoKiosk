"""Launches the kiosk web UI in a full-screen browser (Chromium/Chrome/Edge, else Firefox).

The kiosk UI is an ordinary web page served by the backend, so any modern browser works.
Set ``KIOSK_BROWSER`` to a browser executable to override auto-detection.
"""
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

CHROMIUM_NAMES = ["chromium", "chromium-browser", "google-chrome", "google-chrome-stable",
                  "microsoft-edge", "microsoft-edge-stable", "msedge", "chrome"]
WINDOWS_PATHS = [
    r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe",
    r"%ProgramFiles%\Google\Chrome\Application\chrome.exe",
    r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe",
    r"%LocalAppData%\Google\Chrome\Application\chrome.exe",
]


def find_browser() -> Optional[str]:
    """Path of the browser to use, or None if none is installed."""
    override = os.environ.get("KIOSK_BROWSER")
    if override:
        return shutil.which(override) or (override if Path(override).exists() else None)
    for name in CHROMIUM_NAMES:
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        for raw in WINDOWS_PATHS:
            path = os.path.expandvars(raw)
            if Path(path).exists():
                return path
    return shutil.which("firefox")


def default_profile_dir() -> str:
    """Browser profile folder (keeps the customer's language choice between restarts).

    It lives in the user's home, as a *non-hidden* folder: snap-packaged Chromium - the only
    kind Ubuntu 24.04 ships - may not use hidden folders or paths outside $HOME.
    Override with ``KIOSK_BROWSER_PROFILE``.
    """
    return os.environ.get("KIOSK_BROWSER_PROFILE") or str(Path.home() / "photokiosk-browser")


def build_command(exe: str, url: str, fullscreen: bool = True, profile_dir: Optional[str] = None) -> List[str]:
    """Command line for ``exe``; touch-friendly, no first-run/translate/crash prompts."""
    name = Path(exe).name.lower()
    if "firefox" in name:
        return [exe, "--kiosk", url] if fullscreen else [exe, url]

    cmd = [
        exe,
        f"--user-data-dir={Path(profile_dir or default_profile_dir()).resolve()}",
        "--no-first-run", "--no-default-browser-check", "--noerrdialogs", "--disable-infobars",
        "--disable-translate", "--disable-session-crashed-bubble", "--disable-pinch",
        "--overscroll-history-navigation=0", "--password-store=basic",
        "--check-for-update-interval=31536000",
    ]
    if fullscreen:
        cmd.append("--kiosk")
        if "msedge" in name or "edge" in name:
            cmd.append("--edge-kiosk-type=fullscreen")
        cmd.append(url)
    else:
        cmd.append(f"--app={url}")
    return cmd


def wait_for_server(url: str, timeout: float = 60.0) -> bool:
    """Polls ``url`` until the backend answers (the browser must not open a dead page)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as res:
                if res.status < 500:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    return False


def launch(url: str, fullscreen: bool = True) -> Optional[subprocess.Popen]:
    """Starts the browser; returns its process, or None if no browser is available."""
    exe = find_browser()
    if not exe:
        print("No supported browser found (Chromium, Chrome, Edge or Firefox). "
              f"Open {url} manually, or set KIOSK_BROWSER.")
        return None
    profile = default_profile_dir()
    Path(profile).mkdir(parents=True, exist_ok=True)
    return subprocess.Popen(build_command(exe, url, fullscreen, profile))
