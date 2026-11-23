# PyInstaller runtime hook (Linux): sounddevice finds the bundled PortAudio.
# ctypes.util.find_library asks ldconfig, which never sees a library inside the
# frozen app, so the AppImage's microphone would need the distro's libportaudio2.
import ctypes.util
import os
import sys

_real_find_library = ctypes.util.find_library


def _find_library(name):
    if name == "portaudio":
        bundled = os.path.join(getattr(sys, "_MEIPASS", ""), "libportaudio.so.2")
        if os.path.isfile(bundled):
            return bundled
    return _real_find_library(name)


ctypes.util.find_library = _find_library
