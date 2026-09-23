"""Yap's dog logo, sized and tinted for Windows and the system tray."""

from functools import lru_cache
from pathlib import Path

from PIL import Image


BRAND_NAVY = "#0d3260"
CREAM = "#fdf7e8"
APP_USER_MODEL_ID = "FreeFlow.Yap"
LOGO_PATH = Path(__file__).resolve().parent.parent / "assets" / "yap-logo.png"


def register_windows_app_id():
    """Give Windows a stable identity for taskbar grouping and pinning."""
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        pass


@lru_cache(maxsize=1)
def _logo_mask():
    with Image.open(LOGO_PATH) as source:
        red = source.convert("RGB").getchannel("R")
    # The approved mark is navy on cream. Recover its soft edges while removing
    # the slight variation in the concept image's background.
    mask = red.point(lambda value: max(0, min(255, round((245 - value) * 255 / 215))))
    bounds = mask.getbbox()
    if bounds is None:
        raise ValueError(f"No dog logo found in {LOGO_PATH}")
    left, top, right, bottom = bounds
    side = round(max(right - left, bottom - top) * 1.2)
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    box = (round(center_x - side / 2), round(center_y - side / 2),
           round(center_x + side / 2), round(center_y + side / 2))
    return mask.crop(box)


def icon(color=BRAND_NAVY, size=64):
    """Return the dog mark with a cream background and a status-colored bubble."""
    mask = _logo_mask().resize((size, size), Image.Resampling.LANCZOS)
    image = Image.new("RGBA", (size, size), CREAM)
    image.paste(Image.new("RGBA", (size, size), color), (0, 0), mask)
    return image
