import json
import os
import copy
import sys

FROZEN = getattr(sys, "frozen", False)
ROOT = os.path.dirname(sys.executable if FROZEN else os.path.dirname(os.path.abspath(__file__)))
if FROZEN:
    # Keep settings, recordings, and logs writable without admin rights, even
    # when the executable is launched from Downloads or Program Files.
    APP_DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Yap")
    CONFIG_PATH = os.path.join(APP_DATA_DIR, "config.json")
    DATA_DIR = os.path.join(APP_DATA_DIR, "data")
else:
    APP_DATA_DIR = ROOT
    CONFIG_PATH = os.path.join(ROOT, "config.json")
    DATA_DIR = os.path.join(ROOT, "data")
# Downloaded speech/VAD/speaker models.
MODELS_DIR = DATA_DIR if FROZEN else os.path.join(ROOT, "models")
HISTORY_PATH = os.path.join(DATA_DIR, "history.jsonl")
LOG_PATH = os.path.join(DATA_DIR, "yap.log")

DEFAULTS = {
    # Hold these keys to dictate; release to transcribe and insert.
    "hold_hotkey": "ctrl+windows",
    # Double-tap the hold hotkey to start hands-free dictation; double-tap again to stop.
    "double_tap_hands_free": True,
    "double_tap_window": 0.5,   # max seconds between the two taps
    "tap_max_seconds": 0.3,     # a press shorter than this counts as a tap
    # Extra single-shortcut alternative: press once to start hands-free, again to stop.
    "toggle_hotkey": "ctrl+alt+space",
    "cancel_key": "esc",
    # Microphone name from Settings; "" uses the Windows default input.
    "microphone": "",
    # "en", "fr", ... or "auto" to detect the language each time.
    "language": "en",
    # "parakeet" (NVIDIA Parakeet: fast, accurate English) or "whisper" (multilingual).
    "engine": "parakeet",
    # onnx-asr model name. "nemo-parakeet-tdt-0.6b-v3" adds 24 other European languages.
    "parakeet_model": "nemo-parakeet-tdt-0.6b-v2",
    # Whisper GPU model (needs NVIDIA + CUDA). Falls back to cpu_model automatically.
    "gpu_model": "large-v3-turbo",
    "cpu_model": "small.en",
    "cpu_threads": 8,
    # "paste" (clipboard + Ctrl+V, fast, restores clipboard) or "type" (keystrokes).
    "insert_method": "paste",
    "add_trailing_space": False,
    "remove_fillers": True,
    # Write spoken numbers as digits: "four and a half percent" -> "4.5%", "the fourteenth" -> "the 14th".
    "numbers_as_digits": True,
    # In email apps, lay dictation out as an email: "Hey Ben," / body / "Thanks," on their own lines.
    "email_formatting": True,
    # Where that happens: a program ("outlook.exe") or a word in the window title ("Gmail", for the browser).
    "email_apps": ["outlook.exe", "olk.exe", "hxoutlook.exe", "thunderbird.exe", "mailclient.exe",
                   "superhuman.exe", "Gmail", "Outlook", "Yahoo Mail", "Proton Mail", "Fastmail"],
    "beep": False,
    "min_recording_seconds": 0.35,
    # Days of dictation history to keep: 0 keeps everything, -1 saves none.
    "history_days": 0,
    # Speaking visual: "dog" (runs while you talk, default), "bars", "wave", "orb", or "dots".
    "overlay_style": "dog",
    # Words/names to get right. Near-misses in the transcript are snapped to these
    # spellings (any engine); Whisper also uses them as a recognition hint.
    "vocabulary": ["Wispr Flow", "OpenAI", "Claude", "Anthropic"],
    # Fix consistent mis-hearings: heard -> replacement.
    "replacements": {"open ai": "OpenAI"},
    # Say the phrase, get the expansion. Example: "my email" -> your address.
    "snippets": {},
    # Optional local LLM cleanup via Ollama (https://ollama.com). Free, offline.
    "polish": {
        "enabled": False,
        "url": "http://localhost:11434",
        "model": "llama3.2:3b",
        "style": "Keep my wording and tone; just fix grammar and punctuation.",
        "timeout_seconds": 20,
    },
    "meeting_notes": {
        "use_ollama": True,
        "ollama_url": "http://localhost:11434",
        "ollama_model": "llama3.2:3b",
        "timeout_seconds": 300,
    },
}


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("replacements", "snippets"):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class ConfigError(Exception):
    """config.json can't be used; the message says where the problem is."""


def _read():
    try:
        with open(CONFIG_PATH, encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"{CONFIG_PATH} has a mistake on line {e.lineno}, column {e.colno}: {e.msg}.") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{CONFIG_PATH} must hold a JSON object ({{ ... }}).")
    return data


def load():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULTS, f, indent=2)
        return copy.deepcopy(DEFAULTS)
    return _merge(DEFAULTS, _read())


def save_updates(**updates):
    """Persist top-level settings without discarding custom config entries."""
    os.makedirs(DATA_DIR, exist_ok=True)
    data = _read() if os.path.exists(CONFIG_PATH) else {}
    data.update(copy.deepcopy(updates))

    temporary_path = CONFIG_PATH + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(temporary_path, CONFIG_PATH)
