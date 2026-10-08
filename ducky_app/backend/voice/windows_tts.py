"""Offline Windows Speech synthesis, run on bridge workers and played by the UI."""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from typing import Any

_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
Add-Type -AssemblyName System.Speech
$request = [Console]::In.ReadToEnd() | ConvertFrom-Json
$synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
$stream = $null
try {
  $voices = @($synth.GetInstalledVoices() | Where-Object Enabled | ForEach-Object {
    @{ id = $_.VoiceInfo.Name; label = $_.VoiceInfo.Name; lang = $_.VoiceInfo.Culture.Name }
  })
  if (-not $request.text) {
    $out = @{ ok = $true; voices = $voices }
  } elseif ($voices.Count -eq 0) {
    $out = @{ ok = $false; code = 'voice_missing'; error = 'No Windows Speech voice is installed. Download a voice in Windows Speech settings.' }
  } else {
    $wanted = [string]$request.voice
    $hit = $voices | Where-Object { $_.id -eq $wanted } | Select-Object -First 1
    # Chromium labels append a language description to the same Windows voice.
    if (-not $hit -and $wanted) {
      $hit = $voices | Where-Object { $short = $_.id -replace ' Desktop$', ''; $wanted -eq $short -or $wanted.StartsWith($short + ' -') } | Select-Object -First 1
    }
    if ($wanted -and -not $hit) {
      $out = @{ ok = $false; code = 'voice_missing'; error = "Windows voice '$wanted' is not installed. Download it in Windows Speech settings." }
    } else {
      if ($hit) { $synth.SelectVoice($hit.id) }
      $stream = New-Object System.IO.MemoryStream
      $synth.SetOutputToWaveStream($stream)
      $synth.Speak([string]$request.text)
      $out = @{ ok = $true; audio_base64 = [Convert]::ToBase64String($stream.ToArray()); mime = 'audio/wav' }
    }
  }
  [Console]::Out.WriteLine(($out | ConvertTo-Json -Compress -Depth 5))
} finally {
  $synth.Dispose()
  if ($stream) { $stream.Dispose() }
}
"""


def _run(text: str = "", voice_id: str = "") -> dict[str, Any]:
    if sys.platform != "win32":
        return {"ok": False, "error": "Windows Speech is only available on Windows."}
    # Input is data on stdin, never interpolated into PowerShell source.
    request = json.dumps({"text": str(text or "")[:12000], "voice": str(voice_id or "")[:256]}, ensure_ascii=False)
    encoded = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
            input=request, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if proc.returncode:
            return {"ok": False, "error": proc.stderr.strip()[:400] or "Windows Speech failed."}
        result = json.loads(proc.stdout)
        if not isinstance(result, dict):
            raise ValueError("Invalid Windows Speech response")
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": str(exc) or "Windows Speech failed."}


def start(text: str, voice_id: str = "") -> dict[str, Any]:
    from frontend.ui_web.bridge_jobs import job_start

    if not str(text or "").strip():
        return {"ok": False, "error": "Text is required."}
    return job_start(lambda: _run(text, voice_id))


def voices_start() -> dict[str, Any]:
    from frontend.ui_web.bridge_jobs import job_start

    return job_start(_run)


def poll(job_id: str) -> dict[str, Any]:
    from frontend.ui_web.bridge_jobs import job_poll

    return job_poll(job_id)
