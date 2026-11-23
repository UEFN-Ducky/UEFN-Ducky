# PyInstaller ships no WebKit2 hook: without it the WebKit2-4.1 typelib pywebview's GTK backend
# needs is never collected. The library itself stays the host's (unified.spec LINUX_HOST_LIBS).
from PyInstaller.utils.hooks.gi import GiModuleInfo

module_info = GiModuleInfo("WebKit2", "4.1")
if module_info.available:
    binaries, datas, hiddenimports = module_info.collect_typelib_data()
