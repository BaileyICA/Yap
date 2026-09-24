"""Record both sides of a meeting to local WAV files, then prepare transcripts."""

import json
import os
import queue
import shutil
import threading
import time
import wave
from datetime import datetime

import numpy as np
import sounddevice as sd
import soundcard as sc

from . import config

RATE = 16000
MEETINGS_DIR = os.path.join(config.DATA_DIR, "meetings")


def _pcm(data):
    return (np.clip(data, -1, 1) * 32767).astype("<i2").tobytes()


def _writer(path):
    f = wave.open(path, "wb")
    f.setnchannels(1)
    f.setsampwidth(2)
    f.setframerate(RATE)
    return f


def read_wav(path):
    with wave.open(path, "rb") as f:
        if f.getframerate() != RATE or f.getnchannels() != 1 or f.getsampwidth() != 2:
            raise ValueError(f"Unsupported recording format: {path}")
        return np.frombuffer(f.readframes(f.getnframes()), dtype="<i2").astype(np.float32) / 32768


class MeetingCapture:
    def __init__(self, title, computer_audio=True):
        os.makedirs(MEETINGS_DIR, exist_ok=True)
        self.created = datetime.now()
        self.id = self.created.strftime("%Y%m%d-%H%M%S-%f")
        self.folder = os.path.join(MEETINGS_DIR, self.id)
        os.makedirs(self.folder)
        self.title = title.strip() or self.created.strftime("Meeting %d %b %Y, %I:%M %p")
        self.computer_audio = computer_audio
        self.mic_path = os.path.join(self.folder, "microphone.wav")
        self.system_path = os.path.join(self.folder, "computer.wav")
        self._mic_queue = queue.Queue()
        self._stop = threading.Event()
        self._loop_ready = threading.Event()
        self._loop_error = None
        self._mic_stream = None
        self._threads = []
        self.started = None
        self._loop_started = None

    def start(self):
        try:
            self._start()
        except Exception:
            # Nothing was recorded; don't leave an empty folder that never shows up in the list.
            self._stop.set()
            if self._mic_stream:
                self._mic_stream.close()
                self._mic_stream = None
            for t in self._threads:
                t.join(timeout=4)
            shutil.rmtree(self.folder, ignore_errors=True)
            raise

    def _start(self):
        if self.computer_audio:
            t = threading.Thread(target=self._record_loopback, daemon=True)
            t.start()
            self._threads.append(t)
            if not self._loop_ready.wait(5):
                raise RuntimeError("Computer audio capture did not start.")
            if self._loop_error:
                raise RuntimeError(f"Computer audio capture failed: {self._loop_error}")

        def mic_callback(indata, _frames, _time_info, _status):
            self._mic_queue.put(indata[:, 0].copy())

        self._mic_stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", callback=mic_callback)
        self._mic_stream.start()
        self.started = time.monotonic()
        t = threading.Thread(target=self._write_mic, daemon=True)
        t.start()
        self._threads.append(t)
        self._write_meta("recording")

    def _write_mic(self):
        with _writer(self.mic_path) as out:
            while not self._stop.is_set() or not self._mic_queue.empty():
                try:
                    out.writeframes(_pcm(self._mic_queue.get(timeout=0.2)))
                except queue.Empty:
                    pass

    def _record_loopback(self):
        import ctypes

        # SoundCard is imported by the app's main thread; each recording thread also
        # needs a COM apartment for WASAPI calls. Keep it alive until capture closes.
        ctypes.windll.ole32.CoInitializeEx(None, 0)
        try:
            speaker = sc.default_speaker()
            if speaker is None:
                raise RuntimeError("No default computer audio output was found.")
            device = sc.get_microphone(id=speaker.id, include_loopback=True)
            with device.recorder(samplerate=RATE, blocksize=2048) as source, _writer(self.system_path) as out:
                self._loop_started = time.monotonic()
                self._loop_ready.set()
                written = 0
                origin = self._loop_started
                while not self._stop.is_set():
                    block = source.record(numframes=2048)
                    if block is not None and len(block):
                        mono = np.asarray(block, dtype=np.float32).mean(axis=1)
                        out.writeframes(_pcm(mono))
                        written += len(mono)
                    else:
                        time.sleep(0.03)
                    # WASAPI can return no frames during silence. Preserve timing.
                    elapsed = int((time.monotonic() - origin) * RATE)
                    if elapsed - written > RATE // 4:
                        pad = min(elapsed - written - RATE // 8, RATE)
                        out.writeframes(b"\0\0" * pad)
                        written += pad
        except Exception as exc:
            self._loop_error = str(exc)
            self._loop_ready.set()
        finally:
            ctypes.windll.ole32.CoUninitialize()

    def stop(self):
        if self._mic_stream:
            self._mic_stream.stop()
            self._mic_stream.close()
            self._mic_stream = None
        self._stop.set()
        for t in self._threads:
            t.join(timeout=4)
        duration = time.monotonic() - self.started if self.started else 0
        offset = (self._loop_started - self.started) if self._loop_started and self.started else 0
        warning = f"Computer audio capture stopped: {self._loop_error}" if self._loop_error else None
        self._write_meta("processing", duration=duration, computer_offset=round(offset, 3),
                         capture_warning=warning)
        return self.folder

    def _write_meta(self, status, **extra):
        path = os.path.join(self.folder, "meeting.json")
        data = {"id": self.id, "title": self.title, "created": self.created.isoformat(timespec="seconds"),
                "status": status, "computer_audio": self.computer_audio, **extra}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def list_meetings():
    if not os.path.isdir(MEETINGS_DIR):
        return []
    items = []
    for name in sorted(os.listdir(MEETINGS_DIR), reverse=True):
        path = os.path.join(MEETINGS_DIR, name, "meeting.json")
        try:
            with open(path, encoding="utf-8") as f:
                item = json.load(f)
            item["folder"] = os.path.dirname(path)
            items.append(item)
        except (OSError, ValueError):
            pass
    return items


def load_meeting(folder):
    with open(os.path.join(folder, "meeting.json"), encoding="utf-8") as f:
        return json.load(f)


def save_meeting(folder, data):
    path = os.path.join(folder, "meeting.json")
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(temporary, path)
