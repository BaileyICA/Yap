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

GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
FPS_MS = 25


class Overlay:
    """Always-on-top wave pill that never takes focus, so the target app keeps the caret."""

    def __init__(self):
        self._q = queue.Queue()
        self._level = 0.0
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

        # Glow lines go underneath, sharp lines on top; coords are rewritten every frame.
        glows = [cv.create_line(0, r, 1, r, width=max(2, round(7 * S)), smooth=True, capstyle="round") for _ in WAVES]
        lines = [cv.create_line(0, r, 1, r, width=max(1, round(2 * S)), smooth=True, capstyle="round") for _ in WAVES]
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
                cv.itemconfig(glows[i], fill=glow_c[i])
                cv.itemconfig(lines[i], fill=main_c[i])
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
            for i, (cycles, speed, scale) in enumerate(WAVES):
                pts = []
                for k in range(0, span + 1, max(3, int(5 * S))):
                    u = k / span
                    env = math.sin(math.pi * u) ** 1.4  # pinch to zero at both ends
                    y = r + half * amp * scale * env * math.sin(2 * math.pi * cycles * u + t * speed)
                    pts += (x0 + k, y)
                cv.coords(glows[i], *pts)
                cv.coords(lines[i], *pts)

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
