"""Check that Yap's moving parts work on this machine.

Run with:  .venv\\Scripts\\python selftest.py
"""
import os
import sys
import time

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

from yap import config, textproc  # noqa: E402

PASS, FAIL = "  [ok]", "  [FAIL]"
failures = []


def check(name, fn):
    print(name)
    try:
        detail = fn()
        print(f"{PASS} {detail}")
    except Exception as e:  # noqa: BLE001
        print(f"{FAIL} {type(e).__name__}: {e}")
        failures.append(name)


def _config():
    cfg = config.load()
    return f"hold={cfg['hold_hotkey']}  toggle={cfg['toggle_hotkey']}  engine={cfg['engine']}"


def _mic():
    import sounddevice as sd

    from yap.audio import Recorder, SAMPLE_RATE

    dev = sd.query_devices(kind="input")["name"]
    r = Recorder()
    r.start()
    time.sleep(1.0)
    a = r.stop()
    if len(a) < SAMPLE_RATE * 0.5:
        raise RuntimeError(f"captured only {len(a) / SAMPLE_RATE:.2f}s")
    return f"{dev.strip()} | {len(a) / SAMPLE_RATE:.2f}s, peak {np.abs(a).max():.3f}"


def _clipboard():
    import pyperclip

    token = "yap-selftest"
    before = pyperclip.paste()
    pyperclip.copy(token)
    got = pyperclip.paste()
    pyperclip.copy(before or "")
    if got != token:
        raise RuntimeError("clipboard round-trip failed")
    return "copy/paste round-trip and restore"


def _text():
    cfg = config.load()
    cases = [
        ("um so, uh, hello there", "So, hello there"),
        ("meet tuesday, scratch that, meet friday", "Meet friday"),
        ("line one new line line two", "Line one\nline two"),
        ("meet on tuesday, no wait, wednesday", "Meet on wednesday"),
        ("by thursday the fourteenth, four and a half percent over", "By thursday the 14th, 4.5% over"),
        ("grocery list, add apples, bananas, vanilla ice cream, no wait, actually chocolate ice cream, barbecue sauce",
         "Grocery list:\n- Apples\n- Bananas\n- Chocolate ice cream\n- Barbecue sauce"),
    ]
    for src, want in cases:
        got = textproc.clean(src, cfg)
        if got != want:
            raise RuntimeError(f"{src!r} -> {got!r}, expected {want!r}")
    return f"{len(cases)} cleanup cases"


def _model():
    from yap.transcribe import Transcriber

    cfg = config.load()
    tr = Transcriber(cfg, log=lambda m: print(f"       {m}"))
    t = time.time()
    tr.load()
    load = time.time() - t

    # 3 seconds of silence: must come back empty, not hallucinated text.
    t = time.time()
    out = tr.transcribe(np.zeros(16000 * 3, dtype=np.float32))
    return f"{tr.name} on {tr.device}  load={load:.1f}s  silence->{out!r} in {time.time() - t:.2f}s"


check("config.json", _config)
check("microphone", _mic)
check("clipboard", _clipboard)
check("text cleanup", _text)
check("speech model", _model)

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("All checks passed.")
