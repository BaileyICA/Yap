"""Daily usage totals (data/stats.json): dictations and words per day, kept even when history is off or pruned."""
import json
import os
import threading
import time
from datetime import date, timedelta

from . import config

PATH = os.path.join(config.DATA_DIR, "stats.json")
lock = threading.Lock()
TYPING_WPM = 40  # an average typist; time saved compares typing the same words at this speed


def record(text, seconds):
    """Count one dictation and how long its recording was."""
    with lock:
        days = _load()
        day = days.setdefault(time.strftime("%Y-%m-%d"), [0, 0])
        day.extend([0, 0][len(day) - 2:])  # days seeded from history have no timing yet
        words = len(text.split())
        day[0] += 1
        day[1] += words
        day[2] = round(day[2] + seconds, 1)
        day[3] += words
        temporary = PATH + ".tmp"
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(days, f)
        os.replace(temporary, PATH)


def load():
    """{"YYYY-MM-DD": [dictations, words, seconds recorded, words in those timed dictations]}."""
    with lock:
        return _load()


def _load():
    try:
        with open(PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return _from_history()  # first run after upgrading: seed from whatever history is still on disk
    except (OSError, ValueError):
        return {}


def _from_history():
    days = {}
    try:
        with open(config.HISTORY_PATH, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                    day = days.setdefault(entry["t"][:10], [0, 0])
                except (ValueError, KeyError, TypeError):
                    continue
                day[0] += 1
                day[1] += len(entry.get("text", "").split())
    except OSError:
        pass
    return days


def daily(days, count=14, today=None):
    """[(date, dictations, words)] for the last `count` days, oldest first."""
    today = today or date.today()
    out = []
    for back in range(count - 1, -1, -1):
        day = today - timedelta(days=back)
        uses, words = days.get(day.isoformat(), (0, 0))[:2]
        out.append((day, uses, words))
    return out


def weekly(days, count=8, today=None):
    """[(monday, dictations, words)] for the last `count` Monday-to-Sunday weeks, oldest first."""
    today = today or date.today()
    monday = today - timedelta(days=today.weekday())
    out = []
    for back in range(count - 1, -1, -1):
        start = monday - timedelta(weeks=back)
        week = [days.get((start + timedelta(days=i)).isoformat(), (0, 0)) for i in range(7)]
        out.append((start, sum(d[0] for d in week), sum(d[1] for d in week)))
    return out


def speaking_wpm(days):
    """Words per minute while dictating, from the timed dictations (None until there is one)."""
    timed = [d for d in days.values() if len(d) > 3]
    seconds = sum(d[2] for d in timed)
    return sum(d[3] for d in timed) * 60 / seconds if seconds else None


def minutes_saved(words, wpm):
    """Minutes saved by speaking `words` at `wpm` instead of typing them."""
    return max(0.0, words / TYPING_WPM - words / wpm)
