"""OpenAI speech-to-text: batch Whisper REST + Realtime ephemeral client secret."""

from __future__ import annotations

import base64
import json
import mimetypes
import uuid
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

_TRANSCRIBE_URL = "https://api.openai.com/v1/audio/transcriptions"
_CLIENT_SECRETS_URL = "https://api.openai.com/v1/realtime/client_secrets"
_DEFAULT_MODEL = "gpt-4o-mini-transcribe"
_FALLBACK_MODEL = "whisper-1"
_MIME_EXT = {
    "audio/webm": ".webm",
    "audio/webm;codecs=opus": ".webm",
    "audio/ogg": ".ogg",
    "audio/ogg;codecs=opus": ".ogg",
    "audio/mp4": ".mp4",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/pcm": ".wav",
}


class VoiceError(RuntimeError):
    """User-facing voice failure (missing key, HTTP error, bad audio)."""


def _openai_key() -> str:
    from backend.agent.providers import gateway_providers
    from backend.agent.secrets import get_key

    if "openai" not in gateway_providers():
        raise VoiceError(
            "OpenAI gateway is not installed — Settings → Store → Gateways → OpenAI, "
            "then add an API key under Settings → LLMs for voice."
        )
    key = (get_key("openai") or "").strip()
    if not key:
        raise VoiceError(
            "No OpenAI API key — open Settings → LLMs and add an OpenAI key for voice."
        )
    return key


def _log_voice_usage(*, model: str, input_tokens: int = 1, output_tokens: int = 0) -> None:
    """Fail-soft: voice STT uses the OpenAI key outside chat — still show in Settings usage."""
    try:
        from frontend.ui_web.provider_usage_log import log_gateway_usage

        log_gateway_usage(
            provider="openai",
            model=model,
            usage={
                "input_tokens": max(1, int(input_tokens or 0)),
                "output_tokens": max(0, int(output_tokens or 0)),
            },
            agent="voice",
            ducky_label="Voice",
        )
    except Exception:
        pass


def _ext_for_mime(mime: str) -> str:
    cleaned = (mime or "").strip().lower() or "audio/webm"
    if cleaned in _MIME_EXT:
        return _MIME_EXT[cleaned]
    guessed = mimetypes.guess_extension(cleaned.split(";")[0].strip())
    return guessed or ".webm"


def _multipart_body(
    *,
    fields: dict[str, str],
    file_field: str,
    filename: str,
    file_bytes: bytes,
    file_mime: str,
    boundary: str,
) -> bytes:
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode("utf-8")
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {file_mime}\r\n\r\n"
        ).encode("utf-8")
    )
    parts.append(file_bytes)
    parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode("utf-8"))
    return b"".join(parts)


def _post_json(url: str, *, api_key: str, body: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
    raw = json.dumps(body).encode("utf-8")
    req = Request(
        url,
        data=raw,
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:400]
        except Exception:
            detail = str(exc.reason or exc)
        raise VoiceError(f"OpenAI HTTP {exc.code}: {detail or exc.reason}") from exc
    except URLError as exc:
        raise VoiceError(f"OpenAI request failed: {exc.reason}") from exc
    if not isinstance(payload, dict):
        raise VoiceError("Unexpected OpenAI response")
    return payload


def transcribe_audio(
    b64_audio: str,
    mime: str = "audio/webm",
    *,
    model: str = _DEFAULT_MODEL,
) -> dict[str, Any]:
    """Transcribe a base64 audio blob via OpenAI /v1/audio/transcriptions."""
    try:
        api_key = _openai_key()
    except VoiceError as exc:
        return {"ok": False, "error": str(exc)}
    raw_b64 = (b64_audio or "").strip()
    if not raw_b64:
        return {"ok": False, "error": "empty audio"}
    try:
        audio_bytes = base64.b64decode(raw_b64, validate=False)
    except Exception as exc:
        return {"ok": False, "error": f"invalid base64 audio: {exc}"}
    if not audio_bytes:
        return {"ok": False, "error": "empty audio"}

    file_mime = (mime or "audio/webm").strip() or "audio/webm"
    filename = f"dictation{_ext_for_mime(file_mime)}"
    chosen = (model or _DEFAULT_MODEL).strip() or _DEFAULT_MODEL

    def _call(model_id: str) -> str:
        boundary = f"----duckyvoice{uuid.uuid4().hex}"
        body = _multipart_body(
            fields={"model": model_id},
            file_field="file",
            filename=filename,
            file_bytes=audio_bytes,
            file_mime=file_mime.split(";")[0].strip() or "audio/webm",
            boundary=boundary,
        )
        req = Request(
            _TRANSCRIBE_URL,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
        )
        try:
            with urlopen(req, timeout=90.0) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8", errors="replace")[:400]
            except Exception:
                detail = str(exc.reason or exc)
            raise VoiceError(f"OpenAI HTTP {exc.code}: {detail or exc.reason}") from exc
        except URLError as exc:
            raise VoiceError(f"OpenAI request failed: {exc.reason}") from exc
        text = ""
        if isinstance(payload, dict):
            text = str(payload.get("text") or "").strip()
        if not text:
            raise VoiceError("Empty transcript")
        return text

    try:
        text = _call(chosen)
    except VoiceError as exc:
        if chosen != _FALLBACK_MODEL and "model" in str(exc).lower():
            try:
                text = _call(_FALLBACK_MODEL)
                chosen = _FALLBACK_MODEL
            except VoiceError as fallback_exc:
                return {"ok": False, "error": str(fallback_exc)}
        else:
            return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc) or "transcription failed"}

    # Audio APIs rarely return token usage — estimate so the key use still shows.
    _log_voice_usage(
        model=chosen,
        input_tokens=max(1, len(audio_bytes) // 1000),
        output_tokens=max(1, len(text) // 4),
    )
    return {"ok": True, "text": text, "model": chosen}


def create_realtime_token() -> dict[str, Any]:
    """Mint a short-lived Realtime client secret for browser transcription WebSockets.

    The frontend opens ``wss://api.openai.com/v1/realtime?intent=transcription``
    with the returned ``value`` (ek_…) — audio never passes through this process.
    """
    try:
        api_key = _openai_key()
    except VoiceError as exc:
        return {"ok": False, "error": str(exc)}

    body = {
        "expires_after": {"anchor": "created_at", "seconds": 600},
        "session": {
            "type": "transcription",
            "audio": {
                "input": {
                    "format": {"type": "audio/pcm", "rate": 24000},
                    "transcription": {"model": "gpt-4o-mini-transcribe"},
                    "turn_detection": {
                        "type": "server_vad",
                        "threshold": 0.5,
                        "prefix_padding_ms": 300,
                        "silence_duration_ms": 700,
                    },
                }
            },
        },
    }
    try:
        payload = _post_json(_CLIENT_SECRETS_URL, api_key=api_key, body=body)
    except VoiceError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": str(exc) or "token create failed"}

    value = ""
    if isinstance(payload.get("value"), str):
        value = payload["value"].strip()
    elif isinstance(payload.get("client_secret"), dict):
        value = str(payload["client_secret"].get("value") or "").strip()
    if not value:
        return {"ok": False, "error": "OpenAI returned no client secret"}
    expires_at = payload.get("expires_at")
    # Ephemeral secret mint uses the key; realtime audio bills on OpenAI's side.
    _log_voice_usage(model="gpt-4o-mini-transcribe", input_tokens=1, output_tokens=0)
    return {
        "ok": True,
        "value": value,
        "expires_at": expires_at,
        "ws_url": "wss://api.openai.com/v1/realtime?intent=transcription",
    }
