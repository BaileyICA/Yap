"""Which app the dictation is going into, so text can be formatted to suit it (emails, ...)."""
import ctypes
import os
import re
from ctypes import wintypes

_user32 = ctypes.WinDLL("user32", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32.GetForegroundWindow.restype = wintypes.HWND
_user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
_user32.GetWindowTextLengthW.argtypes = (wintypes.HWND,)
_user32.GetWindowTextW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
_kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.QueryFullProcessImageNameW.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD))
_kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)


def foreground():
    """(program file name, window title) of the window in front, e.g. ("chrome.exe", "Inbox - Gmail")."""
    hwnd = _user32.GetForegroundWindow()
    if not hwnd:
        return "", ""
    title = ctypes.create_unicode_buffer(_user32.GetWindowTextLengthW(hwnd) + 1)
    _user32.GetWindowTextW(hwnd, title, len(title))
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    exe = ""
    process = _kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if process:
        try:
            path = ctypes.create_unicode_buffer(1024)
            size = wintypes.DWORD(len(path))
            if _kernel32.QueryFullProcessImageNameW(process, 0, path, ctypes.byref(size)):
                exe = os.path.basename(path.value)
        finally:
            _kernel32.CloseHandle(process)
    return exe, title.value


def matches(patterns, exe, title):
    """True if any pattern names this program ("outlook.exe") or appears in its window title ("Gmail")."""
    for p in patterns:
        p = p.strip()
        if not p:
            continue
        if p.lower().endswith(".exe"):
            if p.lower() == exe.lower():
                return True
        elif re.search(r"(?<!\w)" + re.escape(p) + r"(?!\w)", title, re.IGNORECASE):
            return True
    return False
