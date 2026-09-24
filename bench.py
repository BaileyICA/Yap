"""Compare speech models on YOUR voice: Whisper (turbo + large-v3) vs NVIDIA Parakeet.

Run:   .venv\\Scripts\\python bench.py                 record 15 s, run every engine, score vs the paragraph
       .venv\\Scripts\\python bench.py --reuse         re-run on the last recording (no need to speak again)
       .venv\\Scripts\\python bench.py --wav my.wav    use an existing 16 kHz mono WAV
       .venv\\Scripts\\python bench.py --engines whisper:large-v3,parakeet
       .venv\\Scripts\\python bench.py --ref "your own reference text"

Scoring is word error rate (WER) against the reference text: lower is better, 0% is perfect.
Each engine runs in its own process (Whisper and Parakeet use different Python environments).
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import wave

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
BENCH_DIR = os.path.join(ROOT, "data", "bench")
LAST_WAV = os.path.join(BENCH_DIR, "last.wav")
RESULTS = os.path.join(BENCH_DIR, "results.json")

PARAGRAPH = (
    "Hey Bailey, can you send the Graymont report to Sarah by Thursday the 14th? "
    "The budget came in at 4.5 percent over, so ask Teys about the Wondai site "
    "before we finalise it with Laminex."
)
PY_WHISPER = os.path.join(ROOT, ".venv", "Scripts", "python.exe")
PY_PARAKEET = os.path.join(ROOT, ".venv-parakeet", "Scripts", "python.exe")
if not os.path.exists(PY_PARAKEET):
    PY_PARAKEET = PY_WHISPER  # the main venv has onnx-asr now that Yap uses Parakeet
PARAKEET_DIR = os.path.join(ROOT, "models", "parakeet-tdt-0.6b-v2")
DEFAULT_ENGINES = "whisper:large-v3-turbo,whisper:large-v3,parakeet"


# ---------- scoring ----------
_SPELLING = {"finalise": "finalize", "organise": "organize", "recognise": "recognize"}


def normalize(text):
    t = text.lower().replace("%", " percent ")
    t = re.sub(r"(\d)(st|nd|rd|th)\b", r"\1", t)          # 14th -> 14
    t = re.sub(r"(?<!\d)[.,](?!\d)|[^\w.\s]|_", " ", t)  # punctuation, but keep the point in 4.5
    words = [w.strip(".") for w in t.split()]
    return [_SPELLING.get(w, w) for w in words if w.strip(".")]


def align(ref, hyp):
    """Word-level edit distance with backtrace. Returns (errors, list of human-readable diffs)."""
    n, m = len(ref), len(hyp)
    d = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1):
        d[i][0] = i
    for j in range(m + 1):
        d[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]))
    diffs, i, j = [], n, m
    while i > 0 or j > 0:
        if i > 0 and j > 0 and d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):
            if ref[i - 1] != hyp[j - 1]:
                diffs.append(f'"{hyp[j - 1]}" instead of "{ref[i - 1]}"')
            i, j = i - 1, j - 1
        elif i > 0 and d[i][j] == d[i - 1][j] + 1:
            diffs.append(f'missed "{ref[i - 1]}"')
            i -= 1
        else:
            diffs.append(f'extra "{hyp[j - 1]}"')
            j -= 1
    return d[n][m], diffs[::-1]


# ---------- audio ----------
def record(seconds):
    from yap.audio import Recorder, SAMPLE_RATE
    import numpy as np

    print("\nRead this out loud when it says SPEAK NOW:\n")
    print(f'    "{PARAGRAPH}"\n')
    print("Starting in ", end="", flush=True)
    for n in (3, 2, 1):
        print(f"{n} ", end="", flush=True)
        time.sleep(1)
    print("\n>>> SPEAK NOW <<<", flush=True)
    rec = Recorder()
    rec.start()
    time.sleep(seconds)
    audio = rec.stop()
    print("Got it.\n")
    os.makedirs(BENCH_DIR, exist_ok=True)
    with wave.open(LAST_WAV, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes((np.clip(audio, -1, 1) * 32767).astype(np.int16).tobytes())
    return LAST_WAV


def prepare_wav(path):
    """Return a 16 kHz mono 16-bit WAV for the engines, converting the input if it isn't one already."""
    import numpy as np

    with wave.open(path, "rb") as w:
        rate, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        if width != 2:
            sys.exit(f"{path}: need 16-bit PCM WAV (got {width * 8}-bit)")
        if rate == 16000 and ch == 1:
            return path
        x = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if rate != 16000:
        x = np.interp(np.linspace(0, len(x) - 1, int(len(x) * 16000 / rate)), np.arange(len(x)), x)
    os.makedirs(BENCH_DIR, exist_ok=True)
    out = os.path.join(BENCH_DIR, "input_16k.wav")
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(np.clip(x, -32768, 32767).astype(np.int16).tobytes())
    print(f"(converted {os.path.basename(path)} from {rate} Hz / {ch} ch to 16 kHz mono)")
    return out


# ---------- engines ----------
def run_engine(spec, wav):
    kind, _, arg = spec.partition(":")
    if kind == "whisper":
        cmd = [PY_WHISPER, os.path.join(ROOT, "bench", "engine_whisper.py"), wav, arg]
    elif kind == "parakeet":
        if not os.path.exists(PY_PARAKEET) or not os.path.exists(os.path.join(PARAKEET_DIR, "encoder-model.onnx")):
            return {"model": "parakeet", "error": "not installed (missing .venv-parakeet or models/parakeet-tdt-0.6b-v2)"}
        cmd = [PY_PARAKEET, os.path.join(ROOT, "bench", "engine_parakeet.py"), wav, PARAKEET_DIR]
        if arg:
            cmd += arg.split("+")  # e.g. parakeet:--cpu or parakeet:--int8
    else:
        return {"model": spec, "error": "unknown engine"}
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=ROOT)
    for line in reversed(p.stdout.strip().splitlines()):
        if line.startswith("{"):
            return json.loads(line)
    tail = (p.stderr.strip().splitlines() or ["no output"])[-1]
    return {"model": spec, "error": tail[:300]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=15.0)
    ap.add_argument("--wav")
    ap.add_argument("--reuse", action="store_true", help="use the last recording")
    ap.add_argument("--engines", default=DEFAULT_ENGINES)
    ap.add_argument("--ref", default=PARAGRAPH)
    args = ap.parse_args()

    wav = args.wav or (LAST_WAV if args.reuse else record(args.seconds))
    if not os.path.exists(wav):
        sys.exit(f"No audio at {wav}. Run without --reuse to record.")
    wav = prepare_wav(wav)
    ref_words = normalize(args.ref)

    results = []
    for spec in args.engines.split(","):
        print(f"Running {spec} ...", flush=True)
        r = run_engine(spec.strip(), wav)
        if "error" not in r:
            hyp = normalize(r["text"])
            errs, diffs = align(ref_words, hyp)
            r["wer"] = round(100.0 * errs / max(1, len(ref_words)), 1)
            r["errors"] = diffs
        results.append(r)

    print("\n" + "=" * 78)
    print(f"{'model':30} {'device':8} {'load':>6} {'speed':>9} {'x realtime':>11} {'WER':>7}")
    print("-" * 78)
    for r in results:
        if "error" in r:
            print(f"{r['model']:30} FAILED: {r['error']}")
            continue
        dev = r["device"].split()[0]
        print(f"{r['model']:30} {dev:8} {r['load_s']:5.1f}s {r['infer_s']:8.2f}s {r['audio_s'] / r['infer_s']:10.1f}x {r['wer']:6.1f}%")
    print("=" * 78)
    for r in results:
        if "error" in r:
            continue
        print(f"\n[{r['model']}]  {r['wer']}% WER")
        print(f"  heard: {r['text']}")
        print("  mistakes: " + ("; ".join(r["errors"]) if r["errors"] else "none"))
        if "GPU provider did not load" in r["device"]:
            print(f"  NOTE: {r['device']}")
    print(f"\nReference: {args.ref}\n")

    os.makedirs(BENCH_DIR, exist_ok=True)
    with open(RESULTS, "w", encoding="utf-8") as f:
        json.dump({"wav": os.path.relpath(wav, ROOT), "ref": args.ref, "results": results}, f, indent=2)
    print(f"(saved to {RESULTS}; re-run without speaking again using --reuse)")


if __name__ == "__main__":
    main()
