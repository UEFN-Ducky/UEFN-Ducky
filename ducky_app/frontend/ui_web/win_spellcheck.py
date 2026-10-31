"""Windows' own spell checker (ISpellChecker, the engine behind WebView2's red underlines)
through ctypes: suggestions for one word, and Add to dictionary."""

from __future__ import annotations

import ctypes
import sys
from ctypes import POINTER, WINFUNCTYPE, byref, c_ulong, c_void_p, c_wchar_p

_CLSID_FACTORY = "{7AB36653-1796-484B-BDFA-E74F1DB7C1DC}"
_IID_FACTORY = "{8E018A9D-2415-4677-BF08-794EA61F94BB}"
_MAX = 5


class _GUID(ctypes.Structure):
    _fields_ = [("d1", c_ulong), ("d2", ctypes.c_ushort), ("d3", ctypes.c_ushort), ("d4", ctypes.c_ubyte * 8)]


def _guid(text: str) -> _GUID:
    g = _GUID()
    ctypes.windll.ole32.CLSIDFromString(c_wchar_p(text), byref(g))
    return g


def _method(obj: c_void_p, index: int, *argtypes):
    """COM vtable slot ``index`` of ``obj`` as a callable (HRESULT raises OSError on failure)."""
    vtbl = ctypes.cast(obj, POINTER(c_void_p))[0]
    fn = ctypes.cast(vtbl, POINTER(c_void_p))[index]
    return lambda *args: WINFUNCTYPE(ctypes.HRESULT, c_void_p, *argtypes)(fn)(obj, *args)


def _release(obj: c_void_p) -> None:
    if obj:
        vtbl = ctypes.cast(obj, POINTER(c_void_p))[0]
        WINFUNCTYPE(c_ulong, c_void_p)(ctypes.cast(vtbl, POINTER(c_void_p))[2])(obj)


def _language() -> str:
    buf = ctypes.create_unicode_buffer(85)
    return buf.value if ctypes.windll.kernel32.GetUserDefaultLocaleName(buf, 85) else "en-US"


def _with_checker(use):
    """Run ``use(checker)`` on a fresh ISpellChecker for the user's language (en-US fallback)."""
    ctypes.windll.ole32.CoInitializeEx(None, 2)  # already set up on this thread: harmless
    factory = c_void_p()
    hr = ctypes.windll.ole32.CoCreateInstance(byref(_guid(_CLSID_FACTORY)), None, 1,
                                              byref(_guid(_IID_FACTORY)), byref(factory))
    if hr < 0 or not factory:
        raise OSError(hr, "ISpellCheckerFactory unavailable")
    try:
        for lang in dict.fromkeys((_language(), "en-US")):
            checker = c_void_p()
            try:
                _method(factory, 5, c_wchar_p, POINTER(c_void_p))(lang, byref(checker))  # CreateSpellChecker
            except OSError:
                continue
            try:
                return use(checker)
            finally:
                _release(checker)
        raise OSError("no spell checker for this language")
    finally:
        _release(factory)


def suggest(word: str) -> list[str] | None:
    """Fixes for a misspelled ``word``; None when it is spelled right or nothing can tell."""
    if sys.platform != "win32" or not word:
        return None

    def misspelled(checker: c_void_p) -> bool:
        errors, first = c_void_p(), c_void_p()
        _method(checker, 4, c_wchar_p, POINTER(c_void_p))(word, byref(errors))  # Check
        try:
            return _method(errors, 3, POINTER(c_void_p))(byref(first)) == 0 and bool(first)  # Next
        finally:
            _release(first)
            _release(errors)

    def run(checker: c_void_p) -> list[str] | None:
        if not misspelled(checker):
            return None
        found = c_void_p()
        _method(checker, 5, c_wchar_p, POINTER(c_void_p))(word, byref(found))  # Suggest
        try:
            out: list[str] = []
            nxt = _method(found, 3, c_ulong, POINTER(c_void_p), POINTER(c_ulong)) if found else None
            while nxt and len(out) < _MAX:
                text, got = c_void_p(), c_ulong()
                if nxt(1, byref(text), byref(got)) != 0 or not got.value:
                    break
                out.append(ctypes.wstring_at(text.value))
                ctypes.windll.ole32.CoTaskMemFree(text)
            return out
        finally:
            _release(found)

    try:
        return _with_checker(run)
    except OSError:
        return None


def add_word(word: str) -> bool:
    """Add to dictionary: the user's Windows dictionary, so the underline goes too."""
    if sys.platform != "win32" or not word:
        return False
    try:
        _with_checker(lambda checker: _method(checker, 6, c_wchar_p)(word))  # Add
        return True
    except OSError:
        return False
