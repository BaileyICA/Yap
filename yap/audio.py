import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
# Windows' catch-all entries: "the default device", which is what an empty setting already means.
_ALIASES = {"Microsoft Sound Mapper - Input", "Primary Sound Capture Driver"}


def _default_api():
    """Host API of the default input (MME on Windows), which resamples to 16 kHz for us."""
    try:
        return sd.query_devices(kind="input")["hostapi"]
    except (sd.PortAudioError, ValueError):
        return 0


def _inputs():
    """[(index, short name, full name)] for the default host API's microphones.

    MME cuts names at 31 characters ("Microphone Array (Realtek(R) Au"), so the full name
    comes from another host API listing the same device.
    """
    devices = sd.query_devices()
    api = _default_api()
    full_names = [d["name"] for d in devices if d["max_input_channels"] > 0 and d["hostapi"] != api]
    out = []
    for index, d in enumerate(devices):
        if d["hostapi"] != api or d["max_input_channels"] <= 0 or d["name"] in _ALIASES:
            continue
        full = next((n for n in full_names if n.startswith(d["name"]) and "\n" not in n), d["name"])
        out.append((index, d["name"], full))
    return out


def microphones():
    """Names to offer in Settings, as full device names."""
    return [full for _i, _short, full in _inputs()]


def refresh():
    """Re-scan audio devices so a mic plugged in after launch shows up. Only call with no stream open."""
    sd._terminate()
    sd._initialize()


def device_index(name):
    """The device to open for a saved microphone name; None means the Windows default."""
    if not name:
        return None
    for index, short, full in _inputs():
        if name in (full, short) or name.startswith(short):
            return index
    raise LookupError(f"Microphone not found: {name}")


def open_input(name, callback, log=None):
    """Start a 16 kHz mono input stream on the chosen microphone, or the default if it's gone."""
    try:
        device = device_index(name)
    except LookupError as e:
        if log:
            log(f"{e}; using the Windows default microphone")
        device = None
    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=callback, device=device)
    stream.start()
    return stream


class Recorder:
    def __init__(self, on_level=None, log=None):
        self._chunks = []
        self._stream = None
        self._lock = threading.Lock()
        self.on_level = on_level
        self.log = log
        self.microphone = ""  # saved device name; "" = Windows default

    def _cb(self, indata, frames, time_info, status):
        chunk = indata[:, 0].copy()
        with self._lock:
            self._chunks.append(chunk)
        if self.on_level:
            self.on_level(float(np.sqrt(np.mean(chunk ** 2))))

    def start(self):
        self._chunks = []
        self._stream = open_input(self.microphone, self._cb, self.log)

    def stop(self):
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
        return audio
