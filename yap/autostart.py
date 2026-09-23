"""'Start with Windows' via the per-user Startup folder (no admin, no registry)."""
import os

from .config import ROOT

_STARTUP = os.path.join(
    os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs", "Startup"
)
_PATH = os.path.join(_STARTUP, "Yap.vbs")


def enabled():
    return os.path.exists(_PATH)


def set_enabled(on):
    if not on:
        if os.path.exists(_PATH):
            os.remove(_PATH)
        return
    pyw = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
    with open(_PATH, "w", encoding="utf-8") as f:
        f.write(
            'Set sh = CreateObject("WScript.Shell")\n'
            f'sh.CurrentDirectory = "{ROOT}"\n'
            f'sh.Run """{pyw}"" -m yap --hidden", 0, False\n'
        )
