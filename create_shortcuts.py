"""Create the Yap app icon plus Start menu and Desktop shortcuts.

Run with:  .venv\\Scripts\\python create_shortcuts.py
"""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from yap.branding import APP_USER_MODEL_ID, BRAND_NAVY, icon  # noqa: E402

ASSETS = os.path.join(ROOT, "assets")
ICO = os.path.join(ASSETS, "yap.ico")
PYW = os.path.join(ROOT, ".venv", "Scripts", "pythonw.exe")

def set_app_id(path, app_id):
    """Stamp a shortcut with Yap's AppUserModelID so pinning the running window
    pins this shortcut (dog icon, -m yap) instead of bare pythonw.exe."""
    import ctypes
    from ctypes import wintypes

    class GUID(ctypes.Structure):
        _fields_ = [("d1", wintypes.DWORD), ("d2", wintypes.WORD), ("d3", wintypes.WORD), ("d4", ctypes.c_ubyte * 8)]

    class PROPERTYKEY(ctypes.Structure):
        _fields_ = [("fmtid", GUID), ("pid", wintypes.DWORD)]

    class PROPVARIANT(ctypes.Structure):
        _fields_ = [("vt", wintypes.WORD), ("reserved", wintypes.WORD * 3),
                    ("value", ctypes.c_wchar_p), ("pad", ctypes.c_void_p)]

    def guid(text):
        g = GUID()
        ctypes.oledll.ole32.CLSIDFromString(text, ctypes.byref(g))
        return g

    ctypes.oledll.ole32.CoInitialize(None)
    store = ctypes.c_void_p()
    ctypes.oledll.shell32.SHGetPropertyStoreFromParsingName(
        path, None, 2, ctypes.byref(guid("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")), ctypes.byref(store))  # GPS_READWRITE, IPropertyStore
    vtable = ctypes.cast(store, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))).contents

    def method(index, restype=ctypes.HRESULT, *argtypes):
        return ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtable[index])

    key = PROPERTYKEY(guid("{9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}"), 5)  # PKEY_AppUserModel_ID
    value = PROPVARIANT(31, (0, 0, 0), app_id)  # VT_LPWSTR
    try:
        method(6, ctypes.HRESULT, ctypes.c_void_p, ctypes.c_void_p)(store, ctypes.byref(key), ctypes.byref(value))  # SetValue
        method(7)(store)  # Commit
    finally:
        method(2, ctypes.c_ulong)(store)  # Release


os.makedirs(ASSETS, exist_ok=True)
img = icon(BRAND_NAVY, 256)
img.save(ICO, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])

ps = f"""
$sh = New-Object -ComObject WScript.Shell
$targets = @(
  (Join-Path $sh.SpecialFolders('Programs') 'Yap.lnk'),
  (Join-Path $sh.SpecialFolders('Desktop') 'Yap.lnk')
)
foreach ($t in $targets) {{
  $s = $sh.CreateShortcut($t)
  $s.TargetPath = '{PYW}'
  $s.Arguments = '-m yap'
  $s.WorkingDirectory = '{ROOT}'
  $s.IconLocation = '{ICO}'
  $s.Description = 'Yap - voice dictation and meeting notes'
  $s.WindowStyle = 7
  $s.Save()
  Write-Output $t
}}
"""
out = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
print(out.stdout.strip() or out.stderr.strip())
for link in out.stdout.splitlines():
    set_app_id(link, APP_USER_MODEL_ID)
