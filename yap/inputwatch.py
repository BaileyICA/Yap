"""Count real (not injected) keystrokes and mouse clicks, so Yap can tell whether the caret may have moved."""
import ctypes
import threading
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_LRESULT = ctypes.c_ssize_t
_HOOKPROC = ctypes.WINFUNCTYPE(_LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_user32.SetWindowsHookExW.argtypes = (ctypes.c_int, _HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD)
_user32.SetWindowsHookExW.restype = wintypes.HHOOK
_user32.CallNextHookEx.argtypes = (wintypes.HHOOK, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)
_user32.CallNextHookEx.restype = _LRESULT
_user32.GetMessageW.argtypes = (ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT)
_user32.GetForegroundWindow.restype = wintypes.HWND


class _KBD(ctypes.Structure):
    _fields_ = [("vkCode", wintypes.DWORD), ("scanCode", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


class _MOUSE(ctypes.Structure):
    _fields_ = [("pt", wintypes.POINT), ("mouseData", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]


_KEY_DOWNS = {0x100, 0x104}  # WM_KEYDOWN, WM_SYSKEYDOWN
_CLICKS = {0x201, 0x204, 0x207, 0x20B}  # left/right/middle/X button down
# Modifiers and lock keys on their own don't change text or move the caret.
_MODIFIER_VKS = {0x10, 0x11, 0x12, 0x14, 0x5B, 0x5C, 0x90, 0x91, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5}
MODIFIER_NAMES = {"ctrl", "shift", "alt", "alt gr", "windows", "caps lock", "num lock", "scroll lock"}

seq = 0  # bumps on every real non-modifier key press or mouse click
_started = False


def _keyboard(code, wparam, lparam):
    global seq
    if code >= 0 and wparam in _KEY_DOWNS:
        info = ctypes.cast(lparam, ctypes.POINTER(_KBD)).contents
        if not info.flags & 0x10 and info.vkCode not in _MODIFIER_VKS:  # LLKHF_INJECTED
            seq += 1
    return _user32.CallNextHookEx(None, code, wparam, lparam)


def _mouse(code, wparam, lparam):
    global seq
    if code >= 0 and wparam in _CLICKS:
        if not ctypes.cast(lparam, ctypes.POINTER(_MOUSE)).contents.flags & 0x01:  # LLMHF_INJECTED
            seq += 1
    return _user32.CallNextHookEx(None, code, wparam, lparam)


_procs = (_HOOKPROC(_keyboard), _HOOKPROC(_mouse))  # keep alive for the life of the hooks


def _run():
    _user32.SetWindowsHookExW(13, _procs[0], None, 0)  # WH_KEYBOARD_LL
    _user32.SetWindowsHookExW(14, _procs[1], None, 0)  # WH_MOUSE_LL
    msg = wintypes.MSG()
    while _user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:  # low-level hooks need a message loop
        pass


def start():
    global _started
    if not _started:
        _started = True
        threading.Thread(target=_run, daemon=True, name="inputwatch").start()


def foreground():
    return _user32.GetForegroundWindow() or 0
