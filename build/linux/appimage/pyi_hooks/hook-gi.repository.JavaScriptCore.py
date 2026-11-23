# WebKit2-4.1's typelib depends on JavaScriptCore-4.1 (no PyInstaller hook either).
from PyInstaller.utils.hooks.gi import GiModuleInfo

module_info = GiModuleInfo("JavaScriptCore", "4.1")
if module_info.available:
    binaries, datas, hiddenimports = module_info.collect_typelib_data()
