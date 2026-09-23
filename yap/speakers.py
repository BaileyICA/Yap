"""Local voice embeddings and conservative speaker grouping for meeting turns."""

import os

import numpy as np

from . import config

MODEL_DIR = os.path.join(config.ROOT, "models", "wespeaker")
MODEL_FILE = os.path.join(MODEL_DIR, "voxceleb_resnet34.onnx")


def _mel(hz):
    return 2595 * np.log10(1 + hz / 700)


def _unmel(mel):
    return 700 * (10 ** (mel / 2595) - 1)


def _features(audio):
    """80-bin log-mel filterbank, matching the WeSpeaker ONNX input."""
    audio = np.asarray(audio, dtype=np.float32) * 32768
    if len(audio) < 400:
        audio = np.pad(audio, (0, 400 - len(audio)))
    frames = np.lib.stride_tricks.sliding_window_view(audio, 400)[::160].copy()
    frames[:, 1:] -= 0.97 * frames[:, :-1].copy()
    frames *= np.hamming(400).astype(np.float32)
    power = np.abs(np.fft.rfft(frames, n=512, axis=1)) ** 2
    edges = _unmel(np.linspace(_mel(20), _mel(8000), 82))
    bins = np.fft.rfftfreq(512, 1 / 16000)
    filters = np.zeros((257, 80), dtype=np.float32)
    for k in range(80):
        left, middle, right = edges[k:k + 3]
        filters[:, k] = np.maximum(0, np.minimum((bins - left) / (middle - left),
                                                  (right - bins) / (right - middle)))
    feats = np.log(np.maximum(power @ filters, 1e-10)).astype(np.float32)
    feats -= feats.mean(axis=0, keepdims=True)
    return feats[None, :, :]


class SpeakerMatcher:
    def __init__(self, log=print):
        import onnxruntime as ort
        from huggingface_hub import hf_hub_download

        if not os.path.exists(MODEL_FILE):
            log("Downloading local speaker model (26 MB)...")
            os.makedirs(MODEL_DIR, exist_ok=True)
            hf_hub_download("Wespeaker/wespeaker-voxceleb-resnet34", "voxceleb_resnet34.onnx",
                            local_dir=MODEL_DIR)
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        self.session = ort.InferenceSession(MODEL_FILE, sess_options=options, providers=["CPUExecutionProvider"])

    def embedding(self, audio):
        vector = self.session.run(["embs"], {"feats": _features(audio)})[0][0].astype(np.float32)
        return vector / max(float(np.linalg.norm(vector)), 1e-8)


def assign_speakers(turns, tracks, log=print, offsets=None):
    """Attach stable numbered labels; leave uncertain short turns unattributed."""
    if not turns:
        return [], "No speech detected"
    try:
        matcher = SpeakerMatcher(log)
    except Exception as exc:
        log(f"Speaker model unavailable: {exc}")
        for turn in turns:
            turn["speaker"] = "Speaker 1" if turn["source"] == "microphone" else "Speaker 2"
        return turns, "Speaker grouping unavailable; labels indicate audio source only"

    embeddings = []
    offsets = offsets or {}
    for turn in turns:
        audio = tracks[turn["source"]]
        middle = (turn["start"] + turn["end"]) / 2 - offsets.get(turn["source"], 0)
        left = max(0, int((middle - 1.2) * 16000))
        right = min(len(audio), int((middle + 1.2) * 16000))
        sample = audio[left:right]
        if len(sample) < 16000 or float(np.sqrt(np.mean(sample ** 2))) < 0.003:
            embeddings.append(None)
            continue
        try:
            embeddings.append(matcher.embedding(sample))
        except Exception as exc:
            log(f"Speaker embedding failed: {exc}")
            embeddings.append(None)

    centroids = []
    counts = []
    for turn, vector in zip(turns, embeddings):
        if vector is None:
            turn["speaker"] = "Unclear speaker"
            continue
        similarities = [float(np.dot(vector, center)) for center in centroids]
        best = int(np.argmax(similarities)) if similarities else -1
        if best < 0 or similarities[best] < 0.68:
            if len(centroids) >= 8:
                turn["speaker"] = "Unclear speaker"
                continue
            best = len(centroids)
            centroids.append(vector.copy())
            counts.append(0)
        else:
            centroids[best] = (centroids[best] * counts[best] + vector) / (counts[best] + 1)
            centroids[best] /= max(float(np.linalg.norm(centroids[best])), 1e-8)
        counts[best] += 1
        turn["speaker"] = f"Speaker {best + 1}"
    return turns, "Voice-based speaker labels are estimates; review before sharing"
