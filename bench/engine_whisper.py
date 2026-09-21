"""Run one Whisper model on a WAV and print a single JSON result line.

Uses Yap's own Transcriber, so the result is exactly what the app would produce
(same VAD, same vocabulary prompt, same GPU-then-CPU fallback).

Usage (main venv):  python engine_whisper.py <wav> <model>
"""
import json
import os
import statistics
import sys
import time
import wave

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import numpy as np  # noqa: E402

from yap import config  # noqa: E402
from yap.transcribe import Transcriber  # noqa: E402


def read_wav(path):
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "need 16 kHz mono 16-bit"
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


wav, model = sys.argv[1], sys.argv[2]
audio = read_wav(wav)

cfg = config.load()
cfg["gpu_model"] = model
tr = Transcriber(cfg, log=lambda m: print(m, file=sys.stderr))
t = time.time()
tr.load()
load_s = time.time() - t

tr.transcribe(audio)  # warm-up
times, text = [], ""
for _ in range(3):
    t = time.time()
    text = tr.transcribe(audio)
    times.append(time.time() - t)

print(json.dumps({
    "engine": "whisper", "model": model, "device": tr.device,
    "load_s": round(load_s, 2), "infer_s": round(statistics.median(times), 3),
    "audio_s": round(len(audio) / 16000, 2), "text": text,
}))
