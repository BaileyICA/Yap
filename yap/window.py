"""Yap's desktop home and meeting review window."""

import os
import ctypes
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

from PIL import ImageTk

from . import config
from .branding import BRAND_NAVY, icon as yap_icon
from .meeting_notes import transcript_text
from .meetings import list_meetings, load_meeting, save_meeting

BG = "#0e111a"
PANEL = "#171c29"
CARD = "#202638"
TEXT = "#f2f3f8"
MUTED = "#aab2c5"
ACCENT = "#a792ff"
ACTIVE = "#262243"
GREEN = "#7ce0bb"
ICON_FONT = ("Segoe MDL2 Assets", 13)
NAV_ITEMS = (
    ("Home", ""),
    ("Meetings", ""),
    ("History", ""),
    ("Settings", ""),
)

GPU_MODELS = (
    "large-v3-turbo",
    "large-v3",
    "medium.en",
    "small.en",
    "base.en",
    "tiny.en",
)
CPU_MODELS = (
    "small.en",
    "base.en",
    "tiny.en",
    "medium.en",
    "large-v3-turbo",
)


class Window:
    def __init__(self, app, visible=True):
        self.app = app
        self.visible = visible
        self.commands = queue.Queue()
        self.root = None
        self.page = "Home"
        self.selected_folder = None
        self.status_text = "Loading speech model..."
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(5)

    def show(self, page=None):
        self.commands.put(("show", page))

    def update(self, status=None, page=None):
        self.commands.put(("update", (status, page)))

    def _run(self):
        root = tk.Tk()
        self.root = root
        root.title("Yap")
        root.geometry("980x680")
        root.minsize(780, 560)
        root.configure(bg=BG)
        # Tk's Windows ICO loader rejects some otherwise valid multi-resolution
        # icons (notably PNG-compressed 256 px frames).  Use a Tk photo for the
        # window/taskbar icon; the .ico remains available to Windows shortcuts.
        self.window_icon = ImageTk.PhotoImage(yap_icon(BRAND_NAVY, 64))
        root.iconphoto(True, self.window_icon)
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._shell()
        self._render()
        if not self.visible:
            root.withdraw()
        self._ready.set()
        self._poll()
        root.mainloop()

    def _shell(self):
        self.sidebar = tk.Frame(self.root, bg=PANEL, width=205)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        brand = tk.Frame(self.sidebar, bg=PANEL)
        brand.pack(anchor="w", padx=22, pady=(24, 18))
        self.logo_image = ImageTk.PhotoImage(yap_icon(BRAND_NAVY, 38))
        tk.Label(brand, image=self.logo_image, bg=PANEL).pack(side="left")
        tk.Label(brand, text="Yap", fg=TEXT, bg=PANEL,
                 font=("Segoe UI Semibold", 21)).pack(side="left", padx=(11, 0))
        tk.Frame(self.sidebar, bg=CARD, height=1).pack(fill="x", padx=18, pady=(0, 14))
        tk.Label(self.sidebar, text="MENU", fg=MUTED, bg=PANEL,
                 font=("Segoe UI Semibold", 8)).pack(anchor="w", padx=26, pady=(0, 6))
        self.nav_rows = {}
        for page, glyph in NAV_ITEMS:
            self.nav_rows[page] = self._nav_row(page, glyph)
        footer = tk.Frame(self.sidebar, bg=PANEL)
        footer.pack(side="bottom", fill="x", padx=22, pady=22)
        tk.Label(footer, text="●", fg=GREEN, bg=PANEL, font=("Segoe UI", 8)).pack(side="left")
        tk.Label(footer, text="Local & private", fg=MUTED, bg=PANEL,
                 font=("Segoe UI", 9)).pack(side="left", padx=(6, 0))
        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)

    def _nav_row(self, page, glyph):
        row = tk.Frame(self.sidebar, bg=PANEL, cursor="hand2")
        row.pack(fill="x", padx=12, pady=2)
        bar = tk.Frame(row, bg=PANEL, width=3)
        bar.pack(side="left", fill="y", pady=8)
        icon = tk.Label(row, text=glyph, bg=PANEL, font=ICON_FONT, width=2)
        icon.pack(side="left", padx=(12, 8), pady=10)
        text = tk.Label(row, text=page, bg=PANEL, font=("Segoe UI Semibold", 11), anchor="w")
        text.pack(side="left", fill="x", expand=True)
        parts = (row, bar, icon, text)

        def paint(hover=False):
            active = self.page == page
            bg = ACTIVE if active else CARD if hover else PANEL
            for widget in (row, icon, text):
                widget.configure(bg=bg)
            bar.configure(bg=ACCENT if active else bg)
            icon.configure(fg=ACCENT if active else TEXT if hover else MUTED)
            text.configure(fg=TEXT if active or hover else MUTED)

        for widget in parts:
            widget.bind("<Button-1>", lambda _e: self._navigate(page))
            widget.bind("<Enter>", lambda _e: paint(True))
            widget.bind("<Leave>", lambda _e: paint(False))
        paint()
        return paint

    def _paint_nav(self):
        for paint in self.nav_rows.values():
            paint()

    def _button(self, parent, label, command, subtle=False):
        return tk.Button(parent, text=label, command=command, relief="flat", cursor="hand2",
                         bg=PANEL if subtle else ACCENT, fg=TEXT if subtle else "#121320",
                         activebackground=CARD if subtle else "#c1b4ff", activeforeground=TEXT,
                         font=("Segoe UI Semibold", 11), padx=15, pady=11, anchor="w")

    def _label(self, parent, text, size=11, color=TEXT, bold=False):
        return tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color,
                        font=("Segoe UI Semibold" if bold else "Segoe UI", size), anchor="w", justify="left")

    def _card(self, parent):
        return tk.Frame(parent, bg=PANEL, padx=22, pady=19)

    def _header(self, title, subtitle):
        self._label(self.main, title, 24, bold=True).pack(anchor="w", padx=30, pady=(26, 3))
        self._label(self.main, subtitle, 10, MUTED).pack(anchor="w", padx=31, pady=(0, 22))

    def _navigate(self, page):
        if page != self.page:
            self._save_detail()
        self.page = page
        self._render()

    def _close(self):
        self._save_detail()
        self.root.withdraw()

    def _render(self):
        self._paint_nav()
        self.root.unbind("<MouseWheel>")
        for child in self.main.winfo_children():
            child.destroy()
        if self.page == "Home":
            self._home()
        elif self.page == "Meetings":
            self._meetings()
        elif self.page == "History":
            self._history()
        else:
            self._settings()

    def _home(self):
        self._header("Good to hear you.", "Dictate anywhere, or give a meeting the attention it deserves.")
        top = self._card(self.main)
        top.pack(fill="x", padx=30, pady=(0, 15))
        self._label(top, "Yap is ready when you are", 17, bold=True).pack(anchor="w")
        self._label(top, f"Hold {self.app.cfg['hold_hotkey']} to dictate into any app.  "
                         f"Press {self.app.cfg['toggle_hotkey']} for hands-free mode.", 10, MUTED).pack(anchor="w", pady=(8, 15))
        self._label(top, self.status_text, 10, GREEN).pack(anchor="w")

        meeting = self._card(self.main)
        meeting.pack(fill="x", padx=30, pady=(0, 15))
        self._label(meeting, "Meeting notes", 17, bold=True).pack(anchor="w")
        self._label(meeting, "Record microphone and computer audio. Yap transcribes, groups voices, "
                    "and prepares notes after you stop.", 10, MUTED).pack(anchor="w", pady=(8, 16))
        self._button(meeting, "Open meetings  →", lambda: self._navigate("Meetings")).pack(anchor="w")

        recent = list_meetings()[:3]
        if recent:
            self._label(self.main, "Recent meetings", 13, bold=True).pack(anchor="w", padx=31, pady=(7, 10))
            for item in recent:
                row = tk.Frame(self.main, bg=PANEL)
                row.pack(fill="x", padx=30, pady=3)
                self._label(row, item["title"], 11, bold=True).pack(side="left", padx=15, pady=12)
                self._label(row, item.get("status", ""), 10, MUTED).pack(side="right", padx=15)

    def _meetings(self):
        self._header("Meetings", "Your recordings and notes stay on this computer.")
        controls = self._card(self.main)
        controls.pack(fill="x", padx=30, pady=(0, 14))
        self._label(controls, "New recording", 15, bold=True).pack(anchor="w")
        row = tk.Frame(controls, bg=PANEL)
        row.pack(fill="x", pady=(12, 8))
        self.title_var = tk.StringVar()
        entry = tk.Entry(row, textvariable=self.title_var, bg=CARD, fg=TEXT, insertbackground=TEXT,
                         relief="flat", font=("Segoe UI", 11))
        entry.pack(side="left", fill="x", expand=True, ipady=8, padx=(0, 12))
        entry.insert(0, "Meeting title (optional)")
        entry.bind("<FocusIn>", lambda _e: entry.delete(0, "end") if entry.get() == "Meeting title (optional)" else None)
        self.computer_var = tk.BooleanVar(value=True)
        tk.Checkbutton(controls, text="Include computer audio", variable=self.computer_var, bg=PANEL,
                       fg=MUTED, selectcolor=CARD, activebackground=PANEL, activeforeground=TEXT,
                       font=("Segoe UI", 10)).pack(anchor="w")
        if self.app.meeting_capture:
            self._button(row, "■  Stop & process", self.app.stop_meeting).pack(side="right")
            elapsed = int(time.monotonic() - self.app.meeting_capture.started) if self.app.meeting_capture.started else 0
            state = f"Recording  {_clock(elapsed)}  •  {self.app.meeting_capture.title}"
        elif self.app.state in ("meeting_starting", "meeting_processing"):
            self._label(row, "Working...", 10, ACCENT).pack(side="right")
            state = self.status_text
        else:
            self._button(row, "●  Start recording", self._start).pack(side="right")
            state = self.status_text
        self.recording_label = self._label(controls, state, 10, GREEN if self.app.meeting_capture else MUTED)
        self.recording_label.pack(anchor="w", pady=(8, 0))

        body = tk.Frame(self.main, bg=BG)
        body.pack(fill="both", expand=True, padx=30, pady=(0, 25))
        left = tk.Frame(body, bg=PANEL, width=245)
        left.pack(side="left", fill="y", padx=(0, 10))
        left.pack_propagate(False)
        self._label(left, "SAVED MEETINGS", 9, MUTED, True).pack(anchor="w", padx=14, pady=(14, 7))
        for item in list_meetings():
            label = item["title"][:28] + ("…" if len(item["title"]) > 28 else "")
            self._button(left, f"{label}\n{item.get('status', '')}",
                         lambda folder=item["folder"]: self._select_meeting(folder), subtle=True).pack(
                             fill="x", padx=6, pady=2)
        right = tk.Frame(body, bg=PANEL)
        right.pack(side="left", fill="both", expand=True)
        self.detail = right
        self._show_detail()

    def _start(self):
        title = self.title_var.get()
        if title == "Meeting title (optional)":
            title = ""
        self.app.start_meeting(title, self.computer_var.get())

    def _select_meeting(self, folder):
        self._save_detail()
        self.selected_folder = folder
        self._show_detail()

    def _show_detail(self):
        for child in self.detail.winfo_children():
            child.destroy()
        self.notes_box = None
        self.name_entries = {}
        if not self.selected_folder:
            self._label(self.detail, "Select a meeting to see its notes and transcript.", 12, MUTED).pack(
                anchor="center", padx=20, pady=40)
            return
        try:
            data = load_meeting(self.selected_folder)
        except (OSError, ValueError):
            self.selected_folder = None
            return
        self._label(self.detail, data["title"], 15, bold=True).pack(anchor="w", padx=17, pady=(15, 4))
        self._label(self.detail, data.get("attribution", data.get("status", "")), 9, MUTED).pack(anchor="w", padx=18)
        if data.get("capture_warning"):
            self._label(self.detail, data["capture_warning"], 9, "#ffbf80").pack(anchor="w", padx=18)
        if data.get("status") == "error":
            self._label(self.detail, data.get("error", "Processing failed"), 10, "#ff9a9a").pack(anchor="w", padx=18, pady=15)
            self._button(self.detail, "Retry processing",
                         lambda: self.app.reprocess_meeting(self.selected_folder)).pack(anchor="w", padx=18, pady=6)
        if data.get("turns"):
            people = sorted({t["speaker"] for t in data["turns"] if t["speaker"] != "Unclear speaker"})
            names = tk.Frame(self.detail, bg=PANEL)
            names.pack(fill="x", padx=17, pady=(10, 6))
            self._label(names, "Speakers — rename after listening", 10, MUTED).pack(anchor="w")
            for speaker in people:
                row = tk.Frame(names, bg=PANEL)
                row.pack(anchor="w", pady=2)
                self._label(row, speaker, 9, MUTED).pack(side="left", padx=(0, 7))
                e = tk.Entry(row, bg=CARD, fg=TEXT, insertbackground=TEXT, relief="flat", width=18,
                             font=("Segoe UI", 9))
                e.insert(0, data.get("speaker_names", {}).get(speaker, ""))
                e.pack(side="left", ipady=3)
                self.name_entries[speaker] = e
        tabs = ttk.Notebook(self.detail)
        tabs.pack(fill="both", expand=True, padx=17, pady=(9, 7))
        notes_frame = tk.Frame(tabs, bg=PANEL)
        transcript_frame = tk.Frame(tabs, bg=PANEL)
        tabs.add(notes_frame, text="Notes")
        tabs.add(transcript_frame, text="Transcript")
        self.notes_box = tk.Text(notes_frame, wrap="word", bg=CARD, fg=TEXT, insertbackground=TEXT,
                                 relief="flat", padx=13, pady=11, font=("Segoe UI", 10))
        self.notes_box.pack(fill="both", expand=True)
        self.notes_box.insert("1.0", data.get("notes", "Processing will begin after you stop recording."))
        transcript = tk.Text(transcript_frame, wrap="word", bg=CARD, fg=TEXT, relief="flat",
                             padx=13, pady=11, font=("Segoe UI", 10))
        transcript.pack(fill="both", expand=True)
        transcript.insert("1.0", transcript_text(data.get("turns", []), data.get("speaker_names", {})))
        transcript.configure(state="disabled")
        actions = tk.Frame(self.detail, bg=PANEL)
        actions.pack(fill="x", padx=17, pady=(0, 14))
        self._button(actions, "Save edits", self._save_detail).pack(side="left")
        self._button(actions, "Open folder", lambda: os.startfile(self.selected_folder), subtle=True).pack(side="right")

    def _save_detail(self):
        if not self.selected_folder or self.notes_box is None or not self.notes_box.winfo_exists():
            return
        try:
            data = load_meeting(self.selected_folder)
            data["notes"] = self.notes_box.get("1.0", "end").rstrip()
            data["speaker_names"] = {key: field.get().strip() for key, field in self.name_entries.items()
                                     if field.winfo_exists() and field.get().strip()}
            save_meeting(self.selected_folder, data)
            with open(os.path.join(self.selected_folder, "notes.md"), "w", encoding="utf-8") as f:
                f.write(data["notes"] + "\n")
            with open(os.path.join(self.selected_folder, "transcript.txt"), "w", encoding="utf-8") as f:
                f.write(transcript_text(data.get("turns", []), data["speaker_names"]) + "\n")
        except (OSError, ValueError) as exc:
            messagebox.showerror("Could not save meeting", str(exc))

    def _settings(self):
        self._header("Settings", "Control how Yap listens and processes notes.")

        body = tk.Frame(self.main, bg=BG)
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y", padx=(0, 8), pady=(0, 12))
        canvas.pack(side="left", fill="both", expand=True)
        content = tk.Frame(canvas, bg=BG)
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")
        content.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(content_id, width=e.width))
        self.root.bind("<MouseWheel>",
                       lambda e: canvas.yview_scroll(int(-e.delta / 120), "units"))

        card = self._card(content)
        card.pack(fill="x", padx=30)
        self._label(card, "Dictation", 16, bold=True).pack(anchor="w")
        self._label(card, f"Hold: {self.app.cfg['hold_hotkey']}\nHands-free: {self.app.cfg['toggle_hotkey']}\n"
                    f"Overlay: {self.app.cfg['overlay_style']}", 11, MUTED).pack(anchor="w", pady=12)
        self._button(card, "Edit config.json", lambda: os.startfile(config.CONFIG_PATH)).pack(anchor="w")

        models = self._card(content)
        models.pack(fill="x", padx=30, pady=15)
        self._label(models, "Speech model", 16, bold=True).pack(anchor="w")
        self._label(models, "Choose the model Yap loads on your graphics card and its CPU fallback. "
                    "Larger models are more accurate, but need more memory and take longer to load.",
                    10, MUTED).pack(anchor="w", pady=(5, 12))

        fields = tk.Frame(models, bg=PANEL)
        fields.pack(fill="x")
        gpu_field = tk.Frame(fields, bg=PANEL)
        gpu_field.pack(side="left", fill="x", expand=True, padx=(0, 9))
        cpu_field = tk.Frame(fields, bg=PANEL)
        cpu_field.pack(side="left", fill="x", expand=True, padx=(9, 0))
        self._label(gpu_field, "NVIDIA GPU model", 9, MUTED, True).pack(anchor="w", pady=(0, 4))
        self._label(cpu_field, "CPU fallback model", 9, MUTED, True).pack(anchor="w", pady=(0, 4))
        self.gpu_model_var = tk.StringVar(value=self.app.cfg["gpu_model"])
        self.cpu_model_var = tk.StringVar(value=self.app.cfg["cpu_model"])
        ttk.Combobox(gpu_field, textvariable=self.gpu_model_var, values=GPU_MODELS,
                     font=("Segoe UI", 10)).pack(fill="x", ipady=3)
        ttk.Combobox(cpu_field, textvariable=self.cpu_model_var, values=CPU_MODELS,
                     font=("Segoe UI", 10)).pack(fill="x", ipady=3)

        actions = tk.Frame(models, bg=PANEL)
        actions.pack(fill="x", pady=(13, 0))
        self.model_save_status = self._label(actions, "Changes take effect after restarting Yap.", 9, MUTED)
        self.model_save_status.pack(side="left")
        self._button(actions, "Save models", self._save_models).pack(side="right")

        notes = self._card(content)
        notes.pack(fill="x", padx=30, pady=(0, 15))
        self._label(notes, "Meeting notes", 16, bold=True).pack(anchor="w")
        self._label(notes, "Speech and speaker matching run locally. Detailed notes use Ollama "
                    "with llama3.2:3b. If it is unavailable, Yap saves the transcript and basic highlights.",
                    10, MUTED).pack(anchor="w", pady=12)
        self._label(notes, "Your recordings: " + os.path.join(config.DATA_DIR, "meetings"), 9, MUTED).pack(anchor="w")

    def _save_models(self):
        gpu_model = self.gpu_model_var.get().strip()
        cpu_model = self.cpu_model_var.get().strip()
        if not gpu_model or not cpu_model:
            messagebox.showerror("Could not save models", "Choose both a GPU model and a CPU fallback model.")
            return
        try:
            config.save_updates(gpu_model=gpu_model, cpu_model=cpu_model)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Could not save models", str(exc))
            return
        self.app.cfg["gpu_model"] = gpu_model
        self.app.cfg["cpu_model"] = cpu_model
        self.model_save_status.configure(text="Saved — restart Yap to load the new model.", fg=GREEN)

    def _history(self):
        import json

        self._header("Dictation history", "Recent speech converted to text.")
        card = self._card(self.main)
        card.pack(fill="both", expand=True, padx=30, pady=(0, 25))
        box = tk.Text(card, wrap="word", bg=CARD, fg=TEXT, relief="flat", padx=15, pady=14,
                      font=("Segoe UI", 11))
        box.pack(fill="both", expand=True)
        try:
            with open(config.HISTORY_PATH, encoding="utf-8") as f:
                lines = f.readlines()[-100:]
            for line in reversed(lines):
                item = json.loads(line)
                box.insert("end", f"{item.get('t', '')}\n{item.get('text', '')}\n\n")
        except (OSError, ValueError):
            box.insert("end", "No dictations yet.")
        box.configure(state="disabled")

    def _poll(self):
        changed = False
        try:
            while True:
                cmd, payload = self.commands.get_nowait()
                if cmd == "show":
                    self._save_detail()
                    if payload:
                        self.page = payload
                    self.root.deiconify()
                    self.root.state("normal")
                    user32 = ctypes.windll.user32
                    user32.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
                    user32.GetAncestor.restype = ctypes.c_void_p
                    hwnd = user32.GetAncestor(self.root.winfo_id(), 2) or self.root.winfo_id()
                    user32.ShowWindow(ctypes.c_void_p(hwnd), 9)  # SW_RESTORE
                    user32.SetForegroundWindow(ctypes.c_void_p(hwnd))
                    self.root.lift()
                    self.root.focus_force()
                    changed = True
                elif cmd == "update":
                    status, page = payload
                    if status:
                        self.status_text = status
                    if page:
                        self.page = page
                    changed = True
        except queue.Empty:
            pass
        if changed:
            self._render()
        if self.page == "Meetings" and self.app.meeting_capture:
            label = getattr(self, "recording_label", None)
            if label and label.winfo_exists() and self.app.meeting_capture.started:
                elapsed = int(time.monotonic() - self.app.meeting_capture.started)
                label.configure(text=f"Recording  {_clock(elapsed)}  •  {self.app.meeting_capture.title}")
        self.root.after(250, self._poll)


def _clock(seconds):
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
