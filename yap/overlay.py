import ctypes
import math
import queue
import threading
import time
import tkinter as tk

BASE_W, BASE_H = 300, 64  # logical pixels; scaled by the display DPI at runtime
BG = "#101015"
KEY = "#010101"  # transparent colour, so the pill can be rounded

# Per state: (glow colours, bright colours) for the three layered waves, plus the label colour.
PALETTES = {
    "listening": (("#0b4f5c", "#5a1250", "#33206b"), ("#00e5ff", "#ff2fd6", "#8b5cf6")),
    "working": (("#5c3f08", "#5c2a00", "#5c4d05"), ("#ffb020", "#ff6a00", "#ffd60a")),
    "done": (("#0d4a22", "#0a3a1c", "#0d4a22"), ("#30d158", "#7dffa0", "#30d158")),
    "info": (("#2a2a30", "#222228", "#2a2a30"), ("#8e8e93", "#a5a5ab", "#8e8e93")),
}
# (cycles across the pill, phase speed rad/s, amplitude scale) for each of the three waves
WAVES = ((1.6, 5.2, 1.00), (2.3, -3.6, 0.72), (3.1, 7.4, 0.48))
STYLES = ("wave", "bars", "orb", "dots")
BAR_COUNT = 27
DOT_COUNT = 7
# "Soft bars": a two-tone gradient per state (dim -> bright), blended per bar by how tall it is,
# instead of the wave/dot styles' fixed per-item colour cycling.
SOFT_BARS = {
    "listening": ("#3a2f70", "#c2b3ff"),
    "working": ("#5c4106", "#ffd580"),
    "done": ("#0d4a22", "#8affc0"),
    "info": ("#38383f", "#c7c7cf"),
}


def _lerp_hex(c1, c2, t):
    t = max(0.0, min(1.0, t))
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(a[i] + (b[i] - a[i]) * t):02x}" for i in range(3))

GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
FPS_MS = 25


class Overlay:
    """Always-on-top wave pill that never takes focus, so the target app keeps the caret."""

    def __init__(self, style="wave"):
        self._q = queue.Queue()
        self._level = 0.0
        self.style = style if style in STYLES else "wave"
        threading.Thread(target=self._run, daemon=True).start()

    def show(self, kind, text):
        self._q.put(("show", kind, text))

    def hide(self):
        self._q.put(("hide", None, None))

    def level(self, v):
        self._level = v

    def _run(self):
        # Per-monitor DPI awareness for this thread only (it must not change the tray icon's
        # window). Without it, Windows bitmap-scales the pill and the thin waves go blurry.
        try:
            ctypes.windll.user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            pass
        root = tk.Tk()
        S = max(1.0, root.winfo_fpixels("1i") / 96.0)  # 1.5 on a 150% display
        W, H = int(BASE_W * S), int(BASE_H * S)
        root.overrideredirect(True)
        root.attributes("-topmost", True, "-alpha", 0.96, "-transparentcolor", KEY)
        root.configure(bg=KEY)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        root.geometry(f"{W}x{H}+{(sw - W) // 2}+{sh - H - int(70 * S)}")
        cv = tk.Canvas(root, width=W, height=H, bg=KEY, highlightthickness=0)
        cv.pack()
        r = H // 2
        cv.create_oval(0, 0, H, H, fill=BG, outline="")
        cv.create_oval(W - H, 0, W, H, fill=BG, outline="")
        cv.create_rectangle(r, 0, W - r, H, fill=BG, outline="")

        # Only the selected visual is created; its coordinates are rewritten every frame.
        glows, lines, bars, orbs, dots = [], [], [], [], []
        if self.style == "wave":
            glows = [cv.create_line(0, r, 1, r, width=max(2, round(7 * S)), smooth=True, capstyle="round") for _ in WAVES]
            lines = [cv.create_line(0, r, 1, r, width=max(1, round(2 * S)), smooth=True, capstyle="round") for _ in WAVES]
        elif self.style == "bars":
            bars = [cv.create_line(0, r, 0, r, width=max(2, round(3 * S)), capstyle="round") for _ in range(BAR_COUNT)]
        elif self.style == "orb":
            orbs = [cv.create_oval(0, 0, 1, 1) for _ in range(3)]
        else:
            dots = [cv.create_oval(0, 0, 1, 1, outline="") for _ in range(DOT_COUNT)]
        label = cv.create_text(int(28 * S), r, text="", fill="white", anchor="w", font=("Segoe UI Semibold", 11))
        caption = cv.create_text(W // 2, H - int(8 * S), text="", fill="#8e8e93", font=("Segoe UI", 7))
        root.withdraw()

        st = {"kind": None, "amp": 0.0, "t0": time.time()}
        styled = False

        def style_noactivate():
            # GA_ROOT walks to the real top-level; GetParent can hand back the desktop
            # for a popup window, and styling that would be a bad idea.
            u = ctypes.windll.user32
            hwnd = u.GetAncestor(root.winfo_id(), 2) or root.winfo_id()
            ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

        def apply(kind, text):
            st["kind"] = kind
            glow_c, main_c = PALETTES.get(kind, PALETTES["info"])
            for i in range(len(WAVES)):
                if glows:
                    cv.itemconfig(glows[i], fill=glow_c[i])
                    cv.itemconfig(lines[i], fill=main_c[i])
            for i, bar in enumerate(bars):
                cv.itemconfig(bar, fill=main_c[i % len(main_c)])
            if orbs:
                cv.itemconfig(orbs[0], fill="", outline=glow_c[0], width=max(2, round(6 * S)))
                cv.itemconfig(orbs[1], fill="", outline=main_c[1], width=max(1, round(2 * S)))
                cv.itemconfig(orbs[2], fill=main_c[0], outline=main_c[2], width=max(1, round(2 * S)))
            for i, dot in enumerate(dots):
                cv.itemconfig(dot, fill=main_c[i % len(main_c)])
            if kind == "listening":
                cv.itemconfig(label, text="")
                cv.itemconfig(caption, text="hands-free" if "hands-free" in text else "")
            else:
                cv.itemconfig(label, text=text, fill=main_c[0])
                cv.itemconfig(caption, text="")

        def draw(now):
            kind = st["kind"]
            t = now - st["t0"]
            # Loudness -> 0..1 with a fast attack and a slow decay, so peaks pop and fall smoothly.
            if kind == "listening":
                target = min(1.0, (self._level * 16) ** 0.6)
                target = max(target, 0.10)  # always a little alive
            elif kind == "working":
                target = 0.30 + 0.12 * math.sin(t * 6)
            else:
                target = 0.10
            rate = 0.55 if target > st["amp"] else 0.10
            st["amp"] += (target - st["amp"]) * rate
            amp = st["amp"]

            # Text states squeeze the waves into the right side; listening uses the whole pill.
            if kind == "listening":
                x0, x1 = r, W - r
            else:
                x0, x1 = int(W * 0.58), W - r
            span = x1 - x0
            half = H / 2 - 9 * S
            if self.style == "wave":
                for i, (cycles, speed, scale) in enumerate(WAVES):
                    pts = []
                    for k in range(0, span + 1, max(3, int(5 * S))):
                        u = k / span
                        env = math.sin(math.pi * u) ** 1.4  # pinch to zero at both ends
                        y = r + half * amp * scale * env * math.sin(2 * math.pi * cycles * u + t * speed)
                        pts += (x0 + k, y)
                    cv.coords(glows[i], *pts)
                    cv.coords(lines[i], *pts)
            elif self.style == "bars":
                dim_c, bright_c = SOFT_BARS.get(kind, SOFT_BARS["info"])
                gap = span / max(1, BAR_COUNT - 1)
                for i, bar in enumerate(bars):
                    u = i / max(1, BAR_COUNT - 1)
                    shape = math.exp(-((u - 0.5) / 0.33) ** 2)  # gaussian envelope, tallest in the middle
                    flutter = math.sin(t * 3.1 + i * 0.9) * math.sin(t * 7.3 + i * 0.41)
                    height = max(2.4 * S, half * amp * shape * (0.35 + 0.65 * max(0.0, flutter)))
                    x = x0 + i * gap
                    cv.coords(bar, x, r - height, x, r + height)
                    cv.itemconfig(bar, fill=_lerp_hex(dim_c, bright_c, shape * amp * 1.3 + 0.15))
            elif self.style == "orb":
                cx = (x0 + x1) / 2
                base = min(half, span * 0.30)
                pulse = 1.0 + 0.10 * math.sin(t * 5.5)
                radii = (base * (0.85 + amp * 0.35) * pulse, base * (0.58 + amp * 0.18), base * (0.22 + amp * 0.16))
                for item, radius in zip(orbs, radii):
                    cv.coords(item, cx - radius, r - radius, cx + radius, r + radius)
            else:
                gap = span / max(1, DOT_COUNT - 1)
                radius = max(2 * S, 2.8 * S + amp * 1.8 * S)
                for i, dot in enumerate(dots):
                    x = x0 + i * gap
                    y = r + half * amp * 0.65 * math.sin(t * 5.5 + i * 0.85)
                    cv.coords(dot, x - radius, y - radius, x + radius, y + radius)

        def tick():
            nonlocal styled
            try:
                while True:
                    cmd, kind, text = self._q.get_nowait()
                    if cmd == "hide":
                        root.withdraw()
                        st["kind"] = None
                    else:
                        apply(kind, text)
                        root.deiconify()
                        if not styled:
                            root.update_idletasks()
                            style_noactivate()
                            styled = True
            except queue.Empty:
                pass
            if st["kind"]:
                draw(time.time())
            root.after(FPS_MS, tick)

        tick()
        root.mainloop()
