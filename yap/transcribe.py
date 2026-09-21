import os
import sys
import glob

import numpy as np


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

from faster_whisper import WhisperModel  # noqa: E402

# Whisper tends to invent these on near-silence.
_HALLUCINATIONS = {
    "thank you.", "thanks for watching.", "thank you for watching.", "you", "bye.", ".",
    "thank you so much.", "please subscribe.",
}


class Transcriber:
    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        self.model = None
        self.device = None
        self.beam = 1

    def load(self):
        silence = np.zeros(16000, dtype=np.float32)
        try:
            self.log(f"Loading {self.cfg['gpu_model']} on GPU...")
            m = WhisperModel(self.cfg["gpu_model"], device="cuda", compute_type="float16")
            list(m.transcribe(silence, beam_size=1)[0])  # warm-up; surfaces missing CUDA kernels/DLLs now
            self.model, self.device, self.beam = m, "cuda", 5
        except Exception as e:  # noqa: BLE001
            self.log(f"GPU unavailable ({type(e).__name__}: {e}); using CPU model {self.cfg['cpu_model']}")
            self.model = WhisperModel(
                self.cfg["cpu_model"], device="cpu", compute_type="int8",
                cpu_threads=int(self.cfg["cpu_threads"]),
            )
            self.device, self.beam = "cpu", 1
        self.log(f"Model ready on {self.device}.")

    def transcribe(self, audio):
        lang = self.cfg["language"]
        prompt = ", ".join(self.cfg["vocabulary"]) or None
        segments, _info = self.model.transcribe(
            audio,
            language=None if lang == "auto" else lang,
            beam_size=self.beam,
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
            condition_on_previous_text=False,
            initial_prompt=prompt,
        )
        text = " ".join(s.text.strip() for s in segments).strip()
        if len(audio) < 16000 * 2 and text.lower() in _HALLUCINATIONS:
            return ""
        return text
