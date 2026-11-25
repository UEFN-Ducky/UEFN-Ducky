"""On-demand CPU-only Whisper Small Q8 dictation for the Windows desktop."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.request
import wave
import zipfile
from pathlib import Path

from frontend.app_paths import resolve_app_data_dir

_VERSION = "1.9.2"
_MODEL = "ggml-small-q8_0.bin"
_CLI = "whisper-cli.exe"
_MODEL_URL = f"https://huggingface.co/ggerganov/whisper.cpp/resolve/5359861c739e955e79d9a303bcbc70fb988958b1/{_MODEL}"
_RUNTIME_URL = f"https://github.com/ggml-org/whisper.cpp/releases/download/v{_VERSION}/whisper-bin-x64.zip"
_MODEL_SHA = "49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f"
_RUNTIME_SHA = "49dcc16de826f20bd53d44f947a1ae49dfa81f86cad67a64d80820cb192d674a"
_MODEL_BYTES = 264_464_607
_RUNTIME_BYTES = 8_194_445
_REQUIRED = (_CLI, "whisper.dll", "ggml.dll", "ggml-base.dll", "ggml-cpu-x64.dll")
_INSTALL_LOCK = threading.Lock()


def _folder() -> Path:
    return resolve_app_data_dir() / "tools" / "whisper" / _VERSION


def installed() -> bool:
    root = _folder()
    try:
        return (root / _MODEL).stat().st_size == _MODEL_BYTES and all((root / name).is_file() for name in _REQUIRED)
    except FileNotFoundError:
        return False


def _download(url: str, target: Path, size: int, checksum: str) -> None:
    digest = hashlib.sha256()
    total = 0
    with urllib.request.urlopen(url, timeout=45) as response, target.open("wb") as out:
        while chunk := response.read(256 * 1024):
            total += len(chunk)
            if total > size:
                raise ValueError("Unexpected download size.")
            digest.update(chunk)
            out.write(chunk)
    if total != size or digest.hexdigest() != checksum:
        raise ValueError("Download verification failed. Click Dictate to retry.")


def install() -> dict:
    if sys.platform != "win32":
        return {"ok": False, "error": "Whisper Small Q8 installation requires Windows."}
    with _INSTALL_LOCK:
        root = _folder()
        try:
            if installed():
                return {"ok": True}
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=root.parent) as tmp:
                stage = Path(tmp)
                archive = stage / "runtime.zip"
                _download(_RUNTIME_URL, archive, _RUNTIME_BYTES, _RUNTIME_SHA)
                _download(_MODEL_URL, stage / _MODEL, _MODEL_BYTES, _MODEL_SHA)
                with zipfile.ZipFile(archive) as package:
                    names = [Path(entry).name for entry in package.namelist()
                             if entry.startswith("Release/ggml-cpu-") and entry.endswith(".dll")]
                    for name in dict.fromkeys([*_REQUIRED, *names]):
                        with package.open(f"Release/{name}") as src, (stage / name).open("wb") as dst:
                            shutil.copyfileobj(src, dst)
                # Publish complete files only. The model is the final readiness marker.
                for path in [*(stage.glob("*.dll")), stage / _CLI, stage / _MODEL]:
                    target = root / path.name
                    if not target.exists() or target.stat().st_size != path.stat().st_size:
                        path.replace(target)
            # Remove the previous Ducky-managed model only after the upgrade succeeds.
            (root / "ggml-tiny-q5_1.bin").unlink(missing_ok=True)
            return {"ok": True}
        except (OSError, ValueError, EOFError, KeyError, zipfile.BadZipFile) as exc:
            return {"ok": False, "error": f"Whisper installation failed: {exc}"}


def transcribe(encoded: str) -> dict:
    if not installed():
        return {"ok": False, "error": "Click Dictate to install Whisper Small Q8."}
    try:
        # The desktop sends short mono, 16 kHz PCM phrases.
        if len(encoded) > 2_000_000:
            raise ValueError("Dictation segment is too long.")
        audio = base64.b64decode(encoded, validate=True)
        with wave.open(io.BytesIO(audio)) as wav:
            if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (1, 2, 16000):
                raise ValueError("Expected mono 16 kHz PCM audio.")
        root = _folder()
        result = subprocess.run(
            # A named output prefix keeps CLI console text enabled even with stdin as input.
            [str(root / _CLI), "-m", str(root / _MODEL), "-l", "auto", "-nt", "-np", "-of", "dictation", "-f", "-"],
            input=audio, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=root,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=90, check=True,
        )
        return {"ok": True, "text": result.stdout.decode("utf-8").strip()}
    except (OSError, ValueError, binascii.Error, EOFError, wave.Error, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"Local dictation failed: {exc}"}
