"""Yap's desktop home and meeting review window."""

import ctypes
import json
import os
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import font as tkfont, messagebox, ttk

from PIL import Image, ImageDraw, ImageFilter, ImageTk

from . import autostart, config
from .branding import BRAND_NAVY, icon as yap_icon
from .meeting_notes import transcript_text
from .meetings import list_meetings, load_meeting, save_meeting

BG = "#0b0d12"
SIDEBAR = "#0f1218"
CARD = "#151922"
RAISED = "#1c2130"
FIELD = "#0f1218"
BORDER = "#242a39"
TEXT = "#eef0f6"
MUTED = "#9199ad"
FAINT = "#5f6779"
ACCENT = "#8b7bff"
ACCENT_HOVER = "#a194ff"
ACCENT_SOFT = "#1f1c3a"
ON_ACCENT = "#0d0b1c"
GREEN = "#4fd49b"
AMBER = "#f2b451"
RED = "#ff6b6b"

# Segoe Fluent Icons / MDL2 Assets code points.
GLYPH = {
    "Home": "", "Meetings": "", "History": "", "Settings": "",
    "record": "", "stop": "", "folder": "", "save": "",
    "keyboard": "", "copy": "", "retry": "",
    "shield": "", "edit": "", "check": "", "chip": "",
}
NAV_ITEMS = ("Home", "Meetings", "History", "Settings")
PLACEHOLDER = "Meeting title (optional)"

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

# Filled in once Tk is running: display scale and the best fonts this PC has.
_scale = 1.0
_fonts = {"text": "Segoe UI", "text_bold": "Segoe UI Semibold",
          "display": "Segoe UI", "display_bold": "Segoe UI Semibold", "icons": "Segoe MDL2 Assets"}


def px(value):
    return int(round(value * _scale))


def font(size, bold=False, display=False):
    key = ("display" if display else "text") + ("_bold" if bold else "")
    return (_fonts[key], size)


def icon_font(size=12):
    return (_fonts["icons"], size)


def _init_fonts(root):
    families = set(tkfont.families(root))
    for key, preferred in (("text", "Segoe UI Variable Text"),
                           ("text_bold", "Segoe UI Variable Text Semibold"),
                           ("display", "Segoe UI Variable Display"),
                           ("display_bold", "Segoe UI Variable Display Semib"),
                           ("icons", "Segoe Fluent Icons")):
        if preferred in families:
            _fonts[key] = preferred


# ---------------------------------------------------------------- drawing ----

_image_cache = {}


def _rgba(color, alpha=255):
    color = color.lstrip("#")
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4)) + (alpha,)


def _corners(root, radius, fill, border):
    """Anti-aliased corner pieces for a rounded rectangle (Tk's canvas can't smooth edges)."""
    key = ("corners", radius, fill, border)
    if key not in _image_cache:
        ss = 4
        size = radius * 2 + 2
        big = Image.new("RGBA", (size * ss, size * ss), (0, 0, 0, 0))
        ImageDraw.Draw(big).rounded_rectangle(
            (0, 0, size * ss - 1, size * ss - 1), radius=radius * ss, fill=_rgba(fill),
            outline=_rgba(border) if border else None, width=ss if border else 0)
        full = big.resize((size, size), Image.Resampling.LANCZOS)
        boxes = ((0, 0), (size - radius, 0), (0, size - radius), (size - radius, size - radius))
        _image_cache[key] = [ImageTk.PhotoImage(full.crop((x, y, x + radius, y + radius)), master=root)
                             for x, y in boxes]
    return _image_cache[key]


def round_rect(canvas, x, y, w, h, radius, fill, border=None, tag="shape"):
    """Draw a crisp rounded rectangle onto a canvas from four corner images and plain rects."""
    radius = max(1, min(radius, w // 2, h // 2))
    tl, tr, bl, br = _corners(canvas.winfo_toplevel(), radius, fill, border)
    canvas.create_rectangle(x + radius, y, x + w - radius, y + h, fill=fill, width=0, tags=tag)
    canvas.create_rectangle(x, y + radius, x + w, y + h - radius, fill=fill, width=0, tags=tag)
    for image, cx, cy in ((tl, x, y), (tr, x + w - radius, y), (bl, x, y + h - radius),
                          (br, x + w - radius, y + h - radius)):
        canvas.create_image(cx, cy, image=image, anchor="nw", tags=tag)
    if border:
        canvas.create_line(x + radius, y, x + w - radius, y, fill=border, tags=tag)
        canvas.create_line(x + radius, y + h - 1, x + w - radius, y + h - 1, fill=border, tags=tag)
        canvas.create_line(x, y + radius, x, y + h - radius, fill=border, tags=tag)
        canvas.create_line(x + w - 1, y + radius, x + w - 1, y + h - radius, fill=border, tags=tag)


class Card(tk.Canvas):
    """A rounded panel. Put widgets in ``.body``; the card grows to fit them unless stretched."""

    def __init__(self, parent, fill=CARD, border=BORDER, radius=14, pad=20, stretch=False, hug=False):
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, bd=0, height=1, width=1)
        self.fill, self.border, self.radius, self.stretch, self.hug = fill, border, px(radius), stretch, hug
        self.pad = (px(pad[0]), px(pad[1])) if isinstance(pad, tuple) else (px(pad), px(pad))
        self.body = tk.Frame(self, bg=fill)
        self._window = self.create_window(self.pad[0], self.pad[1], window=self.body, anchor="nw")
        self.bind("<Configure>", self._layout)
        if not stretch:
            # A 1 px tall canvas never maps its window item, so the body gets no <Configure>
            # until the card has been sized once from the body's requested height.
            self.body.bind("<Configure>", self._fit, add="+")
            self.after_idle(self._fit)

    def _fit(self, _event=None):
        if not self.winfo_exists():
            return
        height = self.body.winfo_reqheight() + 2 * self.pad[1]
        if int(float(self.cget("height"))) != height:
            self.configure(height=height)
        if self.hug:
            width = self.body.winfo_reqwidth() + 2 * self.pad[0]
            if int(float(self.cget("width"))) != width:
                self.configure(width=width)

    def _layout(self, event):
        self.itemconfigure(self._window, width=max(1, event.width - 2 * self.pad[0]))
        if self.stretch:
            self.itemconfigure(self._window, height=max(1, event.height - 2 * self.pad[1]))
        self.repaint(event.width, event.height)

    def repaint(self, width=None, height=None, fill=None, border=None):
        if fill:
            self.fill = fill
            self._recolor(self.body, fill)
        if border is not None:
            self.border = border or None
        width = width or self.winfo_width()
        height = height or self.winfo_height()
        self.delete("shape")
        if width > 2 and height > 2:
            round_rect(self, 0, 0, width, height, self.radius, self.fill, self.border)
            self.tag_lower("shape")

    def _recolor(self, widget, fill):
        try:
            if widget.cget("bg") != FIELD:
                widget.configure(bg=fill)
        except tk.TclError:
            return
        for child in widget.winfo_children():
            if not isinstance(child, (tk.Canvas, tk.Text, tk.Entry)):
                self._recolor(child, fill)
            elif isinstance(child, tk.Canvas):
                child.configure(bg=fill)
                if hasattr(child, "repaint"):
                    child.repaint()


BUTTON_KINDS = {
    #            fill        hover         text     border
    "primary": (ACCENT, ACCENT_HOVER, ON_ACCENT, None),
    "secondary": (RAISED, "#252b3d", TEXT, BORDER),
    "ghost": (None, RAISED, MUTED, None),
    "danger": (RED, "#ff8787", "#1d0707", None),
}


class Button(tk.Canvas):
    """Rounded pill button with an optional Fluent icon."""

    def __init__(self, parent, text, command, kind="primary", icon=None, size=10):
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2")
        self.command, self.kind, self.hover = command, kind, False
        label_font = font(size, bold=True)
        measure = tkfont.Font(root=self, font=label_font)
        text_w = measure.measure(text) if text else 0
        icon_w = tkfont.Font(root=self, font=icon_font(size)).measure(icon) if icon else 0
        gap = px(8) if icon and text else 0
        self.content_w = icon_w + gap + text_w
        self.height = measure.metrics("linespace") + px(16)
        pad = px(16) if text else px(10)
        self.configure(width=self.content_w + 2 * pad, height=self.height)
        self.icon_id = self.create_text(0, 0, text=icon or "", font=icon_font(size), anchor="w", tags="fg")
        self.text_id = self.create_text(0, 0, text=text, font=label_font, anchor="w", tags="fg")
        self._offsets = (0, icon_w + gap)
        self.bind("<Configure>", lambda _e: self._paint())
        self.bind("<Enter>", lambda _e: self._set_hover(True))
        self.bind("<Leave>", lambda _e: self._set_hover(False))
        self.bind("<ButtonRelease-1>", self._click)

    def _set_hover(self, hover):
        self.hover = hover
        self._paint()

    def _click(self, event):
        if 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height():
            self.command()

    def repaint(self):
        self._paint()

    def _paint(self):
        fill, hover, fg, border = BUTTON_KINDS[self.kind]
        w, h = self.winfo_width(), self.winfo_height()
        self.delete("shape")
        color = hover if self.hover else fill
        if color and w > 2:
            round_rect(self, 0, 0, w, h, h // 2, color, None if self.hover else border)
            self.tag_lower("shape")
        if self.kind == "ghost" and self.hover:
            fg = TEXT
        left = (w - self.content_w) // 2
        self.coords(self.icon_id, left + self._offsets[0], h // 2)
        self.coords(self.text_id, left + self._offsets[1], h // 2)
        self.itemconfigure("fg", fill=fg)


class Chip(tk.Canvas):
    """Small rounded status pill with a coloured dot, or a keycap when ``dot`` is None."""

    def __init__(self, parent, text, color=MUTED, dot=True, fill=None, border=None, size=9, bold=False):
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, bd=0)
        self.text, self.color, self.dot = text, color, dot
        self.fill = fill or _mix(color, parent.cget("bg"), 0.16)
        self.border = border
        f = font(size, bold=bold)
        m = tkfont.Font(root=self, font=f)
        self.h = m.metrics("linespace") + px(8)
        self.dot_w = px(12) if dot else 0
        self.w = m.measure(text) + self.dot_w + px(20 if dot else 16)
        self.configure(width=self.w, height=self.h)
        self.font = f
        self.repaint()

    def repaint(self):
        self.delete("all")
        round_rect(self, 0, 0, self.w, self.h, self.h // 2, self.fill, self.border)
        x = px(10 if self.dot else 8)
        if self.dot:
            r = px(3)
            self.create_oval(x, self.h / 2 - r, x + 2 * r, self.h / 2 + r, fill=self.color, width=0)
        self.create_text(x + self.dot_w, self.h / 2, text=self.text, fill=self.color if self.dot else TEXT,
                         font=self.font, anchor="w")


class Switch(tk.Canvas):
    """iOS / Fluent style on-off toggle bound to a BooleanVar."""

    def __init__(self, parent, variable, command=None):
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, bd=0, cursor="hand2",
                         width=px(40), height=px(22))
        self.variable, self.command = variable, command
        self.bind("<Button-1>", self._toggle)
        self.repaint()

    def _toggle(self, _event=None):
        self.variable.set(not self.variable.get())
        self.repaint()
        if self.command:
            self.command(self.variable.get())

    def repaint(self):
        on = self.variable.get()
        w, h = px(40), px(22)
        self.delete("all")
        round_rect(self, 0, 0, w, h, h // 2, ACCENT if on else RAISED, None if on else BORDER)
        r = h // 2 - px(4)
        cx = w - h // 2 if on else h // 2
        self.create_oval(cx - r, h // 2 - r, cx + r, h // 2 + r, fill=ON_ACCENT if on else MUTED, width=0)


def _mix(color, background, amount):
    a, b = _rgba(color), _rgba(background)
    return "#" + "".join(f"{round(b[i] + (a[i] - b[i]) * amount):02x}" for i in range(3))


def _rounded_logo(size):
    """The cream dog mark as a rounded app tile, so it sits cleanly on a dark sidebar."""
    ss = 4
    logo = yap_icon(BRAND_NAVY, size * ss)
    mask = Image.new("L", logo.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, logo.size[0] - 1, logo.size[1] - 1), radius=int(size * ss * 0.26),
                                           fill=255)
    logo.putalpha(mask)
    return logo.resize((size, size), Image.Resampling.LANCZOS)


def _dark_title_bar(root):
    """Ask Windows 11 for a dark caption that matches the window instead of a white strip."""
    try:
        user32, dwm = ctypes.windll.user32, ctypes.windll.dwmapi
        user32.GetAncestor.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        user32.GetAncestor.restype = ctypes.c_void_p
        hwnd = ctypes.c_void_p(user32.GetAncestor(root.winfo_id(), 2) or root.winfo_id())
        on = ctypes.c_int(1)
        dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(on), ctypes.sizeof(on))  # immersive dark mode
        r, g, b, _ = _rgba(SIDEBAR)
        caption = ctypes.c_int(r | g << 8 | b << 16)
        dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))  # caption colour
        border = ctypes.c_int(0x392a24)  # BORDER as COLORREF (0x00bbggrr)
        dwm.DwmSetWindowAttribute(hwnd, 34, ctypes.byref(border), ctypes.sizeof(border))
    except (AttributeError, OSError):
        pass


def _pretty_keys(hotkey):
    names = {"ctrl": "Ctrl", "windows": "Win", "win": "Win", "alt": "Alt", "shift": "Shift", "space": "Space",
             "left windows": "Win", "right windows": "Win", "esc": "Esc"}
    return [names.get(part.strip().lower(), part.strip().title()) for part in hotkey.split("+") if part.strip()]


def _when(stamp):
    try:
        moment = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return ""
    today = datetime.now().date()
    clock = moment.strftime("%I:%M %p").lstrip("0")
    if moment.date() == today:
        return f"Today, {clock}"
    if (today - moment.date()).days == 1:
        return f"Yesterday, {clock}"
    return f"{moment.strftime('%d %b %Y').lstrip('0')}, {clock}"


STATUS_COLORS = {"ready": GREEN, "processing": AMBER, "recording": RED, "error": RED}


# ----------------------------------------------------------------- window ----

class Window:
    def __init__(self, app, visible=True):
        self.app = app
        self.visible = visible
        self.commands = queue.Queue()
        self.root = None
        self.page = "Home"
        self.selected_folder = None
        self.status_text = "Loading speech model..."
        self.detail_tab = "Notes"
        self._ready = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()
        self._ready.wait(5)

    def show(self, page=None):
        self.commands.put(("show", page))

    def update(self, status=None, page=None):
        self.commands.put(("update", (status, page)))

    def _run(self):
        global _scale
        # Per-monitor DPI awareness for this thread only, so text is sharp instead of
        # bitmap-stretched on scaled displays. The tray icon's thread is unaffected.
        try:
            ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
        root = tk.Tk()
        self.root = root
        _scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        _init_fonts(root)
        root.title("Yap")
        root.geometry(f"{px(1060)}x{px(720)}")
        root.minsize(px(960), px(600))
        root.configure(bg=BG)
        # Tk's Windows ICO loader rejects some otherwise valid multi-resolution
        # icons (notably PNG-compressed 256 px frames).  Use a Tk photo for the
        # window/taskbar icon; the .ico remains available to Windows shortcuts.
        self.window_icon = ImageTk.PhotoImage(yap_icon(BRAND_NAVY, 64), master=root)
        root.iconphoto(True, self.window_icon)
        root.protocol("WM_DELETE_WINDOW", self._close)
        self._style()
        self._shell()
        self._render()
        root.update_idletasks()
        _dark_title_bar(root)
        if not self.visible:
            root.withdraw()
        self._ready.set()
        self._poll()
        root.mainloop()

    def _style(self):
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure("Yap.TCombobox", fieldbackground=FIELD, background=FIELD, foreground=TEXT,
                        arrowcolor=MUTED, bordercolor=BORDER, lightcolor=FIELD, darkcolor=FIELD,
                        selectbackground=FIELD, selectforeground=TEXT, insertcolor=TEXT,
                        padding=(px(10), px(7)), arrowsize=px(12))
        style.map("Yap.TCombobox", fieldbackground=[("readonly", FIELD)], bordercolor=[("focus", ACCENT)],
                  lightcolor=[("focus", FIELD)], background=[("active", RAISED)],
                  arrowcolor=[("active", TEXT)])
        self.root.option_add("*TCombobox*Listbox.background", RAISED)
        self.root.option_add("*TCombobox*Listbox.foreground", TEXT)
        self.root.option_add("*TCombobox*Listbox.selectBackground", ACCENT_SOFT)
        self.root.option_add("*TCombobox*Listbox.selectForeground", TEXT)
        self.root.option_add("*TCombobox*Listbox.font", font(10))
        self.root.option_add("*TCombobox*Listbox.relief", "flat")
        style.layout("Yap.Vertical.TScrollbar", [("Vertical.Scrollbar.trough", {
            "sticky": "ns", "children": [("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        style.configure("Yap.Vertical.TScrollbar", troughcolor=BG, background=RAISED, bordercolor=BG,
                        lightcolor=RAISED, darkcolor=RAISED, gripcount=0, arrowsize=px(6), width=px(8))
        style.map("Yap.Vertical.TScrollbar", background=[("active", BORDER)])

    # ---------------------------------------------------------- shell ----

    def _shell(self):
        self.sidebar = tk.Frame(self.root, bg=SIDEBAR, width=px(220))
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)
        tk.Frame(self.root, bg=BORDER, width=1).pack(side="left", fill="y")

        brand = tk.Frame(self.sidebar, bg=SIDEBAR)
        brand.pack(anchor="w", fill="x", padx=px(20), pady=(px(26), px(28)))
        self.logo_image = ImageTk.PhotoImage(_rounded_logo(px(38)), master=self.root)
        tk.Label(brand, image=self.logo_image, bg=SIDEBAR).pack(side="left")
        words = tk.Frame(brand, bg=SIDEBAR)
        words.pack(side="left", padx=(px(12), 0))
        tk.Label(words, text="Yap", fg=TEXT, bg=SIDEBAR, font=font(16, True, True)).pack(anchor="w")
        tk.Label(words, text="Voice dictation", fg=FAINT, bg=SIDEBAR, font=font(9)).pack(anchor="w")

        self.nav_rows = {page: self._nav_row(page) for page in NAV_ITEMS}

        footer = tk.Frame(self.sidebar, bg=SIDEBAR)
        footer.pack(side="bottom", fill="x", padx=px(14), pady=px(16))
        self.status_card = Card(footer, fill=CARD, radius=12, pad=(14, 12))
        self.status_card.pack(fill="x")
        top = tk.Frame(self.status_card.body, bg=CARD)
        top.pack(fill="x")
        self.status_dot = tk.Label(top, text="●", bg=CARD, fg=AMBER, font=font(8))
        self.status_dot.pack(side="left")
        self.status_title = tk.Label(top, text="", bg=CARD, fg=TEXT, font=font(9, True))
        self.status_title.pack(side="left", padx=(px(6), 0))
        self.status_detail = tk.Label(self.status_card.body, text="", bg=CARD, fg=MUTED, font=font(8),
                                      anchor="w", justify="left", wraplength=px(170))
        self.status_detail.pack(anchor="w", pady=(px(3), 0))
        private = tk.Frame(footer, bg=SIDEBAR)
        private.pack(fill="x", pady=(px(12), 0), padx=px(4))
        tk.Label(private, text=GLYPH["shield"], bg=SIDEBAR, fg=FAINT, font=icon_font(9)).pack(side="left")
        tk.Label(private, text="Private · runs on this PC", bg=SIDEBAR, fg=FAINT,
                 font=font(8)).pack(side="left", padx=(px(6), 0))

        self.main = tk.Frame(self.root, bg=BG)
        self.main.pack(side="left", fill="both", expand=True)

    def _nav_row(self, page):
        h = px(42)
        row = tk.Canvas(self.sidebar, bg=SIDEBAR, highlightthickness=0, bd=0, height=h, cursor="hand2")
        row.pack(fill="x", padx=px(12), pady=px(2))
        state = {"hover": False}

        def paint(_event=None):
            active = self.page == page
            w = row.winfo_width()
            row.delete("all")
            if active or state["hover"]:
                round_rect(row, 0, 0, w, h, px(10), ACCENT_SOFT if active else CARD)
            if active:
                row.create_rectangle(0, px(12), px(3), h - px(12), fill=ACCENT, width=0)
            row.create_text(px(18), h // 2, text=GLYPH[page], anchor="w", font=icon_font(12),
                            fill=ACCENT if active else TEXT if state["hover"] else MUTED)
            row.create_text(px(48), h // 2, text=page, anchor="w", font=font(10, bold=active),
                            fill=TEXT if active or state["hover"] else MUTED)

        def hover(on):
            state["hover"] = on
            paint()

        row.bind("<Configure>", paint)
        row.bind("<Button-1>", lambda _e: self._navigate(page))
        row.bind("<Enter>", lambda _e: hover(True))
        row.bind("<Leave>", lambda _e: hover(False))
        return paint

    def _paint_nav(self):
        for paint in self.nav_rows.values():
            paint()
        color, title = self._status_look()
        self.status_dot.configure(fg=color)
        self.status_title.configure(text=title)
        self.status_detail.configure(text=self.status_text)

    def _status_look(self):
        state = getattr(self.app, "state", "")
        lowered = self.status_text.lower()
        if any(word in lowered for word in ("error", "fail", "unavailable", "could not")):
            return RED, "Needs attention"
        if self.app.meeting_capture or state in ("recording", "meeting"):
            return RED, "Recording"
        if state in ("loading", "busy", "meeting_starting", "meeting_processing"):
            return AMBER, "Loading" if state == "loading" else "Working"
        return GREEN, "Ready"

    # -------------------------------------------------------- helpers ----

    def _label(self, parent, text, size=10, color=TEXT, bold=False, display=False, wrap=False):
        label = tk.Label(parent, text=text, bg=parent.cget("bg"), fg=color, font=font(size, bold, display),
                         anchor="w", justify="left")
        if wrap:
            parent.bind("<Configure>", lambda e: label.winfo_exists() and label.configure(
                wraplength=max(px(80), e.width - px(4))), add="+")
        return label

    def _header(self, parent, title, subtitle):
        head = tk.Frame(parent, bg=BG)
        head.pack(fill="x", padx=px(36), pady=(px(30), px(20)))
        words = tk.Frame(head, bg=BG)
        words.pack(side="left", fill="x", expand=True)
        self._label(words, title, 22, bold=True, display=True).pack(anchor="w")
        self._label(words, subtitle, 10, MUTED).pack(anchor="w", pady=(px(2), 0))

    def _section(self, parent, text, pady=None):
        pady = pady if pady is not None else (px(22), px(10))
        self._label(parent, text.upper(), 8, FAINT, bold=True).pack(anchor="w", padx=px(38), pady=pady)

    def _scroll_area(self, parent):
        holder = tk.Frame(parent, bg=BG)
        holder.pack(fill="both", expand=True)
        canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, bd=0)
        bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview, style="Yap.Vertical.TScrollbar")
        canvas.configure(yscrollcommand=bar.set)
        canvas.pack(side="left", fill="both", expand=True)
        content = tk.Frame(canvas, bg=BG)
        content_id = canvas.create_window((0, 0), window=content, anchor="nw")

        def refresh(_event=None):
            height = max(content.winfo_reqheight(), canvas.winfo_height())
            canvas.configure(scrollregion=(0, 0, canvas.winfo_width(), height))
            needed = content.winfo_reqheight() > canvas.winfo_height() + 1
            if needed and not bar.winfo_ismapped():
                bar.pack(side="right", fill="y", padx=(0, px(4)), pady=px(6))
            elif not needed and bar.winfo_ismapped():
                bar.pack_forget()
                canvas.yview_moveto(0)

        content.bind("<Configure>", refresh)
        canvas.bind("<Configure>", lambda e: (canvas.itemconfigure(content_id, width=e.width), refresh()))

        def wheel(event):
            if (str(event.widget) + ".").startswith(str(canvas) + ".") and bar.winfo_ismapped():
                canvas.yview_scroll(int(-event.delta / 120) * 3, "units")

        self._wheel_handlers.append(wheel)
        return content

    def _on_wheel(self, event):
        for handler in self._wheel_handlers:
            handler(event)

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
        self._wheel_handlers = []
        self.root.bind("<MouseWheel>", self._on_wheel)
        for child in self.main.winfo_children():
            child.destroy()
        self.recording_label = None
        if self.page == "Home":
            self._home()
        elif self.page == "Meetings":
            self._meetings()
        elif self.page == "History":
            self._history()
        else:
            self._settings()

    # ----------------------------------------------------------- home ----

    def _home(self):
        page = self._scroll_area(self.main)
        hour = datetime.now().hour
        greeting = "Good morning" if hour < 12 else "Good afternoon" if hour < 18 else "Good evening"
        self._header(page, greeting, "Dictate anywhere, or give a meeting the attention it deserves.")

        hero = tk.Canvas(page, bg=BG, highlightthickness=0, bd=0, height=px(172))
        hero.pack(fill="x", padx=px(36))
        hero.bind("<Configure>", lambda e: self._paint_hero(hero, e.width, e.height))

        stats = tk.Frame(page, bg=BG)
        stats.pack(fill="x", padx=px(36), pady=(px(16), 0))
        meetings = list_meetings()
        tiles = (
            ("Dictations", str(self._history_count()), GLYPH["keyboard"]),
            ("Meetings", str(len(meetings)), GLYPH["Meetings"]),
            ("Speech model", self.app.cfg.get("gpu_model", "—"), GLYPH["chip"]),
        )
        for index, (name, value, glyph) in enumerate(tiles):
            tile = Card(stats, radius=14, pad=(18, 16))
            tile.pack(side="left", fill="x", expand=True,
                      padx=(0 if index == 0 else px(8), 0 if index == len(tiles) - 1 else px(8)))
            top = tk.Frame(tile.body, bg=CARD)
            top.pack(fill="x")
            tk.Label(top, text=glyph, bg=CARD, fg=ACCENT, font=icon_font(11)).pack(side="left")
            self._label(top, name, 9, MUTED).pack(side="left", padx=(px(8), 0))
            self._label(tile.body, value, 16, bold=True, display=True).pack(
                anchor="w", pady=(px(8), 0))

        meeting = Card(page, radius=16, pad=(22, 20))
        meeting.pack(fill="x", padx=px(36), pady=(px(16), 0))
        row = tk.Frame(meeting.body, bg=CARD)
        row.pack(fill="x")
        Button(row, "Start a meeting", lambda: self._navigate("Meetings"), icon=GLYPH["record"]).pack(side="right")
        words = tk.Frame(row, bg=CARD)
        words.pack(side="left", fill="x", expand=True, padx=(0, px(16)))
        self._label(words, "Meeting notes", 13, bold=True, display=True).pack(anchor="w")
        self._label(words, "Record your mic and computer audio. Yap transcribes, groups voices and "
                           "writes notes after you stop.", 9, MUTED, wrap=True).pack(anchor="w", fill="x",
                                                                                    pady=(px(4), 0))

        if meetings:
            self._section(page, "Recent meetings")
            listing = Card(page, radius=16, pad=(6, 6))
            listing.pack(fill="x", padx=px(36), pady=(0, px(30)))
            for index, item in enumerate(meetings[:4]):
                if index:
                    tk.Frame(listing.body, bg=BORDER, height=1).pack(fill="x", padx=px(14))
                self._meeting_row(listing.body, item, lambda folder=item["folder"]: self._open_meeting(folder))
        else:
            tk.Frame(page, bg=BG, height=px(30)).pack()

    def _paint_hero(self, canvas, width, height):
        """Gradient banner with the dictation shortcut drawn as keycaps."""
        canvas.delete("all")
        if width < 10:
            return
        ss = 2
        radius = px(18)
        w, h = width * ss, height * ss
        gradient = Image.linear_gradient("L").rotate(90).resize((w, h))
        start, end = Image.new("RGB", (w, h), "#211b47"), Image.new("RGB", (w, h), "#12182a")
        image = Image.composite(end, start, gradient).convert("RGBA")
        small = (max(1, w // 8), max(1, h // 8))
        glow = Image.new("L", small, 0)
        ImageDraw.Draw(glow).ellipse((small[0] * 0.62, -small[1] * 0.8, small[0] * 1.2, small[1] * 0.75), fill=64)
        glow = glow.filter(ImageFilter.GaussianBlur(small[1] / 4)).resize((w, h), Image.Resampling.BICUBIC)
        image = Image.composite(Image.new("RGBA", (w, h), _rgba(ACCENT)), image, glow)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=radius * ss, fill=255)
        image.putalpha(mask)
        outline = ImageDraw.Draw(image)
        outline.rounded_rectangle((0, 0, w - 1, h - 1), radius=radius * ss, outline=_rgba("#2d2757"), width=ss)
        canvas.hero_image = ImageTk.PhotoImage(image.resize((width, height), Image.Resampling.LANCZOS),
                                               master=self.root)
        canvas.create_image(0, 0, image=canvas.hero_image, anchor="nw")

        x, y = px(28), px(28)
        color, title = self._status_look()
        chip_font = font(9, True)
        chip_w = tkfont.Font(root=canvas, font=chip_font).measure(title) + px(30)
        chip_h = px(24)
        round_rect(canvas, x, y, chip_w, chip_h, chip_h // 2, _mix(color, "#1c1a3a", 0.18), tag="chip")
        canvas.create_oval(x + px(11), y + chip_h / 2 - px(3), x + px(17), y + chip_h / 2 + px(3), fill=color, width=0)
        canvas.create_text(x + px(23), y + chip_h / 2, text=title, fill=color, font=chip_font, anchor="w")

        y += chip_h + px(18)
        keys_font = font(11, True)
        measure = tkfont.Font(root=canvas, font=keys_font)
        cursor = x
        canvas.create_text(cursor, y + px(17), text="Hold", fill=TEXT, font=font(16, True, True), anchor="w")
        cursor += tkfont.Font(root=canvas, font=font(16, True, True)).measure("Hold ") + px(6)
        for index, key in enumerate(_pretty_keys(self.app.cfg["hold_hotkey"])):
            if index:
                canvas.create_text(cursor + px(4), y + px(17), text="+", fill=MUTED, font=font(11), anchor="w")
                cursor += px(20)
            kw = measure.measure(key) + px(22)
            round_rect(canvas, cursor, y, kw, px(34), px(8), "#2a2550", "#433b7a", tag="key")
            canvas.create_line(cursor + px(8), y + px(33), cursor + kw - px(8), y + px(33), fill="#4d4590")
            canvas.create_text(cursor + kw / 2, y + px(17), text=key, fill=TEXT, font=keys_font)
            cursor += kw
        canvas.create_text(cursor + px(12), y + px(17), text="and start talking", fill=TEXT,
                           font=font(16, True, True), anchor="w")

        toggle = " + ".join(_pretty_keys(self.app.cfg["toggle_hotkey"]))
        canvas.create_text(x, y + px(58), anchor="nw", fill="#b9bdd6", font=font(10), width=width - 2 * x,
                           text=f"Words land in whatever app has focus. Double-tap for hands-free "
                                f"(or {toggle}), Esc cancels.")

    def _history_count(self):
        try:
            with open(config.HISTORY_PATH, encoding="utf-8") as f:
                return sum(1 for line in f if line.strip())
        except OSError:
            return 0

    def _meeting_row(self, parent, item, command, selected=False):
        base = ACCENT_SOFT if selected else parent.cget("bg")
        row = Card(parent, fill=base, border=None, radius=10, pad=(14, 11))
        row.pack(fill="x", pady=px(1))
        row.configure(cursor="hand2")
        status = item.get("status", "")
        color = STATUS_COLORS.get(status, MUTED)
        top = tk.Frame(row.body, bg=base)
        top.pack(fill="x")
        title = item.get("title", "Untitled")
        Chip(top, status.title() or "—", color, size=8).pack(side="right")
        self._label(top, title if len(title) <= 42 else title[:40] + "…", 10, bold=True).pack(side="left")
        meta = [_when(item.get("created"))]
        if item.get("duration"):
            meta.append(_clock(int(item["duration"])))
        self._label(row.body, "  ·  ".join(m for m in meta if m), 8, MUTED).pack(anchor="w", pady=(px(2), 0))

        def bind(widget):
            widget.bind("<Button-1>", lambda _e: command())
            if not selected:
                widget.bind("<Enter>", lambda _e: row.repaint(fill=RAISED), add="+")
                widget.bind("<Leave>", lambda _e: row.repaint(fill=base), add="+")
            for child in widget.winfo_children():
                bind(child)

        bind(row)
        return row

    def _open_meeting(self, folder):
        self._save_detail()
        self.selected_folder = folder
        self.page = "Meetings"
        self._render()

    # ------------------------------------------------------- meetings ----

    def _meetings(self):
        self._header(self.main, "Meetings", "Your recordings and notes stay on this computer.")
        body = tk.Frame(self.main, bg=BG)
        body.pack(fill="both", expand=True, padx=px(36), pady=(0, px(28)))
        left = tk.Frame(body, bg=BG, width=px(280))
        left.pack(side="left", fill="y", padx=(0, px(16)))
        left.pack_propagate(False)

        controls = Card(left, radius=16, pad=(18, 18))
        controls.pack(fill="x")
        box = controls.body
        capture = self.app.meeting_capture
        if capture:
            top = tk.Frame(box, bg=CARD)
            top.pack(fill="x")
            self.rec_dot = tk.Label(top, text="●", bg=CARD, fg=RED, font=font(10))
            self.rec_dot.pack(side="left")
            self._label(top, "Recording", 10, RED, bold=True).pack(side="left", padx=(px(6), 0))
            elapsed = int(time.monotonic() - capture.started) if capture.started else 0
            self.recording_label = self._label(box, _clock(elapsed), 28, bold=True, display=True)
            self.recording_label.pack(anchor="w", pady=(px(6), 0))
            self._label(box, capture.title, 9, MUTED, wrap=True).pack(anchor="w", fill="x", pady=(0, px(14)))
            Button(box, "Stop & process", self.app.stop_meeting, "danger", GLYPH["stop"]).pack(fill="x")
        else:
            self._label(box, "New recording", 12, bold=True, display=True).pack(anchor="w")
            field = Card(box, fill=FIELD, border=BORDER, radius=10, pad=(12, 8))
            field.pack(fill="x", pady=(px(12), px(12)))
            self.title_var = tk.StringVar(self.root)
            entry = tk.Entry(field.body, textvariable=self.title_var, bg=FIELD, fg=FAINT, insertbackground=TEXT,
                             relief="flat", font=font(10), highlightthickness=0, bd=0)
            entry.pack(fill="x", ipady=px(2))
            entry.insert(0, PLACEHOLDER)

            def focus_in(_e):
                field.repaint(border=ACCENT)
                if entry.get() == PLACEHOLDER and entry.cget("fg") == FAINT:
                    entry.delete(0, "end")
                    entry.configure(fg=TEXT)

            def focus_out(_e):
                field.repaint(border=BORDER)
                if not entry.get():
                    entry.insert(0, PLACEHOLDER)
                    entry.configure(fg=FAINT)

            entry.bind("<FocusIn>", focus_in)
            entry.bind("<FocusOut>", focus_out)
            entry.bind("<Return>", lambda _e: self._start())
            self.title_entry = entry
            toggle = tk.Frame(box, bg=CARD)
            toggle.pack(fill="x", pady=(0, px(14)))
            self.computer_var = tk.BooleanVar(self.root, value=True)
            Switch(toggle, self.computer_var).pack(side="right")
            self._label(toggle, "Include computer audio", 9, MUTED).pack(side="left")
            if self.app.state in ("meeting_starting", "meeting_processing"):
                Chip(box, "Working…", AMBER).pack(anchor="w")
            else:
                Button(box, "Start recording", self._start, icon=GLYPH["record"]).pack(fill="x")
        if not capture:
            self._label(box, self.status_text, 8, FAINT, wrap=True).pack(anchor="w", fill="x", pady=(px(10), 0))

        self._label(left, "SAVED", 8, FAINT, bold=True).pack(anchor="w", padx=px(4), pady=(px(20), px(8)))
        listing = self._scroll_area(left)
        meetings = list_meetings()
        for item in meetings:
            self._meeting_row(listing, item, lambda folder=item["folder"]: self._select_meeting(folder),
                              selected=item["folder"] == self.selected_folder)
        if not meetings:
            self._label(listing, "No meetings yet. Your recordings will appear here.", 9, FAINT,
                        wrap=True).pack(anchor="w", fill="x", padx=px(4))

        self.detail = Card(body, radius=16, pad=(24, 20), stretch=True)
        self.detail.pack(side="left", fill="both", expand=True)
        self._show_detail()

    def _start(self):
        title = self.title_var.get()
        if title == PLACEHOLDER and self.title_entry.cget("fg") == FAINT:
            title = ""
        self.app.start_meeting(title, self.computer_var.get())

    def _select_meeting(self, folder):
        self._save_detail()
        self.selected_folder = folder
        self._render()

    def _show_detail(self):
        box = self.detail.body
        for child in box.winfo_children():
            child.destroy()
        self.notes_box = None
        self.name_entries = {}
        if not self.selected_folder:
            empty = tk.Frame(box, bg=CARD)
            empty.place(relx=0.5, rely=0.45, anchor="center")
            tk.Label(empty, text=GLYPH["Meetings"], bg=CARD, fg=FAINT, font=icon_font(28)).pack()
            self._label(empty, "No meeting selected", 12, bold=True, display=True).pack(pady=(px(12), px(2)))
            self._label(empty, "Pick one on the left to see its notes and transcript.", 9, MUTED).pack()
            return
        try:
            data = load_meeting(self.selected_folder)
        except (OSError, ValueError):
            self.selected_folder = None
            self._show_detail()
            return

        head = tk.Frame(box, bg=CARD)
        head.pack(fill="x")
        actions = tk.Frame(head, bg=CARD)
        actions.pack(side="right", anchor="n")
        folder = self.selected_folder
        Button(actions, "", lambda: os.startfile(folder), "ghost", GLYPH["folder"], size=11).pack(side="right")
        Button(actions, "Save", self._save_detail, "secondary", GLYPH["save"]).pack(side="right", padx=(0, px(6)))
        words = tk.Frame(head, bg=CARD)
        words.pack(side="left", fill="x", expand=True)
        self._label(words, data["title"], 15, bold=True, display=True, wrap=True).pack(anchor="w", fill="x")
        meta = [_when(data.get("created"))]
        if data.get("duration"):
            meta.append(_clock(int(data["duration"])))
        if data.get("attribution"):
            meta.append(data["attribution"])
        self._label(words, "  ·  ".join(m for m in meta if m), 9, MUTED, wrap=True).pack(
            anchor="w", fill="x", pady=(px(3), 0))

        if data.get("capture_warning"):
            self._banner(box, data["capture_warning"], AMBER)
        if data.get("status") == "error":
            self._banner(box, data.get("error", "Processing failed"), RED,
                         ("Retry", lambda: self.app.reprocess_meeting(folder), GLYPH["retry"]))
        elif data.get("status") in ("recording", "processing"):
            self._banner(box, "Notes will appear here once processing finishes.", AMBER)

        if data.get("turns"):
            people = sorted({t["speaker"] for t in data["turns"] if t["speaker"] != "Unclear speaker"})
            if people:
                names = tk.Frame(box, bg=CARD)
                names.pack(fill="x", pady=(px(16), 0))
                self._label(names, "SPEAKERS  ·  rename after listening", 8, FAINT, bold=True).pack(
                    anchor="w", pady=(0, px(6)))
                flow = tk.Frame(names, bg=CARD)
                flow.pack(fill="x")
                for index, speaker in enumerate(people):
                    field = Card(flow, fill=FIELD, border=BORDER, radius=9, pad=(10, 5))
                    field.grid(row=index // 2, column=index % 2, sticky="ew", padx=(0, px(8)), pady=px(3))
                    inner = field.body
                    tk.Label(inner, text=speaker, bg=FIELD, fg=FAINT, font=font(8)).pack(side="left")
                    e = tk.Entry(inner, bg=FIELD, fg=TEXT, insertbackground=TEXT, relief="flat", width=14,
                                 font=font(9), highlightthickness=0, bd=0)
                    e.insert(0, data.get("speaker_names", {}).get(speaker, ""))
                    e.pack(side="left", fill="x", expand=True, padx=(px(8), 0))
                    e.bind("<FocusIn>", lambda _e, f=field: f.repaint(border=ACCENT))
                    e.bind("<FocusOut>", lambda _e, f=field: f.repaint(border=BORDER))
                    self.name_entries[speaker] = e
                for column in range(min(2, len(people))):
                    flow.grid_columnconfigure(column, weight=1, uniform="speakers")

        tabs = tk.Frame(box, bg=CARD)
        tabs.pack(fill="x", pady=(px(18), px(10)))
        pages = tk.Frame(box, bg=CARD)
        pages.pack(fill="both", expand=True)

        notes_card = Card(pages, fill=FIELD, border=BORDER, radius=12, pad=(4, 4), stretch=True)
        self.notes_box = self._text(notes_card.body)
        self.notes_box.insert("1.0", data.get("notes", "Processing will begin after you stop recording."))
        transcript_card = Card(pages, fill=FIELD, border=BORDER, radius=12, pad=(4, 4), stretch=True)
        transcript = self._text(transcript_card.body)
        transcript.insert("1.0", transcript_text(data.get("turns", []), data.get("speaker_names", {})))
        transcript.configure(state="disabled")

        segments = Card(tabs, fill=FIELD, border=BORDER, radius=11, pad=(3, 3), hug=True)
        segments.pack(side="left")
        buttons = {}

        def choose(name):
            self.detail_tab = name
            for key, card in (("Notes", notes_card), ("Transcript", transcript_card)):
                if key == name:
                    card.pack(fill="both", expand=True)
                else:
                    card.pack_forget()
                buttons[key].kind = "secondary" if key == name else "ghost"
                buttons[key].repaint()

        for name in ("Notes", "Transcript"):
            buttons[name] = Button(segments.body, name, lambda n=name: choose(n), "ghost", size=9)
            buttons[name].pack(side="left")
        choose(self.detail_tab)

    def _text(self, parent):
        box = tk.Text(parent, wrap="word", bg=FIELD, fg=TEXT, insertbackground=TEXT, relief="flat", bd=0,
                      padx=px(14), pady=px(12), font=font(10), highlightthickness=0, spacing1=px(2),
                      spacing3=px(4), selectbackground=ACCENT_SOFT, selectforeground=TEXT, width=1, height=1)
        bar = ttk.Scrollbar(parent, orient="vertical", command=box.yview, style="Yap.Vertical.TScrollbar")

        def scrolled(first, last):
            needed = (float(first), float(last)) != (0.0, 1.0)
            if needed and not bar.winfo_manager():
                bar.pack(side="right", fill="y", pady=px(6), before=box)
            elif not needed and bar.winfo_manager():
                bar.pack_forget()
            bar.set(first, last)

        box.configure(yscrollcommand=scrolled)
        box.pack(side="left", fill="both", expand=True)
        return box

    def _banner(self, parent, text, color, action=None):
        banner = Card(parent, fill=_mix(color, CARD, 0.12), border=_mix(color, CARD, 0.35), radius=10, pad=(12, 9))
        banner.pack(fill="x", pady=(px(12), 0))
        if action:
            label, command, glyph = action
            Button(banner.body, label, command, "secondary", glyph, size=9).pack(side="right")
        tk.Label(banner.body, text="●", bg=banner.fill, fg=color, font=font(8)).pack(side="left", anchor="n",
                                                                                     pady=(px(2), 0))
        words = tk.Frame(banner.body, bg=banner.fill)
        words.pack(side="left", fill="x", expand=True, padx=(px(8), px(8)))
        self._label(words, text, 9, color, wrap=True).pack(anchor="w", fill="x")

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
            messagebox.showerror("Could not save meeting", str(exc), parent=self.root)

    # ------------------------------------------------------- settings ----

    def _settings(self):
        page = self._scroll_area(self.main)
        self._header(page, "Settings", "Control how Yap listens and processes notes.")

        self._section(page, "Dictation", pady=(0, px(10)))
        card = self._settings_card(page)
        self._setting_row(card, "Hold to dictate", "Keep held while you speak, release to paste.",
                          lambda parent: self._keycaps(parent, self.app.cfg["hold_hotkey"]))
        self._divider(card)
        self._setting_row(card, "Hands-free", "Double-tap the hold keys, or press this shortcut.",
                          lambda parent: self._keycaps(parent, self.app.cfg["toggle_hotkey"]))
        self._divider(card)
        self._setting_row(card, "Speaking visual", "The pill shown near the bottom of your screen.",
                          lambda parent: Chip(parent, self.app.cfg["overlay_style"].title(), dot=False,
                                              fill=RAISED, border=BORDER))
        self._divider(card)
        self.autostart_var = tk.BooleanVar(self.root, value=autostart.enabled())
        self._setting_row(card, "Start with Windows", "Launch quietly into the tray when you sign in.",
                          lambda parent: Switch(parent, self.autostart_var, self._set_autostart))
        self._divider(card)
        self._setting_row(card, "Advanced", "Vocabulary, replacements, snippets and more. Restart to apply.",
                          lambda parent: Button(parent, "Edit config.json", lambda: os.startfile(config.CONFIG_PATH),
                                                "secondary", GLYPH["edit"], size=9))

        self._section(page, "Speech model")
        models = self._settings_card(page)
        self._label(models, "Larger models are more accurate, but need more memory and take longer to load.",
                    9, MUTED, wrap=True).pack(anchor="w", fill="x", pady=(0, px(14)))
        fields = tk.Frame(models, bg=CARD)
        fields.pack(fill="x")
        fields.grid_columnconfigure((0, 1), weight=1, uniform="models")
        self.gpu_model_var = tk.StringVar(self.root, value=self.app.cfg["gpu_model"])
        self.cpu_model_var = tk.StringVar(self.root, value=self.app.cfg["cpu_model"])
        for column, (name, var, values) in enumerate((("NVIDIA GPU model", self.gpu_model_var, GPU_MODELS),
                                                      ("CPU fallback model", self.cpu_model_var, CPU_MODELS))):
            field = tk.Frame(fields, bg=CARD)
            field.grid(row=0, column=column, sticky="ew", padx=(0, px(8)) if column == 0 else (px(8), 0))
            self._label(field, name, 9, MUTED, bold=True).pack(anchor="w", pady=(0, px(6)))
            ttk.Combobox(field, textvariable=var, values=values, font=font(10), style="Yap.TCombobox").pack(fill="x")
        actions = tk.Frame(models, bg=CARD)
        actions.pack(fill="x", pady=(px(16), 0))
        Button(actions, "Save models", self._save_models, icon=GLYPH["save"], size=9).pack(side="right")
        self.model_save_status = self._label(actions, "Changes take effect after restarting Yap.", 9, FAINT)
        self.model_save_status.pack(side="left")

        self._section(page, "Meeting notes")
        notes = self._settings_card(page)
        self._label(notes, "Speech and speaker matching run locally. Detailed notes use Ollama with "
                           "llama3.2:3b. If it is unavailable, Yap saves the transcript and basic highlights.",
                    9, MUTED, wrap=True).pack(anchor="w", fill="x")
        folder = os.path.join(config.DATA_DIR, "meetings")
        where = tk.Frame(notes, bg=CARD)
        where.pack(fill="x", pady=(px(14), 0))
        Button(where, "Open", lambda: os.makedirs(folder, exist_ok=True) or os.startfile(folder), "secondary",
               GLYPH["folder"], size=9).pack(side="right")
        self._label(where, folder, 9, FAINT).pack(side="left")
        tk.Frame(page, bg=BG, height=px(30)).pack()

    def _settings_card(self, parent):
        card = Card(parent, radius=16, pad=(22, 18))
        card.pack(fill="x", padx=px(36))
        return card.body

    def _setting_row(self, parent, title, subtitle, control):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", pady=px(4))
        control(row).pack(side="right")
        words = tk.Frame(row, bg=CARD)
        words.pack(side="left", fill="x", expand=True, padx=(0, px(16)))
        self._label(words, title, 10, bold=True).pack(anchor="w")
        self._label(words, subtitle, 9, MUTED).pack(anchor="w", pady=(px(1), 0))

    def _divider(self, parent):
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", pady=px(10))

    def _keycaps(self, parent, hotkey):
        keys = tk.Frame(parent, bg=parent.cget("bg"))
        for index, key in enumerate(_pretty_keys(hotkey)):
            if index:
                tk.Label(keys, text="+", bg=keys.cget("bg"), fg=FAINT, font=font(9)).pack(side="left", padx=px(3))
            Chip(keys, key, dot=False, fill=RAISED, border=BORDER, bold=True).pack(side="left")
        return keys

    def _set_autostart(self, on):
        try:
            autostart.set_enabled(on)
        except OSError as exc:
            messagebox.showerror("Could not change startup", str(exc), parent=self.root)
            self.autostart_var.set(autostart.enabled())

    def _save_models(self):
        gpu_model = self.gpu_model_var.get().strip()
        cpu_model = self.cpu_model_var.get().strip()
        if not gpu_model or not cpu_model:
            messagebox.showerror("Could not save models", "Choose both a GPU model and a CPU fallback model.", parent=self.root)
            return
        try:
            config.save_updates(gpu_model=gpu_model, cpu_model=cpu_model)
        except (OSError, ValueError, TypeError) as exc:
            messagebox.showerror("Could not save models", str(exc), parent=self.root)
            return
        self.app.cfg["gpu_model"] = gpu_model
        self.app.cfg["cpu_model"] = cpu_model
        self.model_save_status.configure(text="Saved — restart Yap to load the new model.", fg=GREEN)

    # -------------------------------------------------------- history ----

    def _history(self):
        page = self._scroll_area(self.main)
        self._header(page, "Dictation history", "Recent speech converted to text. Click copy to reuse it.")
        try:
            with open(config.HISTORY_PATH, encoding="utf-8") as f:
                lines = f.readlines()[-100:]
            items = [json.loads(line) for line in reversed(lines) if line.strip()]
        except (OSError, ValueError):
            items = []
        if not items:
            empty = Card(page, radius=16, pad=(22, 40))
            empty.pack(fill="x", padx=px(36))
            tk.Label(empty.body, text=GLYPH["History"], bg=CARD, fg=FAINT, font=icon_font(26)).pack()
            self._label(empty.body, "No dictations yet", 12, bold=True, display=True).pack(pady=(px(10), px(2)))
            hold = " + ".join(_pretty_keys(self.app.cfg["hold_hotkey"]))
            self._label(empty.body, f"Hold {hold} in any app and start talking.", 9, MUTED).pack()
            return
        day = None
        for item in items:
            stamp = item.get("t", "")
            if day is None or stamp[:10] != day:
                label = _when(stamp).split(",")[0] if stamp else "Earlier"
                self._section(page, label, pady=(0 if day is None else px(14), px(8)))
                day = stamp[:10]
            self._history_entry(page, item)
        tk.Frame(page, bg=BG, height=px(30)).pack()

    def _history_entry(self, parent, item):
        card = Card(parent, radius=12, pad=(18, 12))
        card.pack(fill="x", padx=px(36), pady=px(3))
        text = item.get("text", "")
        row = tk.Frame(card.body, bg=CARD)
        row.pack(fill="x")
        side = tk.Frame(row, bg=CARD)
        side.pack(side="right", anchor="n")
        copy = Button(side, "", lambda: self._copy(text, copy), "ghost", GLYPH["copy"], size=10)
        copy.pack()
        stamp = item.get("t", "")
        self._label(row, _when(stamp).split(", ")[-1] if stamp else "", 8, FAINT).pack(anchor="w")
        body = tk.Frame(row, bg=CARD)
        body.pack(side="left", fill="x", expand=True, padx=(0, px(12)))
        self._label(body, text, 10, wrap=True).pack(anchor="w", fill="x", pady=(px(2), 0))

    def _copy(self, text, button):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        button.kind = "secondary"
        button.itemconfigure(button.icon_id, text=GLYPH["check"])
        button.repaint()

        def reset():
            if button.winfo_exists():
                button.kind = "ghost"
                button.itemconfigure(button.icon_id, text=GLYPH["copy"])
                button.repaint()

        self.root.after(1200, reset)

    # ----------------------------------------------------------- loop ----

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
            label = self.recording_label
            if label and label.winfo_exists() and self.app.meeting_capture.started:
                elapsed = int(time.monotonic() - self.app.meeting_capture.started)
                label.configure(text=_clock(elapsed))
                dot = getattr(self, "rec_dot", None)
                if dot and dot.winfo_exists():
                    dot.configure(fg=RED if elapsed % 2 == 0 else _mix(RED, CARD, 0.35))
        self.root.after(250, self._poll)


def _clock(seconds):
    return f"{seconds // 60:02d}:{seconds % 60:02d}"
