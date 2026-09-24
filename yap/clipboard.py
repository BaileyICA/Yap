"""Save and restore the whole Windows clipboard (images, rich text, files), not just plain text."""
import ctypes
import time
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

_user32.OpenClipboard.argtypes = (wintypes.HWND,)
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.CloseClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.EnumClipboardFormats.argtypes = (wintypes.UINT,)
_user32.EnumClipboardFormats.restype = wintypes.UINT
_user32.GetClipboardData.argtypes = (wintypes.UINT,)
_user32.GetClipboardData.restype = wintypes.HANDLE
_user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
_user32.SetClipboardData.restype = wintypes.HANDLE
_kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalFree.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalLock.restype = wintypes.LPVOID
_kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalSize.argtypes = (wintypes.HGLOBAL,)
_kernel32.GlobalSize.restype = ctypes.c_size_t

_GMEM_MOVEABLE = 0x0002
# Formats held as GDI/metafile handles rather than memory blocks. Windows re-creates
# CF_BITMAP from CF_DIB, so images still come back.
_HANDLE_FORMATS = {2, 3, 9, 14, 0x80, 0x82, 0x83, 0x8E}
_SYNTHESIZED = {1, 7, 16}  # CF_TEXT, CF_OEMTEXT, CF_LOCALE: Windows derives them from CF_UNICODETEXT


def _open():
    for _ in range(20):  # another app may be holding the clipboard for a moment
        if _user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def _restorable(fmt):
    return fmt not in _HANDLE_FORMATS and fmt not in _SYNTHESIZED and not 0x300 <= fmt <= 0x3FF


def save():
    """Snapshot the clipboard as [(format, bytes)], or None if it could not be read."""
    if not _open():
        return None
    items = []
    try:
        fmt = _user32.EnumClipboardFormats(0)
        while fmt:
            if _restorable(fmt):
                handle = _user32.GetClipboardData(fmt)
                if handle:
                    size = _kernel32.GlobalSize(handle)
                    pointer = _kernel32.GlobalLock(handle)
                    if pointer:
                        try:
                            items.append((fmt, ctypes.string_at(pointer, size)))
                        finally:
                            _kernel32.GlobalUnlock(handle)
            fmt = _user32.EnumClipboardFormats(fmt)
    finally:
        _user32.CloseClipboard()
    return items


def restore(items):
    """Put back a snapshot from save(). An empty snapshot leaves the clipboard empty."""
    if items is None or not _open():
        return
    try:
        _user32.EmptyClipboard()
        for fmt, data in items:
            handle = _kernel32.GlobalAlloc(_GMEM_MOVEABLE, max(1, len(data)))
            if not handle:
                continue
            pointer = _kernel32.GlobalLock(handle)
            if not pointer:
                _kernel32.GlobalFree(handle)
                continue
            ctypes.memmove(pointer, data, len(data))
            _kernel32.GlobalUnlock(handle)
            if not _user32.SetClipboardData(fmt, handle):
                _kernel32.GlobalFree(handle)  # on success the clipboard owns the memory
    finally:
        _user32.CloseClipboard()
