import time

import keyboard
import pyperclip

from . import clipboard

_MODIFIERS = ("ctrl", "alt", "shift", "windows")


def wait_for_modifiers_release(timeout=2.0):
    """The hotkey's modifiers are usually still physically held when we finish.

    Injecting Ctrl+V while Win is down produces Ctrl+Win+V, which is not paste --
    so hold off until the user's fingers are actually off the keys.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if not any(keyboard.is_pressed(m) for m in _MODIFIERS):
                return True
        except Exception:  # noqa: BLE001
            return False
        time.sleep(0.02)
    return False


def erase(count):
    """Backspace over the last `count` characters typed or pasted."""
    wait_for_modifiers_release()  # Ctrl+Backspace would delete whole words
    for _ in range(count):
        keyboard.send("backspace")
        time.sleep(0.002)


def insert(text, method="paste"):
    if not text:
        return
    wait_for_modifiers_release()
    if method == "type":
        keyboard.write(text, delay=0)
        return
    try:
        old = clipboard.save()  # every format, so a copied image or formatted text survives
    except Exception:  # noqa: BLE001
        old = None
    pyperclip.copy(text)
    time.sleep(0.03)
    keyboard.send("ctrl+v")
    time.sleep(0.25)  # let the target app read the clipboard before we restore it
    try:
        clipboard.restore(old)
    except Exception:  # noqa: BLE001
        pass
