import plistlib
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _info(version="2.31.0"):
    text = (HERE / "Info.plist.in").read_text(encoding="utf-8").replace("@VERSION@", version)
    return plistlib.loads(text.encode("utf-8"))


def test_info_plist_keys():
    info = _info("2.31.0")
    assert info["CFBundleIdentifier"] == "online.uefnducky.Ducky"
    assert info["CFBundleShortVersionString"] == "2.31.0"
    assert info["CFBundleVersion"] == "2.31.0"
    assert info["LSMinimumSystemVersion"] == "13.0"
    assert info["LSApplicationCategoryType"] == "public.app-category.developer-tools"
    assert info["NSHighResolutionCapable"] is True
    for key in ("NSMicrophoneUsageDescription", "NSAppleEventsUsageDescription", "NSScreenCaptureUsageDescription"):
        assert info[key].strip(), key


def test_entitlements_keys():
    ent = plistlib.loads((HERE / "entitlements.plist").read_bytes())
    want = {
        "com.apple.security.cs.allow-jit",
        "com.apple.security.cs.allow-unsigned-executable-memory",
        "com.apple.security.cs.disable-library-validation",
        "com.apple.security.device.audio-input",
        "com.apple.security.automation.apple-events",
    }
    assert set(ent) == want
    assert all(ent[k] is True for k in want)
