"""'Start with Windows' via the per-user Startup folder (no admin, no registry)."""
import os
import sys

from .config import FROZEN, ROOT

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
    if FROZEN:
        executable = sys.executable
        command = f'"{executable}" --hidden'
    else:
        executable = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")
        command = f'"{executable}" -m yap --hidden'
    vbs_command = command.replace('"', '""')
    with open(_PATH, "w", encoding="utf-8") as f:
        f.write(
            'Set sh = CreateObject("WScript.Shell")\n'
            f'sh.CurrentDirectory = "{ROOT}"\n'
            f'sh.Run "{vbs_command}", 0, False\n'
        )
