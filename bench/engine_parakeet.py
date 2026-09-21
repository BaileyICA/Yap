"""Run NVIDIA Parakeet (via onnx-asr + ONNX Runtime) on a WAV and print one JSON result line.

Usage (parakeet venv):  python engine_parakeet.py <wav> <model_dir> [--cpu] [--int8]
"""
import glob
import json
import os
import statistics
import sys
import time
import wave

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The CUDA 12 build of ONNX Runtime needs cuBLAS/cuDNN (already in the main venv, reused here to
# avoid a second 1.5 GB download) plus cuda-runtime/cuFFT/cuRAND (installed in this venv).
for venv in (".venv-parakeet", ".venv"):
    for d in glob.glob(os.path.join(ROOT, venv, "Lib", "site-packages", "nvidia", "*", "bin")):
        os.add_dll_directory(d)
        os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")

import numpy as np  # noqa: E402
import onnx_asr  # noqa: E402
import onnxruntime as ort  # noqa: E402


def read_wav(path):
    with wave.open(path, "rb") as w:
        assert w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2, "need 16 kHz mono 16-bit"
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


def text_of(result):
    return (result if isinstance(result, str) else getattr(result, "text", str(result))).strip()


wav, model_dir = sys.argv[1], sys.argv[2]
force_cpu = "--cpu" in sys.argv
quant = "int8" if "--int8" in sys.argv else None
audio = read_wav(wav)

providers = ["CPUExecutionProvider"] if force_cpu else ["CUDAExecutionProvider", "CPUExecutionProvider"]
t = time.time()
model = onnx_asr.load_model("nemo-parakeet-tdt-0.6b-v2", path=model_dir, quantization=quant, providers=providers)
load_s = time.time() - t

# Which provider actually took the encoder? A silent CPU fallback would make the timing meaningless.
device = "cpu"
try:
    sess = model.asr._encoder if hasattr(model, "asr") and hasattr(model.asr, "_encoder") else None
    if sess is not None and "CUDAExecutionProvider" in sess.get_providers():
        device = "cuda"
except Exception:  # noqa: BLE001
    pass
if device == "cpu" and not force_cpu:
    device = "cpu (GPU provider did not load; available: %s)" % ",".join(ort.get_available_providers())

model.recognize(audio)  # warm-up
times, text = [], ""
for _ in range(3):
    t = time.time()
    text = text_of(model.recognize(audio))
    times.append(time.time() - t)

print(json.dumps({
    "engine": "parakeet", "model": "parakeet-tdt-0.6b-v2" + (" int8" if quant else ""), "device": device,
    "load_s": round(load_s, 2), "infer_s": round(statistics.median(times), 3),
    "audio_s": round(len(audio) / 16000, 2), "text": text,
}))
