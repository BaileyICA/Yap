import os
import queue
import sys
import threading
import time
import winsound

import keyboard
import pystray
from . import apps, autostart, config, history, inputwatch, textproc, updater
from .audio import Recorder, SAMPLE_RATE
from .branding import icon as _icon
from .inject import erase, insert
from .overlay import Overlay
from .transcribe import Transcriber
from .meetings import MeetingCapture, list_meetings
from .meeting_notes import process_meeting
from .window import Window


def _norm(name):
    name = (name or "").lower()
    for p in ("left ", "right "):
        if name.startswith(p):
            name = name[len(p):]
    return {"win": "windows", "escape": "esc"}.get(name, name)


def _combo(s):
    return {_norm(k.strip()) for k in s.split("+")}


def _typing_keys(combo):
    """Keys in a hotkey that inputwatch counts as typing (everything but modifiers)."""
    return len(combo - inputwatch.MODIFIER_NAMES)


class App:
    def __init__(self):
        self.cfg = config.load()
        self.overlay = Overlay(self.cfg["overlay_style"])
        self.recorder = Recorder(on_level=self.overlay.level, log=self.log)
        self.recorder.microphone = self.cfg["microphone"]
        self.tr = Transcriber(self.cfg, log=self.log)
        self.hold = _combo(self.cfg["hold_hotkey"])
        self.toggle = _combo(self.cfg["toggle_hotkey"])
        self.cancel = _combo(self.cfg["cancel_key"])
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
        self.window = None
        self.key_capture_callback = None
        self.toggle_hotkey_id = None
        self.meeting_capture = None
        # Meetings are processed one at a time on their own thread, so dictation keeps working meanwhile.
        self.meeting_queue = queue.Queue()
        self.meetings_pending = set()
        self.meeting_lock = threading.Lock()
        self.model_ready = threading.Event()
        self.model_error = None
        # "Scratch that": what the last dictations inserted, newest last, while nothing has been
        # typed or clicked since and the same window is in front.
        self.undo_stack = []
        self.undo_hwnd = 0
        self.undo_seq = 0
        self.rec_allowance = 0  # hotkey presses inputwatch will have counted for this recording
        self.update_info = None   # newest release on GitHub, if it's newer than this copy
        self.updating = False
        self.update_notified = None

    def log(self, msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line, flush=True)  # no-op when running windowless under pythonw
        try:
            with open(config.LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except OSError:
            pass

    # ---- recording control (called from keyboard hook thread; keep it fast) ----
    def start_rec(self, mode, via="hold"):
        if self.state != "idle" or not self.enabled:
            return
        self.rec_allowance = _typing_keys(self.hold if via == "hold" else self.toggle)
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

    def stop_rec(self, discard=False, via=None):
        if self.state != "recording":
            return
        if via:
            self.rec_allowance += _typing_keys(self.hold if via == "hold" else self.toggle)
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
        self.jobs.put((audio, self.rec_allowance))

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
            self.stop_rec(via="hold")

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
        if self.key_capture_callback:
            self.key_capture_callback(e)
            return
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
                if self.cancel <= self.down:
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
            self.stop_rec(via="toggle")
        elif self.state == "idle":
            self.start_rec("toggle", via="toggle")

    def begin_key_capture(self, callback):
        """Temporarily route the global keyboard hook to the shortcut recorder."""
        self.key_capture_callback = callback
        self.down.clear()
        self.combo_active = False
        if self.toggle_hotkey_id is not None:
            keyboard.remove_hotkey(self.toggle_hotkey_id)
            self.toggle_hotkey_id = None

    def end_key_capture(self):
        self.key_capture_callback = None
        self.down.clear()
        self.combo_active = False
        if self.toggle_hotkey_id is None:
            self.toggle_hotkey_id = keyboard.add_hotkey(self.cfg["toggle_hotkey"], self.on_toggle)

    def set_keybind(self, setting, hotkey):
        if setting not in ("hold_hotkey", "toggle_hotkey", "cancel_key"):
            raise ValueError("Unknown shortcut setting")
        config.save_updates(**{setting: hotkey})
        self.cfg[setting] = hotkey
        if setting == "hold_hotkey":
            self.hold = _combo(hotkey)
        elif setting == "cancel_key":
            self.cancel = _combo(hotkey)
        else:
            self.toggle = _combo(hotkey)
        if setting == "toggle_hotkey" and self.toggle_hotkey_id is not None:
            keyboard.remove_hotkey(self.toggle_hotkey_id)
            self.toggle_hotkey_id = keyboard.add_hotkey(hotkey, self.on_toggle)
        self.down.clear()
        self.combo_active = False

    # ---- worker: transcribe -> clean -> insert ----
    def worker(self):
        try:
            self.tr.load()
        except Exception as e:  # noqa: BLE001
            self.model_error = str(e)
            self.model_ready.set()
            self.log(f"FATAL: could not load any speech model: {type(e).__name__}: {e}")
            self.overlay.show("info", "Model failed to load")
            self._tray_status("#ff453a", "model failed to load - see data/yap.log")
            if self.window:
                self.window.update(f"Speech model failed to load: {e}")
            return
        self.model_ready.set()
        if self.state == "loading":
            self.state = "idle"
        self.overlay.show("done", f"Ready ({self.tr.device.upper()})")
        threading.Timer(1.5, self.overlay.hide).start()
        self._tray_status()
        if self.window:
            self.window.update(f"Ready — speech model on {self.tr.device.upper()}")
        for item in list_meetings():
            if item.get("status") == "processing":
                self.reprocess_meeting(item["folder"])
        while True:
            audio, allowance = self.jobs.get()
            t = time.time()
            try:
                raw = self.tr.transcribe(audio)
                undo, raw = textproc.undo_command(raw)
                scratched = undo and self._scratch_last(allowance)
                text = textproc.clean(raw, self.cfg)
                text = textproc.polish(text, self.cfg)
                text = self._format_for_app(text)
                if text:
                    if self.cfg["add_trailing_space"]:
                        text += " "
                    insert(text, self.cfg["insert_method"])
                    self._remember(text, allowance)
                    history.append(raw, text, self.cfg["history_days"])
                    self.overlay.show("done", "Done")
                elif undo:
                    self.overlay.show("done" if scratched else "info", "Scratched" if scratched else "Nothing to scratch")
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

    def _format_for_app(self, text):
        """Shape the text for the app it's going into: emails get greeting and sign-off lines."""
        if not text or not self.cfg["email_formatting"]:
            return text
        exe, title = apps.foreground()
        if apps.matches(self.cfg["email_apps"], exe, title):
            self.log(f"Email formatting for {exe or title!r}")
            return textproc.format_email(text)
        return text

    def _undo_ready(self, allowance):
        """True if the caret is still right after the last dictation: same window, nothing typed or clicked."""
        return (bool(self.undo_stack) and inputwatch.foreground() == self.undo_hwnd
                and inputwatch.seq - self.undo_seq <= allowance)

    def _remember(self, text, allowance):
        if not self._undo_ready(allowance):
            self.undo_stack.clear()  # the caret moved; earlier dictations are no longer next to it
        self.undo_stack = self.undo_stack[-19:] + [text]
        self.undo_hwnd, self.undo_seq = inputwatch.foreground(), inputwatch.seq

    def _scratch_last(self, allowance):
        """Delete the previous dictation, if it's safe to. Returns whether anything was removed."""
        if not self._undo_ready(allowance):
            self.undo_stack.clear()
            self.log("Scratch that: nothing to remove (typed, clicked or switched windows since)")
            return False
        text = self.undo_stack.pop()
        erase(len(text))
        self.undo_seq = inputwatch.seq
        self.log(f"Scratched {text!r}")
        return True

    def set_history_days(self, days):
        """Save the history retention setting and apply it now. Returns how many entries were removed."""
        config.save_updates(history_days=days)
        self.cfg["history_days"] = days
        return history.prune(days)

    def set_microphone(self, name):
        """Use this microphone ("" = Windows default) from the next recording on."""
        config.save_updates(microphone=name)
        self.cfg["microphone"] = name
        self.recorder.microphone = name

    def set_email_formatting(self, on):
        config.save_updates(email_formatting=bool(on))
        self.cfg["email_formatting"] = bool(on)

    def set_overlay_style(self, style):
        """Save the speaking visual and switch the pill to it now, with a short preview when idle."""
        config.save_updates(overlay_style=style)
        self.cfg["overlay_style"] = style
        self.overlay.set_style(style)
        if self.state == "idle":
            self.overlay.show("done", "Preview")
            threading.Timer(1.5, lambda: self.state == "idle" and self.overlay.hide()).start()

    # ---- updates ----
    def _update_loop(self):
        time.sleep(20)  # let the speech model start loading first
        while True:
            if self.cfg["check_for_updates"]:
                self.check_for_updates()
            time.sleep(6 * 3600)

    def check_for_updates(self):
        """Ask GitHub for a newer release. Returns a short result for Settings; blocks, so call it off the UI thread."""
        try:
            info = updater.check()
        except Exception as e:  # noqa: BLE001
            self.log(f"Update check failed: {type(e).__name__}: {e}")
            return "Couldn't reach GitHub to check for updates."
        self.update_info = info
        if self.window:
            self.window.refresh_update()
        if not info:
            return f"You're up to date (v{updater.__version__})."
        self.log(f"Update available: v{info['version']}")
        if self.tray:
            self.tray.update_menu()
            if self.update_notified != info["version"]:
                self.update_notified = info["version"]
                try:
                    self.tray.notify("Open Yap and click Update to install it.", f"Yap v{info['version']} is available")
                except Exception:  # noqa: BLE001
                    pass
        return f"Yap v{info['version']} is available."

    def install_update(self):
        """Download the new version, then quit so the helper can swap the exe and start it again."""
        info = self.update_info
        if not info or self.updating:
            return
        if not updater.can_self_update(info):
            os.startfile(info["page"])  # running from source, or the exe's folder isn't writable
            return
        if (self.meeting_capture or self.meetings_pending
                or self.state in ("recording", "busy", "meeting_starting", "meeting_saving")):
            self.window.update("Finish the current recording or meeting, then update.")
            return
        self.updating = True
        self.window.refresh_update()
        shown = [-1]

        def progress(fraction):
            percent = int(fraction * 100)
            if percent != shown[0]:
                shown[0] = percent
                self.window.update(f"Downloading update... {percent}%")

        def run():
            try:
                new_exe = updater.download(info, progress)
                updater.launch_installer(new_exe)
            except Exception as e:  # noqa: BLE001
                self.log(f"Update failed: {type(e).__name__}: {e}")
                self.updating = False
                self.window.refresh_update()
                self.window.update(f"Update failed: {e}")
                return
            self.log(f"Installing Yap v{info['version']} and restarting")
            self.window.update(f"Restarting into Yap v{info['version']}...")
            self.overlay.show("working", f"Updating to v{info['version']}...")
            time.sleep(1.0)
            self.tray.stop()

        threading.Thread(target=run, daemon=True).start()

    # ---- dictionary ----
    def update_dictionary(self, vocabulary=None, replacements=None):
        """Save vocabulary/replacements; the next dictation uses them (the worker reads self.cfg)."""
        updates = {}
        if vocabulary is not None:
            updates["vocabulary"] = list(vocabulary)
        if replacements is not None:
            updates["replacements"] = dict(replacements)
        config.save_updates(**updates)
        self.cfg.update(updates)

    def learn(self, fixes):
        """Remember (heard, written) corrections as replacements; new names also join the vocabulary."""
        replacements = dict(self.cfg["replacements"])
        vocabulary = list(self.cfg["vocabulary"])
        known = {word.lower() for word in vocabulary}
        for heard, written in fixes:
            replacements[heard.lower()] = written
            if any(c.isupper() for c in written) and "'" not in written and written.lower() not in known:
                vocabulary.append(written)
                known.add(written.lower())
        self.update_dictionary(vocabulary, replacements)
        self.log(f"Learned: {', '.join(f'{h!r} -> {w!r}' for h, w in fixes)}")

    # ---- tray ----
    def _tray_status(self, color=None, text=None):
        """Green = ready, grey = paused, red = recording, yellow = loading."""
        if not self.tray:
            return
        if color is None:
            if self.state in ("recording", "meeting"):
                color = "#ff453a"
            elif self.state in ("loading", "busy", "meeting_starting", "meeting_saving") or self.meetings_pending:
                color = "#ffd60a"
            else:
                color = "#30d158" if self.enabled else "#8e8e93"
        if text is None:
            text = "paused" if not self.enabled else {
                "loading": "loading model...", "recording": "listening", "busy": "transcribing",
                "meeting": "recording meeting", "meeting_starting": "starting meeting",
                "meeting_saving": "saving meeting",
            }.get(self.state, "processing meeting" if self.meetings_pending else "ready")
        self.tray.icon = _icon(color)
        self.tray.title = f"Yap ({self.tr.device or 'starting'}) - {text}"

    def _toggle_enabled(self, *_):
        self.enabled = not self.enabled
        self._tray_status()

    def start_meeting(self, title, computer_audio=True):
        if self.state != "idle" or self.meeting_capture:
            if self.window:
                self.window.update("Wait until dictation or the speech model finishes.")
            return
        self.state = "meeting_starting"
        self._tray_status()
        if self.window:
            self.window.update("Starting microphone and computer audio...")

        def begin():
            try:
                capture = MeetingCapture(title, computer_audio, self.cfg["microphone"], self.log)
                capture.start()
                self.meeting_capture = capture
                self.state = "meeting"
                self.log(f"Meeting recording started: {capture.folder}")
                self._tray_status()
                self.window.update("Recording in progress")
            except Exception as exc:  # noqa: BLE001
                self.log(f"Meeting recording failed: {type(exc).__name__}: {exc}")
                self.state = "idle"
                self._tray_status()
                self.window.update(f"Could not start recording: {exc}")

        threading.Thread(target=begin, daemon=True).start()

    def stop_meeting(self):
        capture = self.meeting_capture
        if not capture:
            return
        self.meeting_capture = None
        self.state = "meeting_saving"
        self._tray_status()
        if self.window:
            self.window.update("Saving audio...")

        def finish():
            try:
                folder = capture.stop()
                self.log(f"Meeting recording saved: {folder}")
                self.reprocess_meeting(folder)
            except Exception as exc:  # noqa: BLE001
                self.log(f"Could not stop meeting: {type(exc).__name__}: {exc}")
                self.window.update(f"Recording error: {exc}")
            finally:
                self.state = "idle"
                self._tray_status()

        threading.Thread(target=finish, daemon=False).start()

    def reprocess_meeting(self, folder):
        """Queue a saved meeting for transcription and notes; a folder already queued is ignored."""
        with self.meeting_lock:
            if folder in self.meetings_pending:
                return
            self.meetings_pending.add(folder)
        self.meeting_queue.put(folder)
        self._tray_status()
        if self.window:
            self.window.update("Waiting for speech model..." if not self.model_ready.is_set() else "Processing meeting...")

    def _meeting_worker(self):
        progress = lambda msg: self.window.update(msg) if self.window else None  # noqa: E731
        while True:
            folder = self.meeting_queue.get()
            self.model_ready.wait()
            try:
                if self.model_error:
                    progress(f"Speech model unavailable: {self.model_error}")
                    continue
                process_meeting(folder, self.tr, self.cfg, progress=progress, log=self.log)
                self.log(f"Meeting notes ready: {folder}")
                progress("Meeting notes ready")
            except Exception as exc:  # noqa: BLE001
                self.log(f"Meeting processing failed: {type(exc).__name__}: {exc}")
                progress(f"Meeting processing failed: {exc}")
            finally:
                with self.meeting_lock:
                    self.meetings_pending.discard(folder)
                self._tray_status()

    def _toggle_autostart(self, *_):
        autostart.set_enabled(not autostart.enabled())

    def _open(self, path):
        # .json/.jsonl often have no file association; Notepad always works.
        try:
            os.startfile(path)
        except OSError:
            os.system(f'notepad.exe "{path}"')

    def run(self, show_window=True):
        try:
            history.prune(self.cfg["history_days"])
        except OSError as e:
            self.log(f"Could not prune history: {e}")
        self.window = Window(self, visible=show_window)
        threading.Thread(target=_wait_for_open, args=(self.window,), daemon=True).start()
        threading.Thread(target=self.worker, daemon=True).start()
        threading.Thread(target=self._meeting_worker, daemon=True).start()
        threading.Thread(target=self._update_loop, daemon=True).start()
        inputwatch.start()
        keyboard.hook(self.on_key)
        self.toggle_hotkey_id = keyboard.add_hotkey(self.cfg["toggle_hotkey"], self.on_toggle)
        self.overlay.show("info", "Loading model...")
        hint = f"Hold {self.cfg['hold_hotkey']} to dictate"
        menu = pystray.Menu(
            pystray.MenuItem(hint, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(lambda _: f"⬆  Update to v{self.update_info['version']}" if self.update_info else "Update",
                             lambda *_: self.install_update(), visible=lambda _: bool(self.update_info)),
            pystray.MenuItem("🏠  Open Yap", lambda *_: self.window.show(), default=True),
            pystray.MenuItem("🎙  Record a meeting", lambda *_: self.window.show("Meetings")),
            pystray.MenuItem("🕘  Dictation history", lambda *_: self.window.show("History")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Dictation enabled", self._toggle_enabled, checked=lambda _: self.enabled),
            pystray.MenuItem("Start with Windows", self._toggle_autostart, checked=lambda _: autostart.enabled()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("🛠  Advanced", pystray.Menu(
                pystray.MenuItem("Edit config.json", lambda *_: self._open(config.CONFIG_PATH)),
                pystray.MenuItem("Open history file", lambda *_: self._open(config.HISTORY_PATH) if os.path.exists(config.HISTORY_PATH) else None),
                pystray.MenuItem("Open log", lambda *_: self._open(config.LOG_PATH) if os.path.exists(config.LOG_PATH) else None),
            )),
            pystray.MenuItem("Quit Yap", lambda icon, _: icon.stop()),
        )
        self.tray = pystray.Icon("Yap", _icon("#ffd60a"), "Yap (loading)", menu)
        self.log(f"Hold {self.cfg['hold_hotkey']} to dictate; double-tap it (or press {self.cfg['toggle_hotkey']}) for hands-free.")
        self.tray.run()  # blocks until Quit
        if self.meeting_capture:
            self.meeting_capture.stop()  # leave saved audio for the next launch to process
        keyboard.unhook_all()


def _already_running():
    """Named mutex: a second copy would double-fire every hotkey and fight over the mic.
    """
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.restype = wintypes.HANDLE
    kernel.CreateMutexW(None, False, "Local\\YapSingleInstance")
    return ctypes.windll.kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def _open_event():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.CreateEventW.restype = wintypes.HANDLE
    return kernel.CreateEventW(None, False, False, "Local\\YapOpenWindow")


def _wait_for_open(window):
    import ctypes

    event = _open_event()
    while True:
        if ctypes.windll.kernel32.WaitForSingleObject(event, 1000) == 0:
            window.app.log("Opening Yap window from shortcut")
            window.show()


def _signal_existing():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.windll.kernel32
    kernel.OpenEventW.restype = wintypes.HANDLE
    event = kernel.OpenEventW(0x0002, False, "Local\\YapOpenWindow")
    if event:
        kernel.SetEvent(event)
        kernel.CloseHandle(event)


def main():
    # Register before creating any windows so Windows uses Yap's name and dog
    # icon for its taskbar button instead of treating it as generic pythonw.exe.
    from .branding import register_windows_app_id

    register_windows_app_id()
    if _already_running():
        _signal_existing()
        return
    try:
        app = App()
    except config.ConfigError as e:
        # No console under pythonw/the exe, so say it where the user will see it.
        _fatal(f"{e}\n\nFix the file (or delete it to go back to the defaults), then start Yap again.")
        return
    app.run(show_window="--hidden" not in sys.argv)


def _fatal(message):
    import ctypes

    try:
        os.makedirs(config.DATA_DIR, exist_ok=True)
        with open(config.LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} FATAL: {message}\n")
    except OSError:
        pass
    ctypes.windll.user32.MessageBoxW(None, message, "Yap can't start", 0x10)  # MB_ICONERROR
