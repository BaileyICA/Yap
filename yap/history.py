"""Dictation history (data/history.jsonl): append, read the newest entries, and prune by age."""
import json
import os
import threading
import time

from . import config

lock = threading.Lock()
# Settings label -> history_days. 0 keeps everything; -1 saves nothing.
KEEP = {"Forever": 0, "1 year": 365, "90 days": 90, "30 days": 30, "7 days": 7, "Don't save": -1}
_STAMP = "%Y-%m-%d %H:%M:%S"
_last_prune = 0.0


def append(raw, text, days):
    global _last_prune
    if days < 0:
        return
    with lock, open(config.HISTORY_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": time.strftime(_STAMP), "raw": raw, "text": text}) + "\n")
    if days and time.time() - _last_prune > 86400:
        prune(days)


def recent(limit=100):
    """The newest `limit` entries, newest first, reading only as much of the end of the file as needed."""
    try:
        f = open(config.HISTORY_PATH, "rb")
    except OSError:
        return []
    with lock, f:
        f.seek(0, os.SEEK_END)
        pos, data = f.tell(), b""
        while pos and data.count(b"\n") <= limit:
            step = min(pos, 64 * 1024)
            pos -= step
            f.seek(pos)
            data = f.read(step) + data
    lines = data.decode("utf-8", errors="replace").splitlines()
    if pos:
        lines = lines[1:]  # the first line may be cut in half
    items = []
    for line in reversed(lines):
        if len(items) == limit:
            break
        try:
            items.append(json.loads(line))
        except ValueError:
            pass
    return items


def _cutoff(days):
    return time.strftime(_STAMP, time.localtime(time.time() - days * 86400))


def count_older(days):
    """How many entries prune(days) would remove."""
    if days == 0:
        return 0
    cutoff = _cutoff(days) if days > 0 else None
    with lock:
        try:
            with open(config.HISTORY_PATH, encoding="utf-8") as f:
                return sum(1 for line in f if line.strip() and (cutoff is None or _stamp(line) < cutoff))
        except OSError:
            return 0


def _stamp(line):
    try:
        return json.loads(line).get("t", "")
    except ValueError:
        return ""


def prune(days):
    """Drop entries older than `days` (all of them when days < 0). Returns how many were removed."""
    global _last_prune
    _last_prune = time.time()
    if days == 0:
        return 0
    if days < 0:
        return clear()
    cutoff = _cutoff(days)
    with lock:
        try:
            with open(config.HISTORY_PATH, encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return 0
        kept = [line for line in lines if line.strip() and _stamp(line) >= cutoff]
        if len(kept) == len(lines):
            return 0
        _write(kept)
    return len(lines) - len(kept)


def clear():
    with lock:
        try:
            with open(config.HISTORY_PATH, encoding="utf-8") as f:
                removed = sum(1 for line in f if line.strip())
            os.remove(config.HISTORY_PATH)
        except OSError:
            return 0
    return removed


def rewrite(item, text):
    """Replace one dictation's text, keeping what the model originally heard."""
    with lock:
        with open(config.HISTORY_PATH, encoding="utf-8") as f:
            lines = f.readlines()
        for index in range(len(lines) - 1, -1, -1):
            try:
                entry = json.loads(lines[index])
            except ValueError:
                continue
            if entry.get("t") == item.get("t") and entry.get("text") == item.get("text"):
                entry["text"] = text
                entry["fixed"] = True
                lines[index] = json.dumps(entry) + "\n"
                break
        else:
            raise ValueError("That dictation is no longer in the history file.")
        _write(lines)


def _write(lines):
    temporary = config.HISTORY_PATH + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        f.writelines(lines)
    os.replace(temporary, config.HISTORY_PATH)
