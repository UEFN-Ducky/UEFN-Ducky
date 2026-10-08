"""Windows built-in speech recognition for the desktop panel (no API key).

WebView2 exposes ``webkitSpeechRecognition`` but ships no speech service, so
browser speech always fails with ``network`` inside the app. This drives
``Windows.Media.SpeechRecognition`` (the engine behind Windows dictation) from
one warm PowerShell helper instead: live interim words, finals per phrase, mic
owned by Windows (default input device).

Dictation needs Settings → Privacy & security → Speech → *Online speech
recognition*. When it is off, start() returns ``code="speech_privacy"`` so the
UI can link straight to that page — this module never changes the setting.

Protocol: stdin lines ``start <sid> [lang]`` / ``stop <sid>`` / ``cancel <sid>``
/ ``quit``; stdout JSON lines ``{"t": ..., "sid": ...}`` with t in ready,
started, interim, final, speech_started, speech_stopped, error, ended.
"""

from __future__ import annotations

import atexit
import base64
import json
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from typing import Any

# PowerShell cannot subscribe to WinRT events (Register-ObjectEvent refuses), so a
# tiny reflective C# pump builds typed handlers that only enqueue; the PowerShell
# loop owns every WinRT call. No WinRT references → compiles on stock csc.
_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding $false
[Console]::OutputEncoding = $utf8
[Console]::InputEncoding = $utf8
Add-Type -AssemblyName System.Runtime.WindowsRuntime
Add-Type -TypeDefinition @'
using System;
using System.Collections.Concurrent;
using System.Linq.Expressions;
using System.Reflection;
public static class DuckySttPump {
  public static readonly BlockingCollection<object[]> Queue = new BlockingCollection<object[]>();
  public static void Push(string tag, object args) { Queue.Add(new object[] { tag, args }); }
  public static void Attach(object target, string eventName, string tag) {
    EventInfo ev = target.GetType().GetEvent(eventName);
    Type h = ev.EventHandlerType;
    ParameterInfo[] ps = h.GetMethod("Invoke").GetParameters();
    ParameterExpression s = Expression.Parameter(ps[0].ParameterType, "s");
    ParameterExpression a = Expression.Parameter(ps[1].ParameterType, "a");
    Expression call = Expression.Call(typeof(DuckySttPump).GetMethod("Push"), Expression.Constant(tag), Expression.Convert(a, typeof(object)));
    ev.GetAddMethod().Invoke(target, new object[] { Expression.Lambda(h, call, s, a).Compile() });
  }
  public static void StartStdinReader() {
    var t = new System.Threading.Thread(() => {
      string line;
      while ((line = Console.In.ReadLine()) != null) Push("cmd|", line);
      Push("cmd|", "quit");
    });
    t.IsBackground = true;
    t.Start();
  }
}
'@
[Windows.Media.SpeechRecognition.SpeechRecognizer,Windows.Media.SpeechRecognition,ContentType=WindowsRuntime] | Out-Null
[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime] | Out-Null
$ext = [System.WindowsRuntimeSystemExtensions].GetMethods()
$asTaskOp = ($ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' })[0]
$asTaskAction = ($ext | Where-Object { $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncAction' })[0]
function Await-Op($op, [Type]$t) { $task = $asTaskOp.MakeGenericMethod($t).Invoke($null, @($op)); $task.Wait(-1) | Out-Null; $task.Result }
function Await-Action($op) { $task = $asTaskAction.Invoke($null, @($op)); $task.Wait(-1) | Out-Null }
function Emit($obj) { [Console]::Out.WriteLine(($obj | ConvertTo-Json -Compress)); [Console]::Out.Flush() }
function Hr($err) { $b = $err.Exception.GetBaseException(); '{0:X8}' -f $b.HResult }

$script:rec = $null
$script:sid = ''

function Drop-Rec {
  if ($null -eq $script:rec) { return }
  try { Await-Action ($script:rec.ContinuousRecognitionSession.CancelAsync()) } catch {}
  try { $script:rec.Dispose() } catch {}
  $script:rec = $null
}

function Start-Rec([string]$id, [string]$lang) {
  Drop-Rec
  $script:sid = $id
  $r = $null
  if ($lang) { try { $r = New-Object Windows.Media.SpeechRecognition.SpeechRecognizer (New-Object Windows.Globalization.Language $lang) } catch { $r = $null } }
  if ($null -eq $r) { $r = New-Object Windows.Media.SpeechRecognition.SpeechRecognizer }
  $r.ContinuousRecognitionSession.AutoStopSilenceTimeout = [TimeSpan]::FromMinutes(30)
  [DuckySttPump]::Attach($r, 'HypothesisGenerated', "hyp|$id")
  [DuckySttPump]::Attach($r, 'StateChanged', "state|$id")
  [DuckySttPump]::Attach($r.ContinuousRecognitionSession, 'ResultGenerated', "res|$id")
  [DuckySttPump]::Attach($r.ContinuousRecognitionSession, 'Completed', "done|$id")
  $c = Await-Op ($r.CompileConstraintsAsync()) ([Windows.Media.SpeechRecognition.SpeechRecognitionCompilationResult])
  if ([string]$c.Status -ne 'Success') {
    try { $r.Dispose() } catch {}
    Emit @{ t = 'error'; sid = $id; code = 'language'; message = "Windows speech can't use this language ($([string]$c.Status))." }
    return
  }
  try {
    Await-Action ($r.ContinuousRecognitionSession.StartAsync())
  } catch {
    try { $r.Dispose() } catch {}
    $hr = Hr $_
    $code = 'failed'
    if ($hr -eq '80045509') { $code = 'speech_privacy' }
    elseif ($hr -eq '80070005') { $code = 'mic_privacy' }
    Emit @{ t = 'error'; sid = $id; code = $code; hr = $hr; message = $_.Exception.GetBaseException().Message }
    return
  }
  $script:rec = $r
  Emit @{ t = 'started'; sid = $id; lang = $r.CurrentLanguage.LanguageTag }
}

[DuckySttPump]::StartStdinReader()
Emit @{ t = 'ready' }
while ($true) {
  $item = $null
  if (-not [DuckySttPump]::Queue.TryTake([ref]$item, 1000)) { continue }
  $parts = ([string]$item[0]).Split('|')
  $kind = $parts[0]
  $tag = $parts[1]
  $a = $item[1]
  try {
    if ($kind -eq 'cmd') {
      $words = ([string]$a).Trim().Split(' ')
      $verb = $words[0]
      $arg = ''
      if ($words.Count -gt 1) { $arg = $words[1] }
      if ($verb -eq 'quit') { Drop-Rec; break }
      if ($verb -eq 'start') { $lang = ''; if ($words.Count -gt 2) { $lang = $words[2] }; Start-Rec $arg $lang; continue }
      if ($arg -ne $script:sid -or $null -eq $script:rec) { Emit @{ t = 'ended'; sid = $arg; status = 'NotRunning' }; continue }
      if ($verb -eq 'stop') { try { Await-Action ($script:rec.ContinuousRecognitionSession.StopAsync()) } catch { Drop-Rec; Emit @{ t = 'ended'; sid = $arg; status = 'Stopped' } }; continue }
      if ($verb -eq 'cancel') { Drop-Rec; Emit @{ t = 'ended'; sid = $arg; status = 'UserCanceled' }; continue }
      continue
    }
    if ($tag -ne $script:sid) { continue }
    if ($kind -eq 'hyp') { Emit @{ t = 'interim'; sid = $tag; text = [string]$a.Hypothesis.Text }; continue }
    if ($kind -eq 'res') {
      $res = $a.Result
      if ([string]$res.Status -eq 'Success' -and [string]$res.Confidence -ne 'Rejected' -and ([string]$res.Text).Trim()) {
        Emit @{ t = 'final'; sid = $tag; text = [string]$res.Text; confidence = [string]$res.Confidence }
      }
      continue
    }
    if ($kind -eq 'state') {
      $st = [string]$a.State
      if ($st -eq 'SpeechDetected') { Emit @{ t = 'speech_started'; sid = $tag } }
      elseif ($st -eq 'SoundEnded') { Emit @{ t = 'speech_stopped'; sid = $tag } }
      continue
    }
    if ($kind -eq 'done') {
      $st = [string]$a.Status
      if ($null -ne $script:rec) { try { $script:rec.Dispose() } catch {}; $script:rec = $null }
      if ($st -eq 'MicrophoneUnavailable') { Emit @{ t = 'error'; sid = $tag; code = 'no_mic'; message = 'No microphone is available to Windows speech.' } }
      elseif ($st -eq 'NetworkFailure') { Emit @{ t = 'error'; sid = $tag; code = 'network'; message = "Windows speech couldn't reach the speech service. Check your internet connection." } }
      elseif ($st -eq 'AudioQualityFailure') { Emit @{ t = 'error'; sid = $tag; code = 'audio'; message = 'Windows speech could not hear the microphone clearly.' } }
      Emit @{ t = 'ended'; sid = $tag; status = $st }
      continue
    }
  } catch {
    Emit @{ t = 'error'; sid = $script:sid; code = 'failed'; hr = (Hr $_); message = $_.Exception.GetBaseException().Message }
  }
}
"""

_START_TIMEOUT_S = 12.0
_ENDED_EVENTS = frozenset({"ended"})


def windows_speech_supported() -> bool:
    return sys.platform == "win32"


class _Helper:
    """One warm PowerShell process; events land in a ring with a monotonic seq."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._proc: subprocess.Popen[str] | None = None
        self._events: deque[tuple[int, dict[str, Any]]] = deque(maxlen=4000)
        self._seq = 0
        self._ready = False
        self._stderr_tail: deque[str] = deque(maxlen=20)

    # -- process ---------------------------------------------------------
    def _alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def ensure(self) -> None:
        with self._lock:
            if self._alive():
                return
            self._ready = False
            encoded = base64.b64encode(_SCRIPT.encode("utf-16-le")).decode("ascii")
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._proc = subprocess.Popen(
                [
                    "powershell.exe",
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-EncodedCommand",
                    encoded,
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=flags,
            )
            proc = self._proc
        threading.Thread(target=self._read_stdout, args=(proc,), name="winstt-out", daemon=True).start()
        threading.Thread(target=self._read_stderr, args=(proc,), name="winstt-err", daemon=True).start()

    def _push(self, event: dict[str, Any]) -> None:
        with self._cond:
            self._seq += 1
            self._events.append((self._seq, event))
            if event.get("t") == "ready":
                self._ready = True
            self._cond.notify_all()

    def _read_stdout(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if isinstance(event, dict):
                self._push(event)
        detail = " ".join(self._stderr_tail).strip()
        self._push({"t": "exit", "message": detail[:400] or "Windows speech helper stopped"})

    def _read_stderr(self, proc: subprocess.Popen[str]) -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            text = line.strip()
            if text:
                self._stderr_tail.append(text)

    def send(self, line: str) -> None:
        self.ensure()
        proc = self._proc
        if proc is None or proc.stdin is None:
            raise RuntimeError("Windows speech helper is not running")
        proc.stdin.write(line + "\n")
        proc.stdin.flush()

    def cursor(self) -> int:
        with self._lock:
            return self._seq

    def wait_ready(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            while not self._ready:
                if not self._alive() and not self._ready:
                    return False
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
            return True

    def wait_events(self, sid: str, cursor: int, timeout: float) -> tuple[list[tuple[int, dict[str, Any]]], int]:
        """(seq, event) pairs for ``sid`` (plus helper exits) after ``cursor``; blocks up to ``timeout``."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._cond:
            while True:
                out = [
                    (seq, ev)
                    for seq, ev in self._events
                    if seq > cursor and (ev.get("sid") == sid or ev.get("t") == "exit")
                ]
                if out or time.monotonic() >= deadline:
                    return out, self._seq
                self._cond.wait(max(0.0, deadline - time.monotonic()))

    def shutdown(self) -> None:
        with self._lock:
            proc = self._proc
            self._proc = None
            self._ready = False
        if proc is None or proc.poll() is not None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.write("quit\n")
                proc.stdin.flush()
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


_helper = _Helper()
# The helper also exits when our stdin pipe closes (crash / kill), so no orphan survives.
atexit.register(_helper.shutdown)


_FRIENDLY = {
    "speech_privacy": "Windows speech is off. Turn on Online speech recognition in Windows Settings.",
    "mic_privacy": "Windows blocked the microphone. Allow microphone access for desktop apps in Windows Settings.",
    "no_mic": "No microphone found. Plug one in or pick it in Windows sound settings.",
}


def _friendly_error(code: str, raw: Any) -> str:
    """WinRT messages carry a junk 'text could not be found' prefix — map known codes instead."""
    if code in _FRIENDLY:
        return _FRIENDLY[code]
    text = " ".join(str(raw or "").replace("The text associated with this error code could not be found.", "").split())
    return text or "Windows speech failed."


def _unsupported() -> dict[str, Any]:
    return {"ok": False, "code": "unsupported", "error": "Windows speech is only available on Windows."}


def prewarm() -> dict[str, Any]:
    """Spawn the helper early so the first mic press does not pay PowerShell startup."""
    if not windows_speech_supported():
        return _unsupported()
    try:
        _helper.ensure()
    except Exception as exc:  # noqa: BLE001 — fail soft across the JS bridge
        return {"ok": False, "code": "failed", "error": str(exc)}
    return {"ok": True}


def start_session(lang: str = "") -> dict[str, Any]:
    """Start continuous dictation; returns the session id and the event cursor to poll from."""
    if not windows_speech_supported():
        return _unsupported()
    try:
        _helper.ensure()
        if not _helper.wait_ready(_START_TIMEOUT_S):
            return {"ok": False, "code": "failed", "error": "Windows speech helper did not start."}
        sid = uuid.uuid4().hex[:12]
        seen = _helper.cursor()
        tag = "".join(ch for ch in (lang or "") if ch.isalnum() or ch == "-")[:20]
        _helper.send(f"start {sid} {tag}".strip())
        deadline = time.monotonic() + _START_TIMEOUT_S
        while time.monotonic() < deadline:
            events, _ = _helper.wait_events(sid, seen, deadline - time.monotonic())
            for seq, ev in events:
                seen = seq
                kind = ev.get("t")
                if kind == "started":
                    # Poll from here: words spoken right after start must not be skipped.
                    return {"ok": True, "session": sid, "cursor": seq, "lang": ev.get("lang", "")}
                if kind == "error":
                    code = str(ev.get("code") or "failed")
                    return {"ok": False, "code": code, "error": _friendly_error(code, ev.get("message"))}
                if kind == "exit":
                    return {"ok": False, "code": "failed", "error": str(ev.get("message") or "helper exited")}
        _helper.send(f"cancel {sid}")
        return {"ok": False, "code": "failed", "error": "Windows speech took too long to start."}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": "failed", "error": str(exc) or "Windows speech failed"}


def poll_session(sid: str, cursor: int, wait_ms: int = 400) -> dict[str, Any]:
    """Long-poll: events after ``cursor`` for ``sid``. ``active`` is false once the session ended."""
    sid = str(sid or "")
    try:
        cur = int(cursor)
    except (TypeError, ValueError):
        cur = 0
    wait = max(0, min(int(wait_ms or 0), 2000)) / 1000.0
    pairs, next_cursor = _helper.wait_events(sid, cur, wait)
    events = [ev for _, ev in pairs]
    for ev in events:
        if ev.get("t") == "error":
            ev["message"] = _friendly_error(str(ev.get("code") or ""), ev.get("message"))
    ended = any(ev.get("t") in _ENDED_EVENTS or ev.get("t") == "exit" for ev in events)
    return {"ok": True, "events": events, "cursor": next_cursor, "active": not ended}


def stop_session(sid: str) -> dict[str, Any]:
    """Graceful stop: the last phrase still arrives as a final before ``ended``."""
    try:
        _helper.send(f"stop {str(sid or '')}")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def cancel_session(sid: str) -> dict[str, Any]:
    try:
        _helper.send(f"cancel {str(sid or '')}")
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True}


def shutdown() -> None:
    _helper.shutdown()


_SETTINGS_PAGES = {
    "speech_privacy": "ms-settings:privacy-speech",
    "mic_privacy": "ms-settings:privacy-microphone",
    "sound_input": "ms-settings:sound",
    "voice_download": "ms-settings:speech",
}


def open_windows_settings(page: str) -> dict[str, Any]:
    """Open a whitelisted Windows Settings page (the user flips the switch there)."""
    uri = _SETTINGS_PAGES.get(str(page or "").strip())
    if not uri or not windows_speech_supported():
        return {"ok": False, "error": "unknown settings page"}
    try:
        import os

        os.startfile(uri)  # type: ignore[attr-defined]  # noqa: S606 — fixed ms-settings URI
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": str(exc)}
    return {"ok": True}
