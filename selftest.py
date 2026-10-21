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
        ("Hi. Meet Tuesday. Scratch that. Meet Friday.", "Hi. Meet Friday."),
        ("can you delete that file for me?", "Can you delete that file for me?"),
        ("I will strike that deal today.", "I will strike that deal today."),
        ("let's meet tomorrow, um.", "Let's meet tomorrow."),
        ("line one new line line two", "Line one\nline two"),
        ("meet on tuesday, no wait, wednesday", "Meet on wednesday"),
        ("by thursday the fourteenth, four and a half percent over", "By thursday the 14th, 4.5% over"),
        ("count one two three four five", "Count one two three four five"),
        ("my pin is four seven two nine", "My pin is 4729"),
        ("it costs twenty dollars and fifty cents", "It costs $20.50"),
        ("grocery list, add apples, bananas, vanilla ice cream, no wait, actually chocolate ice cream, barbecue sauce",
         "Grocery list:\n- Apples\n- Bananas\n- Chocolate ice cream\n- Barbecue sauce"),
    ]
    for src, want in cases:
        got = textproc.clean(src, cfg)
        if got != want:
            raise RuntimeError(f"{src!r} -> {got!r}, expected {want!r}")
    emails = [
        ("Hey Ben, I need you to look at this for me, thanks.",
         "Hey Ben,\n\nI need you to look at this for me.\n\nThanks,"),
        ("Dear Mr. Smith, the invoice is attached. Kind regards, Bailey Long.",
         "Dear Mr. Smith,\n\nThe invoice is attached.\n\nKind regards,\nBailey Long"),
        ("Hey, can you send me the file? Thanks so much!", "Hey,\n\nCan you send me the file?\n\nThanks so much,"),
        ("Sounds good, see you then.", "Sounds good, see you then."),
        ("I wanted to say thank you.", "I wanted to say thank you."),
    ]
    for src, want in emails:
        got = textproc.format_email(src)
        if got != want:
            raise RuntimeError(f"email {src!r} -> {got!r}, expected {want!r}")
    said = "Ask cloud about it."
    edits = [  # (field after paste, field now, user's version of the dictation)
        ("Hi. Ask cloud about it. Bye", "Hi. Ask Claude about it. Bye", "Ask Claude about it."),
        ("Ask cloud about it.", "Ask Claude about it. And more", "Ask Claude about it. And more"),
        ("Hi. Ask cloud about it.", "Hello. Ask Claude about it.", None),  # edit reaches outside it
        ("Hi there", "Hi there!", None),  # dictation not in the field
    ]
    for before, after, want in edits:
        got = textproc.edited_dictation(before, after, said)
        if got != want:
            raise RuntimeError(f"edit {before!r} -> {after!r} gave {got!r}, expected {want!r}")
    undos = [("Scratch that, meet Friday", (True, "meet Friday")), ("Delete that.", (True, "")),
             ("Delete that email from my drafts", (False, "Delete that email from my drafts"))]
    for src, want in undos:
        got = textproc.undo_command(src)
        if got != want:
            raise RuntimeError(f"undo {src!r} -> {got!r}, expected {want!r}")
    return f"{len(cases)} cleanup cases, {len(emails)} email cases, {len(edits)} edit cases, {len(undos)} undo cases"


def _stats():
    from datetime import date

    from yap import stats
    days = {"2026-10-12": [1, 5], "2026-10-19": [2, 10], "2026-10-21": [1, 3], "2026-10-22": [4, 40, 12.0, 30]}
    today = date(2026, 10, 22)  # a Thursday
    daily, weekly = stats.daily(days, 3, today), stats.weekly(days, 2, today)
    want_daily = [(date(2026, 10, 20), 0, 0), (date(2026, 10, 21), 1, 3), (date(2026, 10, 22), 4, 40)]
    want_weekly = [(date(2026, 10, 12), 1, 5), (date(2026, 10, 19), 7, 53)]
    if daily != want_daily or weekly != want_weekly:
        raise RuntimeError(f"daily {daily}, weekly {weekly}")
    wpm = stats.speaking_wpm(days)
    if wpm != 150 or stats.minutes_saved(150, wpm) != 2.75:
        raise RuntimeError(f"speaking {wpm} wpm, saved {stats.minutes_saved(150, wpm)} min")
    return "daily and weekly totals, speaking speed, time saved"


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
check("usage stats", _stats)
check("speech model", _model)

print()
if failures:
    print("FAILED:", ", ".join(failures))
    sys.exit(1)
print("All checks passed.")
