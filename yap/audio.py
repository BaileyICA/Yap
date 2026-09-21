import threading

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000


class Recorder:
    def __init__(self, on_level=None):
        self._chunks = []
        self._stream = None
        self._lock = threading.Lock()
        self.on_level = on_level

    def _cb(self, indata, frames, time_info, status):
        chunk = indata[:, 0].copy()
        with self._lock:
            self._chunks.append(chunk)
        if self.on_level:
            self.on_level(float(np.sqrt(np.mean(chunk ** 2))))

    def start(self):
        self._chunks = []
        self._stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="float32", callback=self._cb)
        self._stream.start()

    def stop(self):
        if self._stream is None:
            return np.zeros(0, dtype=np.float32)
        self._stream.stop()
        self._stream.close()
        self._stream = None
        with self._lock:
            audio = np.concatenate(self._chunks) if self._chunks else np.zeros(0, dtype=np.float32)
        return audio
