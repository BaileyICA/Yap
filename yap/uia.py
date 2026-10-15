"""Read the text of the focused field in any app through Windows UI Automation (plain ctypes COM, no extra packages)."""
import ctypes
import threading
from ctypes import wintypes

_ole32 = ctypes.OleDLL("ole32")
_oleaut32 = ctypes.WinDLL("oleaut32")
_oleaut32.SysStringLen.argtypes = (ctypes.c_void_p,)
_oleaut32.SysStringLen.restype = wintypes.UINT
_oleaut32.SysFreeString.argtypes = (ctypes.c_void_p,)

_PTR = ctypes.POINTER(ctypes.c_void_p)
_TEXT_PATTERN, _VALUE_PATTERN = 10014, 10002
_local = threading.local()  # COM objects belong to the thread (apartment) that made them


class _GUID(ctypes.Structure):
    _fields_ = [("a", wintypes.DWORD), ("b", wintypes.WORD), ("c", wintypes.WORD), ("d", ctypes.c_ubyte * 8)]


def _guid(text):
    guid = _GUID()
    _ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
    return guid


def _call(obj, index, *args):
    """Call vtable slot `index` of COM interface `obj`; a failed HRESULT raises OSError."""
    vtable = ctypes.cast(obj, ctypes.POINTER(_PTR)).contents
    argtypes = [ctypes.c_int if isinstance(a, int) else ctypes.c_void_p for a in args]  # ints or byref()s
    return ctypes.WINFUNCTYPE(ctypes.HRESULT, ctypes.c_void_p, *argtypes)(vtable[index])(obj, *args)


def _release(obj):
    if obj:
        ctypes.WINFUNCTYPE(wintypes.ULONG, ctypes.c_void_p)(ctypes.cast(obj, ctypes.POINTER(_PTR)).contents[2])(obj)


def _automation():
    if getattr(_local, "uia", None) is None:
        try:
            _ole32.CoInitializeEx(None, 2)  # COINIT_APARTMENTTHREADED
        except OSError:
            pass  # already initialised in another mode; COM still works
        uia = ctypes.c_void_p()
        _ole32.CoCreateInstance(ctypes.byref(_guid("{ff48dba4-60ef-4201-aa87-54103eef594e}")), None, 1,
                                ctypes.byref(_guid("{30cbe57d-d9d0-452a-ab13-7ac5ac4825ee}")), ctypes.byref(uia))
        _local.uia = uia
    return _local.uia


def _bstr(value):
    if not value:
        return ""
    try:
        return ctypes.wstring_at(value, _oleaut32.SysStringLen(value))
    finally:
        _oleaut32.SysFreeString(value)


def _pattern_text(element):
    pattern = ctypes.c_void_p()
    _call(element, 16, _TEXT_PATTERN, ctypes.byref(pattern))  # IUIAutomationElement::GetCurrentPattern
    if pattern:
        document = ctypes.c_void_p()
        try:
            _call(pattern, 7, ctypes.byref(document))  # IUIAutomationTextPattern::get_DocumentRange
            text = ctypes.c_void_p()
            _call(document, 12, -1, ctypes.byref(text))  # IUIAutomationTextRange::GetText
            return _bstr(text)
        finally:
            _release(document)
            _release(pattern)
    _call(element, 16, _VALUE_PATTERN, ctypes.byref(pattern))
    if pattern:
        try:
            text = ctypes.c_void_p()
            _call(pattern, 4, ctypes.byref(text))  # IUIAutomationValuePattern::get_CurrentValue
            return _bstr(text)
        finally:
            _release(pattern)
    return None


def focused_text():
    """The whole text of the field that has keyboard focus, with \\n line breaks; None if it can't be read."""
    element = ctypes.c_void_p()
    try:
        _call(_automation(), 8, ctypes.byref(element))  # IUIAutomation::GetFocusedElement
        text = _pattern_text(element) if element else None
    except OSError:
        return None
    finally:
        _release(element)
    return None if text is None else text.replace("\r\n", "\n").replace("\r", "\n")
