import ctypes
import math
import queue
import random
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
STYLES = ("dog", "bars", "wave", "orb", "dots")
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
# Pixel dog: fur, shading, eyes/nose, tongue, and the dirt it digs up while transcribing.
FUR, SHADE, DARK, TONGUE, DIRT = "#f2e3c6", "#c9a878", "#1b1b22", "#ff7a9a", "#8a6a44"
# Leg columns (back, back, front, front) for the four frames of the gallop, and the body bob per frame.
GALLOP = ((2, 3, 12, 13), (4, 5, 11, 12), (6, 7, 9, 10), (5, 6, 10, 11))
BOB = (0, -1, -1, 0)
DOG_W = 16  # grid cells wide


def _lerp_hex(c1, c2, t):
    t = max(0.0, min(1.0, t))
    a = tuple(int(c1[i:i + 2], 16) for i in (1, 3, 5))
    b = tuple(int(c2[i:i + 2], 16) for i in (1, 3, 5))
    return "#" + "".join(f"{round(a[i] + (b[i] - a[i]) * t):02x}" for i in range(3))

GWL_EXSTYLE, WS_EX_NOACTIVATE, WS_EX_TOOLWINDOW = -20, 0x08000000, 0x00000080
FPS_MS = 25


class Overlay:
    """Always-on-top wave pill that never takes focus, so the target app keeps the caret."""

    def __init__(self, style="dog"):
        self._q = queue.Queue()
        self._level = 0.0
        self.style = style if style in STYLES else "dog"
        threading.Thread(target=self._run, daemon=True).start()

    def show(self, kind, text):
        self._q.put(("show", kind, text))

    def hide(self):
        self._q.put(("hide", None, None))

    def set_style(self, style):
        self._q.put(("style", style if style in STYLES else "dog", None))

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
        U = 2.5 * S  # one dog pixel
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
        label = cv.create_text(int(28 * S), r, text="", fill="white", anchor="w", font=("Segoe UI Semibold", 11))
        caption = cv.create_text(W // 2, H - int(8 * S), text="", fill="#8e8e93", font=("Segoe UI", 7))
        root.withdraw()

        st = {"kind": None, "text": "", "amp": 0.0, "t0": time.time(), "last": time.time()}
        # The dog's run: position, gallop clock, distance since the last paw print, and what it left behind.
        pup = {"x": 0.0, "legs": 0.0, "dist": 0.0, "foot": 0, "prints": [], "dirt": [], "n": 0}
        # Only the selected visual exists; its coordinates are rewritten every frame.
        vis = {"glows": [], "lines": [], "bars": [], "orbs": [], "dots": []}
        styled = False

        def build():
            cv.delete("visual")
            for items in vis.values():
                items.clear()
            pup["prints"].clear()
            tag = ("visual",)
            if self.style == "wave":
                vis["glows"] = [cv.create_line(0, r, 1, r, width=max(2, round(7 * S)), smooth=True,
                                               capstyle="round", tags=tag) for _ in WAVES]
                vis["lines"] = [cv.create_line(0, r, 1, r, width=max(1, round(2 * S)), smooth=True,
                                               capstyle="round", tags=tag) for _ in WAVES]
            elif self.style == "bars":
                vis["bars"] = [cv.create_line(0, r, 0, r, width=max(2, round(3 * S)), capstyle="round", tags=tag)
                               for _ in range(BAR_COUNT)]
            elif self.style == "orb":
                vis["orbs"] = [cv.create_oval(0, 0, 1, 1, tags=tag) for _ in range(3)]
            elif self.style == "dots":
                vis["dots"] = [cv.create_oval(0, 0, 1, 1, outline="", tags=tag) for _ in range(DOT_COUNT)]
            else:
                # Tk can't clip, so the dog runs off the rounded ends under two see-through masks.
                for cx, a0, a1, step, edge in ((r, 90, 271, 6, 0), (W - r, 90, -91, -6, W)):
                    arc = [(cx + r * math.cos(math.radians(a)), r - r * math.sin(math.radians(a)))
                           for a in range(a0, a1, step)]
                    pts = [(edge, 0)] + arc + [(edge, H)]
                    cv.create_polygon(*[c for p in pts for c in p], fill=KEY, outline="", tags=("visual", "mask"))
            cv.tag_raise(label)
            cv.tag_raise(caption)

        def style_noactivate():
            # GA_ROOT walks to the real top-level; GetParent can hand back the desktop
            # for a popup window, and styling that would be a bad idea.
            u = ctypes.windll.user32
            hwnd = u.GetAncestor(root.winfo_id(), 2) or root.winfo_id()
            ex = u.GetWindowLongW(hwnd, GWL_EXSTYLE)
            u.SetWindowLongW(hwnd, GWL_EXSTYLE, ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)

        def apply(kind, text):
            if kind != st["kind"]:
                cv.delete("paw")
                pup.update(x=24 * S, legs=0.0, dist=0.0, prints=[], dirt=[])
            st["kind"], st["text"] = kind, text
            glow_c, main_c = PALETTES.get(kind, PALETTES["info"])
            for i in range(len(vis["glows"])):
                cv.itemconfig(vis["glows"][i], fill=glow_c[i])
                cv.itemconfig(vis["lines"][i], fill=main_c[i])
            for i, bar in enumerate(vis["bars"]):
                cv.itemconfig(bar, fill=main_c[i % len(main_c)])
            orbs = vis["orbs"]
            if orbs:
                cv.itemconfig(orbs[0], fill="", outline=glow_c[0], width=max(2, round(6 * S)))
                cv.itemconfig(orbs[1], fill="", outline=main_c[1], width=max(1, round(2 * S)))
                cv.itemconfig(orbs[2], fill=main_c[0], outline=main_c[2], width=max(1, round(2 * S)))
            for i, dot in enumerate(vis["dots"]):
                cv.itemconfig(dot, fill=main_c[i % len(main_c)])
            if kind == "listening":
                cv.itemconfig(label, text="")
                cv.itemconfig(caption, text="hands-free" if "hands-free" in text else "")
            else:
                cv.itemconfig(label, text=text, fill=main_c[0])
                cv.itemconfig(caption, text="")

        def dog(ox, oy, pose, frame, collar, t):
            def px(gx, gy, w, h, c):
                cv.create_rectangle(round(ox + gx * U), round(oy + gy * U), round(ox + (gx + w) * U),
                                    round(oy + (gy + h) * U), fill=c, width=0, tags=("visual", "dog"))

            if pose == "sit":
                wag = int(t * 10) % 2
                px(1, 7 if wag else 9, 3, 1, SHADE)
                px(4, 6, 6, 4, FUR)
                px(4, 8, 4, 3, FUR)
                px(5, 10, 4, 1, SHADE)
                px(9, 7, 1, 4, FUR)
                px(10, 7, 1, 4, FUR)
                px(9, 1, 4, 5, FUR)
                px(13, 3, 2, 2, FUR)
                px(14, 3, 1, 1, DARK)
                px(11, 2, 1, 1, DARK)
                px(9, 1, 1, 3, SHADE)
                px(9, 5, 4, 1, collar)
                if int(t * 3) % 2:
                    px(13, 5, 1, 1, TONGUE)
                return
            b = BOB[frame] if pose == "run" else 0
            legs = GALLOP[frame] if pose == "run" else (4, 5, 11, 12)
            wag = frame % 2 if pose == "run" else int(t * (9 if pose == "pant" else 5)) % 2
            px(1, (3 if wag else 5) + b, 2, 1, SHADE)  # tail
            px(2, 4 + b, 1, 1, SHADE)
            px(3, 5 + b, 9, 3, FUR)  # body
            px(4, 7 + b, 7, 1, SHADE)
            hd = 2 if pose == "dig" else 0  # nose to the ground
            px(11, 2 + b + hd, 3, 4, FUR)  # head
            px(14, 4 + b + hd, 2, 2, FUR)
            px(15, 4 + b + hd, 1, 1, DARK)
            px(13, 3 + b + hd, 1, 1, DARK)
            if pose == "run" and frame % 2:
                px(10, 2 + b, 2, 2, SHADE)  # ear flaps back mid-stride
            else:
                px(11, 2 + b + hd, 1, 3, SHADE)
            px(11, 5 + b + hd, 1, 2, collar)
            if pose == "pant":
                px(14, 6 + int(t * 6) % 2, 1, 1, TONGUE)
            for i, lx in enumerate(legs):
                top = 8 + b
                length = 12 - top
                if pose == "run" and frame in (1, 2) and i >= 2:
                    length -= 1  # front paws tuck up in the air
                if pose == "dig" and i >= 2 and (int(t * 10) + i) % 2:
                    length -= 2
                px(lx, top, 1, length, SHADE if i < 2 else FUR)

        def paw(x, y, color, tag):
            pad = (1.8 * S, 1.5 * S)
            cv.create_oval(x - pad[0], y - pad[1], x + pad[0], y + pad[1], fill=color, outline="", tags=("visual", "paw", tag))
            for dx, dy in ((2.4, -1.8), (3.2, 0.0), (2.4, 1.8)):
                cx, cy, tr = x + dx * S, y + dy * S, 0.9 * S
                cv.create_oval(cx - tr, cy - tr, cx + tr, cy + tr, fill=color, outline="", tags=("visual", "paw", tag))

        def draw_dog(kind, amp, t, dt):
            accent = SOFT_BARS.get(kind, SOFT_BARS["info"])[1]
            ground = 50 * S
            oy = ground - 12 * U
            cv.delete("dog")
            alive = []
            for p in pup["prints"]:
                p["life"] -= dt / 1.3
                if p["life"] <= 0:
                    cv.delete(p["tag"])
                else:
                    cv.itemconfig(p["tag"], fill=_lerp_hex(BG, accent, 0.7 * p["life"]))
                    alive.append(p)
            pup["prints"] = alive

            if kind == "listening":
                # Runs while you talk (faster when louder), and pants in place when you pause,
                # though it always finishes trotting back into view first.
                visible = r * 0.5 <= pup["x"] <= W - r - DOG_W * U
                if amp > 0.18 or not visible:
                    speed = (25 + max(amp, 0.2) * 150) * S
                    pup["x"] += speed * dt
                    pup["dist"] += speed * dt
                    pup["legs"] += dt * (5 + amp * 14)
                    frame = int(pup["legs"]) % 4
                    if pup["dist"] > 15 * S:
                        pup["dist"] = 0.0
                        pup["foot"] ^= 1
                        pup["n"] += 1
                        tag = f"paw{pup['n']}"
                        paw(pup["x"] + 4 * U, (53 if pup["foot"] else 59) * S, _lerp_hex(BG, accent, 0.7), tag)
                        pup["prints"].append({"tag": tag, "life": 1.0})
                    if pup["x"] > W + 10 * S:
                        pup["x"] = -DOG_W * U - 4 * S
                    lift = round(amp * 2) if frame in (1, 2) else 0
                    dog(pup["x"], oy - lift * U / 2, "run", frame, accent, t)
                else:
                    dog(pup["x"], oy, "pant", 0, accent, t)
            elif kind == "working":
                dx = W * 0.62
                if random.random() < 0.35:
                    pup["dirt"].append({"x": dx + 10 * U, "y": ground - 2 * S, "vx": -(30 + random.random() * 50) * S,
                                        "vy": -(30 + random.random() * 40) * S, "life": 1.0})
                dirt = []
                for d in pup["dirt"]:
                    d["life"] -= dt * 1.4
                    if d["life"] <= 0:
                        continue
                    d["x"] += d["vx"] * dt
                    d["y"] += d["vy"] * dt
                    d["vy"] += 160 * S * dt
                    x, y = round(d["x"]), round(d["y"])
                    cv.create_rectangle(x, y, x + round(2 * S), y + round(2 * S), fill=_lerp_hex(BG, DIRT, d["life"]),
                                        width=0, tags=("visual", "dog"))
                    dirt.append(d)
                pup["dirt"] = dirt
                cv.create_rectangle(dx + 9 * U, ground, dx + 9 * U + 16 * S, ground + 3 * S, fill="#3a2c1c", width=0,
                                    tags=("visual", "dog"))
                dog(dx, oy, "dig", 0, accent, t)
            elif kind == "done":
                dx = W * 0.64
                dog(dx, oy, "sit", 0, accent, t)
                if int(t * 2) % 2 == 0:  # a blinking pixel checkmark
                    for a, b in ((0, 0), (1, 1), (2, 2), (3, 1), (4, 0), (5, -1), (6, -2)):
                        x, y = dx + DOG_W * U + (a * 1.5) * S, (12 + b * 1.5) * S
                        cv.create_rectangle(x, y, x + 1.6 * S, y + 1.6 * S, fill=accent, width=0, tags=("visual", "dog"))
            else:
                dog(W * 0.64, oy, "pant", 0, accent, t)
            cv.tag_raise("mask")

        def draw(now):
            kind = st["kind"]
            t = now - st["t0"]
            dt = min(0.1, now - st["last"])
            st["last"] = now
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

            if self.style == "dog":
                draw_dog(kind, amp, t, dt)
                return
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
                    cv.coords(vis["glows"][i], *pts)
                    cv.coords(vis["lines"][i], *pts)
            elif self.style == "bars":
                dim_c, bright_c = SOFT_BARS.get(kind, SOFT_BARS["info"])
                gap = span / max(1, BAR_COUNT - 1)
                for i, bar in enumerate(vis["bars"]):
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
                for item, radius in zip(vis["orbs"], radii):
                    cv.coords(item, cx - radius, r - radius, cx + radius, r + radius)
            else:
                gap = span / max(1, DOT_COUNT - 1)
                radius = max(2 * S, 2.8 * S + amp * 1.8 * S)
                for i, dot in enumerate(vis["dots"]):
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
                    elif cmd == "style":
                        self.style = kind
                        build()
                        if st["kind"]:
                            showing, st["kind"] = st["kind"], None
                            apply(showing, st["text"])
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

        build()
        tick()
        root.mainloop()
