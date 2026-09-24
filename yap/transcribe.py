import os
import sys
import glob
import threading

import numpy as np

from . import config


def _add_cuda_dlls():
    """pip-installed nvidia-* wheels keep their DLLs under site-packages/nvidia/*/bin."""
    for p in sys.path:
        for d in glob.glob(os.path.join(p, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(d)
            except OSError:
                pass
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


_add_cuda_dlls()

# Whisper tends to invent these on near-silence.
_HALLUCINATIONS = {
    "thank you.", "thanks for watching.", "thank you for watching.", "you", "bye.", ".",
    "thank you so much.", "please subscribe.",
}

# Parakeet reads a whole clip at once; past this, split on pauses so memory stays flat.
_PARAKEET_MAX_CLIP_S = 90
# Parakeet's encoder emits one step per 80 ms, so a word's last token is at least this long.
_PARAKEET_STEP_S = 0.08


class Transcriber:
    """Speech-to-text with NVIDIA Parakeet (default) or Whisper, GPU first with a CPU fallback."""

    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.engine = cfg.get("engine", "parakeet")
        self.model = None
        self.vad = None
        self.device = None
        self.name = None
        self.beam = 1
        self._vad_lock = threading.Lock()  # dictation and meeting processing can both reach for it

    def load(self):
        if self.engine == "whisper":
            self._load_whisper()
        else:
            self._load_parakeet()
        self.log(f"Model ready: {self.name} on {self.device}.")

    def transcribe(self, audio):
        if self.engine == "whisper":
            text = " ".join(s.text.strip() for s in self._whisper_segments(audio)).strip()
        elif len(audio) > 16000 * _PARAKEET_MAX_CLIP_S:
            text = " ".join(s.text.strip() for s in self._parakeet_vad().recognize(audio)).strip()
        else:
            text = self.model.recognize(audio).strip()
        if len(audio) < 16000 * 2 and text.lower() in _HALLUCINATIONS:
            return ""
        return text

    def words(self, audio):
        """[(start_s, end_s, text)] per word, each text with its leading space, for meeting transcripts."""
        if self.engine == "whisper":
            return self._whisper_words(audio)
        return self._parakeet_words(audio)

    # ---- Parakeet (onnx-asr + ONNX Runtime) ----
    def _load_parakeet(self):
        import onnxruntime as ort

        name = self.cfg["parakeet_model"]
        self.name = name.removeprefix("nemo-")
        if self.name.endswith("-v2") and self.cfg["language"] != "en":
            self.log(f"{self.name} only understands English; use nemo-parakeet-tdt-0.6b-v3 or Whisper for other languages.")
        silence = np.zeros(16000, dtype=np.float32)
        if "CUDAExecutionProvider" in ort.get_available_providers():
            try:
                self.log(f"Loading {self.name} on GPU...")
                model = self._parakeet_session(name, ["CUDAExecutionProvider", "CPUExecutionProvider"], None)
                encoder = getattr(model.asr, "_encoder", None)
                if encoder is not None and "CUDAExecutionProvider" not in encoder.get_providers():
                    raise RuntimeError("CUDA provider did not load")
                model.recognize(silence)  # warm-up; surfaces missing CUDA DLLs now
                self.model, self.device = model, "cuda"
                return
            except Exception as e:  # noqa: BLE001
                self.log(f"GPU unavailable ({type(e).__name__}: {e}); using CPU")
        else:
            self.log(f"No CUDA build of ONNX Runtime; loading {self.name} on CPU...")
        # int8 is ~4x smaller and much faster on CPU, for a small accuracy cost.
        self.model = self._parakeet_session(name, ["CPUExecutionProvider"], "int8")
        self.model.recognize(silence)
        self.device = "cpu"

    def _parakeet_session(self, name, providers, quantization):
        import onnxruntime as ort
        from onnx_asr.loader import Manager

        options = ort.SessionOptions()
        options.intra_op_num_threads = int(self.cfg["cpu_threads"])
        options.log_severity_level = 3  # hide CUDA graph-placement warnings
        folder = os.path.join(config.MODELS_DIR, self.name)
        if not os.path.exists(os.path.join(folder, f"encoder-model{'.' + quantization if quantization else ''}.onnx")):
            self.log(f"Downloading {self.name}{' ' + quantization if quantization else ''} (first run only)...")
        # offline=False lets a folder that holds one precision fetch the other.
        return Manager(options, providers).create_asr(name, folder, quantization=quantization, offline=False)

    def _parakeet_vad(self):
        """Parakeet behind Silero VAD: audio split on pauses, with per-segment token timestamps."""
        with self._vad_lock:
            if self.vad is None:
                import onnxruntime as ort
                from onnx_asr.loader import Manager

                options = ort.SessionOptions()
                options.intra_op_num_threads = 2
                vad = Manager(options, ["CPUExecutionProvider"]).create_vad(
                    "silero", os.path.join(config.MODELS_DIR, "silero-vad"), offline=False)
                self.vad = self.model.with_vad(vad, min_silence_duration_ms=500, max_speech_duration_s=30,
                                               batch_size=4).with_timestamps()
        return self.vad

    def _parakeet_words(self, audio):
        words = []
        for seg in self._parakeet_vad().recognize(audio):
            if not seg.tokens:
                continue
            current = None
            for token, at in zip(seg.tokens, seg.timestamps or [0.0] * len(seg.tokens)):
                at = seg.start + at
                if current is None or token.startswith(" "):
                    if current:
                        words.append(tuple(current))
                    current = [at, at + _PARAKEET_STEP_S, " " + token.lstrip()]
                else:
                    current[1] = at + _PARAKEET_STEP_S
                    current[2] += token
            if current:
                words.append(tuple(current))
        # A word ends by the time the next one starts.
        limits = [w[0] for w in words[1:]] + [len(audio) / 16000]
        return [(start, min(end, limit), text) for (start, end, text), limit in zip(words, limits)]

    # ---- Whisper (faster-whisper / CTranslate2) ----
    def _load_whisper(self):
        from faster_whisper import WhisperModel

        silence = np.zeros(16000, dtype=np.float32)
        try:
            self.log(f"Loading {self.cfg['gpu_model']} on GPU...")
            m = WhisperModel(self.cfg["gpu_model"], device="cuda", compute_type="float16")
            list(m.transcribe(silence, beam_size=1)[0])  # warm-up; surfaces missing CUDA kernels/DLLs now
            self.model, self.device, self.beam, self.name = m, "cuda", 5, self.cfg["gpu_model"]
        except Exception as e:  # noqa: BLE001
            self.log(f"GPU unavailable ({type(e).__name__}: {e}); using CPU model {self.cfg['cpu_model']}")
            self.model = WhisperModel(
                self.cfg["cpu_model"], device="cpu", compute_type="int8",
                cpu_threads=int(self.cfg["cpu_threads"]),
            )
            self.device, self.beam, self.name = "cpu", 1, self.cfg["cpu_model"]

    def _whisper_segments(self, audio, **extra):
        lang = self.cfg["language"]
        segments, _info = self.model.transcribe(
            audio,
            language=None if lang == "auto" else lang,
            beam_size=self.beam,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            initial_prompt=", ".join(self.cfg["vocabulary"]) or None,
            **extra,
        )
        return segments

    def _whisper_words(self, audio):
        words = []
        for segment in self._whisper_segments(audio, word_timestamps=True):
            if segment.words:
                for w in segment.words:
                    if w.word.strip():
                        words.append((max(0.0, w.start), max(w.start, w.end), w.word))
            elif segment.text.strip():
                words.append((segment.start, segment.end, segment.text))
        return words
