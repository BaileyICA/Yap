"""Find newer Yap releases on GitHub and swap the packaged exe for the new one."""

import json
import os
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

from .config import FROZEN
from .version import __version__

REPO = "BaileyICA/Yap"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
ASSET_NAME = "Yap-Windows-x64.zip"
EXE_NAME = "Yap.exe"
USER_AGENT = f"Yap/{__version__} (+https://github.com/{REPO})"


def parse_version(text):
    """'v1.10.2' -> (1, 10, 2); anything after the numbers (e.g. '-dev') is ignored."""
    match = re.match(r"v?(\d+(?:\.\d+)*)", (text or "").strip())
    return tuple(int(part) for part in match.group(1).split(".")) if match else ()


def is_newer(latest, current=__version__):
    a, b = parse_version(latest), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


def check(timeout=10):
    """The latest release if it's newer than this copy, else None. Raises on network errors."""
    request = urllib.request.Request(LATEST_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        release = json.load(response)
    tag = release.get("tag_name", "")
    if release.get("draft") or release.get("prerelease") or not is_newer(tag):
        return None
    asset = next((a for a in release.get("assets", []) if a.get("name") == ASSET_NAME), None)
    return {
        "version": tag.lstrip("v"),
        "download_url": asset and asset.get("browser_download_url"),
        "size": asset.get("size", 0) if asset else 0,
        "page": release.get("html_url") or RELEASES_PAGE,
        "notes": release.get("body") or "",
    }


def can_self_update(info):
    """Only the packaged exe can replace itself, and only where it's allowed to write."""
    if not FROZEN or not info or not info.get("download_url"):
        return False
    folder = os.path.dirname(sys.executable)
    try:
        with tempfile.TemporaryFile(dir=folder):
            pass
    except OSError:
        return False  # e.g. Program Files without admin rights
    return True


def download(info, progress=None):
    """Fetch the release ZIP and pull the new exe out of it. Returns the new exe's path."""
    folder = tempfile.mkdtemp(prefix="YapUpdate-")
    zip_path = os.path.join(folder, ASSET_NAME)
    request = urllib.request.Request(info["download_url"], headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=30) as response, open(zip_path, "wb") as out:
        total = int(response.headers.get("Content-Length") or info.get("size") or 0)
        done = 0
        while chunk := response.read(1 << 16):
            out.write(chunk)
            done += len(chunk)
            if progress and total:
                progress(done / total)
    with zipfile.ZipFile(zip_path) as archive:
        if archive.testzip() is not None:
            raise ValueError("the downloaded update is damaged")
        member = next((n for n in archive.namelist() if os.path.basename(n).lower() == EXE_NAME.lower()), None)
        if member is None:
            raise ValueError(f"the update doesn't contain {EXE_NAME}")
        new_exe = os.path.join(folder, EXE_NAME)
        with archive.open(member) as src, open(new_exe, "wb") as dst:
            while chunk := src.read(1 << 20):
                dst.write(chunk)
    os.remove(zip_path)
    return new_exe


def _ps(text):
    return "'" + text.replace("'", "''") + "'"


def launch_installer(new_exe):
    """Start a hidden helper that waits for Yap to quit, swaps in the new exe and starts it.

    The caller must quit Yap straight afterwards: Windows won't let a running exe be overwritten.
    """
    target = sys.executable
    # A one-file exe runs as a bootloader process plus its Python child; both hold the file.
    pids = ",".join(str(pid) for pid in {os.getpid(), os.getppid()})
    script = f"""
$ErrorActionPreference = 'SilentlyContinue'
foreach ($id in @({pids})) {{ Wait-Process -Id $id -Timeout 60 }}
$new = {_ps(new_exe)}
$target = {_ps(target)}
$ok = $false
for ($i = 0; $i -lt 60 -and -not $ok; $i++) {{
    try {{ Copy-Item -LiteralPath $new -Destination $target -Force -ErrorAction Stop; $ok = $true }}
    catch {{ Start-Sleep -Milliseconds 500 }}
}}
Start-Process -FilePath $target
if ($ok) {{ Remove-Item -LiteralPath (Split-Path $new) -Recurse -Force }}
"""
    script_path = os.path.join(os.path.dirname(new_exe), "install-update.ps1")
    with open(script_path, "w", encoding="utf-8-sig") as f:
        f.write(script)
    # Don't let the new Yap inherit this one's PyInstaller unpack folder, which is deleted on exit.
    meipass = getattr(sys, "_MEIPASS", None)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("_PYI", "_MEI")) and not (meipass and meipass in v)}
    env["PYINSTALLER_RESET_ENVIRONMENT"] = "1"
    command = ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
               "-File", script_path]
    flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    try:
        subprocess.Popen(command, env=env, close_fds=True, creationflags=flags | 0x01000000)  # BREAKAWAY_FROM_JOB
    except OSError:
        subprocess.Popen(command, env=env, close_fds=True, creationflags=flags)
