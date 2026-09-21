import json
import os
import queue
import threading
import time
import winsound

import keyboard
import pystray
from PIL import Image, ImageDraw

from . import autostart, config, textproc
from .audio import Recorder, SAMPLE_RATE
from .inject import insert
from .overlay import Overlay
from .transcribe import Transcriber


def _norm(name):
    name = (name or "").lower()
    for p in ("left ", "right "):
        if name.startswith(p):
            name = name[len(p):]
    return {"win": "windows", "escape": "esc"}.get(name, name)


def _combo(s):
    return {_norm(k.strip()) for k in s.split("+")}


def _icon(color):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((2, 2, 62, 62), fill=color)
    # Five bars of a sound wave, tallest in the middle; reads clearly even at 16x16.
    for i, h in enumerate((12, 26, 40, 26, 12)):
        x = 10 + i * 11
        d.rounded_rectangle((x, 32 - h // 2, x + 6, 32 + h // 2), 3, fill="white")
    return img


class App:
    def __init__(self):
        self.cfg = config.load()
        self.overlay = Overlay()
        self.recorder = Recorder(on_level=self.overlay.level)
        self.tr = Transcriber(self.cfg, log=self.log)
        self.hold = _combo(self.cfg["hold_hotkey"])
        self.cancel = _norm(self.cfg["cancel_key"])
        self.down = set()
        self.state = "loading"  # loading | idle | recording | busy
        self.mode = None        # hold | toggle
        self.t0 = 0.0
        self.combo_active = False   # is the whole hold hotkey currently down?
        self.combo_t = 0.0
        self.combo_clean = True     # False if another key joined the combo (it's a different shortcut)
        self.swallow_release = False
        self.last_tap = 0.0
        self.enabled = True
        self.jobs = queue.Queue()
        self.tray = None

    def log(self, msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)  # no-op when running windowless under pythonw
        try:
            with open(config.LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # ---- recording control (called from keyboard hook thread; keep it fast) ----
    def start_rec(self, mode):
        if self.state != "idle" or not self.enabled:
            return
        try:
            self.recorder.start()
        except Exception as e:  # noqa: BLE001
            self.overlay.show("info", "No microphone")
            self.log(f"mic error: {e}")
            threading.Timer(1.5, self.overlay.hide).start()
            return
        self.state, self.mode, self.t0 = "recording", mode, time.time()
        self.overlay.show("listening", "Listening..." if mode == "hold" else "Listening (hands-free)")
        self._tray_status()
        self._beep(880)

    def stop_rec(self, discard=False):
        if self.state != "recording":
            return
        audio = self.recorder.stop()
        dur = len(audio) / SAMPLE_RATE
        self.mode = None
        if discard or dur < self.cfg["min_recording_seconds"]:
            self.state = "idle"
            self.overlay.hide()
            self._tray_status()
            return
        self.state = "busy"
        self._tray_status()
        self.overlay.show("working", "Transcribing...")
        self._beep(660)
        self.jobs.put(audio)

    def _beep(self, freq):
        if self.cfg["beep"]:
            threading.Thread(target=winsound.Beep, args=(freq, 60), daemon=True).start()

    def _defuse_win(self):
        # A Win press that ends with no other key in between opens the Start menu on
        # release. A bare Shift tap is a no-op everywhere and breaks that sequence.
        if "windows" in self.hold:
            try:
                keyboard.send("shift")
            except Exception:  # noqa: BLE001
                pass

    def _combo_pressed(self, now):
        self.combo_t = now
        self.combo_clean = True
        self.swallow_release = False
        self._defuse_win()
        double = self.cfg["double_tap_hands_free"] and now - self.last_tap <= self.cfg["double_tap_window"]
        if self.state == "idle":
            if double:
                self.last_tap = 0.0
                self.swallow_release = True
                self.start_rec("toggle")
            else:
                self.start_rec("hold")
        elif self.state == "recording" and self.mode == "toggle" and double:
            self.last_tap = 0.0
            self.swallow_release = True
            self.stop_rec()

    def _combo_released(self, now):
        is_tap = self.combo_clean and now - self.combo_t <= self.cfg["tap_max_seconds"]
        if self.state == "recording" and self.mode == "hold":
            # A tap is too short to be dictation: throw the audio away and remember the tap
            # so a second one, straight after, can switch hands-free mode on.
            if is_tap:
                self.stop_rec(discard=True)
                self.last_tap = now
            else:
                self.stop_rec()
        elif is_tap and not self.swallow_release:
            self.last_tap = now  # first tap of a double-tap to stop hands-free mode

    def on_key(self, e):
        k = _norm(e.name)
        now = time.time()
        if e.event_type == "down":
            if k in self.down:
                return  # auto-repeat
            self.down.add(k)
            if self.combo_active:
                if k not in self.hold:
                    self.combo_clean = False  # part of another shortcut, e.g. Ctrl+Win+Left
            elif self.hold <= self.down:
                self.combo_active = True
                self._combo_pressed(now)
            if self.state == "recording":
                if k == self.cancel:
                    self.stop_rec(discard=True)
                elif self.mode == "hold" and k not in self.hold and now - self.t0 < 0.5:
                    self.stop_rec(discard=True)  # it was a shortcut like Ctrl+Win+Arrow, not dictation
        else:
            self.down.discard(k)
            if self.combo_active and k in self.hold:
                self.combo_active = False
                self._combo_released(now)

    def on_toggle(self):
        if self.state == "recording":
            self.stop_rec()
        elif self.state == "idle":
            self.start_rec("toggle")

    # ---- worker: transcribe -> clean -> insert ----
    def worker(self):
        try:
            self.tr.load()
        except Exception as e:  # noqa: BLE001
            self.log(f"FATAL: could not load any speech model: {type(e).__name__}: {e}")
            self.overlay.show("info", "Model failed to load")
            self._tray_status("#ff453a", "model failed to load - see data/yap.log")
            return
        self.state = "idle"
        self.overlay.show("done", f"Ready ({self.tr.device.upper()})")
        threading.Timer(1.5, self.overlay.hide).start()
        self._tray_status()
        while True:
            audio = self.jobs.get()
            t = time.time()
            try:
                raw = self.tr.transcribe(audio)
                text = textproc.clean(raw, self.cfg)
                text = textproc.polish(text, self.cfg)
                if text:
                    if self.cfg["add_trailing_space"]:
                        text += " "
                    insert(text, self.cfg["insert_method"])
                    self._history(raw, text)
                    self.overlay.show("done", "Done")
                else:
                    self.overlay.show("info", "Nothing heard")
                self.log(f"{len(audio) / SAMPLE_RATE:.1f}s audio -> {time.time() - t:.2f}s: {text!r}")
            except Exception as e:  # noqa: BLE001
                self.log(f"error: {type(e).__name__}: {e}")
                self.overlay.show("info", "Error (see data/yap.log)")
            time.sleep(0.6)
            if self.state == "busy":
                self.state = "idle"
                self.overlay.hide()
                self._tray_status()

    def _history(self, raw, text):
        with open(config.HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "raw": raw, "text": text}) + "\n")

    # ---- tray ----
    def _tray_status(self, color=None, text=None):
        """Green = ready, grey = paused, red = recording, yellow = loading."""
        if not self.tray:
            return
        if color is None:
            if self.state == "recording":
                color = "#ff453a"
            elif self.state == "loading":
                color = "#ffd60a"
            else:
                color = "#30d158" if self.enabled else "#8e8e93"
        if text is None:
            text = "paused" if not self.enabled else {
                "loading": "loading model...", "recording": "listening", "busy": "transcribing",
            }.get(self.state, "ready")
        self.tray.icon = _icon(color)
        self.tray.title = f"Yap ({self.tr.device or 'starting'}) - {text}"

    def _toggle_enabled(self, *_):
        self.enabled = not self.enabled
        self._tray_status()

    def _toggle_autostart(self, *_):
        autostart.set_enabled(not autostart.enabled())

    def _open(self, path):
        # .json/.jsonl often have no file association; Notepad always works.
        try:
            os.startfile(path)
        except OSError:
            os.system(f'notepad.exe "{path}"')

    def run(self):
        threading.Thread(target=self.worker, daemon=True).start()
        keyboard.hook(self.on_key)
        keyboard.add_hotkey(self.cfg["toggle_hotkey"], self.on_toggle)
        self.overlay.show("info", "Loading model...")
        hint = f"Hold {self.cfg['hold_hotkey']} to dictate"
        menu = pystray.Menu(
            pystray.MenuItem(hint, None, enabled=False),
            pystray.Menu.SEPARATOR,
            # Default item = what a left-click / double-click on the tray icon does.
            pystray.MenuItem("Enabled", self._toggle_enabled, checked=lambda _: self.enabled, default=True),
            pystray.MenuItem("Start with Windows", self._toggle_autostart, checked=lambda _: autostart.enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Edit settings (config.json)", lambda *_: self._open(config.CONFIG_PATH)),
            pystray.MenuItem("Open history", lambda *_: self._open(config.HISTORY_PATH) if os.path.exists(config.HISTORY_PATH) else None),
            pystray.MenuItem("Open log", lambda *_: self._open(config.LOG_PATH) if os.path.exists(config.LOG_PATH) else None),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit Yap", lambda icon, _: icon.stop()),
        )
        self.tray = pystray.Icon("Yap", _icon("#ffd60a"), "Yap (loading)", menu)
        self.log(f"Hold {self.cfg['hold_hotkey']} to dictate; double-tap it (or press {self.cfg['toggle_hotkey']}) for hands-free.")
        self.tray.run()  # blocks until Quit
        keyboard.unhook_all()


def _already_running():
    """Named mutex: a second copy would double-fire every hotkey and fight over the mic.
    """
    import ctypes

    ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\YapSingleInstance")
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def main():
    if _already_running():
        return
    App().run()
