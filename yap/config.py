import json
import os
import copy

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")
DATA_DIR = os.path.join(ROOT, "data")
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
    # "en", "fr", ... or "auto" to detect the language each time.
    "language": "en",
    # GPU model (needs NVIDIA + CUDA). Falls back to cpu_model automatically.
    "gpu_model": "large-v3-turbo",
    "cpu_model": "small.en",
    "cpu_threads": 8,
    # "paste" (clipboard + Ctrl+V, fast, restores clipboard) or "type" (keystrokes).
    "insert_method": "paste",
    "add_trailing_space": False,
    "remove_fillers": True,
    "beep": False,
    "min_recording_seconds": 0.35,
    # Speaking visual: "bars" (soft rounded equalizer, default), "wave", "orb", or "dots".
    "overlay_style": "bars",
    # Words/names Whisper should get right. Also biases recognition.
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


def load():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(DEFAULTS, f, indent=2)
        return copy.deepcopy(DEFAULTS)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return _merge(DEFAULTS, json.load(f))


def save_updates(**updates):
    """Persist top-level settings without discarding custom config entries."""
    os.makedirs(DATA_DIR, exist_ok=True)
    data = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    data.update(copy.deepcopy(updates))

    temporary_path = CONFIG_PATH + ".tmp"
    with open(temporary_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(temporary_path, CONFIG_PATH)
